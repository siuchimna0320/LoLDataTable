"""選手積分詳情頁（dpm.lol）：圖4 隊友列、圖5 帳號列、圖3 逐場明細。

路由 /ladder/p/<displayName>。整個頁面同樣由 clientside 以 innerHTML
渲染（見 assets/dpm_detail.js），server 端只提供單包 JSON。
"""
from __future__ import annotations

import logging
from urllib.parse import unquote

import dash
from dash import Input, Output, clientside_callback, dcc, html, no_update

from backend import dpm_queries

logger = logging.getLogger(__name__)

dash.register_page(
    __name__,
    path_template="/ladder/p/<name>",
    name="選手積分", title="選手積分", order=11)


def layout(name: str | None = None, **_kwargs):
    return html.Div([
        dcc.Store(id="dpm-detail-data"),
        # 圖4：戰隊 LOGO ＋ 同隊選手 chips
        html.Div(id="dpm-mates-bar", className="dpm-detail-bar"),
        # 圖5：路線＋名稱＋合併帳號＋篩選 chips
        html.Div(id="dpm-info-host"),
        # 圖3：單雙積分逐場表格
        html.Div(id="dpm-match-host", className="dpm-match-host"),
    ], className="page dpm-page", **{"data-pname": name or ""})


@dash.callback(
    Output("dpm-detail-data", "data"),
    Input("url-location", "pathname"),
)
def _load_detail(pathname):
    """依網址最後一段取選手檔＋合併帳號＋逐場明細。"""
    if not pathname or "/ladder/p/" not in pathname:
        return no_update
    raw_name = pathname.split("/ladder/p/", 1)[1].split("?", 1)[0]
    name = unquote(raw_name)
    try:
        detail = dpm_queries.pro_detail(name)
        if not detail:
            return {"found": False, "name": name}
        detail["found"] = True
        detail["matches"] = dpm_queries.pro_matches(name, limit=200)
        return detail
    except Exception as exc:  # noqa: BLE001
        logger.warning("選手詳情查詢失敗（%s）：%s", name, exc)
        return {"found": False, "name": name, "error": str(exc)}


clientside_callback(
    "function (data) { return window.dpmDetail.render(data); }",
    Output("dpm-match-host", "data-rendered"),
    Input("dpm-detail-data", "data"),
)
