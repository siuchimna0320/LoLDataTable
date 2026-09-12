"""模擬 BP 頁：指定先選方後走 20 步禁選流程，可點選手英雄池頭像，
完成後試算九分項加權選角評分（FR-16）。"""
from __future__ import annotations

import dash
import plotly.graph_objects as go
from dash import (ALL, Input, Output, State, callback, ctx, dcc, html)

from backend import data_access
from backend.metrics.draft_score import evaluate
from frontend.components import common
from frontend.theme import PLOTLY_TEMPLATE

dash.register_page(__name__, title="模擬BP", order=8)

POSITIONS = ["top", "jng", "mid", "bot", "sup"]

# 九分項顯示名（順序對應 config.DRAFT_SCORE_WEIGHTS）
COMPONENT_ZH = {
    "patch_strength": "版本強弱", "player_mastery": "選手熟練",
    "team_winrate": "戰隊勝率", "lane_matchup": "對線對位",
    "synergy": "隊友相合", "composition": "陣容完整",
    "ban_value": "禁用價值", "flex_pick": "可錯位",
    "team_form": "戰隊近況",
}

try:
    _TEAMS = data_access.get_options().get("teams", [])
except Exception:  # noqa: BLE001
    _TEAMS = []
_CHAMPS = sorted(common.load_champ_mapping().keys())


def _bp_sequence(first_side: str) -> list[tuple[str, str]]:
    """20 步 BP 順序：(陣營, 階段)。second 為後選方。"""
    second = "red" if first_side == "blue" else "blue"
    seq = [(first_side, "ban"), (second, "ban")] * 3          # 前 6 禁
    seq += [(first_side, "pick"), (second, "pick"),
            (second, "pick"), (first_side, "pick"),
            (first_side, "pick"), (second, "pick")]           # 前 6 選
    seq += [(second, "ban"), (first_side, "ban"),
            (second, "ban"), (first_side, "ban")]             # 後 4 禁
    seq += [(second, "pick"), (first_side, "pick"),
            (first_side, "pick"), (second, "pick")]           # 後 4 選
    return seq


def _phase_zh(phase: str) -> str:
    return "禁用" if phase == "ban" else "選將"


def _used_champs(state: dict) -> set:
    return {s["champ"] for s in state.get("log", [])}


def _side_actions(state: dict, side: str, phase: str) -> list[dict]:
    return [s for s in state.get("log", [])
            if s["side"] == side and s["phase"] == phase]


def _slot_badge(index: int) -> html.Span:
    return html.Span(str(index),
                     style={"position": "absolute", "top": -6, "left": -6,
                            "background": "#F5B945", "color": "#0B1020",
                            "borderRadius": "50%", "width": 16, "height": 16,
                            "fontSize": 10, "display": "flex",
                            "alignItems": "center", "justifyContent": "center"})


def _board(side: str, label: str, state: dict) -> html.Div:
    """單側已完成的禁／選棋盤。"""
    picks = _side_actions(state, side, "pick")
    bans = _side_actions(state, side, "ban")
    pick_slots = []
    for i in range(5):
        if i < len(picks):
            a = picks[i]
            pick_slots.append(html.Span([
                common.champ_img(a["champ"], 44), _slot_badge(i + 1),
                html.Span(common.POSITION_ZH.get(a.get("pos"), ""),
                          style={"fontSize": 9, "color": "#F5B945",
                                 "display": "block"}),
            ], className="bp-slot filled",
                style={"position": "relative", "textAlign": "center"}))
        else:
            pick_slots.append(html.Span("—", className="bp-slot"))
    ban_slots = []
    for i in range(5):
        if i < len(bans):
            ban_slots.append(html.Span([
                common.champ_img(bans[i]["champ"], 38), _slot_badge(i + 1)],
                className="bp-slot ban filled",
                style={"position": "relative"}))
        else:
            ban_slots.append(html.Span("—", className="bp-slot ban"))
    first_tag = "（先選）" if state.get("first") == side else "（後選）"
    return html.Div([
        html.H3(label + first_tag, className=f"text-{side}",
                style={"margin": "4px 0"}),
        html.Div([html.Span("選：", style={"color": "#8A97AB", "fontSize": 12}),
                  *pick_slots], style={"marginBottom": 8}),
        html.Div([html.Span("禁：", style={"color": "#8A97AB", "fontSize": 12}),
                  *ban_slots]),
    ], className=f"bp-side bp-{side}")


def _pool_avatar(side: str, pos: str, champ: str, pool_row: dict,
                 meta: dict, used: set) -> html.Button:
    """選手英雄池頭像（帶場數/勝率/KDA 與全體 BP%/KDA hover 資訊）。"""
    m = meta.get(champ, {})
    title = (f'{champ}｜{pool_row["games"]} 場 勝率 {pool_row["win_rate"]}% '
             f'KDA {pool_row.get("kda", "—")}｜'
             f'全體 BP {m.get("bp_rate", "—")}% 禁用 {m.get("ban_rate", "—")}% '
             f'KDA {m.get("kda", "—")}')
    return html.Button(
        common.champ_img(champ, 34),
        id={"type": "mb-pool", "side": side, "pos": pos, "champ": champ},
        key=f"{side}-{pos}-{champ}",
        title=title, disabled=champ in used,
        style={"background": "none", "border": "none", "padding": 2,
               "cursor": "pointer" if champ not in used else "not-allowed",
               "opacity": 0.3 if champ in used else 1},
    )


def _roster_panels(roster: dict, meta: dict, state: dict) -> list:
    used = _used_champs(state)
    panels = []
    for side, label in (("blue", "藍方英雄池"), ("red", "紅方英雄池")):
        side_data = roster.get(side, {})
        blocks = []
        for pos in POSITIONS:
            info = side_data.get(pos)
            if not info:
                continue
            avatars = [_pool_avatar(side, pos, r["champion"], r, meta, used)
                       for r in info.get("pool", [])[:8]]
            blocks.append(html.Div([
                html.Span(f'{common.POSITION_ZH[pos]} {info["player"]}',
                          style={"color": "#F5B945", "fontSize": 11,
                                 "marginRight": 6}),
                html.Span(avatars, style={"display": "inline-flex",
                                          "flexWrap": "wrap", "gap": 2}),
            ], style={"marginBottom": 6}))
        panels.append(common.panel(label, html.Div(blocks)))
    return panels


def _ban_suggestions(meta_rows: list, used: set,
                     step: tuple[str, str] | None) -> html.Div:
    """禁用階段的高 BP 英雄點選條。step 為 (陣營, 階段)。"""
    if not step or step[1] != "ban":
        return html.Div()
    side = step[0]
    cands = sorted((r for r in meta_rows if r["champion"] not in used),
                   key=lambda r: r.get("bp_rate") or 0, reverse=True)[:10]
    return common.panel(
        f'禁用建議（點頭像填入 {("藍" if side == "blue" else "紅")}方下一禁）',
        html.Div([
            html.Span([
                html.Button(
                    common.champ_img(r["champion"], 36),
                    id={"type": "mb-pool", "side": side, "pos": "",
                        "champ": r["champion"]},
                    key=f"{side}-ban-{r['champion']}",
                    title=(f'{r["champion"]}｜BP {r.get("bp_rate", 0)}% '
                           f'禁用 {r.get("ban_rate", 0)}% '
                           f'KDA {r.get("kda", "—")}'),
                    style={"background": "none", "border": "none",
                           "padding": 2, "cursor": "pointer", "margin": 2},
                )]) for r in cands
        ], style={"display": "flex", "flexWrap": "wrap"}))


def layout():
    return html.Div([
        common.panel("模擬設定", html.Div([
            html.Div([html.Label("藍方戰隊"),
                      dcc.Dropdown(id="mb-team-blue",
                                   options=[{"label": t, "value": t}
                                            for t in _TEAMS],
                                   placeholder="選擇戰隊", searchable=True,
                                   style={"color": "#0B1020", "minWidth": 200})],
                     className="filter-item"),
            html.Div([html.Label("紅方戰隊"),
                      dcc.Dropdown(id="mb-team-red",
                                   options=[{"label": t, "value": t}
                                            for t in _TEAMS],
                                   placeholder="選擇戰隊", searchable=True,
                                   style={"color": "#0B1020", "minWidth": 200})],
                     className="filter-item"),
            html.Div([html.Label("先選方"),
                      dcc.RadioItems(id="mb-firstpick",
                                     options=[{"label": "藍方先選",
                                               "value": "blue"},
                                              {"label": "紅方先選",
                                               "value": "red"}],
                                     value="blue", inline=True,
                                     style={"color": "#E6ECF5"})],
                     className="filter-item"),
            html.Button("開始模擬", id="mb-start", n_clicks=0,
                        style={"background": "#4C8DFF", "color": "#fff",
                               "border": "none", "borderRadius": 8,
                               "padding": "8px 20px", "cursor": "pointer",
                               "alignSelf": "flex-end"}),
        ], style={"display": "flex", "gap": 18, "flexWrap": "wrap",
                  "alignItems": "flex-end"})),
        dcc.Store(id="mb-state", data={"started": False, "log": []}),
        dcc.Store(id="mb-roster", data={}),
        dcc.Store(id="mb-meta", data={}),
        html.Div(id="mb-step", style={"margin": "8px 0"}),
        html.Div(id="mb-boards", className="bp-board"),
        html.Div(id="mb-pools"),
        html.Div([
            html.Button("上一步", id="mb-undo", n_clicks=0,
                        style={"background": "#22304A", "color": "#E6ECF5",
                               "border": "none", "borderRadius": 8,
                               "padding": "8px 18px", "cursor": "pointer",
                               "marginRight": 10}),
            html.Button("重新開始", id="mb-reset", n_clicks=0,
                        style={"background": "#22304A", "color": "#E6ECF5",
                               "border": "none", "borderRadius": 8,
                               "padding": "8px 18px", "cursor": "pointer"}),
        ], style={"margin": "10px 0"}),
        common.panel("手動指定英雄", html.Div([
            dcc.Dropdown(id="mb-manual-champ",
                         options=[{"label": c, "value": c} for c in _CHAMPS],
                         placeholder="搜尋英雄", searchable=True,
                         style={"color": "#0B1020", "maxWidth": 260}),
            dcc.RadioItems(id="mb-manual-pos",
                           options=[{"label": zh, "value": key}
                                    for key, zh in
                                    common.POSITION_ZH.items()],
                           value="top", inline=True,
                           style={"color": "#E6ECF5", "fontSize": 12}),
            html.Button("填入當前步驟", id="mb-manual-btn", n_clicks=0,
                        style={"background": "#F5B945", "color": "#0B1020",
                               "border": "none", "borderRadius": 8,
                               "padding": "7px 16px", "cursor": "pointer"}),
        ], style={"display": "flex", "gap": 14, "alignItems": "center",
                  "flexWrap": "wrap"})),
        html.Div(id="mb-result", style={"marginTop": 12}),
    ], className="page")


def _step_banner(state: dict) -> html.Div:
    log = state.get("log", [])
    seq = _bp_sequence(state.get("first", "blue"))
    if len(log) >= len(seq):
        return common.kpi_card("BP 完成", "可試算選角評分")
    side, phase = seq[len(log)]
    side_count = len(_side_actions(state, side, phase)) + 1
    color = "text-blue" if side == "blue" else "text-red"
    return html.Div([
        html.H3(f'第 {len(log) + 1} / {len(seq)} 步：'
                f'{"藍" if side == "blue" else "紅"}方 '
                f'{_phase_zh(phase)} #{side_count}',
                className=color, style={"margin": 0}),
    ])


@callback(
    Output("mb-state", "data"),
    Output("mb-roster", "data"),
    Output("mb-meta", "data"),
    Input("mb-start", "n_clicks"),
    State("mb-team-blue", "value"),
    State("mb-team-red", "value"),
    State("mb-firstpick", "value"),
    State("global-filter", "data"),
    prevent_initial_call=True,
)
def _start(n, team_blue, team_red, first_side, data):
    if not team_blue or not team_red:
        return dash.no_update, dash.no_update, dash.no_update
    f = common.make_filter(data)
    roster = {}
    for side, team in (("blue", team_blue), ("red", team_red)):
        raw = data_access.team_roster(team, f)
        roster[side] = {
            pos: {"player": info["player"],
                  # NaN 轉 None，避免 dcc.Store 序列化出非法 JSON token
                  "pool": info["pool"].head(8).where(
                      info["pool"].notna(), None).to_dict("records")}
            for pos, info in raw.items() if pos in POSITIONS
        }
    stats = data_access.champion_stats(f, 5)
    meta = stats[["champion", "bp_rate", "ban_rate", "kda"]].where(
        stats[["champion", "bp_rate", "ban_rate", "kda"]].notna(),
        None).to_dict("records")
    return ({"started": True, "first": first_side,
             "teams": {"blue": team_blue, "red": team_red}, "log": []},
            roster, {"rows": meta})


@callback(
    Output("mb-state", "data", allow_duplicate=True),
    Input({"type": "mb-pool", "side": ALL, "pos": ALL, "champ": ALL},
          "n_clicks"),
    Input("mb-manual-btn", "n_clicks"),
    State("mb-state", "data"),
    State("mb-manual-champ", "value"),
    State("mb-manual-pos", "value"),
    prevent_initial_call=True,
)
def _assign(_clicks, _manual_n, state, manual_champ, manual_pos):
    """英雄池頭像或手動指定 → 填入當前步驟並自動推進。"""
    if not state or not state.get("started"):
        return dash.no_update
    log = state.get("log", [])
    seq = _bp_sequence(state.get("first", "blue"))
    if len(log) >= len(seq):
        return dash.no_update
    side, phase = seq[len(log)]
    # 動態掛載的 pattern 按鈕會以 n_clicks=None 誤發一次回調，需過濾
    trigger_value = ctx.triggered[0]["value"] if ctx.triggered else None
    if trigger_value is None:
        return dash.no_update
    triggered = ctx.triggered_id
    if isinstance(triggered, dict) and triggered.get("type") == "mb-pool":
        champ, click_side, pos = (triggered.get("champ"),
                                  triggered.get("side"),
                                  triggered.get("pos"))
        # 禁用建議條的按鈕帶有正確 side、pos 為空；英雄池按鈕須符合當前陣營
        if click_side and click_side != side:
            return dash.no_update
        if phase == "pick" and not pos:
            return dash.no_update
    else:
        if not manual_champ:
            return dash.no_update
        champ, pos = manual_champ, (manual_pos if phase == "pick" else "")
    if champ in _used_champs(state):
        return dash.no_update
    # 禁用步驟不帶位置；選將步驟才記錄英雄對應位置
    log_pos = pos if phase == "pick" else ""
    state = {**state, "log": log + [
        {"step": len(log) + 1, "side": side, "phase": phase,
         "champ": champ, "pos": log_pos}]}
    return state


@callback(
    Output("mb-state", "data", allow_duplicate=True),
    Input("mb-undo", "n_clicks"),
    State("mb-state", "data"),
    prevent_initial_call=True,
)
def _undo(_n, state):
    if not state.get("log"):
        return dash.no_update
    return {**state, "log": state["log"][:-1]}


@callback(
    Output("mb-state", "data", allow_duplicate=True),
    Output("mb-result", "children", allow_duplicate=True),
    Input("mb-reset", "n_clicks"),
    State("mb-state", "data"),
    prevent_initial_call=True,
)
def _reset(_n, state):
    if not state.get("started"):
        return dash.no_update, dash.no_update
    return {**state, "log": []}, None


@callback(
    Output("mb-boards", "children"),
    Output("mb-step", "children"),
    Output("mb-pools", "children"),
    Output("mb-result", "children"),
    Input("mb-state", "data"),
    State("mb-roster", "data"),
    State("mb-meta", "data"),
    State("global-filter", "data"),
)
def _render(state, roster, meta_bundle, data):
    if not state or not state.get("started"):
        return None, None, None, None
    boards = html.Div([
        _board("blue", state["teams"]["blue"], state),
        _board("red", state["teams"]["red"], state),
    ], className="bp-board")
    meta = {r["champion"]: r for r in meta_bundle.get("rows", [])}
    pools = html.Div(_roster_panels(roster, meta, state), className="grid-2")
    log = state["log"]
    seq = _bp_sequence(state.get("first", "blue"))
    step = seq[len(log)] if len(log) < len(seq) else None
    suggestion = _ban_suggestions(meta_bundle.get("rows", []),
                                  _used_champs(state), step)
    pools = html.Div([pools, suggestion])
    # 全部 20 步完成即自動試算
    result = _try_evaluate(state, data) if len(log) >= len(seq) else None
    return boards, _step_banner(state), pools, result


def _try_evaluate(state: dict, data) -> html.Div:
    """將步驟日誌組成 evaluate 所需的 picks/bans 並試算。"""
    team_a = state["teams"]["blue"]
    team_b = state["teams"]["red"]
    picks = {team_a: {}, team_b: {}}
    bans = {team_a: [], team_b: []}
    for side, team in (("blue", team_a), ("red", team_b)):
        for a in _side_actions(state, side, "pick"):
            if a.get("pos"):
                picks[team][a["pos"]] = a["champ"]
        bans[team] = [a["champ"] for a in _side_actions(state, side, "ban")]
    f = common.make_filter(data)
    try:
        result = evaluate(team_a, team_b, picks, bans, f)
    except Exception as exc:  # noqa: BLE001
        return common.empty_state("試算失敗", str(exc))
    return html.Div([
        _score_card(team_a, result[team_a], "blue"),
        _score_card(team_b, result[team_b], "red"),
    ], className="bp-board")


def _score_card(team: str, score: dict, side: str) -> html.Div:
    comps = score["components"]
    labels = list(COMPONENT_ZH.keys())
    fig = go.Figure(go.Bar(
        x=[comps[k] for k in labels],
        y=[COMPONENT_ZH[k] for k in labels],
        orientation="h",
        marker_color=["#4C8DFF" if v >= 50 else "#F05A6A" for v in
                      [comps[k] for k in labels]],
        text=[f"{comps[k]}" for k in labels],
    ))
    fig.update_layout(template=PLOTLY_TEMPLATE, height=300,
                      margin=dict(l=80, r=30, t=10, b=30),
                      xaxis=dict(range=[0, 100]))
    return html.Div([
        html.H3([team, f'　總分 {score["total"]}'],
                className=f"text-{side}"),
        dcc.Graph(figure=fig),
    ], className="panel")
