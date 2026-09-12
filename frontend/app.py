"""Plotly Dash 主程式：11 頁導航、全域篩選、本機英雄頭像路由。

啟動：python frontend/app.py（由專案根目錄）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import dash
from dash import dcc, html
from flask import send_from_directory

# 確保可匯入 backend / frontend 套件
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend import config, data_access  # noqa: E402
from frontend.components.common import POSITION_ZH  # noqa: E402

app = dash.Dash(
    __name__,
    use_pages=True,
    pages_folder="pages",
    suppress_callback_exceptions=True,
    title="LOL 數據表",
    update_title=None,
)
server = app.server


# ---------------------------------------------------------------------------
# 本機英雄頭像路由（NFR-7：不依賴瀏覽器外網）
# ---------------------------------------------------------------------------
@server.route("/champ-img/<path:filename>")
def serve_champ_icon(filename: str):
    return send_from_directory(config.CHAMP_ICON_DIR, filename)


# ---------------------------------------------------------------------------
# 篩選選項（倉儲不存在時降級為空選項）
# ---------------------------------------------------------------------------
try:
    OPTIONS = data_access.get_options()
except Exception as exc:  # noqa: BLE001
    OPTIONS = {"leagues": [], "years": [], "patches": [], "teams": [],
               "date_min": None, "date_max": None}
    print(f"[警告] 無法讀取倉儲選項：{exc}")

try:
    _latest = json.loads(config.LATEST_JSON.read_text(encoding="utf-8"))
    DATA_TIME = _latest.get("generated_at", "—")
except Exception:  # noqa: BLE001
    DATA_TIME = "未產生（請先執行管線）"

_DEFAULT_YEAR = [config.CURRENT_YEAR] if config.CURRENT_YEAR in OPTIONS["years"] \
    else (OPTIONS["years"][-1:] if OPTIONS["years"] else [])


def _dropdown(idx: str, label: str, options, multi: bool = True,
              value=None) -> html.Div:
    return html.Div([
        html.Label(label),
        dcc.Dropdown(
            id=idx, options=[{"label": str(o), "value": o} for o in options],
            value=value, multi=multi, clearable=True,
            style={"color": "#0B1020", "fontSize": 12, "minWidth": 110},
        ),
    ], className="filter-item")


app.layout = html.Div([
    html.Header([
        html.Div([html.Span("◆", className="dot"), "LOL 數據表"],
                 className="app-logo"),
        html.Span(f"資料時間 {DATA_TIME}", className="data-time"),
    ], className="app-header"),
    html.Nav([
        dcc.Link(page["title"], href=page["path"],
                 className="nav-link", id=f"nav-{page['name']}")
        for page in dash.page_registry.values()
    ], className="nav-pills"),
    html.Div([
        _dropdown("f-league", "賽事", OPTIONS["leagues"]),
        _dropdown("f-year", "年份", OPTIONS["years"], value=_DEFAULT_YEAR),
        _dropdown("f-patch", "版本", OPTIONS["patches"]),
        html.Div([
            html.Label("日期"),
            dcc.DatePickerRange(
                id="f-date", minimum_nights=0, clearable=True,
                display_format="YYYY-MM-DD",
                start_date=OPTIONS["date_min"], end_date=OPTIONS["date_max"],
                style={"fontSize": 11},
            ),
        ], className="filter-item"),
        _dropdown("f-position", "位置",
                  [{"label": v, "value": k} for k, v in POSITION_ZH.items()]),
        html.Div([
            html.Label("最少場數"),
            dcc.Input(id="f-min-games", type="number", min=0, value=0,
                      style={"width": 70, "background": "#0E1526",
                             "color": "#E6ECF5", "border": "1px solid #22304A",
                             "borderRadius": 6, "padding": 6}),
        ], className="filter-item"),
    ], className="filter-bar"),
    dcc.Store(id="global-filter", data={"years": _DEFAULT_YEAR}),
    dash.page_container,
])


# 篩選控制項 → 全域 Store
@app.callback(
    dash.Output("global-filter", "data"),
    dash.Input("f-league", "value"),
    dash.Input("f-year", "value"),
    dash.Input("f-patch", "value"),
    dash.Input("f-date", "start_date"),
    dash.Input("f-date", "end_date"),
    dash.Input("f-position", "value"),
    dash.Input("f-min-games", "value"),
    prevent_initial_call=False,
)
def _sync_filter(leagues, years, patches, date_start, date_end,
                 positions, min_games):
    return {
        "leagues": leagues or [],
        "years": years or [],
        "patches": patches or [],
        "date_start": date_start,
        "date_end": date_end,
        "positions": positions or [],
        "min_games": int(min_games or 0),
    }


if __name__ == "__main__":
    app.run(host=config.DASH_HOST, port=config.DASH_PORT, debug=False)
