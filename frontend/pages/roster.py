"""陣容頁：戰隊五位選手近期英雄池（頭像、場數、勝率、KDA）。"""
from __future__ import annotations

import dash
from dash import Input, Output, callback, dcc, html

from backend import data_access
from frontend.components import common

dash.register_page(__name__, path="/roster", title="陣容", order=6)

# 進入此頁可帶 ?team=xxx
try:
    _ALL_TEAMS = data_access.get_options().get("teams", [])
except Exception:  # noqa: BLE001
    _ALL_TEAMS = []


def _player_card(pos: str, player: str, pool, ban_map: dict) -> html.Div:
    rows = []
    for _, r in pool.head(8).iterrows():
        ban_rate = ban_map.get(r["champion"])
        ban_txt = (f"禁{ban_rate:.0f}%"
                   if ban_rate is not None and ban_rate == ban_rate else "")
        rows.append(html.Div([
            common.champ_img(r["champion"], 26),
            html.Span(r["champion"], className="pname-c"),
            html.Span(f"{int(r['games'])}場", style={"color": "#8A97AB"}),
            html.Span(f"{r['win_rate']}%",
                      style={"color": "#4CAF7A" if r["win_rate"] >= 50
                             else "#F05A6A", "width": 46}),
            html.Span(ban_txt, style={"color": "#F5B945", "width": 42}),
            html.Span(f'上次 {r["last_played"]}',
                      style={"color": "#556070", "fontSize": 10}),
        ], className="pool-row"))
    return html.Div([
        html.Div([
            html.Span(common.POSITION_ZH.get(pos, pos),
                      style={"color": "#F5B945", "marginRight": 6}),
            player,
        ], className="pname"),
        html.Div(rows) if rows else html.P("無出賽紀錄",
                                           style={"color": "#8A97AB"}),
    ], className="player-card")


def layout(team: str | None = None):
    return html.Div([
        common.panel("選擇戰隊", dcc.Dropdown(
            id="roster-team",
            options=[{"label": t, "value": t} for t in _ALL_TEAMS],
            value=team, searchable=True,
            style={"color": "#0B1020", "fontSize": 12, "maxWidth": 420},
        )),
        html.Div(id="roster-content"),
    ], className="page")


@callback(
    Output("roster-content", "children"),
    Input("roster-team", "value"),
    Input("global-filter", "data"),
)
def _render(team, data):
    if not team:
        return common.empty_state("請選擇戰隊", "顯示五位選手近期常用英雄與勝率")
    f = common.make_filter(data)
    roster = data_access.team_roster(team, f)
    if not roster:
        return common.empty_state("篩選範圍內無此戰隊資料", team)
    form = data_access.recent_team_form(team, f)
    form_txt = f"近 10 場勝率 {form}%" if form == form else "近況資料不足"
    # 英雄池頭像附帶「整體禁用率」與上次出場日期（FR-14）
    stats = data_access.champion_stats(f)
    ban_map = (dict(zip(stats["champion"], stats["ban_rate"]))
               if not stats.empty else {})
    cards = [
        _player_card(pos, info["player"], info["pool"], ban_map)
        for pos, info in roster.items()
        if pos in common.POSITION_ZH
    ]
    return html.Div([
        html.Div([
            html.H2(team, style={"margin": 0}),
            html.Span(form_txt, style={"color": "#8A97AB", "marginLeft": 14}),
        ], style={"display": "flex", "alignItems": "baseline",
                  "marginBottom": 10}),
        html.Div(cards, className="roster-grid"),
    ])
