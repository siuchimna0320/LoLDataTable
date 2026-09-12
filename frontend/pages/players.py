"""選手頁：五位置數據表與雙選手 8 軸雷達對比（軸指標可自選）。"""
from __future__ import annotations

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dash_table, dcc, html

from backend import data_access
from backend.metrics.scoring import pct_rank, score_players
from frontend.components import common
from frontend.theme import PLOTLY_TEMPLATE

dash.register_page(__name__, title="選手", order=4)

# 規格 8 軸；資料源無單殺欄位，依 FR-20 以可溯源的「輸出佔比」誠實替代
AXIS_OPTIONS = [
    ("win_rate", "勝率"), ("champ_pool", "角色池"), ("kda", "KDA"),
    ("kp", "參與率"), ("dmg_share", "輸出佔比（資料源無單殺率）"),
    ("gpm", "分均金錢"), ("dpm", "分均輸出"), ("dtaken", "分均承傷"),
]
DEFAULT_AXES = [c for c, _ in AXIS_OPTIONS]

_TABLE_COLS = [
    {"name": "排名", "id": "rank", "type": "numeric"},
    {"name": "選手", "id": "playername"},
    {"name": "戰隊", "id": "team"},
    {"name": "位置", "id": "position_zh"},
    {"name": "場數", "id": "games", "type": "numeric"},
    {"name": "勝率", "id": "win_rate", "type": "numeric"},
    {"name": "KDA", "id": "kda", "type": "numeric"},
    {"name": "參團率", "id": "kp", "type": "numeric"},
    {"name": "DPM", "id": "dpm", "type": "numeric"},
    {"name": "中期金差", "id": "gold_mid", "type": "numeric"},
    {"name": "均分", "id": "score", "type": "numeric"},
]

# 雷達需要的完整欄位（存於 dcc.Store，避免重複查倉儲）
_STORE_COLS = ["playername", "score"] + [c for c, _ in AXIS_OPTIONS]


def _radar_figure(df: pd.DataFrame, name_a: str, name_b: str,
                  axes: list[str]) -> go.Figure:
    """以同儕百分位繪製雙選手雷達。"""
    labels = [dict(AXIS_OPTIONS)[c] for c in axes]
    fig = go.Figure()
    for name, color in ((name_a, "#4C8DFF"), (name_b, "#F5B945")):
        row = df[df["playername"] == name]
        if row.empty:
            continue
        vals = [float(pct_rank(df[col].fillna(df[col].median()))[row.index[0]])
                for col in axes]
        fig.add_trace(go.Scatterpolar(
            r=vals + vals[:1], theta=labels + labels[:1],
            fill="toself", name=name,
            line=dict(color=color), opacity=0.6,
        ))
    fig.update_layout(template=PLOTLY_TEMPLATE, polar=dict(
        radialaxis=dict(visible=True, range=[0, 100], color="#8A97AB"),
        angularaxis=dict(color="#E6ECF5")), height=400,
        margin=dict(l=30, r=30, t=30, b=20),
        legend=dict(orientation="h"))
    return fig


def layout():
    return html.Div([
        common.panel("選手數據", html.Div([
            dcc.Tabs(id="player-pos-tabs", value="all",
                     colors={"border": "#22304A", "primary": "#4C8DFF",
                             "background": "#0E1526"},
                     children=[dcc.Tab(label="全部", value="all")] + [
                         dcc.Tab(label=zh, value=key)
                         for key, zh in common.POSITION_ZH.items()]),
            dash_table.DataTable(
                id="player-table", columns=_TABLE_COLS, data=[],
                sort_action="native", filter_action="native",
                page_size=20, page_action="native",
                **common.table_styles(),
            ),
        ])),
        dcc.Store(id="player-store", data=[]),
        common.panel("雙選手雷達對比", html.Div([
            html.Div([
                html.Div([html.Label("選手 A"),
                          dcc.Dropdown(id="radar-a", style={"color": "#0B1020"})],
                         className="filter-item"),
                html.Div([html.Label("選手 B"),
                          dcc.Dropdown(id="radar-b", style={"color": "#0B1020"})],
                         className="filter-item"),
            ], style={"display": "flex", "gap": 16, "marginBottom": 8}),
            dcc.Checklist(
                id="radar-axes",
                options=[{"label": zh, "value": col}
                         for col, zh in AXIS_OPTIONS],
                value=DEFAULT_AXES, inline=True,
                style={"color": "#E6ECF5", "fontSize": 12},
                inputStyle={"marginRight": 4, "marginLeft": 10}),
            dcc.Graph(id="player-radar"),
        ])),
    ], className="page")


@callback(
    Output("player-table", "data"),
    Output("player-store", "data"),
    Output("radar-a", "options"),
    Output("radar-b", "options"),
    Output("radar-a", "value"),
    Output("radar-b", "value"),
    Input("global-filter", "data"),
    Input("player-pos-tabs", "value"),
)
def _load(data, pos_tab):
    f = common.make_filter(data)
    if pos_tab != "all":
        f.positions = [pos_tab]
    min_games = int((data or {}).get("min_games") or 5)
    df = score_players(data_access.player_stats(f, min_games))
    if df.empty:
        return [], [], [], [], None, None
    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    df["position_zh"] = [common.POSITION_ZH.get(p, p)
                         for p in df["position"]]
    table = df[[c["id"] for c in _TABLE_COLS]].round(1).replace(
        {np.nan: None}).to_dict("records")
    store = df[_STORE_COLS].round(2).replace(
        {np.nan: None}).to_dict("records")
    options = [{"label": n, "value": n} for n in df["playername"]]
    return (table, store, options, options,
            df.iloc[0]["playername"],
            df.iloc[1]["playername"] if len(df) > 1 else df.iloc[0]["playername"])


@callback(
    Output("player-radar", "figure"),
    Input("radar-a", "value"),
    Input("radar-b", "value"),
    Input("radar-axes", "value"),
    Input("player-store", "data"),
)
def _radar(name_a, name_b, axes, store):
    if not store or not axes:
        return go.Figure()
    df = pd.DataFrame(store)
    return _radar_figure(df, name_a, name_b, axes)
