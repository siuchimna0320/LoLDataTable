"""Plotly Dash 主程式：12 頁導航、全域篩選、本機資源圖示路由。

啟動：python frontend/app.py（由專案根目錄）
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import dash
from dash import dcc, html
from flask import send_from_directory

# 確保可匯入 backend / frontend 套件
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend import config, data_access, pipeline_runner  # noqa: E402
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
# 檔名以英雄／資源名稱為鍵，內容不變，給一年不可變快取，
# 避免大表格（如 BP 棋盤數千格）每次互動都重新下載圖片而凍結頁面
# ---------------------------------------------------------------------------
def _send_cached(directory, filename):
    """靜態資源回應並加上長快取標頭。"""
    resp = send_from_directory(directory, filename)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@server.route("/champ-img/<path:filename>")
def serve_champ_icon(filename: str):
    return _send_cached(config.CHAMP_ICON_DIR, filename)


# 歷代英雄頭像（英雄詳情頁，舊版 DDragon 方形圖本地快取）
@server.route("/champ-era/<path:filename>")
def serve_champ_era(filename: str):
    return _send_cached(config.CHAMP_ERA_DIR, filename)


# ---------------------------------------------------------------------------
# Data Dragon 圖鑑圖示路由（伺服器端按需代理並快取，瀏覽器不直連外網）
# ---------------------------------------------------------------------------
@server.route("/dd-img/<group>/<path:filename>")
def serve_dd_icon(group: str, filename: str):
    from flask import abort
    from backend import ddragon_data

    path = ddragon_data.icon_path(group, filename)
    if path is None:
        abort(404)
    return _send_cached(path.parent, path.name)


@server.route("/pos-img/<code>.svg")
def serve_position_icon(code: str):
    """五路位置 SVG 圖示（top/jng/mid/bot/sup）。"""
    from flask import abort
    from backend import ddragon_data

    path = ddragon_data.position_icon_path(code)
    if path is None:
        abort(404)
    return _send_cached(path.parent, path.name)


# ---------------------------------------------------------------------------
# DPM.LOL 資源代理（階級徽章、職業戰隊隊徽）：首次按需下載並本地常駐快取
# ---------------------------------------------------------------------------
@server.route("/dpm-rank/<tier>.webp")
def serve_dpm_rank(tier: str):
    from flask import abort
    from backend import dpm_assets

    path = dpm_assets.rank_path(tier.upper())
    if path is None:
        abort(404)
    return _send_cached(path.parent, path.name)


@server.route("/dpm-team/<path:code>.webp")
def serve_dpm_team(code: str):
    from flask import abort
    from backend import dpm_assets

    path = dpm_assets.team_path(code)
    if path is None:
        abort(404)
    return _send_cached(path.parent, path.name)


# ---------------------------------------------------------------------------
# 戰隊縮寫徽章：OE 無戰隊圖標資源，以隊名決定色＋縮寫生成本地 SVG
# ---------------------------------------------------------------------------
_TEAM_BADGE_CACHE: dict[str, str] = {}


@server.route("/team-badge/<path:name>.svg")
def serve_team_badge(name: str):
    from flask import Response

    svg = _TEAM_BADGE_CACHE.get(name)
    if svg is None:
        # 取各單字首字（最多 3 碼），無空白時取前 2 字元
        words = [w for w in name.replace(".", " ").split() if w]
        initials = "".join(w[0] for w in words[:3]).upper()
        if len(initials) < 2:
            initials = name[:3].upper()
        # 隊名雜湊決定色相（飽和/亮度固定以維持深底可讀性）
        hue = sum(ord(ch) * (i + 7) for i, ch in enumerate(name)) % 360
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="34" height="34" '
            f'viewBox="0 0 34 34">'
            f'<rect width="34" height="34" rx="7" '
            f'fill="hsl({hue},42%,24%)" stroke="hsl({hue},60%,62%)"/>'
            f'<text x="17" y="22" text-anchor="middle" font-family="Arial" '
            f'font-size="{11 if len(initials) > 2 else 13}" font-weight="700" '
            f'fill="hsl({hue},75%,82%)">{initials[:3]}</text></svg>')
        _TEAM_BADGE_CACHE[name] = svg
    resp = Response(svg, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


# ---------------------------------------------------------------------------
# 篩選選項（倉儲不存在時降級為空選項）
# ---------------------------------------------------------------------------
try:
    OPTIONS = data_access.get_options()
except Exception as exc:  # noqa: BLE001
    OPTIONS = {"leagues": [], "years": [], "patches": [], "teams": [],
               "date_min": None, "date_max": None}
    print(f"[警告] 無法讀取倉儲選項：{exc}")

def _fmt_iso(ts: str | None) -> str:
    """ISO 字串 2026-09-17T00:34:14 → 2026-09-17 00:34；異常值原樣回傳。"""
    if not ts:
        return "—"
    return str(ts).replace("T", " ")[:16]


try:
    _latest = json.loads(config.LATEST_JSON.read_text(encoding="utf-8"))
    DATA_TIME = _fmt_iso(_latest.get("generated_at"))
except Exception:  # noqa: BLE001
    DATA_TIME = "未產生（請先執行管線）"

# 賽季選項已倒序（最新在首）；當前年度不在清單時退回第一個（最新）賽季
_DEFAULT_YEAR = [config.CURRENT_YEAR] if config.CURRENT_YEAR in OPTIONS["years"] \
    else (OPTIONS["years"][:1] if OPTIONS["years"] else [])


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


def _text_filter(idx: str, label: str, placeholder: str,
                 width: int = 130) -> html.Div:
    """深色文字篩選框（英雄／戰隊選手）。"""
    return html.Div([
        html.Label(label),
        dcc.Input(id=idx, type="text", placeholder=placeholder,
                  style={"width": width, "background": "#0E1526",
                         "color": "#E6ECF5", "border": "1px solid #22304A",
                         "borderRadius": 6, "padding": 6, "fontSize": 12}),
    ], className="filter-item")


app.layout = html.Div(id="app-shell", children=[
    html.Header([
        html.Div([html.Span("◆", className="dot"), "LOL 數據表"],
                 className="app-logo"),
        html.Div(className="oracle-head-right", children=[
            html.Span(f"資料時間 {DATA_TIME}", id="oracle-data-time",
                      className="data-time"),
            html.Span(id="oracle-update-state",
                      className="oracle-update-state"),
            html.Button("⟳ 更新數據", id="oracle-update-btn",
                        className="oracle-update-btn", n_clicks=0),
        ]),
    ], className="app-header"),
    html.Nav([
        dcc.Link(page["title"], href=page["path"],
                 className="nav-link", id=f"nav-{page['name']}")
        # 排除動態路由（如 /ladder/p/<name>）與已被 dpm 積分頁取代的積分榜
        for page in dash.page_registry.values()
        if not page.get("path_template") and page["path"] != "/standings"
    ], className="nav-pills"),
    # 全站搜尋列順序統一：賽事、賽季、版本、時間、場數、路線、英雄、戰隊選手
    html.Div([
        _dropdown("f-league", "賽事", OPTIONS["leagues"]),
        _dropdown("f-year", "賽季", OPTIONS["years"], value=_DEFAULT_YEAR),
        _dropdown("f-patch", "版本", OPTIONS["patches"]),
        html.Div([
            html.Label("時間"),
            dcc.DatePickerRange(
                id="f-date", minimum_nights=0, clearable=True,
                display_format="YYYY-MM-DD",
                start_date=OPTIONS["date_min"], end_date=OPTIONS["date_max"],
                style={"fontSize": 11},
            ),
        ], className="filter-item"),
        html.Div([
            html.Label("場數"),
            dcc.Input(id="f-min-games", type="number", min=0, value=0,
                      style={"width": 70, "background": "#0E1526",
                             "color": "#E6ECF5", "border": "1px solid #22304A",
                             "borderRadius": 6, "padding": 6}),
        ], className="filter-item"),
        _dropdown("f-position", "路線",
                  [{"label": v, "value": k} for k, v in POSITION_ZH.items()]),
        _text_filter("f-champion", "英雄", "輸入英雄"),
        _text_filter("f-member", "戰隊選手", "輸入戰隊或選手", width=150),
    ], id="global-filter-bar", className="filter-bar"),
    dcc.Location(id="url-location", refresh=False),
    dcc.Store(id="global-filter", data={"years": _DEFAULT_YEAR}),
    # Oracle 管線手動更新狀態（按鈕輪詢，與 cron 共用同一子進程入口）
    dcc.Store(id="oracle-update-status"),
    dcc.Interval(id="oracle-update-tick", interval=5000, disabled=True),
    dash.page_container,
])


# 英雄頁／戰隊頁／選手頁／陣容頁已有專屬頁內篩選列（與全站相同的八個欄位），
# 進入這些頁面時隱藏頂部全域篩選列，避免兩排重複；元件保留於 DOM 維持 callback。
app.clientside_callback(
    """
    function (pathname) {
        var pages = ["/champions", "/teams", "/players", "/roster",
                     "/ladder", "/compendium"];
        var hidden = pathname && pages.some(function (p) {
            return pathname.indexOf(p) === 0;
        });
        return {"display": hidden ? "none" : "flex"};
    }
    """,
    dash.Output("global-filter-bar", "style"),
    dash.Input("url-location", "pathname"),
)


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
    dash.Input("f-champion", "value"),
    dash.Input("f-member", "value"),
    prevent_initial_call=False,
)
def _sync_filter(leagues, years, patches, date_start, date_end,
                 positions, min_games, champion, member):
    return {
        "leagues": leagues or [],
        "years": years or [],
        "patches": patches or [],
        "date_start": date_start,
        "date_end": date_end,
        "positions": positions or [],
        "min_games": int(min_games or 0),
        "champion": (champion or "").strip(),
        "member": (member or "").strip(),
    }


# ---------------------------------------------------------------------------
# Oracle's Elixir 比賽資料手動更新（頁首右上角「⟳ 更新數據」）
# 背景子進程跑完整管線，每 5 秒輪詢；與日後 cron 為同一入口。
# ---------------------------------------------------------------------------
def _fmt_ts(epoch) -> str:
    """epoch 秒 → MM-DD HH:MM；無效值回空字串。"""
    try:
        return datetime.fromtimestamp(float(epoch)).strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return ""


def _oracle_state_text(s: dict) -> str:
    """狀態列只給簡短結果；時間由左側「資料時間」承擔，不在此重複。

    唯獨失敗（資料時間不會更新）保留嘗試時間，利於排查。
    """
    if not s:
        return ""
    if s.get("running"):
        started = _fmt_ts(s.get("started_at"))
        return f"更新中…（{started} 開始）" if started else "更新中…"
    stage = s.get("stage")
    if stage == "done":
        return "更新完成"
    if stage == "degraded":
        return "部分下載失敗，詳見 pipeline.log"
    if stage == "failed":
        when = _fmt_ts(s.get("finished_at"))
        return f"更新失敗（{when}）：{s.get('error') or '未知錯誤'}"
    return ""


@app.callback(
    dash.Output("oracle-update-status", "data"),
    dash.Output("oracle-update-tick", "disabled"),
    dash.Input("url-location", "pathname"),
)
def _init_oracle_status(_pathname):
    """進站即帶出當前狀態（cron 或其他行程可能正在更新）。"""
    status = pipeline_runner.status()
    return status, not status.get("running")


@app.callback(
    dash.Output("oracle-update-status", "data", allow_duplicate=True),
    dash.Output("oracle-update-tick", "disabled", allow_duplicate=True),
    dash.Input("oracle-update-btn", "n_clicks"),
    prevent_initial_call=True,
)
def _start_oracle_update(_n_clicks):
    """按鈕啟動背景更新；已在執行中則重複點擊不生效。"""
    pipeline_runner.start_update()
    status = pipeline_runner.status()
    return status, not status.get("running")


@app.callback(
    dash.Output("oracle-update-status", "data", allow_duplicate=True),
    dash.Output("oracle-update-tick", "disabled", allow_duplicate=True),
    dash.Input("oracle-update-tick", "n_intervals"),
    prevent_initial_call=True,
)
def _poll_oracle_update(_n):
    """每 5 秒輪詢；行程結束後自動停止 interval。"""
    status = pipeline_runner.status()
    return status, not status.get("running")


@app.callback(
    dash.Output("oracle-update-state", "children"),
    dash.Output("oracle-update-btn", "disabled"),
    dash.Output("oracle-data-time", "children"),
    dash.Input("oracle-update-status", "data"),
)
def _render_oracle_state(status):
    """狀態 → 狀態文字、按鈕禁用、完成時刷新資料時間標示。"""
    running = bool(status and status.get("running"))
    data_time = dash.no_update
    if status and status.get("stage") in ("done", "degraded") \
            and status.get("data_time"):
        data_time = f"資料時間 {_fmt_iso(status['data_time'])}"
    return _oracle_state_text(status), running, data_time


# ---------------------------------------------------------------------------
# 全站圖片懶載入：Dash 的 html.Img 不支援 loading 參數，
# 改用 MutationObserver 對所有動態新增的 img 即時補上 loading="lazy"，
# 避免刷野頁一次發出數百個圖片請求拖慢首屏。
# ---------------------------------------------------------------------------
app.clientside_callback(
    """
    function (_children) {
        if (window.__lazyImgInstalled) return "1";
        window.__lazyImgInstalled = true;
        var markLazy = function (root) {
            (root.querySelectorAll ? root.querySelectorAll("img") : [])
                .forEach(function (img) {
                    img.setAttribute("loading", "lazy");
                });
        };
        markLazy(document);
        new MutationObserver(function (mutations) {
            mutations.forEach(function (m) {
                m.addedNodes.forEach(function (node) {
                    if (node.nodeType !== 1) return;
                    if (node.tagName === "IMG")
                        node.setAttribute("loading", "lazy");
                    markLazy(node);
                });
            });
        }).observe(document.body, {childList: true, subtree: true});
        return "1";
    }
    """,
    dash.Output("app-shell", "data-lazy-img"),
    dash.Input("app-shell", "children"),
)


if __name__ == "__main__":
    app.run(host=config.DASH_HOST, port=config.DASH_PORT, debug=False)
