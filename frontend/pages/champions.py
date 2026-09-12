"""英雄頁：可搜尋排序的英雄數據表，點列查看英雄詳情。"""
from __future__ import annotations

import dash
import plotly.graph_objects as go
from dash import dash_table, dcc, html
from dash import Input, Output, State, callback

from backend import data_access
from backend.metrics.scoring import score_champions
from frontend.components import common
from frontend.theme import PLOTLY_TEMPLATE

dash.register_page(__name__, title="英雄", order=3)

_COLUMNS = [
    {"name": "英雄", "id": "champion_col", "type": "text",
     "presentation": "markdown"},
    {"name": "場數", "id": "games", "type": "numeric"},
    {"name": "勝-敗", "id": "record"},
    {"name": "勝率", "id": "win_rate", "type": "numeric"},
    {"name": "BP率", "id": "bp_rate", "type": "numeric"},
    {"name": "禁用率", "id": "ban_rate", "type": "numeric"},
    {"name": "藍方勝率", "id": "blue_win_rate"},
    {"name": "紅方勝率", "id": "red_win_rate"},
    {"name": "KDA", "id": "kda", "type": "numeric"},
    {"name": "參團率", "id": "kp", "type": "numeric"},
    {"name": "中期金差", "id": "gold_mid", "type": "numeric"},
    {"name": "分均金錢", "id": "gpm", "type": "numeric"},
    {"name": "分均輸出", "id": "dpm", "type": "numeric"},
    {"name": "分均承傷", "id": "dtaken", "type": "numeric"},
    {"name": "攻擊", "id": "attack", "type": "numeric"},
    {"name": "防禦", "id": "defense", "type": "numeric"},
    {"name": "均分", "id": "score", "type": "numeric"},
]


def layout():
    return html.Div([
        common.panel("英雄數據", html.Div([
            dcc.Input(id="champ-search", type="text", placeholder="搜尋英雄…",
                      debounce=False,
                      style={"marginBottom": 8, "width": 240,
                             "background": "#0E1526", "color": "#E6ECF5",
                             "border": "1px solid #22304A", "borderRadius": 6,
                             "padding": 7}),
            dash_table.DataTable(
                id="champ-table", columns=_COLUMNS, data=[],
                sort_action="native", page_size=24, page_action="native",
                markdown_options={"html": False},
                **common.table_styles(),
            ),
        ])),
        html.Div(id="champ-detail"),
    ], className="page")


@callback(
    Output("champ-table", "data"),
    Input("global-filter", "data"),
    Input("champ-search", "value"),
)
def _load_table(data, keyword):
    f = common.make_filter(data)
    min_games = int((data or {}).get("min_games") or 0)
    df = score_champions(data_access.champion_stats(f, min_games))
    if df.empty:
        return []
    if keyword:
        df = df[df["champion"].str.contains(keyword, case=False, na=False)]
    df = df.sort_values("score", ascending=False)
    df["champion_col"] = [
        f"![{c}]({common.champ_url(c)}) {c}" for c in df["champion"]]
    df["record"] = [f"{int(w)}-{int(g) - int(w)}"
                    for w, g in zip(df["wins"], df["games"])]
    # 該方未出賽時顯示「—」，不以空白或 0 誤導
    for c in ("blue_win_rate", "red_win_rate"):
        df[c] = df[c].fillna("—")
    keep = [c["id"] for c in _COLUMNS]
    return df[keep].round(1).to_dict("records")


@callback(
    Output("champ-detail", "children"),
    Input("champ-table", "active_cell"),
    Input("global-filter", "data"),
    State("champ-table", "derived_virtual_data"),
)
def _detail(active, data, rows):
    if not active or not rows:
        return None
    name = rows[active["row"]].get("champion_col", "")
    champion = name.split(") ", 1)[-1]  # 去掉 markdown 圖片前綴
    f = common.make_filter(data)
    detail = data_access.champion_detail(champion, f)
    pos_df, side_df, masters = detail["by_position"], detail["by_side"], \
        detail["masters"]
    if pos_df.empty:
        return common.empty_state("沒有此英雄資料", champion)

    # 分位置勝率
    fig_pos = go.Figure(go.Bar(
        x=pos_df["win_rate"],
        y=[common.POSITION_ZH.get(p, p) for p in pos_df["position"]],
        orientation="h", marker_color=common.theme.COLORS["blue"],
        text=[f"{g}場 {w}%" for g, w in zip(pos_df["games"], pos_df["win_rate"])],
    ))
    fig_pos.update_layout(template=PLOTLY_TEMPLATE, height=220,
                          margin=dict(l=40, r=10, t=10, b=30),
                          xaxis_title="勝率 %")

    # 10/15/25 分金差曲線（取最常用位置）
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

    side_kpis = []
    for _, r in side_df.iterrows():
        cls = "text-blue" if r["side"] == "Blue" else "text-red"
        side_kpis.append(
            common.kpi_card(f"{r['side']}方勝率", f"{r['win_rate']}%",
                            f"{int(r['games'])} 場", cls))

    master_rows = []
    for _, r in masters.iterrows():
        master_rows.append(html.Tr([
            html.Td(str(r["playername"])),
            html.Td(str(r.get("team", "—"))),
            html.Td(common.POSITION_ZH.get(r["position"], r["position"])),
            html.Td(f"{int(r['games'])}"),
            html.Td(f"{r['win_rate']}%"),
            html.Td(f"{0 if r['kda'] is None or r['kda'] != r['kda'] else r['kda']:.2f}"),
        ]))
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
            html.Div([
                dcc.Graph(figure=fig_pos),
                dcc.Graph(figure=fig_gd),
            ], className="grid-2"),
            html.H3("熟練度最高選手", style={"fontSize": 13}),
            masters_tbl,
        ], style={"display": "flex", "flexDirection": "column", "gap": 10}),
    )
