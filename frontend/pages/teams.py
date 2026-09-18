"""戰隊頁：雙戰隊雷達多邊圖比較＋戰隊數據總表格。

- 預設口徑：最新賽季（2026）六大頂級聯賽共 64 隊（LCP/LPL/LCK/LEC/LCS/CBLOL）
- 篩選列與全站一致：賽事／賽季／版本／時間／場數／路線／英雄／戰隊選手
- 左側 12 槽軸編輯器可隨時置換指標；中間 12 邊形雷達，軸外側顯示
  兩隊數值與母體排名；未選戰隊時仍顯示空的 12 邊形
- 下方 25 欄總表，戰隊欄顯示真實 LOGO＋官方縮寫＋聯賽膠囊
"""
from __future__ import annotations

import json
from functools import lru_cache
from urllib.parse import quote

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import (ALL, Input, Output, State, callback, dash_table, dcc,
                  html)
from dash.dash_table.Format import Format

from backend import data_access
from backend.data_access import Filter, TOP_LEAGUES
from backend.metrics import scoring
from frontend.components import common
from frontend.theme import PLOTLY_TEMPLATE

dash.register_page(__name__, title="戰隊", order=5)

# 缺值佔位（依使用者要求，無相關數值先顯示 123456）
PLACEHOLDER = "123456"

_POSITION_OPTIONS = [
    {"label": "全部", "value": "ALL"},
    {"label": "上路", "value": "top"},
    {"label": "打野", "value": "jng"},
    {"label": "中路", "value": "mid"},
    {"label": "下路", "value": "bot"},
    {"label": "輔助", "value": "sup"},
]

# 雷達指標目錄：(欄位, 中文名, 格式) —— 12 槽可從中任意置換
# fmt：pct 百分比／signed 帶正負號整數／kd 二位小數／vs 一位小數／int 整數
AXIS_CATALOG = [
    ("win_rate", "勝率", "pct"),
    ("champ_pool", "角色池", "int"),
    ("tower_rate", "首塔", "pct"),
    ("grub2_rate", "2+巢蟲", "pct"),
    ("dragon_rate", "首龍", "pct"),
    ("herald_rate", "預示者", "pct"),
    ("baron_rate", "首巴龍", "pct"),
    ("gold_mid", "中期金差", "signed"),
    ("team_gpm", "分均金錢", "int"),
    ("team_dpm", "分均輸出", "int"),
    ("kd", "平均KD", "kd"),
    ("vspm", "視野分數", "vs"),
    ("gd10", "金錢差@10", "signed"),
    ("dragons", "場均小龍", "vs"),
    ("firstblood_rate", "首殺率", "pct"),
    ("team_kpm", "團隊節奏", "vs"),
    ("k_pg", "場均擊殺", "vs"),
    ("d_pg", "場均死亡", "vs"),
    ("side_diff", "藍紅勝率差", "signed"),
    ("avg_len", "場均時長", "vs"),
]
_AXIS_LABELS = dict((c, zh) for c, zh, _ in AXIS_CATALOG)
_AXIS_FMT = dict((c, f) for c, _, f in AXIS_CATALOG)
DEFAULT_AXES = [c for c, _, _ in AXIS_CATALOG[:12]]
_SLOT_COUNT = 12
# 雷達極座標外圍文字的半徑（百分位 0–100，外圍標註介於 108–140）
_R_TEXT_A, _R_TEXT_NAME, _R_TEXT_B = 110, 124, 139
_R_MAX = 152

_PCT_FMT = Format(scheme="f", precision=0).symbol_suffix("%")

_COLUMNS = [
    {"name": "#", "id": "rank", "type": "numeric"},
    {"name": "戰隊", "id": "team_col", "type": "text",
     "presentation": "markdown"},
    {"name": "場數", "id": "games", "type": "numeric"},
    {"name": "時間", "id": "avg_len", "type": "numeric",
     "format": Format(scheme="f", precision=1)},
    {"name": "勝率", "id": "win_col", "type": "text"},
    {"name": "主動權", "id": "initiative", "type": "text"},
    {"name": "被動選序", "id": "passive_pick", "type": "text"},
    {"name": "被動選邊", "id": "passive_side", "type": "text"},
    {"name": "藍紅勝率差", "id": "side_diff", "type": "numeric"},
    {"name": "角色池", "id": "champ_pool", "type": "numeric"},
    {"name": "首塔", "id": "tower_rate", "type": "numeric",
     "format": _PCT_FMT},
    {"name": "2+巢蟲", "id": "grub2_rate", "type": "numeric",
     "format": _PCT_FMT},
    {"name": "首龍", "id": "dragon_rate", "type": "numeric",
     "format": _PCT_FMT},
    {"name": "預示者", "id": "herald_rate", "type": "numeric",
     "format": _PCT_FMT},
    {"name": "小龍", "id": "dragons", "type": "numeric",
     "format": Format(scheme="f", precision=1)},
    {"name": "首巴龍", "id": "baron_rate", "type": "numeric",
     "format": _PCT_FMT},
    {"name": "金錢差@10", "id": "gd10", "type": "numeric"},
    {"name": "中期金差", "id": "gold_mid", "type": "numeric"},
    {"name": "分均金錢", "id": "team_gpm", "type": "numeric"},
    {"name": "分均輸出", "id": "team_dpm", "type": "numeric"},
    {"name": "平均KD", "id": "kd_col", "type": "text"},
    {"name": "視野分數", "id": "vspm", "type": "numeric",
     "format": Format(scheme="f", precision=1)},
    # 表頭僅顯示名稱，計算方式滑鼠移上才由 tooltip 說明
    {"name": "攻擊值", "id": "attack", "type": "numeric"},
    {"name": "防禦值", "id": "defense", "type": "numeric"},
    {"name": "評分", "id": "score", "type": "numeric"},
]
# 雷達快取需涵蓋整個指標目錄，供 12 槽任意置換
_STORE_COLS = [c for c, _, _ in AXIS_CATALOG] + ["teamname", "league"]

_LEAGUE_CLASS = {
    "LCP": "lg-lcp", "LPL": "lg-lpl", "LCK": "lg-lck", "LEC": "lg-lec",
    "LCS": "lg-lcs", "CBLOL": "lg-cblol",
}


@lru_cache(maxsize=1)
def _options() -> dict:
    """頁內篩選選項（倉儲不存在時降級為空）。"""
    try:
        return data_access.get_options()
    except Exception as exc:  # noqa: BLE001
        print(f"[警告] 戰隊頁無法讀取選項：{exc}")
        return {"leagues": [], "years": [], "patches": []}


def _dropdown(idd: str, label: str, options, value=None, multi: bool = True,
              width: int = 120) -> html.Div:
    """深色下拉。"""
    if value is None:
        value = [] if multi else "ALL"
    return html.Div([
        html.Label(label),
        dcc.Dropdown(
            id=idd, options=options, value=value,
            multi=multi, clearable=False if not multi else True,
            placeholder="全部",
            style={"color": "#0B1020", "fontSize": 12, "minWidth": width},
        ),
    ], className="filter-item", style={"minWidth": width})


def _text_input(idd: str, label: str, placeholder: str,
                width: int = 120) -> html.Div:
    """深色文字篩選框。"""
    return html.Div([
        html.Label(label),
        dcc.Input(id=idd, type="text", placeholder=placeholder,
                  style={"width": width, "background": "#0E1526",
                         "color": "#E6ECF5", "border": "1px solid #22304A",
                         "borderRadius": 6, "padding": 6, "fontSize": 12}),
    ], className="filter-item")


def _filter_panel() -> html.Div:
    """頁內篩選列（順序與全站一致；預設最新賽季六大頂級聯賽）。"""
    opts = _options()
    return html.Div([
        _dropdown("team-league", "賽事",
                  [{"label": x, "value": x} for x in opts["leagues"]],
                  value=list(TOP_LEAGUES)),
        _dropdown("team-year", "賽季",
                  [{"label": x, "value": x} for x in opts["years"]],
                  value=[data_access.latest_top_year()]),
        _dropdown("team-patch", "版本",
                  [{"label": x, "value": x} for x in opts["patches"]]),
        html.Div([
            html.Label("時間"),
            dcc.DatePickerRange(
                id="team-date", display_format="YYYY-MM-DD",
                minimum_nights=0, clearable=True,
                start_date=None, end_date=None,
                style={"fontSize": 11}),
        ], className="filter-item"),
        html.Div([
            html.Label("場數"),
            dcc.Input(id="team-min-games", type="number", min=0, value=0,
                      style={"width": 64, "background": "#0E1526",
                             "color": "#E6ECF5", "border": "1px solid #22304A",
                             "borderRadius": 6, "padding": 6, "fontSize": 12}),
        ], className="filter-item"),
        _dropdown("team-position", "路線", _POSITION_OPTIONS, multi=False,
                  width=92),
        _text_input("team-champ", "英雄", "輸入英雄"),
        _text_input("team-member", "戰隊選手", "輸入戰隊或選手", width=150),
        html.Button("↺ 0", id="team-reset", n_clicks=0, title="重設篩選",
                    className="hero-reset-btn"),
    ], className="hero-filter-bar panel")


def _axis_slot(index: int, axis_col: str | None) -> html.Div:
    """單一雷達軸槽：下拉（可置換指標）＋紅 X 清除。"""
    return html.Div([
        dcc.Dropdown(
            id={"type": "team-axis-slot", "index": index},
            options=[{"label": zh, "value": c}
                     for c, zh, _ in AXIS_CATALOG],
            value=axis_col, clearable=True, searchable=False,
            placeholder="＋ 加軸",
            style={"color": "#0B1020", "fontSize": 12, "flex": 1}),
        html.Button("✕", id={"type": "team-axis-clear", "index": index},
                    className="team-axis-clear", n_clicks=0),
    ], className="team-axis-row")


def _compare_panel() -> html.Div:
    """左側選兩支戰隊＋12 槽軸編輯器，中間雷達多邊圖。"""
    return common.panel("戰隊對比", html.Div([
        html.Div([
            html.Div([
                html.Label("戰隊 A（藍）"),
                dcc.Dropdown(id="team-radar-a",
                             style={"color": "#0B1020", "fontSize": 12}),
            ], className="filter-item", style={"minWidth": 200}),
            html.Div([
                html.Label("戰隊 B（紅）"),
                dcc.Dropdown(id="team-radar-b",
                             style={"color": "#0B1020", "fontSize": 12}),
            ], className="filter-item", style={"minWidth": 200}),
            dcc.Checklist(
                id="team-h2h", options=[{"label": "僅限雙方對戰數據",
                                         "value": "h2h"}],
                value=[], style={"color": "#E6ECF5", "fontSize": 12,
                                 "marginTop": 2}),
            html.Div([
                html.Span(id="team-axis-title", className="team-axis-title",
                          children="雷達軸（12）："),
                html.Button("↺", id="team-radar-reset", n_clicks=0,
                            title="恢復預設 12 軸",
                            className="team-axis-reset"),
            ], style={"display": "flex", "alignItems": "center", "gap": 8}),
            html.Div(
                [_axis_slot(i, DEFAULT_AXES[i]) for i in range(_SLOT_COUNT)],
                className="team-axis-grid"),
        ], className="team-axis-panel"),
        # 雷達本體：尺寸與響應式（窄屏堆疊）一律由 CSS 控制
        html.Div(dcc.Graph(
            id="team-radar",
            config={"displaylogo": False, "responsive": True}),
            className="team-radar-wrap"),
    ], className="team-compare-flex"), extra_class="team-radar-card")


def layout(**_query_kwargs):
    # Dash pages 會把網址查詢字串參數以 kwarg 傳入，統一吸收避免 TypeError
    return html.Div([
        _filter_panel(),
        dcc.Store(id="team-query", data={}),
        dcc.Store(id="team-store", data=[]),
        _compare_panel(),
        common.panel("戰隊數據", html.Div([
            html.Div(id="team-count", style={"marginBottom": 6}),
            html.Div(id="team-table-wrap"),
        ])),
    ], className="page")


# ---------------------------------------------------------------------------
# 篩選控制項 ↔ 查詢條件
# ---------------------------------------------------------------------------
@callback(
    Output("team-query", "data"),
    Output("team-reset", "children"),
    Input("team-league", "value"),
    Input("team-year", "value"),
    Input("team-patch", "value"),
    Input("team-date", "start_date"),
    Input("team-date", "end_date"),
    Input("team-min-games", "value"),
    Input("team-position", "value"),
    Input("team-champ", "value"),
    Input("team-member", "value"),
)
def _sync_query(leagues, years, patches, date_start, date_end,
                min_games, position, champ, member):
    data = {
        "leagues": leagues or [],
        "years": years or [],
        "patches": patches or [],
        "date_start": date_start,
        "date_end": date_end,
        "min_games": int(min_games or 0),
        "positions": [] if not position or position == "ALL" else [position],
        "champion": (champ or "").strip(),
        "member": (member or "").strip(),
    }
    active = sum(bool(data[k]) for k in
                 ("leagues", "years", "patches", "positions", "champion",
                  "member")) + sum(1 for k in ("date_start", "date_end")
                                   if data[k]) + (1 if data["min_games"] else 0)
    return data, f"↺ {active}"


@callback(
    Output("team-league", "value"),
    Output("team-year", "value"),
    Output("team-patch", "value"),
    Output("team-date", "start_date"),
    Output("team-date", "end_date"),
    Output("team-min-games", "value"),
    Output("team-position", "value"),
    Output("team-champ", "value"),
    Output("team-member", "value"),
    Input("team-reset", "n_clicks"),
    prevent_initial_call=True,
)
def _reset_filters(_clicks):
    # 重設回到預設口徑（最新賽季六大頂級聯賽）
    return (list(TOP_LEAGUES), [data_access.latest_top_year()], [],
            None, None, 0, "ALL", "", "")


@callback(
    Output({"type": "team-axis-slot", "index": ALL}, "value"),
    Output("team-axis-title", "children"),
    Input("team-radar-reset", "n_clicks"),
    Input({"type": "team-axis-clear", "index": ALL}, "n_clicks"),
    State({"type": "team-axis-slot", "index": ALL}, "value"),
    prevent_initial_call=False,
)
def _axis_slots(_reset_n, clear_clicks, values):
    """重設鈕恢復 12 預設軸；紅 X 清空同槽；其餘狀況保持。"""
    trigger = dash.callback_context.triggered_id
    values = list(values or [])
    if trigger == "team-radar-reset":
        values = list(DEFAULT_AXES)
    elif isinstance(trigger, dict) and trigger.get("type") == \
            "team-axis-clear":
        idx = trigger["index"]
        values[idx] = None
    active = sum(1 for v in values if v)
    return values, f"雷達軸（{active}）："


def _make_filter(query: dict) -> Filter:
    """查詢字典 → Filter（含英雄／戰隊選手關鍵字）。"""
    return Filter(
        leagues=query.get("leagues") or None,
        years=[int(y) for y in query["years"]] if query.get("years") else None,
        patches=query.get("patches") or None,
        date_start=query.get("date_start") or None,
        date_end=query.get("date_end") or None,
        positions=query.get("positions") or None,
        champion=query.get("champion") or None,
        member=query.get("member") or None,
    )


def _ranking_df(query: dict) -> pd.DataFrame:
    """戰隊排行＋攻擊／防禦／評分，依評分排序。"""
    df = data_access.team_ranking(
        _make_filter(query), min_games=query.get("min_games", 0))
    if df.empty:
        return df
    df = scoring.score_teams(df)
    df = df.sort_values(["score", "games"], ascending=False).reset_index(
        drop=True)
    df["rank"] = df.index + 1
    return df


# ---------------------------------------------------------------------------
# 資料 → 表格、快取、戰隊下拉
# ---------------------------------------------------------------------------
def _axis_text(col: str, v) -> str:
    """軸數值的顯示文字。"""
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return PLACEHOLDER
    fmt = _AXIS_FMT.get(col, "int")
    if fmt == "pct":
        return f"{v:.0f}%"
    if fmt == "signed":
        return f"{v:+.0f}"
    if fmt == "kd":
        return f"{v:.2f}"
    if fmt == "vs":
        return f"{v:.1f}"
    return f"{v:.0f}"


def _clean_text(v) -> str | None:
    """空值／pandas NaN／字串 'nan' 一律視為缺值，避免組出髒路徑。"""
    if v is None:
        return None
    if isinstance(v, float) and not np.isfinite(v):
        return None
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


def _team_cell(name: str, abbr: str | None, logo: str | None,
               league: str | None) -> str:
    """戰隊欄 HTML：真實 LOGO＋官方縮寫＋聯賽彩色膠囊。"""
    logo = _clean_text(logo)
    if logo:
        src = f"/assets/team_logos/{logo}"
    else:
        # 缺真實 LOGO 時退回動態生成的縮寫徽章，不發出無效請求
        src = f"/team-badge/{quote(name)}.svg"
    safe_name = _clean_text(name) or "?"
    safe_abbr = _clean_text(abbr) or safe_name
    safe_league = _clean_text(league) or ""
    pill_cls = _LEAGUE_CLASS.get(safe_league, "lg-other")
    return (f"<img class='team-logo' src='{src}'>"
            f"<span class='team-abbr'>{safe_abbr}</span>"
            f"<span class='league-pill {pill_cls}'>{safe_league}</span>")


def _finite_or_none(v) -> float | None:
    """NaN/Inf 轉 None（小樣本隊伍部分聚合值可能缺失）。"""
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    return fv if np.isfinite(fv) else None


def _table_records(df: pd.DataFrame) -> list[dict]:
    """DataTable 記錄。"""
    records = []
    for row in df.itertuples(index=False):
        losses = int(row.games) - int(row.wins)
        win_col = f"{row.win_rate:5.1f}% ({int(row.wins)}-{losses})"
        if np.isfinite(row.kd) and np.isfinite(row.k_pg) and np.isfinite(
                row.d_pg):
            kd_col = f"{row.kd:4.2f} ({row.k_pg:4.2f}/{row.d_pg:4.2f})"
        else:
            kd_col = PLACEHOLDER
        pool = _finite_or_none(row.champ_pool)
        records.append({
            "rank": int(row.rank),
            "team_col": _team_cell(row.teamname, getattr(row, "abbr", None),
                                   getattr(row, "logo_file", None),
                                   row.league),
            "games": int(row.games),
            "avg_len": _finite_or_none(row.avg_len),
            "win_col": win_col,
            "initiative": PLACEHOLDER,
            "passive_pick": PLACEHOLDER,
            "passive_side": PLACEHOLDER,
            "side_diff": _finite_or_none(row.side_diff),
            "champ_pool": int(pool) if pool is not None else None,
            "tower_rate": _finite_or_none(row.tower_rate),
            "grub2_rate": _finite_or_none(row.grub2_rate),
            "dragon_rate": _finite_or_none(row.dragon_rate),
            "herald_rate": _finite_or_none(row.herald_rate),
            "dragons": _finite_or_none(row.dragons),
            "baron_rate": _finite_or_none(row.baron_rate),
            "gd10": _finite_or_none(row.gd10),
            "gold_mid": _finite_or_none(row.gold_mid),
            "team_gpm": _finite_or_none(row.team_gpm),
            "team_dpm": _finite_or_none(row.team_dpm),
            "kd_col": kd_col,
            "vspm": _finite_or_none(row.vspm),
            "attack": _finite_or_none(row.attack),
            "defense": _finite_or_none(row.defense),
            "score": _finite_or_none(row.score),
        })
    return records


def _table(df: pd.DataFrame) -> dash_table.DataTable:
    """戰隊數據總表格。"""
    styles = common.table_styles()
    styles["style_table"] = {"overflowX": "auto"}
    styles["style_cell"]["minWidth"] = 62
    styles["style_cell"]["maxWidth"] = 200
    styles["style_cell_conditional"] = [
        {"if": {"column_id": "team_col"}, "textAlign": "left",
         "minWidth": 180},
        {"if": {"column_id": "rank"}, "width": 40, "color": "#8A97AB"},
        {"if": {"column_id": "initiative"}, "color": "#8A97AB"},
        {"if": {"column_id": "passive_pick"}, "color": "#8A97AB"},
        {"if": {"column_id": "passive_side"}, "color": "#8A97AB"},
        {"if": {"column_id": "win_col"}, "textAlign": "right"},
        {"if": {"column_id": "kd_col"}, "textAlign": "right",
         "fontFamily": "Consolas, monospace", "fontSize": 11},
        {"if": {"column_id": "score"}, "fontWeight": 700,
         "color": "#F05A6A"},
    ]
    styles["style_data_conditional"] = [
        {"if": {"row_index": "odd"},
         "backgroundColor": "rgba(255,255,255,0.02)"},
        {"if": {"column_id": "side_diff", "filter_query": "{side_diff} < 0"},
         "color": "#4C8DFF"},
        {"if": {"column_id": "side_diff", "filter_query": "{side_diff} > 0"},
         "color": "#F05A6A", "fontWeight": 700},
        {"if": {"column_id": "gd10", "filter_query": "{gd10} < 0"},
         "color": "#4C8DFF"},
        {"if": {"column_id": "gd10", "filter_query": "{gd10} > 0"},
         "color": "#3DDC84"},
        {"if": {"column_id": "gold_mid",
                "filter_query": "{gold_mid} < 0"}, "color": "#4C8DFF"},
        {"if": {"column_id": "gold_mid",
                "filter_query": "{gold_mid} > 0"}, "color": "#3DDC84"},
        {"if": {"column_id": "attack", "filter_query": "{attack} >= 90"},
         "color": "#F05A6A", "fontWeight": 700},
        {"if": {"column_id": "defense", "filter_query": "{defense} >= 90"},
         "color": "#F05A6A", "fontWeight": 700},
    ]
    return dash_table.DataTable(
        id="team-table", columns=_COLUMNS, data=_table_records(df),
        sort_action="native",
        sort_by=[{"column_id": "rank", "direction": "asc"}],
        page_size=40, page_action="native", page_current=0,
        markdown_options={"html": True, "link_target": "_blank"},
        tooltip_header={
            "attack": {"value": scoring.team_attack_tooltip(),
                       "type": "text"},
            "defense": {"value": scoring.team_defense_tooltip(),
                        "type": "text"},
            "score": {"value": scoring.team_score_tooltip(),
                      "type": "text"},
        },
        tooltip_delay=150,
        **styles,
    )


@callback(
    Output("team-table-wrap", "children"),
    Output("team-store", "data"),
    Output("team-count", "children"),
    Output("team-radar-a", "options"),
    Output("team-radar-b", "options"),
    Input("team-query", "data"),
)
def _load(query):
    query = query or {}
    try:
        df = _ranking_df(query)
    except Exception as exc:  # noqa: BLE001
        empty = common.empty_state("查詢失敗", str(exc))
        return empty, [], "0 隊", [], []
    options = [{"label": f"{r.abbr or r.teamname}　{r.teamname}",
                "value": r.teamname}
               for r in df.itertuples(index=False)]
    if df.empty:
        return (common.empty_state("沒有符合的戰隊",
                                   "請放寬場數門檻或調整篩選條件"),
                [], "0 隊", options, options)
    # to_json 會把 NaN 輸出成 null，避免 Dash JSON 序列化 500
    store = json.loads(df[_STORE_COLS].round(2).to_json(
        orient="records"))
    return _table(df), store, f"{len(df)} 隊", options, options


# ---------------------------------------------------------------------------
# 雷達多邊圖
# ---------------------------------------------------------------------------
def _pct(value: float, base: pd.Series) -> float:
    """值在母體分佈中的同儕百分位（0–100）。"""
    s = pd.to_numeric(base, errors="coerce").dropna()
    if s.empty or not np.isfinite(value):
        return 50.0
    return float((s < value).mean() * 100)


def _rank_of(base: pd.Series, value) -> int | None:
    """母體降序名次（並列同名次）。"""
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return None
    s = pd.to_numeric(base, errors="coerce").dropna()
    if s.empty:
        return None
    return int((s > value).sum() + 1)


def _value_label(col: str, value, rank: int | None) -> str:
    """數值＋排名文字，如 63% #8。"""
    text = _axis_text(col, value)
    return f"{text}  #{rank}" if rank else text


def _empty_polygon(axes: list[str]) -> go.Figure:
    """無戰隊時的空 12 邊形（僅網格與軸名）。"""
    return _radar_figure(axes, None, None, None, None)


def _radar_figure(axes: list[str], full_df: pd.DataFrame | None,
                  comp_df: pd.DataFrame | None,
                  name_a: str | None, name_b: str | None) -> go.Figure:
    """繪製雷達；full_df 為百分位／排名母體，comp_df 為實際取值來源。

    外圍三層文字：A 數值#排名（藍）、軸名（灰）、B 數值#排名（紅）。
    未選戰隊時 comp_df 為 None，僅留軸名形成空 12 邊形。
    """
    n = max(len(axes), 1)
    angles = [360 * i / n for i in range(n)]

    fig = go.Figure()
    if full_df is not None and comp_df is not None:
        rows = {}
        for name in (name_a, name_b):
            hit = comp_df[comp_df["teamname"] == name]
            rows[name] = None if hit.empty else hit.iloc[0]
        for name, color in ((name_a, "#4C8DFF"), (name_b, "#F05A6A")):
            row = rows[name]
            if row is None:
                continue
            r_vals = [_pct(float(row[c]), full_df[c]) if not pd.isna(
                row[c]) else None for c in axes]
            # 收合多邊形（首尾相接）；細線＋小頂點（同色邊框）
            fig.add_trace(go.Scatterpolar(
                r=r_vals + r_vals[:1], theta=angles + angles[:1],
                fill="toself", name=name, mode="lines+markers",
                line=dict(color=color, width=1),
                marker=dict(size=3, color=color,
                            line=dict(color=color, width=1)),
                fillcolor=color, opacity=0.55,
                customdata=[_AXIS_LABELS[c] for c in axes] * 2,
                hovertemplate="<b>%{fullData.name}</b><br>%{customdata}"
                              "<extra></extra>"))
        # 外圍三層文字（數值#排名／軸名）
        # 藍上紅下：除半徑不同外，再各偏移少許角度（切線方向），
        # 讓左/右水平軸的兩列橫式數值能上下錯開，避免與軸名重疊
        for name, radius, color, layer in (
                (name_a, _R_TEXT_A, "#4C8DFF", "a"),
                (name_b, _R_TEXT_B, "#F05A6A", "b")):
            dtheta = -3.2 if layer == "a" else 3.2
            row = rows[name]
            texts, t_theta = [], []
            for i, col in enumerate(axes):
                if row is None or pd.isna(row[col]):
                    continue
                rank = _rank_of(full_df[col], row[col])
                texts.append(_value_label(col, row[col], rank))
                t_theta.append(angles[i] + dtheta)
            if texts:
                fig.add_trace(go.Scatterpolar(
                    r=[radius] * len(texts), theta=t_theta, mode="text",
                    text=texts, textfont=dict(color=color, size=11),
                    hoverinfo="skip", showlegend=False))
        fig.update_layout(
            legend=dict(orientation="h", y=1.09, font_size=12))
    # 軸名固定中層（無資料時仍顯示）
    fig.add_trace(go.Scatterpolar(
        r=[_R_TEXT_NAME] * n, theta=angles,
        mode="text", text=[_AXIS_LABELS[c] for c in axes],
        textfont=dict(color="#E6ECF5", size=13),
        hoverinfo="skip", showlegend=False))
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        polar=dict(
            # 只藏數值標籤，保留同心網格環（visible=False 會連網格一起消失）
            radialaxis=dict(showticklabels=False, ticks="",
                            range=[0, _R_MAX], tick0=20, dtick=20,
                            gridcolor="#22304A", linecolor="#22304A"),
            # 明確指定 n 個軸的網格位置，空狀態也有完整 n 邊形骨架
            angularaxis=dict(
                tickvals=angles, showticklabels=False, ticks="",
                direction="clockwise", rotation=90,
                gridcolor="#2A3A58", linecolor="#2A3A58"),
            bgcolor="rgba(0,0,0,0)",
            # Plotly 7：linear＝直線構成多邊形（舊名 polygon 已廢棄）
            gridshape="linear"),
        # 不設固定 height：雷達隨對比卡片（約半頁高）響應式縮放，避免溢出
        margin=dict(l=40, r=40, t=42, b=52),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


@callback(
    Output("team-radar", "figure"),
    Input("team-radar-a", "value"),
    Input("team-radar-b", "value"),
    Input({"type": "team-axis-slot", "index": ALL}, "value"),
    Input("team-h2h", "value"),
    State("team-store", "data"),
    State("team-query", "data"),
)
def _render_radar(name_a, name_b, axis_values, h2h, store, query):
    axes = [v for v in (axis_values or []) if v]
    if not store or not axes:
        # 連軸都清空時，仍以預設 12 軸顯示空網格
        return _radar_figure(axes or DEFAULT_AXES, None, None, None, None)
    full_df = pd.DataFrame(store)
    if not name_a and not name_b:
        return _radar_figure(axes, None, None, None, None)
    if h2h and "h2h" in h2h and name_a and name_b:
        try:
            # 對戰樣本小，h2h 查詢不套用場數門檻
            comp_df = data_access.team_ranking(
                _make_filter(query or {}), min_games=0,
                h2h_teams=(name_a, name_b))
            if comp_df.empty:
                comp_df = full_df
        except Exception:  # noqa: BLE001
            comp_df = full_df
    else:
        comp_df = full_df
    return _radar_figure(axes, full_df, comp_df,
                         name_a or None, name_b or None)
