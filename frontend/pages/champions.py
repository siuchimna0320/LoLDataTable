"""英雄頁：DPM 風格英雄數據總覽。

篩選：賽事／賽季／版本／時間／場數／路線／英雄／戰隊選手。
視圖：表格、散布圖、積分。指標含 BP 率、禁用率、勝負、贏線率、後選率、
參團率、KDA、中期金差、分均金錢/輸出/承傷/視分，與同路百分位加權評分。
缺值以佔位字串「123456」呈現，避免空欄誤判。
"""
from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dash_table, dcc, html

from backend import data_access
from backend.data_access import Filter
from backend.metrics import scoring
from frontend.components import common
from frontend.theme import COLORS, PLOTLY_TEMPLATE

dash.register_page(__name__, title="英雄", order=3)

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
_VIEW_OPTIONS = [
    {"label": "📋 表格", "value": "table"},
    {"label": "📊 散布圖", "value": "scatter"},
    {"label": "🏅 積分", "value": "score"},
]
_LANE_COLORS = {"top": "#F05A6A", "jng": "#3DDC84", "mid": "#4C8DFF",
                "bot": "#F5B945", "sup": "#B57EDC"}

# 表格欄位定義（複合欄 win_col/kda_col 採補空白字串以利原生文字排序）
_COLUMNS = [
    {"name": "#", "id": "rank", "type": "numeric"},
    {"name": "英雄", "id": "champion_col", "type": "text",
     "presentation": "markdown"},
    {"name": "路線", "id": "pos_col", "type": "text", "presentation": "markdown"},
    {"name": "BP%", "id": "bp_rate", "type": "numeric"},
    {"name": "禁用率", "id": "ban_rate", "type": "numeric"},
    {"name": "場數", "id": "games", "type": "numeric"},
    {"name": "勝率", "id": "win_col", "type": "text"},
    {"name": "贏線率", "id": "lane_win", "type": "numeric"},
    {"name": "後選率", "id": "late_rate", "type": "numeric"},
    {"name": "參與率", "id": "kp", "type": "numeric"},
    {"name": "KDA", "id": "kda_col", "type": "text"},
    {"name": "中期金差", "id": "gold15", "type": "numeric"},
    {"name": "分均金錢", "id": "gpm", "type": "numeric"},
    {"name": "分均輸出", "id": "dpm", "type": "numeric"},
    {"name": "分均承傷", "id": "dtaken", "type": "numeric"},
    {"name": "分均視分", "id": "vspm", "type": "numeric"},
    # 表頭僅顯示「評分」，計算方式由 tooltip_header 於滑鼠移上時說明
    {"name": "評分", "id": "lane_score", "type": "numeric"},
]


@lru_cache(maxsize=1)
def _options() -> dict:
    """頁內篩選選項（倉儲不存在時降級為空）。"""
    try:
        return data_access.get_options()
    except Exception as exc:  # noqa: BLE001
        print(f"[警告] 英雄頁無法讀取選項：{exc}")
        return {"leagues": [], "years": [], "patches": [],
                "date_min": None, "date_max": None}


def _dropdown(idd: str, label: str, options, multi: bool = True,
              width: int = 120) -> html.Div:
    """深色下拉（篩選列用）。"""
    return html.Div([
        html.Label(label),
        dcc.Dropdown(
            id=idd, options=options, value=[] if multi else "ALL",
            multi=multi, clearable=False if not multi else True,
            placeholder="全部",
            style={"color": "#0B1020", "fontSize": 12, "minWidth": width},
        ),
    ], className="filter-item", style={"minWidth": width})


def _filter_panel() -> html.Div:
    """頁內篩選列：賽事／賽季／版本／時間／場數／路線／英雄／戰隊選手。"""
    opts = _options()
    return html.Div([
        _dropdown("hero-league", "賽事",
                  [{"label": x, "value": x} for x in opts["leagues"]]),
        _dropdown("hero-year", "賽季",
                  [{"label": x, "value": x} for x in opts["years"]]),
        _dropdown("hero-patch", "版本",
                  [{"label": x, "value": x} for x in opts["patches"]]),
        html.Div([
            html.Label("時間"),
            dcc.DatePickerRange(
                id="hero-date", display_format="YYYY-MM-DD",
                minimum_nights=0, clearable=True,
                start_date=None, end_date=None,
                style={"fontSize": 11}),
        ], className="filter-item"),
        html.Div([
            html.Label("場數"),
            dcc.Input(id="hero-min-games", type="number", min=0, value=0,
                      style={"width": 64, "background": "#0E1526",
                             "color": "#E6ECF5", "border": "1px solid #22304A",
                             "borderRadius": 6, "padding": 6, "fontSize": 12}),
        ], className="filter-item"),
        _dropdown("hero-position", "路線", _POSITION_OPTIONS, multi=False,
                  width=92),
        html.Div([
            html.Label("英雄"),
            dcc.Input(id="hero-champ", type="text", placeholder="輸入英雄",
                      debounce=False,
                      style={"width": 120, "background": "#0E1526",
                             "color": "#E6ECF5", "border": "1px solid #22304A",
                             "borderRadius": 6, "padding": 6, "fontSize": 12}),
        ], className="filter-item"),
        html.Div([
            html.Label("戰隊選手"),
            dcc.Input(id="hero-member", type="text", placeholder="輸入戰隊或選手",
                      debounce=False,
                      style={"width": 150, "background": "#0E1526",
                             "color": "#E6ECF5", "border": "1px solid #22304A",
                             "borderRadius": 6, "padding": 6, "fontSize": 12}),
        ], className="filter-item"),
        html.Button("↺ 0", id="hero-reset", n_clicks=0, title="重設篩選",
                    className="hero-reset-btn"),
        html.Div([
            html.Label("　"),
            dcc.RadioItems(id="hero-view-mode", options=_VIEW_OPTIONS,
                           value="table", inline=True,
                           className="hero-view-toggle"),
        ], className="filter-item", style={"marginLeft": "auto"}),
    ], className="hero-filter-bar panel")


def _intro_banner(count: int) -> html.Div:
    """英雄數與評分口徑簡介（對照參考圖橫幅）。"""
    return html.Div([
        html.Span("🧙", style={"fontSize": 22}),
        html.Span("英雄", className="hero-banner-title"),
        html.Span(f"{count} 隻", className="hero-banner-count"),
        html.Span("評分＝同路百分位加權（滑欄名看權重）",
                  className="hero-banner-note"),
        html.Span("｜", style={"color": "#3a4660"}),
        html.Span("點英雄名看版本趨勢與出場紀錄",
                  className="hero-banner-note"),
    ], className="hero-banner")


def layout():
    return html.Div([
        _filter_panel(),
        html.Div(id="hero-banner", children=_intro_banner(0)),
        dcc.Store(id="hero-query", data={}),
        html.Div(id="hero-view"),
        html.Div(id="champ-detail"),
    ], className="page")


# ---------------------------------------------------------------------------
# 篩選控制項 → 查詢條件 Store（含啟動初次載入）
# ---------------------------------------------------------------------------
@callback(
    Output("hero-query", "data"),
    Output("hero-reset", "children"),
    Input("hero-league", "value"),
    Input("hero-year", "value"),
    Input("hero-patch", "value"),
    Input("hero-date", "start_date"),
    Input("hero-date", "end_date"),
    Input("hero-min-games", "value"),
    Input("hero-position", "value"),
    Input("hero-champ", "value"),
    Input("hero-member", "value"),
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
        "champ": (champ or "").strip(),
        "member": (member or "").strip(),
    }
    active = sum(bool(data[k]) for k in
                 ("leagues", "years", "patches", "positions", "champ",
                  "member")) + sum(1 for k in ("date_start", "date_end")
                                   if data[k]) + (1 if data["min_games"] else 0)
    return data, f"↺ {active}"


# 重設按鈕：所有控制項回到預設
@callback(
    Output("hero-league", "value"),
    Output("hero-year", "value"),
    Output("hero-patch", "value"),
    Output("hero-date", "start_date"),
    Output("hero-date", "end_date"),
    Output("hero-min-games", "value"),
    Output("hero-position", "value"),
    Output("hero-champ", "value"),
    Output("hero-member", "value"),
    Input("hero-reset", "n_clicks"),
    prevent_initial_call=True,
)
def _reset_filters(_clicks):
    return [], [], [], None, None, 0, "ALL", "", ""


# ---------------------------------------------------------------------------
# 資料聚合（三視圖共用）
# ---------------------------------------------------------------------------
def _ranking_df(query: dict) -> pd.DataFrame:
    """依查詢條件取英雄排行並加入同路評分，依評分排序。"""
    f = Filter(
        leagues=query.get("leagues") or None,
        years=[int(y) for y in query["years"]] if query.get("years") else None,
        patches=query.get("patches") or None,
        date_start=query.get("date_start") or None,
        date_end=query.get("date_end") or None,
        positions=query.get("positions") or None,
    )
    df = data_access.champion_ranking(
        f, min_games=query.get("min_games", 0),
        champ_search=query.get("champ") or None,
        member_search=query.get("member") or None)
    if df.empty:
        return df
    df = scoring.score_champions_lane(df)
    df = df.sort_values(["lane_score", "games"], ascending=False).reset_index(
        drop=True)
    df["rank"] = df.index + 1
    return df


def _pct(v, decimals: int = 0) -> str:
    """百分比格式化，缺值顯示 123456。"""
    if v is None or pd.isna(v):
        return PLACEHOLDER
    return f"{v:.{decimals}f}%"


def _signed(v, decimals: int = 0) -> str | float:
    """帶正負號整數（DataTable 排序仍用數值欄，故僅供積分視圖）。"""
    if v is None or pd.isna(v):
        return PLACEHOLDER
    return f"{v:+.{decimals}f}"


def _pos_markdown(positions: list | None) -> str:
    """路線圖示 markdown（最多 3 個，佔比作 tooltip）。"""
    if not positions:
        return ""
    parts = []
    for item in positions:
        code = item.get("position", "")
        zh = common.POSITION_ZH.get(code, code)
        parts.append(f"![{zh}](/pos-img/{code}.svg)")
    return " ".join(parts)


def _table_data(df: pd.DataFrame) -> list[dict]:
    """DataTable 記錄（複合欄以補空格字串排序）。"""
    records = []
    for row in df.itertuples(index=False):
        losses = int(row.games) - int(row.wins)
        win_col = (f"{row.win_rate:5.1f}% ({int(row.wins)}-{losses})"
                   if pd.notna(row.win_rate) else PLACEHOLDER)
        kda_col = (f"{row.kda:4.2f} ({row.k_pg:4.1f} / {row.d_pg:4.1f} / "
                   f"{row.a_pg:4.1f})") if pd.notna(row.kda) else PLACEHOLDER
        records.append({
            "rank": int(row.rank),
            "champion_col": (
                f"![{row.champion}]({common.champ_url(row.champion)}) "
                f"{row.champion}"),
            "pos_col": _pos_markdown(row.positions),
            "bp_rate": None if pd.isna(row.bp_rate) else float(row.bp_rate),
            "ban_rate": None if pd.isna(row.ban_rate) else float(row.ban_rate),
            "games": int(row.games),
            "win_col": win_col,
            "lane_win": None if pd.isna(row.lane_win) else float(row.lane_win),
            "late_rate": None if pd.isna(row.late_rate) else float(
                row.late_rate),
            "kp": None if pd.isna(row.kp) else float(row.kp),
            "kda_col": kda_col,
            "gold15": None if pd.isna(row.gold15) else float(row.gold15),
            "gpm": None if pd.isna(row.gpm) else float(row.gpm),
            "dpm": None if pd.isna(row.dpm) else float(row.dpm),
            "dtaken": None if pd.isna(row.dtaken) else float(row.dtaken),
            "vspm": None if pd.isna(row.vspm) else float(row.vspm),
            "lane_score": None if pd.isna(row.lane_score) else float(
                row.lane_score),
        })
    return records


def _table_view(df: pd.DataFrame) -> dash_table.DataTable:
    """表格視圖。"""
    styles = common.table_styles()
    styles["style_table"] = {"overflowX": "auto"}
    styles["style_cell"]["minWidth"] = 64
    styles["style_cell"]["maxWidth"] = 190
    styles["style_cell_conditional"] = [
        {"if": {"column_id": "champion_col"}, "textAlign": "left",
         "minWidth": 150},
        {"if": {"column_id": "rank"}, "width": 40, "color": "#8A97AB"},
        {"if": {"column_id": "pos_col"}, "minWidth": 92},
        {"if": {"column_id": "lane_score"}, "fontWeight": 700,
         "color": "#F05A6A"},
        {"if": {"column_id": "win_col"}, "textAlign": "right"},
        {"if": {"column_id": "kda_col"}, "textAlign": "right",
         "fontFamily": "Consolas, monospace", "fontSize": 11},
    ]
    styles["style_data_conditional"] = [
        {"if": {"row_index": "odd"},
         "backgroundColor": "rgba(255,255,255,0.02)"},
        {"if": {"column_id": "bp_rate", "filter_query": "{bp_rate} >= 80"},
         "color": "#F05A6A", "fontWeight": 700},
        {"if": {"column_id": "ban_rate", "filter_query": "{ban_rate} >= 50"},
         "color": "#F05A6A", "fontWeight": 700},
        {"if": {"column_id": "games", "filter_query": "{games} >= 700"},
         "color": "#F05A6A", "fontWeight": 700},
        {"if": {"column_id": "gold15", "filter_query": "{gold15} < 0"},
         "color": "#4C8DFF"},
        {"if": {"column_id": "gold15", "filter_query": "{gold15} > 0"},
         "color": "#3DDC84"},
    ]
    return dash_table.DataTable(
        id="champ-table", columns=_COLUMNS, data=_table_data(df),
        sort_action="native",
        sort_by=[{"column_id": "rank", "direction": "asc"}],
        page_size=40, page_action="native", page_current=0,
        markdown_options={"html": False, "link_target": "_blank"},
        # 滑鼠移到「評分」表頭才顯示同路百分位加權的計算口徑
        tooltip_header={
            "lane_score": {
                "value": scoring.lane_score_tooltip(),
                "type": "text",
            }
        },
        tooltip_delay=150,
        **styles,
    )


def _scatter_view(df: pd.DataFrame) -> dcc.Graph:
    """散布圖：分均輸出 × 勝率，氣泡大小＝場數、顏色＝主路線。"""
    fig = go.Figure()
    for lane, code in _LANE_COLORS.items():
        sub = df[df["main_position"] == lane]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub["dpm"], y=sub["win_rate"], mode="markers",
            name=common.POSITION_ZH.get(lane, lane),
            marker=dict(size=(sub["games"] ** 0.5) * 1.6 + 8,
                        color=code, opacity=0.75,
                        line=dict(width=1, color="#0B1020")),
            customdata=sub[["champion", "games", "bp_rate", "lane_score"]],
            hovertemplate=("<b>%{customdata[0]}</b><br>分均輸出 %{x:.0f}"
                           "<br>勝率 %{y:.1f}%<br>場數 %{customdata[1]}"
                           "<br>BP% %{customdata[2]:.0f}"
                           "<br>評分 %{customdata[3]:.0f}<extra></extra>"),
        ))
    # 評分前 10 直接標名
    for row in df.head(10).itertuples(index=False):
        fig.add_annotation(x=row.dpm, y=row.win_rate, text=row.champion,
                           showarrow=False, yshift=10,
                           font=dict(size=10, color="#E6ECF5"))
    fig.add_hline(50, line_color="#8A97AB", line_dash="dot", opacity=0.5)
    fig.update_layout(
        template=PLOTLY_TEMPLATE, height=620,
        margin=dict(l=50, r=20, t=30, b=45),
        xaxis_title="分均輸出", yaxis_title="勝率 %",
        legend_title="主路線", hovermode="closest")
    return dcc.Graph(id="hero-scatter", figure=fig)


def _score_view(df: pd.DataFrame) -> html.Div:
    """積分視圖：排名列＋大型評分徽章。"""
    rows = []
    for row in df.head(100).itertuples(index=False):
        losses = int(row.games) - int(row.wins)
        score_color = "#F05A6A" if row.lane_score >= 80 else (
            "#F5B945" if row.lane_score >= 65 else "#8A97AB")
        lane_imgs = [html.Img(src=f"/pos-img/{quote(p['position'])}.svg",
                              width=15, height=15,
                              title=common.POSITION_ZH.get(p["position"], ""))
                     for p in (row.positions or [])]
        rows.append(html.Tr([
            html.Td(int(row.rank), className="hs-rank"),
            html.Td(html.Span([
                common.champ_img(row.champion, 28),
                html.Span(row.champion,
                          style={"marginLeft": 8, "fontWeight": 700}),
                html.Span(lane_imgs,
                          style={"marginLeft": 8, "display": "inline-flex",
                                 "gap": 2, "alignItems": "center"}),
            ], className="champ-cell")),
            html.Td(f"{row.bp_rate:.0f}%"),
            html.Td(f"{row.win_rate:.0f}% ({int(row.wins)}-{losses})"),
            html.Td(f"{row.kda:.1f}"),
            html.Td(_signed(row.gold15)),
            html.Td(f"{row.dpm:.0f}"),
            html.Td(f"{row.kp:.0f}%"),
            html.Td(f"{row.lane_score:.0f}",
                    style={"color": score_color, "fontWeight": 700,
                           "fontSize": 16}),
        ]))
    header = html.Thead(html.Tr([
        html.Th(x) for x in
        ["#", "英雄", "BP%", "勝率", "KDA", "中期金差", "分均輸出",
         "參與率", "評分"]
    ]))
    return html.Div(html.Table([header, html.Tbody(rows)],
                               className="hero-score-table"),
                    style={"marginTop": 6})


@callback(
    Output("hero-view", "children"),
    Output("hero-banner", "children"),
    Input("hero-query", "data"),
    Input("hero-view-mode", "value"),
)
def _render(query, view_mode):
    query = query or {}
    try:
        df = _ranking_df(query)
    except Exception as exc:  # noqa: BLE001
        return common.empty_state("查詢失敗", str(exc)), _intro_banner(0)
    banner = _intro_banner(len(df))
    if df.empty:
        return common.empty_state(
            "沒有符合的英雄", "請放寬場數門檻或調整篩選條件"), banner
    if view_mode == "scatter":
        return _scatter_view(df), banner
    if view_mode == "score":
        return _score_view(df), banner
    return _table_view(df), banner


# ---------------------------------------------------------------------------
# 點表格英雄列 → 詳情（沿用資料庫詳情查詢，篩選條件與本頁同步）
# ---------------------------------------------------------------------------
@callback(
    Output("champ-detail", "children"),
    Input("champ-table", "active_cell"),
    State("champ-table", "derived_virtual_data"),
    State("hero-query", "data"),
    prevent_initial_call=True,
)
def _detail(active, rows, query):
    if not active or not rows:
        return None
    champion = rows[active["row"]].get("champion_col", "").split(") ", 1)[-1]
    query = query or {}
    f = Filter(
        leagues=query.get("leagues") or None,
        years=[int(y) for y in query["years"]] if query.get("years") else None,
        patches=query.get("patches") or None,
        date_start=query.get("date_start") or None,
        date_end=query.get("date_end") or None,
        positions=query.get("positions") or None,
    )
    detail = data_access.champion_detail(champion, f)
    pos_df = detail["by_position"]
    if pos_df.empty:
        return common.empty_state("沒有此英雄資料", champion)

    fig_pos = go.Figure(go.Bar(
        x=pos_df["win_rate"],
        y=[common.POSITION_ZH.get(p, p) for p in pos_df["position"]],
        orientation="h", marker_color=COLORS["blue"],
        text=[f"{g}場 {w}%" for g, w in
              zip(pos_df["games"], pos_df["win_rate"])],
    ))
    fig_pos.update_layout(template=PLOTLY_TEMPLATE, height=220,
                          margin=dict(l=40, r=10, t=10, b=30),
                          xaxis_title="勝率 %")

    top_pos = pos_df.iloc[0]
    fig_gd = go.Figure(go.Scatter(
        x=["10 分", "15 分", "25 分"],
        y=[top_pos.get("gd10"), top_pos.get("gd15"), top_pos.get("gd25")],
        mode="lines+markers", line=dict(color="#F5B945", width=3),
    ))
    fig_gd.add_hline(0, line_color="#8A97AB", line_dash="dot")
    fig_gd.update_layout(template=PLOTLY_TEMPLATE, height=220,
                         margin=dict(l=40, r=10, t=30, b=30),
                         title=f"{common.POSITION_ZH.get(top_pos['position'], top_pos['position'])}位金差",
                         yaxis_title="金差")

    side_df, masters = detail["by_side"], detail["masters"]
    side_kpis = [
        common.kpi_card(f"{r['side']}方勝率", f"{r['win_rate']}%",
                        f"{int(r['games'])} 場",
                        "text-blue" if r["side"] == "Blue" else "text-red")
        for _, r in side_df.iterrows()
    ]
    master_rows = [
        html.Tr([
            html.Td(str(r["playername"])),
            html.Td(str(r.get("team", "—"))),
            html.Td(common.POSITION_ZH.get(r["position"], r["position"])),
            html.Td(str(int(r["games"]))),
            html.Td(f"{r['win_rate']}%"),
            html.Td("—" if pd.isna(r["kda"]) else f"{r['kda']:.2f}"),
        ]) for _, r in masters.iterrows()
    ]
    masters_tbl = html.Table([
        html.Thead(html.Tr([html.Th(x) for x in
                            ["選手", "戰隊", "位置", "場數", "勝率", "KDA"]])),
        html.Tbody(master_rows),
    ], style={"width": "100%", "borderCollapse": "collapse", "fontSize": 12})

    return common.panel(
        f"英雄詳情｜{champion}",
        html.Div([
            html.Div(side_kpis, className="grid-kpi",
                     style={"gridTemplateColumns": "repeat(2,1fr)"}),
            html.Div([dcc.Graph(figure=fig_pos), dcc.Graph(figure=fig_gd)],
                     className="grid-2"),
            html.H3("熟練度最高選手", style={"fontSize": 13}),
            masters_tbl,
        ], style={"display": "flex", "flexDirection": "column", "gap": 10}),
    )
