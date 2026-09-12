"""天梯頁：選手 SoloQ 積分骨架（FR-18）。

Oracle's Elixir 為賽後逐場數據，不含選手 Riot 帳號的 SoloQ 天梯資料，
故本頁保留骨架，待日後以 Riot API（需 API Key，由環境變數提供）接入。
"""
from __future__ import annotations

import dash
from dash import html

from frontend.components import common

dash.register_page(__name__, title="天梯", order=10)


def layout():
    return html.Div([
        common.panel("職業選手 SoloQ 天梯", common.empty_state(
            "天梯功能開發中",
            "SoloQ 積分與名次來自 Riot 官方天梯（Riot API），"
            "Oracle's Elixir 不提供此類資料。此頁保留骨架，"
            "未來設定 Riot API Key（環境變數，不寫入程式碼）後，"
            "即可顯示選手當前分數、名次、常用英雄與近期勝率。",
        )),
        html.Div([
            common.panel("預定欄位（骨架）", html.Div([
                html.Div(["選手", "戰隊", "伺服器", "積分", "名次"],
                         style={"display": "grid",
                                "gridTemplateColumns": "2fr 2fr 1fr 1fr 1fr",
                                "color": "#8A97AB", "padding": "8px 4px",
                                "borderBottom": "1px solid #22304A"}),
                *[html.Div(["—"] * 5,
                           style={"display": "grid",
                                  "gridTemplateColumns": "2fr 2fr 1fr 1fr 1fr",
                                  "padding": "10px 4px",
                                  "borderBottom": "1px dashed #22304A",
                                  "color": "#556070"})
                  for _ in range(5)],
            ])),
        ]),
    ], className="page")
