"""總覽頁：KPI、近 7 日賽區分布、版本升降榜、連勝連敗、英雄快照。"""
from __future__ import annotations

import dash
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

from backend import data_access
from frontend.components import common
from frontend.theme import PLOTLY_TEMPLATE

dash.register_page(__name__, path="/", title="總覽", order=1)


def _mini_list(rows: list, up_good: bool = True) -> html.Div:
    items = []
    for i, (name, val) in enumerate(rows, 1):
        cls = "up" if (val > 0) == up_good else "down"
        items.append(html.Div([
            html.Span(i, className="rank"),
            html.Span(str(name), className="name"),
            html.Span(f"{val}", className=f"val {cls if val else ''}"),
        ], className="mini-row"))
    return html.Div(items or html.P("暫無資料", style={"color": "#8A97AB"}),
                    className="mini-list")


def layout():
    return html.Div([
        html.Div(id="ov-kpi", className="grid-kpi"),
        html.Div([
            common.panel("近 7 日場次（依賽區）", dcc.Graph(id="ov-last7")),
        ]),
        html.Div([
            common.panel("版本間 BP 率直升",
                         html.Div(id="ov-risers")),
            common.panel("版本間 BP 率暴跌",
                         html.Div(id="ov-fallers")),
        ], className="grid-2"),
        common.panel("進行中賽段（近 30 天）", html.Div(id="ov-stages")),
        html.Div([
            common.panel("戰隊最長連勝", html.Div(id="ov-winstreak")),
            common.panel("戰隊最長連敗", html.Div(id="ov-losestreak")),
        ], className="grid-2"),
        common.panel("熱門英雄快照", html.Div(id="ov-snapshot")),
    ], className="page")


@callback(
    Output("ov-kpi", "children"),
    Output("ov-last7", "figure"),
    Output("ov-risers", "children"),
    Output("ov-fallers", "children"),
    Output("ov-winstreak", "children"),
    Output("ov-losestreak", "children"),
    Output("ov-snapshot", "children"),
    Output("ov-stages", "children"),
    Input("global-filter", "data"),
)
def _render(data):
    f = common.make_filter(data)
    kpi = data_access.overview_kpi(f)
    kpis = [
        common.kpi_card("總場數", f"{kpi['games']:,}", "篩選範圍內"),
        common.kpi_card("藍方勝率", f"{kpi['blue_win_rate']}%",
                        color="text-blue"),
        common.kpi_card("紅方勝率", f"{kpi['red_win_rate']}%",
                        color="text-red"),
        common.kpi_card("場均時長", f"{kpi['avg_game_length']} 分"),
    ]

    last7 = data_access.games_by_day(f)
    fig = go.Figure()
    if not last7.empty:
        for league, sub in last7.groupby("league"):
            fig.add_trace(go.Bar(x=sub["date_iso"], y=sub["games"],
                                 name=str(league)))
        fig.update_layout(barmode="stack", template=PLOTLY_TEMPLATE,
                          height=280, margin=dict(l=30, r=10, t=10, b=30),
                          xaxis_title="日期", yaxis_title="場次")

    changes = data_access.patch_changes(f)
    def _change_rows(records):
        return [(f"{r['champion']}  {r['old_rate']}%→{r['new_rate']}%",
                 r["delta"]) for r in records]
    risers = _mini_list(_change_rows(changes["risers"])) if changes["risers"] \
        else html.P("版本資料不足（需至少 2 個版本）", style={"color": "#8A97AB"})
    fallers = _mini_list(_change_rows(changes["fallers"]), up_good=False) \
        if changes["fallers"] else html.P(
            "版本資料不足（需至少 2 個版本）", style={"color": "#8A97AB"})

    streaks = data_access.team_streaks(f)
    wins = _mini_list(streaks["win_streak"])
    losses = _mini_list(streaks["lose_streak"], up_good=False)

    champs = data_access.champion_stats(f, 20).head(20)
    icons = [html.Span([common.champ_img(c, 34),
                        html.Span(f" {int(g)} 場",
                                  style={"fontSize": 10, "color": "#8A97AB"})],
                       style={"margin": 4}, title=c)
             for c, g in zip(champs["champion"], champs["games"])]

    stages = data_access.active_stages(f)
    if stages.empty:
        stage_el = html.P("近 30 天無比賽紀錄", style={"color": "#8A97AB"})
    else:
        stage_el = html.Div([
            html.Div([
                html.Span(f'{r["league"]} {r["split"] or ""}'.strip(),
                          className="name", style={"color": "#E6ECF5"}),
                html.Span(f'{int(r["games"])} 場',
                          style={"color": "#8A97AB", "marginRight": 12}),
                html.Span(f'最近 {r["last_date"]}',
                          style={"color": "#F5B945", "fontSize": 11}),
            ], className="mini-row")
            for _, r in stages.head(12).iterrows()
        ], className="mini-list")

    return (kpis, fig, risers, fallers, wins, losses,
            html.Div(icons), stage_el)
