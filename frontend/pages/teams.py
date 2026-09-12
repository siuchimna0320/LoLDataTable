"""戰隊頁：戰隊數據表與雙戰隊 12 軸雷達（軸指標可自選）、選邊資訊。"""
from __future__ import annotations

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dash_table, dcc, html
from urllib.parse import quote

from backend import data_access
from backend.metrics.scoring import pct_rank, score_teams
from frontend.components import common
from frontend.theme import PLOTLY_TEMPLATE

dash.register_page(__name__, title="戰隊", order=5)

# FR-13 戰隊雷達 12 軸
AXIS_OPTIONS = [
    ("win_rate", "勝率"), ("champ_pool", "角色池"),
    ("tower_rate", "一塔率"), ("grub2_rate", "2+ 巢蟲率"),
    ("dragon_rate", "首龍率"), ("herald_rate", "預示者率"),
    ("baron_rate", "首巴龍率"), ("gold_mid", "中期金差"),
    ("firstblood_rate", "首經濟率"), ("team_gpm", "分均金錢"),
    ("team_dpm", "分均輸出"), ("vspm", "視野分數"),
]
DEFAULT_AXES = [c for c, _ in AXIS_OPTIONS]

_COLUMNS = [
    {"name": "排名", "id": "rank", "type": "numeric"},
    {"name": "戰隊", "id": "teamname"},
    {"name": "場數", "id": "games", "type": "numeric"},
    {"name": "勝率", "id": "win_rate", "type": "numeric"},
    {"name": "場均時長", "id": "avg_len", "type": "numeric"},
    {"name": "小龍率", "id": "dragon_rate", "type": "numeric"},
    {"name": "先鋒率", "id": "herald_rate", "type": "numeric"},
    {"name": "巴龍率", "id": "baron_rate", "type": "numeric"},
    {"name": "一塔率", "id": "tower_rate", "type": "numeric"},
    {"name": "分均金錢", "id": "team_gpm", "type": "numeric"},
    {"name": "分均輸出", "id": "team_dpm", "type": "numeric"},
    {"name": "前期金差", "id": "gd10", "type": "numeric"},
    {"name": "中期金差", "id": "gold_mid", "type": "numeric"},
    {"name": "視野/分", "id": "vspm", "type": "numeric"},
    {"name": "英雄池", "id": "champ_pool", "type": "numeric"},
    {"name": "均分", "id": "score", "type": "numeric"},
]

_STORE_COLS = ["teamname", "score", "fp_blue", "fp_red", "sp_blue",
               "sp_red"] + [c for c, _ in AXIS_OPTIONS]


def _radar_figure(df: pd.DataFrame, name_a: str, name_b: str,
                  axes: list[str]) -> go.Figure:
    """以同儕百分位繪製雙戰隊雷達。"""
    labels = [dict(AXIS_OPTIONS)[c] for c in axes]
    fig = go.Figure()
    for name, color in ((name_a, "#4C8DFF"), (name_b, "#F5B945")):
        row = df[df["teamname"] == name]
        if row.empty:
            continue
        vals = [float(pct_rank(df[col].fillna(df[col].median()))[row.index[0]])
                for col in axes]
        fig.add_trace(go.Scatterpolar(
            r=vals + vals[:1], theta=labels + labels[:1],
            fill="toself", name=name, line=dict(color=color), opacity=0.65))
    fig.update_layout(template=PLOTLY_TEMPLATE, polar=dict(
        radialaxis=dict(visible=True, range=[0, 100], color="#8A97AB"),
        angularaxis=dict(color="#E6ECF5")), height=420,
        margin=dict(l=20, r=20, t=30, b=20),
        legend=dict(orientation="h"))
    return fig


def layout():
    return html.Div([
        common.panel("戰隊數據", dash_table.DataTable(
            id="team-table", columns=_COLUMNS, data=[],
            sort_action="native", filter_action="native",
            page_size=20, page_action="native",
            **common.table_styles(),
        )),
        dcc.Store(id="team-store", data=[]),
        common.panel("雙戰隊雷達對比（12 軸可自選）", html.Div([
            html.Div([
                html.Div([html.Label("戰隊 A"),
                          dcc.Dropdown(id="team-radar-a",
                                       style={"color": "#0B1020"})],
                         className="filter-item"),
                html.Div([html.Label("戰隊 B"),
                          dcc.Dropdown(id="team-radar-b",
                                       style={"color": "#0B1020"})],
                         className="filter-item"),
            ], style={"display": "flex", "gap": 16, "marginBottom": 8}),
            dcc.Checklist(
                id="team-radar-axes",
                options=[{"label": zh, "value": col}
                         for col, zh in AXIS_OPTIONS],
                value=DEFAULT_AXES, inline=True,
                style={"color": "#E6ECF5", "fontSize": 12},
                inputStyle={"marginRight": 4, "marginLeft": 10}),
            dcc.Graph(id="team-radar"),
        ])),
        common.panel("選邊與先選", html.Div(id="team-side")),
    ], className="page")


@callback(
    Output("team-table", "data"),
    Output("team-store", "data"),
    Output("team-radar-a", "options"),
    Output("team-radar-b", "options"),
    Output("team-radar-a", "value"),
    Output("team-radar-b", "value"),
    Input("global-filter", "data"),
)
def _load(data):
    f = common.make_filter(data)
    min_games = int((data or {}).get("min_games") or 5)
    df = score_teams(data_access.team_stats(f, min_games))
    if df.empty:
        return [], [], [], [], None, None
    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    keep = [c["id"] for c in _COLUMNS]
    table = df[keep].round(1).replace({np.nan: None}).to_dict("records")
    store = df[_STORE_COLS].round(2).replace(
        {np.nan: None}).to_dict("records")
    options = [{"label": n, "value": n} for n in df["teamname"]]
    return (table, store, options, options, df.iloc[0]["teamname"],
            df.iloc[1]["teamname"] if len(df) > 1 else df.iloc[0]["teamname"])


@callback(
    Output("team-radar", "figure"),
    Input("team-radar-a", "value"),
    Input("team-radar-b", "value"),
    Input("team-radar-axes", "value"),
    Input("team-store", "data"),
)
def _radar(name_a, name_b, axes, store):
    if not store or not axes:
        return go.Figure()
    df = pd.DataFrame(store)
    return _radar_figure(df, name_a, name_b, axes)


@callback(
    Output("team-side", "children"),
    Input("team-radar-a", "value"),
    Input("team-store", "data"),
)
def _side(selected, store):
    """戰隊 A 的選邊／先選分佈卡片。"""
    if not store or not selected:
        return "—"
    row = pd.DataFrame(store)
    row = row[row["teamname"] == selected]
    if row.empty:
        return "—"
    r = row.iloc[0]
    # first_pick 可能全為 NULL（早期年份），以 0 兜底避免渲染失敗
    fp_blue = int(r.get("fp_blue") or 0)
    fp_red = int(r.get("fp_red") or 0)
    sp_blue = int(r.get("sp_blue") or 0)
    sp_red = int(r.get("sp_red") or 0)
    total_fp = fp_blue + fp_red
    return html.Div([
        html.Div([
            common.kpi_card("藍色先選場次", fp_blue, color="text-blue"),
            common.kpi_card("紅色先選場次", fp_red, color="text-red"),
            common.kpi_card("藍色後選場次", sp_blue, color="text-blue"),
            common.kpi_card("紅色後選場次", sp_red, color="text-red"),
        ], className="grid-kpi"),
        html.P(f"先選總計 {total_fp} 場（firstPick 記錄僅部分年份完整）",
               style={"color": "#8A97AB", "fontSize": 12}),
        dcc.Link("查看此戰隊陣容與英雄池 →",
                 href=f"/roster?team={quote(selected)}",
                 style={"color": "#F5B945"}),
    ])
