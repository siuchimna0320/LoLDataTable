"""圖鑑頁：英雄歷史選用/禁用圖鑑牆；道具與符文分頁為誠實空狀態。"""
from __future__ import annotations

import dash
from dash import Input, Output, callback, dcc, html

from backend import data_access
from frontend.components import common

dash.register_page(__name__, title="圖鑑", order=11)


# FR-19 圖鑑分頁（英雄以外皆為資料源限制下的誠實骨架）
_SKELETON_TABS = {
    "versions": ("版本更動", "版本平衡內容需接入 Riot 官方 Patch Notes 資料源。"),
    "summoners": ("召喚師技能", "Oracle's Elixir 不含召喚師技能層級資料。"),
    "items": ("道具", "Oracle's Elixir 不含道具層級資料，此分頁保留骨架。"),
    "runes": ("符文", "Oracle's Elixir 不含符文層級資料，此分頁保留骨架。"),
    "events": ("賽事", "官方賽程與賽事中繼資料需接入 Riot/LoL Esports API。"),
    "jungle": ("刷野速度", "逐刷野計時需逐幀/事件層級資料，目前資料源未提供。"),
}


def _champion_cards(df, keyword: str | None):
    if keyword:
        df = df[df["champion"].str.contains(keyword, case=False, na=False)]
    cards = []
    for _, r in df.iterrows():
        pos_zh = common.POSITION_ZH.get(r.get("main_position"), "")
        cards.append(html.Div([
            common.champ_img(r["champion"], 52),
            html.Div(str(r["champion"]),
                     style={"fontSize": 11, "marginTop": 3,
                            "maxWidth": 64, "overflow": "hidden",
                            "textOverflow": "ellipsis",
                            "whiteSpace": "nowrap"}),
            html.Div(f'選{int(r["picks"])} / 禁{int(r["bans"])}',
                     style={"fontSize": 10, "color": "#8A97AB"}),
            html.Div(pos_zh, style={"fontSize": 10, "color": "#F5B945"}),
        ], style={"textAlign": "center", "width": 66, "margin": 6},
            title=f'{r["champion"]}（{pos_zh}）：選用 {int(r["picks"])} 次、'
                  f'禁用 {int(r["bans"])} 次'))
    return cards


def layout():
    tabs = [dcc.Tab(label="英雄圖鑑", value="champions")]
    tabs += [dcc.Tab(label=zh, value=key)
             for key, (zh, _) in _SKELETON_TABS.items()]
    return html.Div([
        dcc.Tabs(id="comp-tabs", value="champions",
                 colors={"border": "#22304A", "primary": "#4C8DFF",
                         "background": "#0E1526"},
                 children=tabs),
        html.Div(id="comp-content", style={"paddingTop": 12}),
    ], className="page")


@callback(
    Output("comp-content", "children"),
    Input("comp-tabs", "value"),
    Input("global-filter", "data"),
)
def _render(tab, data):
    if tab != "champions":
        title, note = _SKELETON_TABS.get(tab, ("開發中", ""))
        return common.empty_state(
            f"{title}圖鑑開發中",
            note + "未來接入 Riot 官方資料源後提供。")
    years = [int(y) for y in (data or {}).get("years", [])] or None
    df = data_access.champion_wall(years)
    if df.empty:
        return common.empty_state("沒有資料", "請調整年份篩選")
    return html.Div([
        dcc.Input(id="comp-search", type="text", placeholder="搜尋英雄…",
                  style={"marginBottom": 10, "width": 240,
                         "background": "#0E1526", "color": "#E6ECF5",
                         "border": "1px solid #22304A", "borderRadius": 6,
                         "padding": 7}),
        html.Div(id="comp-wall",
                 children=_champion_cards(df, None),
                 style={"display": "flex", "flexWrap": "wrap"}),
    ])


# 圖鑑牆內搜尋（牆已存在時只過濾，不重查倉儲）
@callback(
    Output("comp-wall", "children"),
    Input("comp-search", "value"),
    Input("global-filter", "data"),
    prevent_initial_call=True,
)
def _search(keyword, data):
    years = [int(y) for y in (data or {}).get("years", [])] or None
    return _champion_cards(data_access.champion_wall(years), keyword)
