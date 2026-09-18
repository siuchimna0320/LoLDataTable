"""比賽 BP 頁：系列賽分組的藍／紅方 BP 棋盤。

- 以系列賽分組（同日同隊伍對），最新在上，每列為一局：
  局數／時長／勝方／藍方（先選後選）／5 選角／5 禁用　[中間分隔]
  紅方 5 禁用／5 選角／紅方
- 頂部 KPI：總場數、缺 BP 場數、場均時長、藍紅勝率、先選比例、
  先選勝率、BP 完整率（皆依全站篩選即時重算）
- 欄名下方可逐欄輸入：藍方／紅方隊名、英雄（出現於選角或禁用）

效能說明：棋盤（每局 20 個頭像、60 局近 5000 個 DOM 節點）若以 Dash
元件樹回傳，Dash 4 renderer 在子樹整批取代時，會對每個元件掃描全部
layoutHashes（O(n²)），導致主線程凍結十餘秒。故後端只回精簡 JSON 至
dcc.Store，由 clientside callback 以單次 innerHTML 產出整個棋盤，
Dash 元件樹只增加一個 Store，完全避開該瓶頸。
"""
from __future__ import annotations

from urllib.parse import quote

import dash
from dash import (Input, Output, callback, clientside_callback, dcc, html)

from backend import data_access
from frontend.components import common

dash.register_page(__name__, title="比賽BP", order=7)

# 五位置（中間分隔欄圖示，左至右）
_POSITIONS = ["top", "jng", "mid", "bot", "sup"]
_RENDER_GAMES = 60  # 明細最多渲染場數（KPI 仍採全量）

_GOLD = "#F5B945"


# ---------------------------------------------------------------------------
# 傳往用戶端的精簡資料結構
# ---------------------------------------------------------------------------
def _pick_slot(pick: dict) -> list:
    """選角槽：[頭像 URL（無圖為 None）, 英雄名, 全局選角順位]。"""
    name = pick.get("champion")
    return [common.champ_url(name), name, pick["order"]]


def _ban_slot(name: str | None) -> list:
    """禁用槽：[頭像 URL（無圖為 None）, 英雄名]。"""
    return [common.champ_url(name), name]


def _game_payload(g: dict) -> dict:
    """單局精簡視圖（27 欄：局數／時長／勝／藍方／選擇／禁用1-5／路線1-5／紅方／選擇／禁用1-5／路線1-5）。

    picks 已由後端按 role 排序（top→jng→mid→bot→sup），禁用按 BP 先後順序（ban1→ban5）。
    """
    bans_b = [_ban_slot(x) for x in g["blue_bans"]]
    bans_r = [_ban_slot(x) for x in g["red_bans"]]
    picks_b = [_pick_slot(x) for x in g["blue_picks"]]
    picks_r = [_pick_slot(x) for x in g["red_picks"]]

    def _pad(items, n, default=None):
        """確保長度 = n，不夠補 default（無資料）。"""
        if len(items) >= n:
            return items[:n]
        return items + [default] * (n - len(items))

    bans_b, bans_r = _pad(bans_b, 5), _pad(bans_r, 5)
    picks_b, picks_r = _pad(picks_b, 5), _pad(picks_r, 5)

    return {
        "gid": g["gameid"], "no": g["game_no"], "len": g["length"],
        "bw": 1 if g["blue_win"] else 0,          # 藍方是否勝
        "win": g["winner_abbr"], "side": g["winner_side"],
        "ba": g["blue_abbr"], "ra": g["red_abbr"],
        "fk": 1 if g["fp_known"] else 0,
        "bfp": 1 if g["blue_fp"] else 0,
        # 禁用（BP 先後 ban1→ban5，無則為 None）
        "bb0": bans_b[0], "bb1": bans_b[1], "bb2": bans_b[2],
        "bb3": bans_b[3], "bb4": bans_b[4],
        "rb0": bans_r[0], "rb1": bans_r[1], "rb2": bans_r[2],
        "rb3": bans_r[3], "rb4": bans_r[4],
        # picks（按 role 排序：top→jng→mid→bot→sup；無則為 None）
        "bp0": picks_b[0], "bp1": picks_b[1], "bp2": picks_b[2],
        "bp3": picks_b[3], "bp4": picks_b[4],
        "rp0": picks_r[0], "rp1": picks_r[1], "rp2": picks_r[2],
        "rp3": picks_r[3], "rp4": picks_r[4],
    }


def _patch_txt(patch) -> str:
    try:
        return f"{float(patch):.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "—"


def _series_payload(s: dict) -> dict:
    """系列賽標頭＋各局精簡資料。"""
    return {
        "d": s["date"][5:], "patch": _patch_txt(s["patch"]),
        "lg": s["league"], "st": s["stage"],
        "wa": s["abbr_a"], "wb": s["abbr_b"],
        "sa": s["score_a"], "sb": s["score_b"], "win": s["winner"],
        "games": [_game_payload(g) for g in s["games"]],
    }


def _board_payload(data: dict) -> dict:
    """組成 bp-board-data Store 的完整 payload。"""
    if not data["stats"]["games"]:
        return {"series": [], "more": "",
                "empty_title": "篩選範圍內沒有比賽",
                "empty_desc": "請放寬賽事／賽季／版本或隊名、英雄關鍵字。"}
    more = ""
    if data["total_games"] > data["rendered_games"]:
        more = (f"僅顯示最新 {data['rendered_games']} 場（共 "
                f"{data['total_games']} 場），請用上方篩選縮小範圍。")
    return {"series": [_series_payload(x) for x in data["series"]],
            "more": more, "empty_title": "", "empty_desc": ""}


# ---------------------------------------------------------------------------
# 表頭與 KPI（少數元件，維持 Python 元件）
# ---------------------------------------------------------------------------
def _pos_icon_cell(pos: str) -> html.Img:
    """路線表頭的 position icon（SVG）。"""
    return html.Img(
        src=f"/pos-img/{quote(pos)}.svg",
        width=22, height=22,
        title=common.POSITION_ZH[pos],
        className="bp-pos-icon")


# 27 欄表頭欄名（含對應 payload 位置）
# label 可以是 str（文字）或 html.Img（position icon）；None 表示該格表頭留空
# input_kind: "kw"=真實 dcc.Input 關鍵字輸入, "empty"=虛線占位（逐欄篩選用）
_BP_COLS = [
    # (label_or_None, side_cls, input_kind)
    # -------- 藍方側 --------
    ("局數", None, "kw_col"),
    ("時長", None, "kw_col"),
    ("勝", None, "kw_col"),
    ("藍方", "bp-blue-side", "kw_col"),
    ("選擇", "bp-blue-side", "kw_col"),
    # 禁用 5 格：只在中間格顯示「禁用」兩字，其餘 4 格表頭留空
    (None, "bp-blue-side", "kw_col"),
    (None, "bp-blue-side", "kw_col"),
    ("禁用", "bp-blue-side", "kw_col"),
    (None, "bp-blue-side", "kw_col"),
    (None, "bp-blue-side", "kw_col"),
    # 路線 5 格：position icon 代替文字
    (_pos_icon_cell("top"), "bp-blue-side", "kw_col"),
    (_pos_icon_cell("jng"), "bp-blue-side", "kw_col"),
    (_pos_icon_cell("mid"), "bp-blue-side", "kw_col"),
    (_pos_icon_cell("bot"), "bp-blue-side", "kw_col"),
    (_pos_icon_cell("sup"), "bp-blue-side", "kw_col"),
    # -------- 紅方側（對稱） --------
    ("紅方", "bp-red-side", "kw_col"),
    ("選擇", "bp-red-side", "kw_col"),
    (None, "bp-red-side", "kw_col"),
    (None, "bp-red-side", "kw_col"),
    ("禁用", "bp-red-side", "kw_col"),
    (None, "bp-red-side", "kw_col"),
    (None, "bp-red-side", "kw_col"),
    (_pos_icon_cell("top"), "bp-red-side", "kw_col"),
    (_pos_icon_cell("jng"), "bp-red-side", "kw_col"),
    (_pos_icon_cell("mid"), "bp-red-side", "kw_col"),
    (_pos_icon_cell("bot"), "bp-red-side", "kw_col"),
    (_pos_icon_cell("sup"), "bp-red-side", "kw_col"),
]


def _col_input(kind: str, index: int):
    """逐欄篩選輸入元件（共用 helper）。

    - kind="kw_col" → 真實 dcc.Input，id 為 bp-col-{index}（clientside 收集到單一 Store）
    - kind="empty" → 虛線占位（舊式，本頁已不用）
    """
    if kind == "kw_col":
        return dcc.Input(
            id=f"bp-col-{index}", type="text", debounce=True,
            className="bp-col-input")
    return html.Span(className="bp-col-input bp-col-input-empty")


def _table_header() -> html.Div:
    """27 欄雙行表頭（列名在上、逐欄篩選輸入在下）。"""
    cells = []
    for i, (label, side_cls, kind) in enumerate(_BP_COLS):
        # label 可為 None / str / html.Img
        if label is None:
            label_html = html.Span(className="bp-col-label bp-col-label-empty")
        elif isinstance(label, str):
            label_html = html.Span(label, className="bp-col-name")
        else:
            # html.Img（position icon）
            label_html = label
        input_ = _col_input(kind, i)
        cell_cls = "bp-head-cell" + (f" {side_cls}-col" if side_cls else "")
        cells.append(html.Div([label_html, input_], className=cell_cls))
    return html.Div(cells, className="bp-row bp-head-row")


def _chip(label: str, value: str, sub: str = "",
          color: str | None = None) -> html.Span:
    return html.Span([
        label, " ",
        html.B(value, style={"color": color} if color else None),
        html.Span(sub, className="bp-kpi-sub") if sub else None,
    ], className="bp-kpi")


def _kpi_chips(s: dict) -> html.Div:
    n = s["games"]
    fp_txt = ("—" if s["fp_win_pct"] is None
              else f"{s['fp_win_pct']}%")
    return html.Div([
        _chip("共", f"{n} 場"),
        _chip("範圍內缺", f"{s['missing']} 場"),
        _chip("🕐 場均時長", s["avg_length"]),
        _chip("● 勝率", f"{s['blue_wr']}%/{s['red_wr']}%",
              f"（{s['blue_wins']}/{n}）"),
        _chip("先選比例",
              f"{s['fp_blue_pct']}%/{s['fp_red_pct']}%",
              f"（{s['fp_blue_n']}/{s['fp_blue_n'] + s['fp_red_n']}）"),
        _chip("先選勝率", fp_txt,
              f"（{s['fp_wins']}/{s['fp_den']}）" if n else ""),
        _chip("BP 完整率", f"{s['complete_pct']}%",
              f"（{s['complete_n']}/{n}）"),
    ], className="bp-kpi-bar")


def layout():
    # 表頭（含逐欄輸入）必須為靜態元件：callback 以這些輸入框為 Input，
    # 若由 callback 動態產生會因初始不存在而無法觸發
    return html.Div([
        html.Div([
            html.Div([
                html.Span("🎯 BP 紀錄", className="bp-title"),
                html.Span(id="bp-title-meta", className="bp-title-meta"),
                html.Span(
                    "金色數字＝選角順位（1–10）；淡色底＝該邊勝方；最新在上；"
                    "可在欄名下方輸入逐欄篩選。",
                    className="bp-title-hint"),
            ], className="bp-title-row"),
            html.Div(id="bp-kpi"),
            html.Div([
                _table_header(),
                dcc.Loading([
                    dcc.Store(id="bp-board-data"),
                    html.Div(id="bp-body", className="bp-scroll"),
                ], color=_GOLD, className="bp-loading",
                   parent_className="bp-loading-parent"),
            ], className="bp-table"),
        ], className="panel bp-panel"),
    ], className="page")


# ---------------------------------------------------------------------------
# 篩選 → KPI＋精簡 JSON（棋盤 DOM 全數交給用戶端生成）
# ---------------------------------------------------------------------------
# 27 欄逐欄篩選 Input：對應 _BP_COLS 的順序
_BP_COL_INPUTS = [Input(f"bp-col-{i}", "value") for i in range(27)]


@callback(
    Output("bp-title-meta", "children"),
    Output("bp-kpi", "children"),
    Output("bp-board-data", "data"),
    Input("global-filter", "data"),
    *_BP_COL_INPUTS,
)
def _render(flt, *col_values):
    """把 27 欄 input value 打包成 dict 傳後端做逐欄後過濾。"""
    # col_values 長度應 = 27；None / 空字串的欄位直接略過
    col_filters = {
        i: str(v).strip() for i, v in enumerate(col_values)
        if v and str(v).strip()
    }
    f = common.make_filter(flt)
    data = data_access.bp_board_data(
        f, render_games=_RENDER_GAMES, col_filters=col_filters)
    s = data["stats"]
    meta = f"{s['series_total']} 系列 · {s['games']} 場"
    return meta, _kpi_chips(s), _board_payload(data)


# 棋盤用戶端渲染：單次 innerHTML 輸出近 5000 節點（含原生 lazy 圖片），
# 不經 Dash/React 協調，避免 O(n²) 的元件樹更新凍結主線程
clientside_callback(
    """
    function (data) {
        if (!data) { return window.dash_clientside.no_update; }
        var host = document.getElementById('bp-body');
        if (!host) { return window.dash_clientside.no_update; }

        var BLUE = '#4C8DFF', RED = '#F05A6A';

        function esc(v) {
            return String(v == null ? '' : v).replace(
                /[&<>"']/g,
                function (c) {
                    return {'&': '&amp;', '<': '&lt;', '>': '&gt;',
                            '"': '&quot;', "'": '&#39;'}[c];
                });
        }

        // 槽位：[url, name, order?]；無資料回 null
        function icon(slot, size) {
            if (!slot) { return ''; }
            var url = slot[0], name = slot[1] == null ? '' : slot[1];
            if (url) {
                return '<img class="champ-icon" src="' + esc(url) + '" '
                    + 'width="' + size + '" height="' + size + '" '
                    + 'loading="lazy" decoding="async" title="'
                    + esc(name) + '">';
            }
            if (!name) { return ''; }
            // 無圖時顯示首 2 字縮寫，方便閱讀
            return '<span class="bp-empty-champ" title="' + esc(name) + '">'
                + esc(name.slice(0, 2)) + '</span>';
        }

        // 隊名 + 徽章（兩者同欄）
        function teamCell(abbr, fk, isFp) {
            var badge = fk
                ? '<span class="' + (isFp ? 'bp-fp' : 'bp-sp') + '">'
                  + (isFp ? '先選' : '後選') + '</span>'
                : '';
            return '<div class="bp-side-name"><b>' + esc(abbr) + '</b>'
                + badge + '</div>';
        }

        // 只有徽章的「選擇」欄
        function pickLabelCell(fk, isFp) {
            if (!fk) { return '<div class="bp-cell"></div>'; }
            return '<div class="bp-cell"><span class="'
                + (isFp ? 'bp-fp' : 'bp-sp') + '">'
                + (isFp ? '先選' : '後選') + '</span></div>';
        }

        // 單一禁用格
        function banCell(slot) {
            return '<div class="bp-cell bp-ban-cell bp-side-col">'
                + '<div class="bp-ban" title="' + esc(slot ? slot[1] : '')
                + '">' + icon(slot, 24) + '</div></div>';
        }

        // 單一 pick 格（含右上順位金數字）
        function pickCell(slot) {
            if (!slot) {
                return '<div class="bp-cell bp-pick-cell bp-side-col"></div>';
            }
            var order = slot[2];
            var orderHtml = order != null
                ? '<span class="bp-order">' + order + '</span>' : '';
            return '<div class="bp-cell bp-pick-cell bp-side-col">'
                + '<div class="bp-pick" title="' + esc(slot[1] || '')
                + '">' + icon(slot, 30) + orderHtml + '</div></div>';
        }

        function gameRow(g) {
            var winColor = g.side === 'blue' ? BLUE : RED;
            // 勝方半邊底色由 CSS 用 data-win 屬性自動上色
            var winSide = g.bw ? 'blue' : 'red';
            // 27 欄順序：局數/時長/勝/藍方/藍選/藍禁1-5/藍路1-5/
            //           紅方/紅選/紅禁1-5/紅路1-5
            var cols = [];
            // 1. 局數（美觀：G{n}）
            cols.push('<div class="bp-cell bp-gameno">G' + g.no + '</div>');
            // 2. 時長
            cols.push('<div class="bp-cell bp-length">' + esc(g.len) + '</div>');
            // 3. 勝
            cols.push('<div class="bp-cell bp-winner" style="color:'
                + winColor + '">' + esc(g.win) + '</div>');
            // 4. 藍方隊名
            cols.push('<div class="bp-cell bp-side bp-blue-side">'
                + '<b class="bp-team-abbr">' + esc(g.ba) + '</b></div>');
            // 5. 藍選擇（先選/後選徽章）
            cols.push(pickLabelCell(g.fk, g.bfp)
                .replace('bp-cell', 'bp-cell bp-side bp-blue-side', 1));
            // 6-10. 藍禁用 1-5
            for (var i = 0; i < 5; i++) {
                cols.push('<div class="bp-cell bp-side bp-blue-side bp-ban-cell">'
                    + '<div class="bp-ban" title="'
                    + esc((g['bb' + i] || [])[1] || '') + '">'
                    + icon(g['bb' + i], 24) + '</div></div>');
            }
            // 11-15. 藍路線（按 role 排序 top/jng/mid/bot/sup）
            for (var i = 0; i < 5; i++) {
                var slot = g['bp' + i];
                var order = slot ? slot[2] : null;
                var orderHtml = order != null
                    ? '<span class="bp-order">' + order + '</span>' : '';
                cols.push('<div class="bp-cell bp-side bp-blue-side bp-pick-cell">'
                    + '<div class="bp-pick" title="'
                    + esc(slot ? slot[1] : '') + '">'
                    + icon(slot, 30) + orderHtml + '</div></div>');
            }
            // 16. 紅方隊名
            cols.push('<div class="bp-cell bp-side bp-red-side">'
                + '<b class="bp-team-abbr">' + esc(g.ra) + '</b></div>');
            // 17. 紅選擇（先選/後選徽章）
            cols.push(pickLabelCell(g.fk, 1 - g.bfp)
                .replace('bp-cell', 'bp-cell bp-side bp-red-side', 1));
            // 18-22. 紅禁用 1-5
            for (var i = 0; i < 5; i++) {
                cols.push('<div class="bp-cell bp-side bp-red-side bp-ban-cell">'
                    + '<div class="bp-ban" title="'
                    + esc((g['rb' + i] || [])[1] || '') + '">'
                    + icon(g['rb' + i], 24) + '</div></div>');
            }
            // 23-27. 紅路線
            for (var i = 0; i < 5; i++) {
                var slot = g['rp' + i];
                var order = slot ? slot[2] : null;
                var orderHtml = order != null
                    ? '<span class="bp-order">' + order + '</span>' : '';
                cols.push('<div class="bp-cell bp-side bp-red-side bp-pick-cell">'
                    + '<div class="bp-pick" title="'
                    + esc(slot ? slot[1] : '') + '">'
                    + icon(slot, 30) + orderHtml + '</div></div>');
            }
            return '<div class="bp-row bp-game-row" data-win="'
                + winSide + '" data-gameid="' + esc(g.gid) + '">'
                + cols.join('') + '</div>';
        }

        function seriesBlock(s) {
            var ac = s.win === 'a'
                ? 'bp-score-team is-win' : 'bp-score-team';
            var bcls = s.win === 'b'
                ? 'bp-score-team is-win' : 'bp-score-team';
            return '<div class="bp-series"><div class="bp-series-head">'
                + '<span class="bp-series-date">' + esc(s.d) + '</span>'
                + '<span class="bp-series-patch">' + esc(s.patch) + '</span>'
                + '<span class="bp-league-pill">' + esc(s.lg) + '</span>'
                + '<span class="bp-series-stage">' + esc(s.st) + '</span>'
                + '<div class="bp-series-score">'
                + '<span class="' + ac + '">' + esc(s.wa) + '</span>'
                + '<span class="bp-score-n" style="color:' + BLUE + '">'
                + s.sa + '</span>'
                + '<span class="bp-score-dash">-</span>'
                + '<span class="bp-score-n" style="color:' + RED + '">'
                + s.sb + '</span>'
                + '<span class="' + bcls + '">' + esc(s.wb) + '</span>'
                + '</div></div>'
                + s.games.map(gameRow).join('') + '</div>';
        }

        if (!data.series.length) {
            host.innerHTML = '<div class="empty-state"><h3>'
                + esc(data.empty_title) + '</h3><p>'
                + esc(data.empty_desc) + '</p></div>';
            return window.dash_clientside.no_update;
        }
        host.innerHTML = data.series.map(seriesBlock).join('')
            + (data.more
                ? '<div class="bp-more-note">' + esc(data.more) + '</div>'
                : '');
        host.scrollTop = 0;
        return window.dash_clientside.no_update;
    }
    """,
    Output("bp-body", "data-rendered"),
    Input("bp-board-data", "data"),
)
