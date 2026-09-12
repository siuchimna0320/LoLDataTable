"""比賽 BP 頁：近期比賽清單、藍紅方 BP 順序棋盤與先選標記、頂部 KPI。"""
from __future__ import annotations

import dash
from dash import Input, Output, State, callback, dash_table, html

from backend import data_access
from frontend.components import common

dash.register_page(__name__, title="比賽BP", order=7)

_COLS = [
    {"name": "日期", "id": "date_iso"},
    {"name": "賽區", "id": "league"},
    {"name": "版本", "id": "patch"},
    {"name": "藍方", "id": "blue_team"},
    {"name": "紅方", "id": "red_team"},
    {"name": "時長(分)", "id": "length"},
]


def _slot(champion: str | None, banned: bool, order: int | None = None):
    cls = "bp-slot ban" if banned else "bp-slot"
    if not champion:
        return html.Span("—", className=cls)
    return html.Span([
        common.champ_img(champion, 44),
        html.Span(str(order or ""),
                  style={"position": "absolute", "top": -6, "left": -6,
                         "background": "#F5B945", "color": "#0B1020",
                         "borderRadius": "50%", "width": 16, "height": 16,
                         "fontSize": 10, "display": "flex",
                         "alignItems": "center", "justifyContent": "center"}),
    ], className=f"{cls} filled", style={"position": "relative"})


def _pick_badge(firstpick) -> html.Span | None:
    """先選／後選徽章；資料缺失（早期年份）則不渲染。"""
    if firstpick is None or firstpick != firstpick:
        return None
    is_fp = int(firstpick) == 1
    return html.Span(
        "先選" if is_fp else "後選",
        style={"marginLeft": 8, "padding": "1px 8px", "borderRadius": 10,
               "fontSize": 11, "fontWeight": 700,
               "background": "#F5B945" if is_fp else "#22304A",
               "color": "#0B1020" if is_fp else "#E6ECF5"})


def _side_board(side_zh: str, side: str, draft: dict, won: bool,
                firstpick) -> html.Div:
    picks = [_slot(draft["picks"].get(i), False, i) for i in range(1, 6)]
    bans = [_slot(draft["bans"].get(i), True, i) for i in range(1, 6)]
    color = "text-blue" if side == "Blue" else "text-red"
    return html.Div([
        html.H3([side_zh, " 勝" if won else "", _pick_badge(firstpick)],
                className=color, style={"margin": "4px 0"}),
        html.Div([html.Span("選：", style={"color": "#8A97AB", "fontSize": 12}),
                  *picks], style={"marginBottom": 8}),
        html.Div([html.Span("禁：", style={"color": "#8A97AB", "fontSize": 12}),
                  *bans]),
    ], className=f"bp-side bp-{side.lower()}")


def _summary(df) -> html.Div:
    """列表場次的藍紅勝率與先選勝率 KPI。"""
    games = len(df)
    blue_wr = round(float(df["blue_result"].mean()) * 100, 1) if games else 0
    red_wr = round(100 - blue_wr, 1) if games else 0
    # 先選方勝率：藍先選看藍結果、紅先選看紅結果；僅統計有標記者
    fp_rows = df[df["blue_firstpick"].notna()]
    if not fp_rows.empty:
        fp_wins = ((fp_rows["blue_firstpick"] == 1) &
                   (fp_rows["blue_result"] == 1)).sum()
        fp_wins += ((fp_rows["red_firstpick"] == 1) &
                    (fp_rows["red_result"] == 1)).sum()
        fp_wr = round(float(fp_wins) / len(fp_rows) * 100, 1)
    else:
        fp_wr = "—"
    return html.Div([
        common.kpi_card("列表場數", games),
        common.kpi_card("藍方勝率", f"{blue_wr}%", color="text-blue"),
        common.kpi_card("紅方勝率", f"{red_wr}%", color="text-red"),
        common.kpi_card("先選方勝率",
                        f"{fp_wr}%" if fp_wr != "—" else "—"),
    ], className="grid-kpi")


def layout():
    return html.Div([
        common.panel("選擇比賽（點列查看 BP）", html.Div([
            html.Div(id="bp-summary", style={"marginBottom": 10}),
            dash_table.DataTable(
                id="bp-match-table", columns=_COLS, data=[],
                page_size=12, page_action="native",
                **common.table_styles(),
            ),
        ])),
        html.Div(id="bp-board"),
    ], className="page")


@callback(
    Output("bp-match-table", "data"),
    Output("bp-summary", "children"),
    Input("global-filter", "data"),
)
def _load(data):
    f = common.make_filter(data)
    df = data_access.match_bp_records(f, 60)
    if df.empty:
        return [], _summary(df)
    df["length"] = (df["gamelength"] / 60).round(1)
    df["patch"] = df["patch"].apply(
        lambda p: f"{float(p):g}" if p == p and p is not None else "—")
    return df[[c["id"] for c in _COLS]].fillna("—").to_dict("records"), _summary(df)


@callback(
    Output("bp-board", "children"),
    Input("bp-match-table", "active_cell"),
    State("bp-match-table", "derived_virtual_indices"),
    State("global-filter", "data"),
)
def _board(active, indices, data):
    if not active:
        return None
    f = common.make_filter(data)
    df = data_access.match_bp_records(f, 60)
    row_idx = indices[active["row"]]
    row = df.iloc[row_idx]
    draft = data_access.match_draft(row["gameid"])
    blue_won = bool(row["blue_result"])
    no_draft = not draft["Blue"]["picks"] and not draft["Red"]["picks"]
    note = html.P("此年份來源資料未提供逐手 BP 記錄，僅能顯示陣容或缺項。",
                  style={"color": "#F5B945", "fontSize": 12, "margin": "8px 0"}
                  ) if no_draft else None
    return common.panel(
        f'{row["date_iso"]} {row["blue_team"]} vs {row["red_team"]}',
        html.Div([
            note,
            html.Div([
                _side_board(f'藍方 {row["blue_team"]}', "Blue", draft["Blue"],
                            blue_won, row.get("blue_firstpick")),
                _side_board(f'紅方 {row["red_team"]}', "Red", draft["Red"],
                            not blue_won, row.get("red_firstpick")),
            ], className="bp-board"),
        ]),
        tag=f'patch {float(row["patch"]):g}' if row["patch"] == row["patch"]
        else None,
    )
