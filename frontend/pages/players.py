"""選手頁：雙選手 8 邊形雷達對比＋22 欄選手數據總表。

- 預設口徑：最新賽季（2026）六大頂級聯賽戰隊選手（排除 Versus 表演賽）
- 左側 8 槽軸編輯器可隨時置換／清空／一鍵加軸；右側 8 邊形雷達，
  軸外側顯示兩位選手的數值與母體排名；未選選手時仍顯示空 8 邊形
- 下方表格嚴格依指定 22 欄順序；計算欄位滑鼠移上會顯示計算方式
"""
from __future__ import annotations

import json

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

dash.register_page(__name__, title="選手", order=4)

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

# 雷達指標目錄：(欄位, 中文名, 格式) —— 8 槽可從中任意置換
# fmt：pct 百分比／signpct 帶正負號百分比／signed 帶正負號整數／
#       kd 一位小數／vs 一位小數／int 整數
AXIS_CATALOG = [
    ("win_rate", "勝率", "pct"),
    ("champ_pool", "角色池", "int"),
    ("kda", "KDA", "kd"),
    ("kp", "參與率", "pct"),
    ("lane_win", "贏線率", "pct"),
    ("gpm", "分均金錢", "int"),
    ("dpm", "分均輸出", "int"),
    ("dtaken", "分均承傷", "int"),
    ("dmg_share", "輸出比", "pct"),
    ("death_share", "死亡比", "pct"),
    ("fb_diff", "首殺差", "signpct"),
    ("gold_eff", "金轉傷", "pct"),
    ("gold_mid", "中期金差", "signed"),
    ("vspm", "分均視分", "vs"),
    ("attack", "攻擊值", "int"),
    ("defense", "防禦值", "int"),
    ("score", "評分", "int"),
]
_AXIS_LABELS = dict((c, zh) for c, zh, _ in AXIS_CATALOG)
_AXIS_FMT = dict((c, f) for c, _, f in AXIS_CATALOG)
# 預設 8 軸：由頂端「勝率」順時針，對齊參考圖 3 的軸序
DEFAULT_AXES = ["win_rate", "champ_pool", "kda", "kp", "lane_win",
                "gpm", "dpm", "dtaken"]
_SLOT_COUNT = 8
# 雷達極座標外圍文字半徑（百分位 0–100，外圍標註介於 108–140）
_R_TEXT_A, _R_TEXT_NAME, _R_TEXT_B = 110, 124, 139
_R_MAX = 152

_PCT_FMT = Format(scheme="f", precision=0).symbol_suffix("%")
_KD10_FMT = Format(scheme="f", precision=1, sign="+")

# 22 欄總表（嚴格依指定順序）
_COLUMNS = [
    {"name": "#", "id": "rank", "type": "numeric"},
    {"name": "選手", "id": "player_col", "type": "text",
     "presentation": "markdown"},
    {"name": "場數", "id": "games", "type": "numeric"},
    {"name": "勝率", "id": "win_rate", "type": "numeric",
     "format": _PCT_FMT},
    {"name": "角色池", "id": "champ_pool", "type": "numeric"},
    {"name": "參與率", "id": "kp", "type": "numeric", "format": _PCT_FMT},
    {"name": "後選率", "id": "late_pick", "type": "text"},
    {"name": "KDA", "id": "kda_col", "type": "text",
     "presentation": "markdown"},
    {"name": "KDA差@10", "id": "kda_diff10", "type": "numeric",
     "format": _KD10_FMT},
    {"name": "贏線率", "id": "lane_win_col", "type": "text",
     "presentation": "markdown"},
    {"name": "輸出比", "id": "dmg_share", "type": "numeric",
     "format": _PCT_FMT},
    {"name": "死亡比", "id": "death_share", "type": "numeric",
     "format": _PCT_FMT},
    {"name": "首殺差", "id": "fb_diff", "type": "numeric",
     "format": Format(scheme="f", precision=0, sign="+")
     .symbol_suffix("%")},
    {"name": "金轉傷", "id": "gold_eff", "type": "numeric",
     "format": _PCT_FMT},
    {"name": "中期金差", "id": "gold_mid", "type": "numeric",
     "format": Format(scheme="f", precision=0, sign="+")},
    {"name": "分均金錢", "id": "gpm", "type": "numeric"},
    {"name": "分均輸出", "id": "dpm", "type": "numeric"},
    {"name": "分均承傷", "id": "dtaken", "type": "numeric"},
    {"name": "分均視分", "id": "vspm", "type": "numeric",
     "format": Format(scheme="f", precision=1)},
    {"name": "攻擊值", "id": "attack", "type": "numeric"},
    {"name": "防禦值", "id": "defense", "type": "numeric"},
    {"name": "評分", "id": "score", "type": "numeric"},
]

# 雷達快取需涵蓋整個指標目錄與表格 HTML 組字所需原始欄位
_STORE_COLS = (["playername", "team", "league", "position", "abbr",
                "games", "wins", "k_pg", "d_pg", "a_pg", "lane_gd15"]
               + [c for c, _, _ in AXIS_CATALOG])

_LEAGUE_CLASS = {
    "LCP": "lg-lcp", "LPL": "lg-lpl", "LCK": "lg-lck", "LEC": "lg-lec",
    "LCS": "lg-lcs", "CBLOL": "lg-cblol",
}

# 計算欄位說明（滑鼠移到數字上顯示；直接取自 DATA 的欄位不列入）
FORMULAS = {
    "win_rate": "勝率＝勝場數 ÷ 總場數 × 100%（由每場勝/敗結果聚合）",
    "champ_pool": "角色池＝該選手在篩選範圍內使用過的「不同英雄」數量"
                  "（COUNT DISTINCT champion）",
    "kp": "參與率＝每場（擊殺＋助攻）÷ 團隊總擊殺 × 100%，再跨場平均",
    "late_pick": "後選率需選角順位（BP pick order）資料，目前 Oracle "
                 "資料集沒有此欄位，暫以 123456 佔位",
    "kda_col": "KDA＝(總擊殺＋總助攻) ÷ 總死亡（死亡為 0 時記 K+A）；"
               "括號內為場均 擊殺／死亡／助攻",
    "kda_diff10": "KDA差@10＝與同局、同位置對手相比，10 分鐘時 "
                  "(擊殺＋助攻)÷死亡 的差值，再跨場平均（死亡 0 以 1 計）",
    "lane_win_col": "贏線率＝15 分鐘時金錢領先對位（golddiff@15＞0）的"
                    "場數佔比；括號為平均 golddiff@15",
    "dmg_share": "輸出比＝每場對英雄傷害佔團隊總傷害的比例（damageshare）"
                 "× 100%，再跨場平均",
    "death_share": "死亡比＝每場選手死亡數 ÷ 團隊總死亡數 × 100%，"
                   "再跨場平均",
    "fb_diff": "首殺差＝(首殺率＋首殺助攻率 − 首殺死亡率) × 100%，"
               "再跨場平均",
    "gold_eff": "金轉傷＝分均輸出 ÷ 分均金錢 × 100%（每 100 經濟轉化的"
                "傷害佔比）",
    "gold_mid": "中期金差＝golddiff@25 − golddiff@10（缺 25 分數據時以 "
                "golddiff@15 代替），再跨場平均",
    "gpm": "分均金錢＝totalgold × 60 ÷ 遊戲秒數（每分鐘經濟），"
           "再跨場平均",
    "attack": "攻擊值＝勝率、KDA、參與率、分均輸出、首殺率五項同儕百分位"
              "的平均，再做開根號精英化拉伸（100×√(平均/100)）",
    "defense": "防禦值＝勝率、中期金差、分均視分、場均死亡（反向）四項同儕"
               "百分位的平均，再做開根號精英化拉伸（100×√(平均/100)）",
    "score": "評分＝攻擊值×70%＋防禦值×30%，另加場數穩定性加分"
             "（最多 5 分），最高 100 分",
}
# 數字格 tooltip（玩家移到「數字上」即可看到計算方式）
_CELL_TIP_COLS = ["win_rate", "champ_pool", "kp", "late_pick", "kda_col",
                  "kda_diff10", "lane_win_col", "dmg_share", "death_share",
                  "fb_diff", "gold_eff", "gold_mid", "gpm", "attack",
                  "defense", "score"]


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


def _options() -> dict:
    """頁內篩選選項（倉儲不存在時降級為空）。"""
    try:
        return data_access.get_options()
    except Exception as exc:  # noqa: BLE001
        print(f"[警告] 選手頁無法讀取選項：{exc}")
        return {"leagues": [], "years": [], "patches": []}


def _filter_panel() -> html.Div:
    """頁內篩選列（預設最新賽季六大頂級聯賽）。"""
    opts = _options()
    return html.Div([
        _dropdown("player-league", "賽事",
                  [{"label": x, "value": x} for x in opts["leagues"]],
                  value=list(TOP_LEAGUES)),
        _dropdown("player-year", "賽季",
                  [{"label": x, "value": x} for x in opts["years"]],
                  value=[data_access.latest_top_year()]),
        _dropdown("player-patch", "版本",
                  [{"label": x, "value": x} for x in opts["patches"]]),
        html.Div([
            html.Label("時間"),
            dcc.DatePickerRange(
                id="player-date", display_format="YYYY-MM-DD",
                minimum_nights=0, clearable=True,
                start_date=None, end_date=None,
                style={"fontSize": 11}),
        ], className="filter-item"),
        html.Div([
            html.Label("場數"),
            dcc.Input(id="player-min-games", type="number", min=0, value=0,
                      style={"width": 64, "background": "#0E1526",
                             "color": "#E6ECF5", "border": "1px solid #22304A",
                             "borderRadius": 6, "padding": 6, "fontSize": 12}),
        ], className="filter-item"),
        _dropdown("player-position", "路線", _POSITION_OPTIONS, multi=False,
                  width=92),
        _text_input("player-champ", "英雄", "輸入英雄"),
        _text_input("player-member", "戰隊選手", "輸入戰隊或選手", width=150),
        html.Button("↺ 0", id="player-reset", n_clicks=0, title="重設篩選",
                    className="hero-reset-btn"),
    ], className="hero-filter-bar panel")


def _axis_slot(index: int, axis_col: str | None) -> html.Div:
    """單一雷達軸槽：下拉（可置換指標）＋紅 X 清除。"""
    return html.Div([
        dcc.Dropdown(
            id={"type": "player-axis-slot", "index": index},
            options=[{"label": zh, "value": c}
                     for c, zh, _ in AXIS_CATALOG],
            value=axis_col, clearable=True, searchable=False,
            placeholder="＋ 加軸",
            style={"color": "#0B1020", "fontSize": 12, "flex": 1}),
        html.Button("✕", id={"type": "player-axis-clear", "index": index},
                    className="team-axis-clear", n_clicks=0),
    ], className="team-axis-row")


def _compare_panel() -> html.Div:
    """左側選兩位選手＋8 槽軸編輯器，右側雷達 8 邊形。"""
    return common.panel("選手對比", html.Div([
        html.Div([
            html.Div([
                html.Label("選手 A（藍）"),
                dcc.Dropdown(id="player-radar-a",
                             style={"color": "#0B1020", "fontSize": 12}),
            ], className="filter-item", style={"minWidth": 200}),
            html.Div([
                html.Label("選手 B（紅）"),
                dcc.Dropdown(id="player-radar-b",
                             style={"color": "#0B1020", "fontSize": 12}),
            ], className="filter-item", style={"minWidth": 200}),
            html.Div([
                dcc.Checklist(
                    id="player-h2h", options=[{"label": "僅限雙方對戰數據",
                                               "value": "h2h"}],
                    value=[], style={"color": "#E6ECF5", "fontSize": 12}),
                html.Span(id="player-h2h-note",
                          style={"color": "#8A97AB", "fontSize": 12,
                                 "marginLeft": 6}),
            ], style={"display": "flex", "alignItems": "center",
                      "marginTop": 2}),
            html.Div([
                html.Span(id="player-axis-title", className="team-axis-title",
                          children="雷達軸（8）："),
                html.Button("＋軸", id="player-radar-add", n_clicks=0,
                            title="加入第一個未使用的指標",
                            className="team-axis-reset",
                            style={"width": "auto", "padding": "0 10px",
                                   "marginLeft": 8}),
                html.Button("↺", id="player-radar-reset", n_clicks=0,
                            title="恢復預設 8 軸",
                            className="team-axis-reset"),
            ], style={"display": "flex", "alignItems": "center", "gap": 8}),
            html.Div(
                [_axis_slot(i, DEFAULT_AXES[i]) for i in range(_SLOT_COUNT)],
                className="team-axis-grid"),
        ], className="team-axis-panel"),
        html.Div(dcc.Graph(
            id="player-radar",
            config={"displaylogo": False, "responsive": True}),
            className="team-radar-wrap"),
    ], className="team-compare-flex"), extra_class="team-radar-card")


def layout(**_query_kwargs):
    # Dash pages 會把網址查詢字串參數以 kwarg 傳入，統一吸收避免 TypeError
    return html.Div([
        _filter_panel(),
        dcc.Store(id="player-query", data={}),
        dcc.Store(id="player-store", data=[]),
        _compare_panel(),
        common.panel("選手數據", html.Div([
            html.Div(id="player-count", style={"marginBottom": 6}),
            html.Div(id="player-table-wrap"),
        ])),
    ], className="page")


# ---------------------------------------------------------------------------
# 篩選控制項 ↔ 查詢條件
# ---------------------------------------------------------------------------
@callback(
    Output("player-query", "data"),
    Output("player-reset", "children"),
    Input("player-league", "value"),
    Input("player-year", "value"),
    Input("player-patch", "value"),
    Input("player-date", "start_date"),
    Input("player-date", "end_date"),
    Input("player-min-games", "value"),
    Input("player-position", "value"),
    Input("player-champ", "value"),
    Input("player-member", "value"),
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
    Output("player-league", "value"),
    Output("player-year", "value"),
    Output("player-patch", "value"),
    Output("player-date", "start_date"),
    Output("player-date", "end_date"),
    Output("player-min-games", "value"),
    Output("player-position", "value"),
    Output("player-champ", "value"),
    Output("player-member", "value"),
    Input("player-reset", "n_clicks"),
    prevent_initial_call=True,
)
def _reset_filters(_clicks):
    # 重設回到預設口徑（最新賽季六大頂級聯賽）
    return (list(TOP_LEAGUES), [data_access.latest_top_year()], [],
            None, None, 0, "ALL", "", "")


def _next_free_axis(values: list) -> str | None:
    """回傳第一個未使用的指標欄位（給＋軸鈕）。"""
    used = {v for v in values if v}
    for col, _, _ in AXIS_CATALOG:
        if col not in used:
            return col
    return None


@callback(
    Output({"type": "player-axis-slot", "index": ALL}, "value"),
    Output("player-axis-title", "children"),
    Input("player-radar-add", "n_clicks"),
    Input("player-radar-reset", "n_clicks"),
    Input({"type": "player-axis-clear", "index": ALL}, "n_clicks"),
    State({"type": "player-axis-slot", "index": ALL}, "value"),
    prevent_initial_call=False,
)
def _axis_slots(_add_n, _reset_n, _clear_clicks, values):
    """＋軸補第一個空格／↺ 恢復 8 預設軸／紅 X 清空同槽。"""
    trigger = dash.callback_context.triggered_id
    values = list(values or [])
    if trigger == "player-radar-reset":
        values = list(DEFAULT_AXES)
    elif trigger == "player-radar-add":
        if None in values or (len(values) < _SLOT_COUNT):
            free_idx = next((i for i, v in enumerate(values) if not v), None)
            nxt = _next_free_axis(values)
            if free_idx is not None and nxt:
                values[free_idx] = nxt
    elif isinstance(trigger, dict) and trigger.get("type") == \
            "player-axis-clear":
        values[trigger["index"]] = None
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
    """選手排行＋攻擊／防禦／評分，依評分排序。"""
    df = data_access.player_ranking(
        _make_filter(query), min_games=query.get("min_games", 0))
    if df.empty:
        return df
    df = scoring.score_players(df)
    df = df.sort_values(["score", "games"], ascending=False).reset_index(
        drop=True)
    df["rank"] = df.index + 1
    return df


# ---------------------------------------------------------------------------
# 資料 → 表格、快取、選手下拉
# ---------------------------------------------------------------------------
def _axis_text(col: str, v) -> str:
    """軸數值的顯示文字。"""
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return PLACEHOLDER
    fmt = _AXIS_FMT.get(col, "int")
    if fmt == "pct":
        return f"{v:.0f}%"
    if fmt == "signpct":
        return f"{v:+.0f}%"
    if fmt == "signed":
        return f"{v:+.0f}"
    if fmt == "kd":
        return f"{v:.1f}"
    if fmt == "vs":
        return f"{v:.1f}"
    return f"{v:.0f}"


def _finite_or_none(v) -> float | None:
    """NaN/Inf 轉 None。"""
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    return fv if np.isfinite(fv) else None


def _player_cell(abbr: str | None, name: str, league: str | None) -> str:
    """選手欄 HTML：隊伍縮寫＋空白＋選手名＋聯賽彩色膠囊。"""
    pill_cls = _LEAGUE_CLASS.get(league or "", "lg-other")
    return (f"<span class='team-abbr'>{abbr or ''}</span> "
            f"<span class='player-name'>{name}</span> "
            f"<span class='league-pill {pill_cls}'>{league or ''}</span>")


def _table_records(df: pd.DataFrame) -> list[dict]:
    """DataTable 記錄（22 欄；HTML 欄位一併組字）。"""
    records = []
    for row in df.itertuples(index=False):
        kda_col = PLACEHOLDER
        if np.isfinite(row.kda) and np.isfinite(row.k_pg) and np.isfinite(
                row.d_pg) and np.isfinite(row.a_pg):
            kda_col = (f"<span class='kda-main'>{row.kda:.1f}</span>"
                       f"<span class='cell-sub'>"
                       f"({row.k_pg:.1f} / {row.d_pg:.1f} / {row.a_pg:.1f})"
                       f"</span>")
        lane_col = PLACEHOLDER
        if np.isfinite(row.lane_win):
            gd = f"{row.lane_gd15:+.0f}" if np.isfinite(row.lane_gd15) else ""
            lane_col = (f"<span class='kda-main'>{row.lane_win:.0f}%</span>"
                        f"<span class='cell-sub'>({gd})</span>")
        records.append({
            "rank": int(row.rank),
            "player_col": _player_cell(getattr(row, "abbr", None),
                                       row.playername, row.league),
            "games": int(row.games),
            "win_rate": _finite_or_none(row.win_rate),
            "champ_pool": int(row.champ_pool) if np.isfinite(
                row.champ_pool) else None,
            "kp": _finite_or_none(row.kp),
            "late_pick": PLACEHOLDER,
            "kda_col": kda_col,
            "kda_diff10": _finite_or_none(row.kda_diff10),
            "lane_win_col": lane_col,
            "dmg_share": _finite_or_none(row.dmg_share),
            "death_share": _finite_or_none(row.death_share),
            "fb_diff": _finite_or_none(row.fb_diff),
            "gold_eff": _finite_or_none(row.gold_eff),
            "gold_mid": _finite_or_none(row.gold_mid),
            "gpm": _finite_or_none(row.gpm),
            "dpm": _finite_or_none(row.dpm),
            "dtaken": _finite_or_none(row.dtaken),
            "vspm": _finite_or_none(row.vspm),
            "attack": _finite_or_none(row.attack),
            "defense": _finite_or_none(row.defense),
            "score": _finite_or_none(row.score),
        })
    return records


def _tooltip_data(records: list[dict]) -> list[dict]:
    """每列計算欄位都掛上計算方式說明。"""
    return [{col: {"value": FORMULAS[col], "type": "text"}
             for col in _CELL_TIP_COLS} for _ in records]


def _max_conditions(records: list[dict]) -> list[dict]:
    """各數值欄最大值以紅色強調（對照參考圖欄內最強值標紅）。"""
    conds = []
    for col in ("games", "champ_pool", "win_rate", "kp", "dmg_share",
                "gpm", "dpm", "dtaken", "vspm", "gold_mid", "attack",
                "defense", "score"):
        vals = [r[col] for r in records if r.get(col) is not None]
        if vals:
            conds.append({"if": {"column_id": col,
                                 "filter_query": f"{{{col}}} = {max(vals)}"},
                          "color": "#F05A6A", "fontWeight": 700})
    return conds


def _table(df: pd.DataFrame) -> dash_table.DataTable:
    """22 欄選手數據總表格。"""
    styles = common.table_styles()
    styles["style_table"] = {"overflowX": "auto"}
    styles["style_cell"]["minWidth"] = 58
    styles["style_cell"]["maxWidth"] = 200
    styles["style_cell_conditional"] = [
        {"if": {"column_id": "player_col"}, "textAlign": "left",
         "minWidth": 168},
        {"if": {"column_id": "rank"}, "width": 40, "color": "#8A97AB"},
        {"if": {"column_id": "late_pick"}, "color": "#8A97AB"},
        {"if": {"column_id": "kda_col"}, "textAlign": "center",
         "lineHeight": "1.15"},
        {"if": {"column_id": "lane_win_col"}, "textAlign": "center",
         "lineHeight": "1.15"},
        {"if": {"column_id": "score"}, "fontWeight": 700},
    ]
    # 攻擊／防禦／評分：高分暖色徽章（對照參考圖的紅橘色票）
    badge = []
    for col in ("attack", "defense", "score"):
        badge += [
            {"if": {"column_id": col, "filter_query": f"{{{col}}} >= 95"},
             "backgroundColor": "rgba(224,49,49,.90)", "color": "#FFFFFF",
             "borderRadius": 4, "fontWeight": 700},
            {"if": {"column_id": col,
                    "filter_query": f"{{{col}}} >= 90 && {{{col}}} < 95"},
             "backgroundColor": "rgba(224,90,60,.50)", "color": "#FFFFFF",
             "borderRadius": 4, "fontWeight": 700},
            {"if": {"column_id": col,
                    "filter_query": f"{{{col}}} >= 85 && {{{col}}} < 90"},
             "backgroundColor": "rgba(225,140,60,.38)", "color": "#FFE9D6",
             "borderRadius": 4},
            {"if": {"column_id": col,
                    "filter_query": f"{{{col}}} >= 80 && {{{col}}} < 85"},
             "backgroundColor": "rgba(225,170,70,.26)", "color": "#FBE3C0",
             "borderRadius": 4},
            {"if": {"column_id": col, "filter_query": f"{{{col}}} < 80"},
             "backgroundColor": "rgba(255,255,255,.06)",
             "color": "#C7D0DE", "borderRadius": 4},
        ]
    records = _table_records(df)
    styles["style_data_conditional"] = [
        {"if": {"row_index": "odd"},
         "backgroundColor": "rgba(255,255,255,0.02)"},
        {"if": {"column_id": "fb_diff", "filter_query": "{fb_diff} < 0"},
         "color": "#4C8DFF"},
        {"if": {"column_id": "fb_diff", "filter_query": "{fb_diff} > 0"},
         "color": "#F05A6A"},
        {"if": {"column_id": "kda_diff10",
                "filter_query": "{kda_diff10} < 0"}, "color": "#4C8DFF"},
        {"if": {"column_id": "kda_diff10",
                "filter_query": "{kda_diff10} > 0"}, "color": "#F05A6A"},
        {"if": {"column_id": "gold_mid", "filter_query": "{gold_mid} < 0"},
         "color": "#4C8DFF"},
        {"if": {"column_id": "gold_mid", "filter_query": "{gold_mid} > 0"},
         "color": "#F05A6A"},
        *_max_conditions(records), *badge,
    ]
    return dash_table.DataTable(
        id="player-table", columns=_COLUMNS, data=records,
        sort_action="native",
        sort_by=[{"column_id": "rank", "direction": "asc"}],
        page_size=40, page_action="native", page_current=0,
        markdown_options={"html": True, "link_target": "_blank"},
        tooltip_data=_tooltip_data(records),
        tooltip_header={col: {"value": FORMULAS[col], "type": "text"}
                        for col in _CELL_TIP_COLS},
        tooltip_delay=120,
        **styles,
    )


@callback(
    Output("player-table-wrap", "children"),
    Output("player-store", "data"),
    Output("player-count", "children"),
    Output("player-radar-a", "options"),
    Output("player-radar-b", "options"),
    Input("player-query", "data"),
)
def _load(query):
    query = query or {}
    try:
        df = _ranking_df(query)
    except Exception as exc:  # noqa: BLE001
        empty = common.empty_state("查詢失敗", str(exc))
        return empty, [], "0 位", [], []
    options = [{"label": f"{r.abbr or '　'} {r.playername}",
                "value": r.playername}
               for r in df.itertuples(index=False)]
    if df.empty:
        return (common.empty_state("沒有符合的選手",
                                   "請放寬場數門檻或調整篩選條件"),
                [], "0 位", options, options)
    # to_json 會把 NaN 輸出成 null，避免 Dash JSON 序列化 500
    store = json.loads(df[_STORE_COLS].round(2).to_json(orient="records"))
    return _table(df), store, f"{len(df)} 位", options, options


# ---------------------------------------------------------------------------
# 8 邊形雷達
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
    """數值＋排名文字，如 71% #3。"""
    text = _axis_text(col, value)
    return f"{text}  #{rank}" if rank else text


def _radar_figure(axes: list[str], full_df: pd.DataFrame | None,
                  comp_df: pd.DataFrame | None,
                  name_a: str | None, name_b: str | None) -> go.Figure:
    """繪製雷達；full_df 為百分位／排名母體，comp_df 為實際取值來源。

    外圍三層文字：A 數值#排名（藍）、軸名（灰）、B 數值#排名（紅）。
    未選選手時 comp_df 為 None，僅留軸名形成空 8 邊形。
    """
    n = max(len(axes), 1)
    angles = [360 * i / n for i in range(n)]

    fig = go.Figure()
    if full_df is not None and comp_df is not None:
        rows = {}
        for name in (name_a, name_b):
            hit = comp_df[comp_df["playername"] == name]
            rows[name] = None if hit.empty else hit.iloc[0]
        for name, color in ((name_a, "#4C8DFF"), (name_b, "#F05A6A")):
            row = rows[name]
            if row is None:
                continue
            r_vals = [_pct(float(row[c]), full_df[c]) if not pd.isna(
                row[c]) else None for c in axes]
            fig.add_trace(go.Scatterpolar(
                r=r_vals + r_vals[:1], theta=angles + angles[:1],
                fill="toself", name=name, mode="lines+markers",
                # 多邊形外觀採細線＋小頂點（同色邊框），避免圓點過重
                line=dict(color=color, width=1),
                marker=dict(size=3, color=color,
                            line=dict(color=color, width=1)),
                fillcolor=color, opacity=0.55,
                customdata=[_AXIS_LABELS[c] for c in axes] * 2,
                hovertemplate="<b>%{fullData.name}</b><br>%{customdata}"
                              "<extra></extra>"))
        # 藍上紅下：半徑分層＋切線微偏，避免左右水平軸文字與軸名重疊
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
            radialaxis=dict(showticklabels=False, ticks="",
                            range=[0, _R_MAX], tick0=20, dtick=20,
                            gridcolor="#22304A", linecolor="#22304A"),
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
    Output("player-radar", "figure"),
    Output("player-h2h-note", "children"),
    Input("player-radar-a", "value"),
    Input("player-radar-b", "value"),
    Input({"type": "player-axis-slot", "index": ALL}, "value"),
    Input("player-h2h", "value"),
    State("player-store", "data"),
    State("player-query", "data"),
)
def _render_radar(name_a, name_b, axis_values, h2h, store, query):
    """空 8 邊形／單選／雙選／h2h 對戰數據四種狀態。"""
    axes = [v for v in (axis_values or []) if v]
    if not store or not axes:
        return _radar_figure(axes or DEFAULT_AXES, None, None, None, None), ""
    full_df = pd.DataFrame(store)
    if not name_a and not name_b:
        return _radar_figure(axes, None, None, None, None), ""

    def _pos(name):
        hit = full_df.loc[full_df["playername"] == name, "position"]
        return str(hit.iloc[0]).upper() if len(hit) else ""

    note = ""
    if h2h and "h2h" in h2h and name_a and name_b:
        # 對戰口徑：只看兩人親自對戰，且統計限縮在兩人的位置列
        positions = {p for p in (_pos(name_a), _pos(name_b)) if p}
        h2h_filter = Filter(**{**_make_filter(query or {}).__dict__,
                               "positions": sorted(positions) or None})
        try:
            comp_df = data_access.player_ranking(
                h2h_filter, min_games=0,
                h2h_players=(name_a, name_b))
            if comp_df.empty:
                comp_df = full_df
        except Exception:  # noqa: BLE001
            comp_df = full_df
        pa, pb = _pos(name_a), _pos(name_b)
        note = f"（{pa} 路統計）" if pa == pb else f"（{pa}／{pb} 對戰）"
    else:
        comp_df = full_df
    return _radar_figure(axes, full_df, comp_df,
                         name_a or None, name_b or None), note
