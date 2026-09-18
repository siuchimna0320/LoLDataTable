"""積分頁：職業選手單雙排積分榜（資料來源 dpm.lol）。

架構比照 BP 頁：server callback 只把精簡 JSON 放進 dcc.Store，
整張大表（數百列、數千張英雄圖）由 clientside callback 以單次
innerHTML 產出，避免 Dash 大型元件樹導致頁面凍結。
"""
from __future__ import annotations

import logging

import dash
from dash import Input, Output, clientside_callback, dcc, html

from backend import dpm_queries, dpm_runner

logger = logging.getLogger(__name__)

dash.register_page(__name__, path="/ladder", name="積分", title="積分",
                   order=10)

# 路線與賽區篩選（圖2 膠囊列）
_POS_PILLS = [("all", "全部"), ("top", "上路"), ("jng", "打野"),
              ("mid", "中路"), ("bot", "下路"), ("sup", "輔助")]
_REGION_PILLS = [("all", "全部"), ("LCK", "LCK"), ("LPL", "LPL"),
                 ("LCP", "LCP"), ("LEC", "LEC"), ("LCS", "LCS"),
                 ("CBLOL", "CBLOL")]
# 國際盃賽本期未做，保留灰色膠囊
_CUP_PILLS = ["FST", "MSI", "EWC"]

# 一週評分 tooltip 計算方式說明
_WEEK_TIP = ("一週評分＝該帳號最近 7 天單雙排每場 DPM.LOL 評分（dpmScore）"
             "的算術平均；括號為 7 天內勝－敗場數。一週 KDA 為同期 (K+A)/D。")

# 每日戰況日期子分頁（昨天＝KST 前一日，近3天／近7天含今天）
_DAY_PILLS = [("y", "昨天"), ("d3", "近3天"), ("d7", "近7天")]


def _pill(value: str, label: str, group: str) -> html.Span:
    """單顆篩選膠囊；active 由前端狀態切換。"""
    cls = "dpm-pill" + (" active" if value == "all" else "")
    return html.Span(label, className=cls, **{f"data-{group}": value})


def _day_pill(value: str, label: str) -> html.Span:
    """每日戰況的日期分頁膠囊。"""
    cls = "dpm-pill dpm-day-pill" + (" active" if value == "y" else "")
    return html.Span(label, className=cls, **{"data-days": value})


def layout():
    return html.Div([
        dcc.Store(id="dpm-ladder-data"),
        dcc.Store(id="dpm-daily-data"),
        dcc.Store(id="dpm-scrape-status"),
        dcc.Interval(id="dpm-scrape-tick", interval=4000, disabled=True),

        # 圖2：排行榜／每日戰況分頁＋路線／賽區膠囊＋手動 SCRAPE
        html.Div(className="dpm-bar", children=[
            html.Div(className="dpm-tabs", children=[
                html.Span("🏆 排行榜", className="dpm-tab active",
                          **{"data-tab": "ladder"}),
                html.Span("📋 每日戰況", className="dpm-tab",
                          **{"data-tab": "daily"}),
            ]),
            html.Span("路線", className="dpm-bar-label"),
            html.Div(className="dpm-pill-group", children=[
                _pill(v, t, "pos") for v, t in _POS_PILLS]),
            html.Span("賽區", className="dpm-bar-label"),
            html.Div(className="dpm-pill-group", children=[
                _pill(v, t, "region") for v, t in _REGION_PILLS]),
            html.Span("｜", className="dpm-cup-divider"),
            html.Div(className="dpm-pill-group", children=[
                html.Span(c, className="dpm-pill dpm-cup is-disabled",
                          title="國際盃賽視角開發中") for c in _CUP_PILLS]),
            html.Div(className="dpm-bar-spacer"),
            html.Button("⟳ SCRAPE", id="dpm-scrape-btn",
                        className="dpm-scrape-btn", n_clicks=0,
                        title="立即重新爬取 dpm.lol 全部伺服器"),
            html.Span(id="dpm-scrape-state", className="dpm-scrape-state"),
        ]),

        # 排行榜副標
        html.Div(id="dpm-sub-ladder", className="dpm-sub", children=[
            html.Span("🏆 積分　單雙排積分，高到低｜點欄位名排序・逆場・評分・"
                      "角色池資料來源 "),
            html.A("dpm.lol", href="https://dpm.lol", target="_blank",
                   rel="noopener noreferrer"),
        ]),

        # 每日戰況副標＋日期子分頁（預設隱藏，切 tab 時由 JS 顯示）
        html.Div(id="dpm-sub-daily", className="dpm-sub dpm-sub-daily",
                 style={"display": "none"}, children=[
            html.Span("📋 各隊每日戰況　各隊選手單雙排逐日戰況（勝藍・敗紅）"),
            html.Div(className="dpm-day-tabs", children=[
                _day_pill(v, t) for v, t in _DAY_PILLS]),
        ]),
        html.Div(id="dpm-daily-info", className="dpm-sub dpm-daily-info",
                 style={"display": "none"}),

        html.Div(id="dpm-ladder-body", className="dpm-ladder-host",
                 **{"data-tip": _WEEK_TIP}),
    ], className="page dpm-page")


# ---------------------------------------------------------------------------
# Server callbacks：載入積分榜 JSON、啟動與輪詢背景爬取
# ---------------------------------------------------------------------------
def _ladder_payload() -> dict:
    """查整榜列資料；查詢失敗回空列與錯誤訊息，不讓頁面崩壞。"""
    try:
        return {"rows": dpm_queries.ladder_rows()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("積分榜查詢失敗：%s", exc)
        return {"rows": [], "error": str(exc)}


def _daily_payload_safe() -> dict:
    """查每日戰況資料；失敗回空結構，頁面仍可切換查看。"""
    try:
        return dpm_queries.daily_payload()
    except Exception as exc:  # noqa: BLE001
        logger.warning("每日戰況查詢失敗：%s", exc)
        return {"bounds": None, "teams": [], "error": str(exc)}


@dash.callback(
    Output("dpm-ladder-data", "data"),
    Input("url-location", "pathname"),
)
def _load_ladder_on_nav(pathname):
    """進入積分頁（含瀏覽器前後導航）時載入榜單。"""
    if not pathname or not pathname.startswith("/ladder"):
        raise dash.exceptions.PreventUpdate
    return _ladder_payload()


@dash.callback(
    Output("dpm-daily-data", "data"),
    Input("url-location", "pathname"),
)
def _load_daily_on_nav(pathname):
    """進入積分頁時一併備好每日戰況（切 tab 零等待）。"""
    if not pathname or not pathname.startswith("/ladder"):
        raise dash.exceptions.PreventUpdate
    return _daily_payload_safe()


@dash.callback(
    Output("dpm-ladder-data", "data", allow_duplicate=True),
    Input("dpm-scrape-status", "data"),
    prevent_initial_call=True,
)
def _reload_after_scrape(status_data):
    """爬取完成（stage=done）時自動重取榜單；運行中不刷新。"""
    if not status_data or status_data.get("stage") != "done":
        raise dash.exceptions.PreventUpdate
    return _ladder_payload()


@dash.callback(
    Output("dpm-daily-data", "data", allow_duplicate=True),
    Input("dpm-scrape-status", "data"),
    prevent_initial_call=True,
)
def _reload_daily_after_scrape(status_data):
    """爬取完成時一併重取每日戰況。"""
    if not status_data or status_data.get("stage") != "done":
        raise dash.exceptions.PreventUpdate
    return _daily_payload_safe()


@dash.callback(
    Output("dpm-scrape-status", "data", allow_duplicate=True),
    Output("dpm-scrape-tick", "disabled", allow_duplicate=True),
    Input("url-location", "pathname"),
    prevent_initial_call="initial_duplicate",
)
def _init_scrape_status(pathname):
    """進入積分頁時帶出當前狀態（含 cron／其他行程啟動的爬取）。"""
    if not pathname or not pathname.startswith("/ladder"):
        raise dash.exceptions.PreventUpdate
    status = dpm_runner.status()
    return status, not status.get("thread_alive")


@dash.callback(
    Output("dpm-scrape-status", "data"),
    Output("dpm-scrape-tick", "disabled"),
    Input("dpm-scrape-btn", "n_clicks"),
    prevent_initial_call=True,
)
def _start_scrape(n_clicks):
    """右上角手動 SCRAPE：背景執行緒啟動，不封鎖頁面。"""
    dpm_runner.start_scrape()
    status = dpm_runner.status()
    return status, not status.get("thread_alive")


@dash.callback(
    Output("dpm-scrape-status", "data", allow_duplicate=True),
    Output("dpm-scrape-tick", "disabled", allow_duplicate=True),
    Input("dpm-scrape-tick", "n_intervals"),
    prevent_initial_call=True,
)
def _poll_scrape(_n):
    """每 4 秒輪詢；執行緒結束後自動停止 interval。"""
    status = dpm_runner.status()
    return status, not status.get("thread_alive")


# ---------------------------------------------------------------------------
# Clientside：整張積分榜 innerHTML 渲染（見 assets/dpm_ladder.js）
# ---------------------------------------------------------------------------
clientside_callback(
    "function (data) { return window.dpmLadder.render(data); }",
    Output("dpm-ladder-body", "data-rendered"),
    Input("dpm-ladder-data", "data"),
)

clientside_callback(
    "function (data) { return window.dpmLadder.render(data); }",
    Output("dpm-daily-data", "data-loaded"),
    Input("dpm-daily-data", "data"),
)

clientside_callback(
    "function (s) { return window.dpmLadder.renderStatus(s); }",
    Output("dpm-scrape-state", "children"),
    Input("dpm-scrape-status", "data"),
)
