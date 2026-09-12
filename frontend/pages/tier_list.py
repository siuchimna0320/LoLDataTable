"""英雄 Tier 頁：依評分分位分組 S/A/B/C/D 的英雄牆。"""
from __future__ import annotations

import dash
from dash import Input, Output, callback, html

from backend import data_access
from backend.metrics.scoring import assign_tiers, score_champions
from frontend.components import common

dash.register_page(__name__, title="英雄Tier", order=9)

TIER_STYLE = {
    "S": ("#F05A6A", "S 級｜版本頂角"),
    "A": ("#F5B945", "A 級｜強勢首選"),
    "B": ("#4C8DFF", "B 級｜穩定可用"),
    "C": ("#8A97AB", "C 級｜視隊伍取捨"),
    "D": ("#556070", "D 級｜冷門"),
}


def _tier_block(tier: str, group) -> html.Div:
    color, zh = TIER_STYLE[tier]
    cards = []
    for _, r in group.iterrows():
        pos_zh = common.POSITION_ZH.get(r.get("main_position"), "")
        kda = r.get("kda")
        kda_txt = f" KDA {kda:.1f}" if kda == kda else ""
        cards.append(html.Span([
            common.champ_img(r["champion"], 42),
            html.Span(f'{r["score"]}',
                      style={"fontSize": 10, "color": color,
                             "display": "block"}),
            html.Span(pos_zh,
                      style={"fontSize": 9, "color": "#8A97AB",
                             "display": "block"}),
        ], style={"textAlign": "center", "margin": 5, "width": 52},
            title=(f'{r["champion"]}（{pos_zh}）｜{int(r["games"])}場 '
                   f'勝率{r["win_rate"]}% BP率{r.get("bp_rate", 0)}% '
                   f'禁用率{r.get("ban_rate", 0)}%{kda_txt}')))
    return html.Div([
        html.Div([
            html.Span(tier, style={"color": color, "fontSize": 22,
                                   "fontWeight": 700, "width": 34}),
            html.Span(zh, style={"color": "#8A97AB"}),
        ], style={"display": "flex", "alignItems": "center", "gap": 10,
                  "marginBottom": 6}),
        html.Div(cards, style={"display": "flex", "flexWrap": "wrap"}),
    ], className="panel")


def layout():
    return html.Div([
        html.Div(html.H2("英雄 Tier List（依當前篩選範圍同儕分位）",
                         className="panel-title"), className="panel"),
        html.Div(id="tier-blocks"),
    ], className="page")


@callback(
    Output("tier-blocks", "children"),
    Input("global-filter", "data"),
)
def _render(data):
    f = common.make_filter(data)
    min_games = int((data or {}).get("min_games") or 10)
    df = score_champions(data_access.champion_stats(f, min_games))
    if df.empty:
        return common.empty_state("樣本不足", "請調低最少場數門檻或放寬篩選條件")
    df = assign_tiers(df)
    # 固定由 S 到 D 由上至下呈現（FR-17）
    return [_tier_block(tier, df[df["tier"] == tier])
            for tier in ("S", "A", "B", "C", "D")
            if (df["tier"] == tier).any()]
