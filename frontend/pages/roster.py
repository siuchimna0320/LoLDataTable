"""陣容頁：戰隊五路選手英雄池。

- 篩選列輸入戰隊縮寫（如 T1、GEN）後，由左至右顯示
  上路／打野／中路／下路／輔助五張卡片，每張卡片可切換該位置候選選手
- 三種資料模式：職業賽（最新賽季）／積分賽（最新賽季私下排位，
  目前倉儲無此資料來源）／職業生涯（所有賽季）
- 英雄池欄位：英雄（僅圖片）、場數、勝率、KDA、上次（mm-dd），
  預設按場數降冪
"""
from __future__ import annotations

from urllib.parse import quote

import dash
import pandas as pd
from dash import ALL, Input, Output, State, callback, dcc, html

from backend import data_access
from backend.data_access import Filter
from frontend.components import common

dash.register_page(__name__, path="/roster", title="陣容", order=6)

# 五路由左至右固定順序（對照參考圖 2）
POSITIONS = data_access.ROSTER_POSITIONS
POS_ZH = common.POSITION_ZH

# 模式：pro 職業賽＝最新賽季；ranked 積分賽＝私下排位（無資料源）；
# career 職業生涯＝所有賽季
MODE_PRO, MODE_RANKED, MODE_CAREER = "pro", "ranked", "career"
MODE_OPTIONS = [
    {"label": "🏆 職業賽", "value": MODE_PRO},
    {"label": "🎯 積分賽", "value": MODE_RANKED},
    {"label": "📜 職業生涯", "value": MODE_CAREER},
]

_POSITION_OPTIONS = [{"label": "全部", "value": "ALL"}] + [
    {"label": POS_ZH[p], "value": p} for p in POSITIONS]


def _dropdown(idd: str, label: str, options, value=None, multi: bool = True,
              width: int = 110) -> html.Div:
    """深色下拉。"""
    if value is None:
        value = [] if multi else "ALL"
    return html.Div([
        html.Label(label),
        dcc.Dropdown(
            id=idd, options=options, value=value,
            multi=multi, clearable=False if not multi else True,
            placeholder="全部",
            style={"color": "#0B1020", "fontSize": 12, "minWidth": width}),
    ], className="filter-item", style={"minWidth": width})


def _options() -> dict:
    """頁內篩選選項（倉儲不存在時降級為空）。"""
    try:
        return data_access.get_options()
    except Exception as exc:  # noqa: BLE001
        print(f"[警告] 陣容頁無法讀取選項：{exc}")
        return {"leagues": [], "years": [], "patches": []}


def _filter_panel() -> html.Div:
    """頁內篩選列：賽事／賽季／版本／時間／場數／路線／戰隊＋模式切換。"""
    opts = _options()
    return html.Div([
        _dropdown("roster-league", "賽事",
                  [{"label": x, "value": x} for x in opts["leagues"]]),
        _dropdown("roster-year", "賽季",
                  [{"label": x, "value": x} for x in opts["years"]]),
        _dropdown("roster-patch", "版本",
                  [{"label": x, "value": x} for x in opts["patches"]]),
        html.Div([
            html.Label("時間"),
            dcc.DatePickerRange(
                id="roster-date", display_format="YYYY-MM-DD",
                minimum_nights=0, clearable=True,
                start_date=None, end_date=None, style={"fontSize": 11}),
        ], className="filter-item"),
        html.Div([
            html.Label("場數"),
            dcc.Input(id="roster-min-games", type="number", min=0, value=0,
                      style={"width": 64, "background": "#0E1526",
                             "color": "#E6ECF5", "border": "1px solid #22304A",
                             "borderRadius": 6, "padding": 6, "fontSize": 12}),
        ], className="filter-item"),
        _dropdown("roster-position", "路線", _POSITION_OPTIONS, multi=False,
                  width=92),
        html.Div([
            html.Label("戰隊"),
            dcc.Input(id="roster-team", type="text", placeholder="輸入戰隊",
                      debounce=True,
                      style={"width": 130, "background": "#0E1526",
                             "color": "#E6ECF5", "border": "1px solid #22304A",
                             "borderRadius": 6, "padding": 6, "fontSize": 12}),
        ], className="filter-item"),
        html.Div([
            html.Label("\u00a0"),
            dcc.RadioItems(id="roster-mode", options=MODE_OPTIONS,
                           value=MODE_PRO, inline=True,
                           className="roster-mode"),
        ], className="filter-item", style={"marginLeft": "auto"}),
    ], className="hero-filter-bar panel")


def layout(**_query_kwargs):
    # Dash pages 會把網址查詢參數以 kwarg 傳入，統一吸收避免 TypeError
    return html.Div([
        _filter_panel(),
        dcc.Store(id="roster-query", data={}),
        dcc.Store(id="roster-scope", data={}),
        html.Div(id="roster-content"),
    ], className="page")


# ---------------------------------------------------------------------------
# 篩選控制項 → 查詢條件
# ---------------------------------------------------------------------------
@callback(
    Output("roster-query", "data"),
    Input("roster-league", "value"),
    Input("roster-year", "value"),
    Input("roster-patch", "value"),
    Input("roster-date", "start_date"),
    Input("roster-date", "end_date"),
    Input("roster-min-games", "value"),
    Input("roster-position", "value"),
    Input("roster-team", "value"),
    Input("roster-mode", "value"),
)
def _sync_query(leagues, years, patches, date_start, date_end,
                min_games, position, team, mode):
    return {
        "leagues": leagues or [],
        "years": years or [],
        "patches": patches or [],
        "date_start": date_start,
        "date_end": date_end,
        "min_games": int(min_games or 0),
        "positions": [] if not position or position == "ALL" else [position],
        "team": (team or "").strip(),
        "mode": mode or MODE_PRO,
    }


def _make_filter(query: dict) -> Filter:
    """依模式決定年份口徑：職業賽＝最新賽季（可被賽季下拉覆蓋）、
    職業生涯＝所有賽季；其餘篩選照帶。"""
    mode = query.get("mode", MODE_PRO)
    years = [int(y) for y in query["years"]] if query.get("years") else None
    if mode == MODE_PRO and not years:
        years = [data_access.latest_top_year()]
    if mode == MODE_CAREER:
        years = None
    return Filter(
        leagues=query.get("leagues") or None,
        years=years,
        patches=query.get("patches") or None,
        date_start=query.get("date_start") or None,
        date_end=query.get("date_end") or None,
        positions=query.get("positions") or None,
    )


# ---------------------------------------------------------------------------
# 卡片渲染
# ---------------------------------------------------------------------------
def _win_color(rate: float) -> str:
    """勝率上色：高標紅、低標藍、其餘淺色。"""
    if rate >= 60:
        return "#F05A6A"
    if rate < 45:
        return "#4C8DFF"
    return "#C7D0DE"


def _kda_color(kda: float) -> str:
    """KDA 上色：≥5 紅、<2.5 藍。"""
    if kda >= 5:
        return "#F05A6A"
    if kda < 2.5:
        return "#4C8DFF"
    return "#C7D0DE"


def _last_md(date_iso: str | None) -> str:
    """ISO 日期 → mm-dd。"""
    return date_iso[5:10] if date_iso else "—"


def _summary_children(summary: dict, career: bool) -> list:
    """卡片標題列右側：[2013–2026｜]角色池 N 總場 N 勝率 N%。"""
    years_txt = ""
    if career and summary.get("y0") and summary.get("y1"):
        years_txt = f"{summary['y0']}–{summary['y1']}｜"
    return [
        (html.Span(years_txt, className="roster-stat")
         if years_txt else None),
        html.Span(["角色池 ", html.B(str(summary["pool_n"]))],
                  className="roster-stat"),
        html.Span(["總場 ", html.B(str(summary["games"]))],
                  className="roster-stat"),
        html.Span(["勝率 ", html.B(f"{summary['win_rate']:.0f}%")],
                  className="roster-stat",
                  style={"color": _win_color(summary["win_rate"])}),
    ]


def _pool_rows(pool: pd.DataFrame, recent_anchor: pd.Timestamp | None,
               min_games: int, career: bool = False) -> list:
    """英雄池明細列：英雄圖片／場數／勝率／KDA／上次。

    職業賽顯示 mm-dd；職業生涯顯示相對天數（如「31天前」）。
    """
    if pool is None or pool.empty:
        tip = ("此門檻下無英雄紀錄（可把欄數調回 0）" if min_games
               else "無出賽紀錄")
        return [html.Div(tip, className="roster-empty")]
    rows = []
    for r in pool.itertuples(index=False):
        recent = False
        days = None
        if recent_anchor is not None and r.last_played:
            days = (recent_anchor - pd.Timestamp(r.last_played)).days
            recent = days <= 14
        if career:
            if days is None:
                last_txt = "—"
            elif days <= 0:
                last_txt = "今天"
            elif days == 1:
                last_txt = "昨天"
            else:
                last_txt = f"{days}天前"
        else:
            last_txt = _last_md(r.last_played)
        rows.append(html.Div([
            html.Div(common.champ_img(r.champion, 28),
                     className="roster-cell-champ"),
            html.Div(str(int(r.games)), className="roster-cell",
                     style={"color": "#F05A6A", "fontWeight": 700}),
            html.Div(f"{r.win_rate:.0f}%", className="roster-cell",
                     style={"color": _win_color(float(r.win_rate))}),
            html.Div(f"{r.kda:.1f}" if pd.notna(r.kda) else "—",
                     className="roster-cell",
                     style={"color": _kda_color(float(r.kda))
                            if pd.notna(r.kda) else "#556070"}),
            html.Div(last_txt, className="roster-cell",
                     style={"color": "#F5B945" if recent else "#8A97AB"}),
        ], className="roster-pool-row"))
    return rows


def _pool_table(pool: pd.DataFrame, recent_anchor,
                min_games: int, career: bool = False) -> html.Div:
    """欄頭＋可捲動明細。"""
    return html.Div([
        html.Div([
            html.Div("英雄", className="roster-cell-champ roster-head"),
            html.Div("場數", className="roster-cell roster-head"),
            html.Div("勝率", className="roster-cell roster-head"),
            html.Div("KDA", className="roster-cell roster-head"),
            html.Div("上次", className="roster-cell roster-head"),
        ], className="roster-pool-head"),
        html.Div(_pool_rows(pool, recent_anchor, min_games, career),
                 className="roster-pool-body"),
    ])


def _position_icon(pos: str, size: int = 26) -> html.Img:
    """路線圖示（僅圖片）。"""
    return html.Img(src=f"/pos-img/{quote(pos)}.svg", width=size, height=size,
                    className="roster-pos-icon", title=POS_ZH.get(pos, pos))


def _player_card(pos: str, candidates: pd.DataFrame, selected: str | None,
                 summary: dict | None, pool: pd.DataFrame | None,
                 career: bool, recent_anchor,
                 min_games: int) -> html.Div:
    """單張五路選手卡片。"""
    options = [{"label": n, "value": n}
               for n in candidates["playername"].tolist()] if (
        candidates is not None and not candidates.empty) else []
    has = bool(options)
    body = (_pool_table(pool, recent_anchor, min_games, career)
            if has and summary is not None
            else html.Div("篩選範圍內無此路線出賽紀錄",
                          className="roster-empty roster-no-record"))
    return html.Div([
        html.Div([
            _position_icon(pos),
            dcc.Dropdown(
                id={"type": "roster-player", "index": pos},
                options=options, value=selected if has else None,
                clearable=False, searchable=True, disabled=not has,
                placeholder="無選手",
                style={"color": "#0B1020", "fontSize": 12,
                       "flex": 1, "minWidth": 0}),
        ], className="roster-card-title"),
        html.Div(_summary_children(summary, career)
                 if summary else [],
                 id={"type": "roster-summary", "index": pos},
                 className="roster-card-stats"),
        html.Div(body, id={"type": "roster-pool", "index": pos}),
    ], className="roster-card")


_RANKED_NOTICE = (
    "目前倉儲僅收錄職業賽數據（Oracle's Elixir），"
    "尚無選手私下積分賽（排位）資料來源，故此模式暫時無法顯示；"
    "日後匯入排位資料後即可開放。")


def _hint_bar() -> html.Div:
    """未選戰隊時的提示（對照參考圖 1）。"""
    return html.Div(
        "請先在上方選擇戰隊（或至少篩選一路線），即可看到選手的角色池。",
        className="roster-hint")


@callback(
    Output("roster-content", "children"),
    Output("roster-scope", "data"),
    Input("roster-query", "data"),
)
def _render(query):
    query = query or {}
    mode = query.get("mode", MODE_PRO)
    if mode == MODE_RANKED:
        return (common.empty_state("尚無積分賽資料", _RANKED_NOTICE),
                {"mode": mode})
    team_kw = query.get("team", "")
    if not team_kw:
        return _hint_bar(), {"mode": mode}
    team = data_access.resolve_team(team_kw)
    if team is None:
        return (common.empty_state(
            "找不到戰隊", f"「{team_kw}」沒有唯一命中的戰隊，請改用縮寫"
            "（如 T1、GEN、BLG）或完整隊名"), {"mode": mode})
    f = _make_filter(query)
    min_games = query.get("min_games", 0)
    try:
        candidates = data_access.roster_candidates(team, f)
    except Exception as exc:  # noqa: BLE001
        return common.empty_state("查詢失敗", str(exc)), {"mode": mode}
    if candidates.empty:
        return (common.empty_state(
            "篩選範圍內無此戰隊資料",
            f"{team} 在目前篩選（賽事／版本／時間）下沒有出賽紀錄"),
            {"mode": mode, "team": team})

    # 最近出賽日：14 天內的「上次」日期以金色標示；職業生涯模式
    # 「上次」改顯示相對天數，故各卡共用同一個資料最新日當錨
    anchor_iso = str(candidates["last_date"].max())
    recent_anchor = pd.Timestamp(anchor_iso)
    chosen_positions = (query.get("positions") or POSITIONS)
    cards = []
    for pos in POSITIONS:
        if pos not in chosen_positions:
            continue
        grp = candidates[candidates["position"] == pos]
        selected = grp.iloc[0]["playername"] if not grp.empty else None
        summary, pool = (None, None)
        if selected:
            summary, pool = data_access.roster_player_detail(
                team, pos, selected, f, min_games=min_games)
        cards.append(_player_card(
            pos, grp, selected, summary, pool,
            career=mode == MODE_CAREER, recent_anchor=recent_anchor,
            min_games=min_games))
    scope = {"mode": mode, "team": team, "min_games": min_games,
             "filter": f.__dict__, "anchor": anchor_iso,
             "positions": chosen_positions}
    header = html.Div([
        html.H2(team, style={"margin": 0, "fontSize": 18}),
        html.Span({"pro": "職業賽（最新賽季）",
                   "career": "職業生涯（所有賽季）"}.get(mode, ""),
                  style={"color": "#8A97AB", "marginLeft": 12}),
    ], style={"display": "flex", "alignItems": "baseline",
              "margin": "4px 0 10px"})
    return html.Div([header, html.Div(cards, className="roster-grid")]), scope


# ---------------------------------------------------------------------------
# 卡片內切換選手：重取該選手摘要與英雄池
# ---------------------------------------------------------------------------
@callback(
    Output({"type": "roster-summary", "index": ALL}, "children"),
    Output({"type": "roster-pool", "index": ALL}, "children"),
    Input({"type": "roster-player", "index": ALL}, "value"),
    State("roster-scope", "data"),
    prevent_initial_call=True,
)
def _switch_player(values, scope):
    """下拉改選時，各卡片依元件 id 的位置重新取摘要與英雄池。"""
    scope = scope or {}
    team = scope.get("team")
    empty_summaries = [[] for _ in values]
    if not team:
        return empty_summaries, [
            html.Div("", className="roster-pool-body") for _ in values]
    f = Filter(**{k: v for k, v in (scope.get("filter") or {}).items()})
    min_games = scope.get("min_games", 0)
    career = scope.get("mode") == MODE_CAREER
    anchor = pd.Timestamp(scope["anchor"]) if scope.get("anchor") else None
    # ALL 群組值的順序＝卡片渲染順序（POSITIONS 再依路線篩選），
    # 故與 scope 記錄的 positions 直接對齊即可
    pairs = list(zip(scope.get("positions") or POSITIONS, values))
    summaries, pools = [], []
    for pos, player in pairs:
        if not player:
            summaries.append([])
            pools.append(html.Div("篩選範圍內無此路線出賽紀錄",
                                  className="roster-empty roster-no-record"))
            continue
        summary, pool = data_access.roster_player_detail(
            team, pos, player, f, min_games=min_games)
        summaries.append(_summary_children(summary, career))
        pools.append(_pool_table(pool, anchor, min_games, career))
    return summaries, pools
