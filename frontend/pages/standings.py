"""積分榜頁：由對戰記錄推算各賽區／賽段戰隊戰績。

Oracle's Elixir 未提供官方錦標賽積分，故採「同日同對手」分組為一場
系列賽（BO1/3/5），呈現大場勝負、小場勝負、淨勝場與當前連勝／敗。
"""
from __future__ import annotations

import dash
from dash import Input, Output, State, callback, dcc, html, dash_table

from backend import data_access
from frontend.components import common

dash.register_page(__name__, title="積分榜", order=6.5)

_COLUMNS = [
    {"name": "排名", "id": "rank"},
    {"name": "戰隊", "id": "team"},
    {"name": "大場勝-敗(和)", "id": "series_record"},
    {"name": "小場勝-敗", "id": "game_record"},
    {"name": "淨勝場", "id": "game_diff"},
    {"name": "小場勝率", "id": "win_rate_pct"},
    {"name": "當前連勢", "id": "streak"},
]


def _controls() -> html.Div:
    """賽區／賽段／季後賽開關。"""
    return common.panel("積分榜", html.Div([
        html.Div([html.Label("賽區"),
                  dcc.Dropdown(id="std-league", placeholder="選擇賽區",
                               searchable=True,
                               style={"color": "#0B1020", "minWidth": 180})],
                 className="filter-item"),
        html.Div([html.Label("賽段"),
                  dcc.Dropdown(id="std-split", placeholder="選擇賽段",
                               searchable=False, clearable=False,
                               style={"color": "#0B1020", "minWidth": 200})],
                 className="filter-item"),
        html.Div([html.Label("範圍"),
                  dcc.RadioItems(
                      id="std-playoffs",
                      options=[{"label": "常規賽", "value": "regular"},
                               {"label": "含季後賽", "value": "all"}],
                      value="regular", inline=True,
                      style={"color": "#E6ECF5"})],
                 className="filter-item"),
    ], style={"display": "flex", "gap": 18, "flexWrap": "wrap",
              "alignItems": "flex-end"}))


def _table(df) -> dash_table.DataTable:
    styles = common.table_styles()
    # table_styles() 已提供預設條件式樣式，這裡改為覆寫/追加而非重複傳參
    styles["style_cell_conditional"] = [
        {"if": {"column_id": "team"}, "textAlign": "left"},
    ]
    styles["style_data_conditional"] = styles["style_data_conditional"] + [
        {"if": {"column_id": "streak",
                "filter_query": "{streak} contains '連勝'"},
         "color": "#3DDC84", "fontWeight": 700},
        {"if": {"column_id": "streak",
                "filter_query": "{streak} contains '連敗'"},
         "color": "#F05A6A", "fontWeight": 700},
    ]
    return dash_table.DataTable(
        id="std-table", columns=_COLUMNS, data=df.to_dict("records"),
        page_size=20, sort_action="native", **styles,
    )


def layout():
    return html.Div([
        _controls(),
        dcc.Store(id="std-groups"),
        html.Div(id="std-table-wrap", style={"marginTop": 10}),
        html.Div("口徑說明：大場以「同日同對手」系列賽推算；"
                 "小場勝率＝小場勝÷(勝+敗)。官方冠軍積分需另接賽事資料源。",
                 style={"color": "#8A97AB", "fontSize": 11, "marginTop": 8}),
    ], className="page")


@callback(
    Output("std-groups", "data"),
    Output("std-league", "options"),
    Output("std-league", "value"),
    Input("global-filter", "data"),
)
def _sync_groups(data):
    """依全域篩選取得可用賽區/賽段，預設選最新年度的第一個賽區。"""
    f = common.make_filter(data)
    groups = data_access.standings_groups(f)
    if groups.empty:
        return [], [], None
    records = groups.to_dict("records")
    leagues = sorted(groups["league"].unique().tolist())
    return records, leagues, records[0]["league"]


@callback(
    Output("std-split", "options"),
    Output("std-split", "value"),
    Input("std-league", "value"),
    State("std-groups", "data"),
)
def _sync_splits(league, groups):
    splits = [g["split"] for g in (groups or []) if g["league"] == league]
    splits = list(dict.fromkeys(splits))
    return ([{"label": s, "value": s} for s in splits],
            splits[0] if splits else None)


@callback(
    Output("std-table-wrap", "children"),
    Input("std-league", "value"),
    Input("std-split", "value"),
    Input("std-playoffs", "value"),
    Input("global-filter", "data"),
)
def _render(league, split, scope, data):
    if not league or not split:
        return common.empty_state("請選擇賽區與賽段", "選擇後顯示積分榜")
    f = common.make_filter(data)
    df = data_access.standings(f, include_playoffs=(scope == "all"))
    if df.empty:
        return common.empty_state("沒有資料", "請調整篩選條件")
    df = df[(df["league"] == league) & (df["split"] == split)].copy()
    if df.empty:
        return common.empty_state("此賽段無戰績", "可能尚未開打")
    df["series_record"] = df.apply(
        lambda r: f'{r["series_w"]}-{r["series_l"]}'
                  + (f'({r["series_d"]})' if r["series_d"] else ""), axis=1)
    df["game_record"] = df.apply(
        lambda r: f'{r["game_w"]}-{r["game_l"]}', axis=1)
    df["win_rate_pct"] = df["win_rate"].map(lambda v: f"{v}%")
    streak_zh = {"W": "連勝", "L": "連敗", "D": "和"}
    df["streak"] = df.apply(
        lambda r: f'{streak_zh.get(r["streak_type"], "—")} {r["streak_n"]}'
        if r["streak_n"] else "—", axis=1)
    years = sorted(df["year"].unique().tolist())
    title = (f'{league}｜{split}｜{years[0]}'
             f'{"（含季後賽）" if scope == "all" else "（常規賽）"}')
    return html.Div([
        html.H3(title, style={"color": "#F5B945", "margin": "6px 0 10px"}),
        _table(df[["rank", "team", "series_record", "game_record",
                   "game_diff", "win_rate_pct", "streak"]]),
    ])
