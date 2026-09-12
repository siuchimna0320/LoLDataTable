"""近況頁：近期系列賽戰果列表。"""
from __future__ import annotations

import dash
from dash import Input, Output, callback, html

from backend import data_access
from frontend.components import common

dash.register_page(__name__, title="近況", order=2)


def _series_card(item: dict) -> html.Div:
    blue = item["blue"]
    red = item["red"]
    blue_cls = "text-blue" if item["blue_wins"] > item["red_wins"] else "text-muted"
    red_cls = "text-red" if item["red_wins"] > item["blue_wins"] else "text-muted"
    games = [
        html.Span(
            f"第{g['game']}局 {g['length']}分 · "
            f"{g['winner']}勝" + (f"（{g['patch']}）" if g["patch"] else ""),
            style={"fontSize": 11, "color": "#8A97AB", "marginRight": 14},
        ) for g in item["games"]
    ]
    return html.Div([
        html.Div([
            html.Span(item["date"], style={"color": "#8A97AB", "width": 90}),
            html.Span(item["league"], style={"width": 55, "color": "#F5B945"}),
            html.Span(blue, className=blue_cls, style={"width": 160}),
            html.B(f"{item['blue_wins']} : {item['red_wins']}",
                   style={"margin": "0 14px"}),
            html.Span(red, className=red_cls, style={"width": 160}),
        ], style={"display": "flex", "alignItems": "center",
                  "marginBottom": 4, "fontWeight": 700}),
        html.Div(games, style={"paddingLeft": 150}),
    ], className="panel", style={"padding": "10px 14px", "marginBottom": 8})


def layout():
    return html.Div([
        html.Div(html.H2("近期戰果", className="panel-title"), className="panel"),
        html.Div(id="recent-list"),
    ], className="page")


@callback(Output("recent-list", "children"),
          Input("global-filter", "data"))
def _render(data):
    f = common.make_filter(data)
    series = data_access.recent_series(f, limit=60)
    if not series:
        return common.empty_state("篩選範圍內沒有比賽", "請調整年份、賽區或日期條件")
    return [_series_card(item) for item in series]
