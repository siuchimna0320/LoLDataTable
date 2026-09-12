"""圖鑑頁：英雄 BP 圖鑑牆＋Data Dragon 版本/道具/召喚師/符文圖鑑。

賽事與刷野速度分頁保留誠實骨架（需賽程 API／逐幀事件資料）。
圖鑑靜態資料由 backend.ddragon_data 於伺服器端預取快取，
圖示走本機 /dd-img/ 代理路由，瀏覽器不需直連外網。
"""
from __future__ import annotations

from urllib.parse import quote

import dash
from dash import Input, Output, State, callback, dcc, html

from backend import data_access, ddragon_data, jungle_data
from frontend.components import common

dash.register_page(__name__, title="圖鑑", order=11)

# 仍受限於資料源的骨架分頁
_SKELETON_TABS = {
    "events": ("賽事", "官方賽程、賽事中繼與即時比分需接入 LoL Esports API。"),
}

# 道具分類標籤英→中（未列出的標籤以原文呈現）
_ITEM_TAG_ZH = {
    "Damage": "攻擊", "SpellDamage": "法術", "Health": "生命",
    "Armor": "護甲", "SpellBlock": "魔抗", "MagicResist": "魔抗",
    "Boots": "鞋子", "Consumable": "消耗品", "Jungle": "打野",
    "Trinket": "飾品", "LifeSteal": "吸血", "CriticalStrike": "暴擊",
    "AttackSpeed": "攻速", "Mana": "魔力", "ManaRegen": "回魔",
    "HealthRegen": "回血", "CooldownReduction": "冷剛",
    "AbilityHaste": "技能急速", "OnHit": "命中特效",
    "ArmorPenetration": "物穿", "MagicPenetration": "法穿",
    "Vision": "視野", "Stealth": "隱身", "Lane": "對線",
    "Active": "主動",
}
_RUNE_SLOT_ZH = ["基石符文", "第 2 排", "第 3 排", "第 4 排"]


def _dd_icon(group: str, icon: str, size: int = 36) -> html.Img:
    """本機代理的 Data Dragon 圖示。"""
    return html.Img(
        src=f"/dd-img/{group}/{quote(icon, safe='')}",
        width=size, height=size, className="champ-icon",
        style={"borderRadius": 6, "background": "#1b2740"})


# ---------------------------------------------------------------------------
# 英雄圖鑑牆
# ---------------------------------------------------------------------------
def _champion_cards(df, keyword: str | None):
    if keyword:
        df = df[df["champion"].str.contains(keyword, case=False, na=False)]
    cards = []
    for _, r in df.iterrows():
        pos_zh = common.POSITION_ZH.get(r.get("main_position"), "")
        cards.append(html.Div([
            common.champ_img(r["champion"], 52),
            html.Div(str(r["champion"]),
                     style={"fontSize": 11, "marginTop": 3,
                            "maxWidth": 64, "overflow": "hidden",
                            "textOverflow": "ellipsis",
                            "whiteSpace": "nowrap"}),
            html.Div(f'選{int(r["picks"])} / 禁{int(r["bans"])}',
                     style={"fontSize": 10, "color": "#8A97AB"}),
            html.Div(pos_zh, style={"fontSize": 10, "color": "#F5B945"}),
        ], style={"textAlign": "center", "width": 66, "margin": 6},
            title=f'{r["champion"]}（{pos_zh}）：選用 {int(r["picks"])} 次、'
                  f'禁用 {int(r["bans"])} 次'))
    return cards


# ---------------------------------------------------------------------------
# Data Dragon 圖鑑分頁
# ---------------------------------------------------------------------------
def _versions_tab(bundle: dict) -> html.Div:
    current = bundle["version"]
    chips = []
    for index, ver in enumerate(bundle["versions"]):
        is_current = ver == current
        chips.append(html.Span(
            ver + ("（目前）" if is_current else ""),
            style={
                "display": "inline-block", "margin": 4, "padding": "4px 12px",
                "borderRadius": 14, "fontSize": 12,
                "background": "#4C8DFF" if is_current else "#1b2740",
                "color": "#fff" if is_current else "#C7D2E4",
                "border": "1px solid #22304A"}))
    note = ("版本平衡內容（英雄/道具改動明細）需另接官方 Patch Notes，"
            "此處列出 Data Dragon 提供的版本清單。")
    return html.Div([
        html.Div(chips, style={"margin": "8px 0"}),
        html.P(note, style={"color": "#8A97AB", "fontSize": 12}),
    ])


def _item_card(item: dict) -> html.Div:
    tags = "、".join(_ITEM_TAG_ZH.get(t, t) for t in item["tags"][:3])
    tooltip = (f'{item["name"]}｜{item["gold"]} G｜{tags}\n'
               f'{item.get("plaintext", "")}')
    return html.Div([
        _dd_icon("item", item["icon"], 38),
        html.Div(item["name"],
                 style={"fontSize": 11, "marginTop": 4, "maxWidth": 78,
                        "overflow": "hidden", "textOverflow": "ellipsis",
                        "whiteSpace": "nowrap"}),
        html.Div(f'{item["gold"]} G',
                 style={"fontSize": 10, "color": "#F5B945"}),
    ], style={"textAlign": "center", "width": 82, "margin": 6,
              "padding": "6px 2px", "border": "1px solid #1b2740",
              "borderRadius": 8, "background": "#10192E"},
        title=tooltip)


def _items_tab(bundle: dict, keyword: str | None = None,
               tag: str | None = None) -> html.Div:
    items = bundle["items"]
    all_tags = sorted({t for it in items for t in it["tags"]})
    tag_options = [{"label": "全部分類", "value": "ALL"}] + [
        {"label": _ITEM_TAG_ZH.get(t, t), "value": t} for t in all_tags]
    if keyword:
        items = [it for it in items
                 if keyword.lower() in it["name"].lower()]
    if tag and tag != "ALL":
        items = [it for it in items if tag in it["tags"]]
    return html.Div([
        html.Div([
            dcc.Input(id="comp-item-search", type="text",
                      placeholder="搜尋道具…", value=keyword,
                      style={"width": 200, "background": "#0E1526",
                             "color": "#E6ECF5", "border": "1px solid #22304A",
                             "borderRadius": 6, "padding": 7}),
            dcc.Dropdown(id="comp-item-tag", options=tag_options,
                         value=tag or "ALL", clearable=False,
                         style={"color": "#0B1020", "width": 180,
                                "marginLeft": 10}),
        ], style={"display": "flex", "alignItems": "center",
                  "margin": "8px 0"}),
        html.Div(f'共 {len(items)} 件道具',
                 style={"color": "#8A97AB", "fontSize": 11}),
        html.Div([_item_card(it) for it in items],
                 style={"display": "flex", "flexWrap": "wrap"}),
    ])


def _summoner_card(spell: dict) -> html.Div:
    return html.Div([
        _dd_icon("spell", spell["icon"], 44),
        html.Div(spell["name"],
                 style={"fontSize": 12, "marginTop": 6,
                        "color": "#E6ECF5"}),
        html.Div(f'CD {spell["cooldown"]} 秒',
                 style={"fontSize": 10, "color": "#8A97AB", "marginTop": 2}),
    ], style={"textAlign": "center", "width": 110, "margin": 8,
              "padding": "10px 4px", "border": "1px solid #1b2740",
              "borderRadius": 8, "background": "#10192E"},
        title=f'{spell["name"]}（CD {spell["cooldown"]}s）\n'
              f'{spell["description"]}')


def _summoners_tab(bundle: dict) -> html.Div:
    return html.Div([
        html.Div("召喚師技能（召喚峽谷）",
                 style={"color": "#8A97AB", "fontSize": 12, "margin": "8px 0"}),
        html.Div([_summoner_card(s) for s in bundle["summoners"]],
                 style={"display": "flex", "flexWrap": "wrap"}),
    ])


def _rune_chip(rune: dict, keystone: bool = False) -> html.Div:
    size = 40 if keystone else 28
    return html.Div([
        _dd_icon("perk", rune["icon"], size),
        html.Div(rune["name"],
                 style={"fontSize": 10 if not keystone else 11,
                        "marginTop": 3, "maxWidth": 92,
                        "color": "#C7D2E4"}),
    ], style={"textAlign": "center", "width": 96, "margin": 5},
        title=f'{rune["name"]}\n{rune.get("desc", "")}')


def _runes_tab(bundle: dict) -> html.Div:
    tree_cards = []
    for tree in bundle["runes"]:
        rows = []
        for index, slot in enumerate(tree["slots"]):
            rows.append(html.Div([
                html.Span(_RUNE_SLOT_ZH[index],
                          style={"color": "#F5B945", "fontSize": 11,
                                 "width": 72, "display": "inline-block"}),
                html.Span([_rune_chip(r, keystone=(index == 0))
                           for r in slot],
                          style={"display": "inline-flex",
                                 "flexWrap": "wrap"}),
            ], style={"margin": "6px 0"}))
        tree_cards.append(common.panel(
            tree["name"],
            html.Div([
                html.Div([_dd_icon("perk", tree["icon"], 30),
                          html.Span(tree["name"],
                                    style={"fontSize": 14, "marginLeft": 8,
                                           "color": "#E6ECF5"})],
                         style={"display": "flex", "alignItems": "center",
                                "marginBottom": 6}),
                *rows,
            ]), tag="符文系"))
    tree_cards.append(html.P(
        "※ 屬性碎片（攻速/彈性/護甲等）未收錄於 Data Dragon 符文 JSON，"
        "故未列出。",
        style={"color": "#8A97AB", "fontSize": 11}))
    return html.Div(tree_cards,
                    style={"display": "grid",
                           "gridTemplateColumns": "repeat(auto-fill,minmax(420px,1fr))",
                           "gap": 12})


# ---------------------------------------------------------------------------
# 刷野速度（Jungle Clear Compilation 社群編纂表）
# ---------------------------------------------------------------------------
_SHARD_COLORS = ["#F5B945", "#4C8DFF", "#3DDC84"]


def _play_button(url: str) -> html.A:
    """紅色 YouTube 播放按鈕（CSS 字元，無外部資源）。"""
    return html.A(
        "▶", href=url, target="_blank", rel="noopener noreferrer",
        title="開啟示範影片",
        style={"background": "#F04444", "color": "#fff", "width": 24,
               "height": 24, "lineHeight": "24px", "textAlign": "center",
               "borderRadius": 6, "fontSize": 11, "flex": "none"})


def _skill_badge(slot: str, icon_file: str | None) -> html.Span:
    """1-3 級起手技能：有圖示用圖示，否則用 Q/W/E 字牌備援。"""
    if icon_file:
        return html.Img(
            src=f"/dd-img/spell/{quote(icon_file, safe='')}",
            width=20, height=20,
            style={"borderRadius": 4, "background": "#1b2740"})
    return html.Span(
        slot,
        style={"width": 20, "height": 20, "lineHeight": "20px",
               "textAlign": "center", "fontSize": 10, "fontWeight": 700,
               "color": "#F5B945", "border": "1px solid #F5B945",
               "borderRadius": 4, "display": "inline-block"})


def _shard_diamonds(shards: list) -> list:
    """三排符文碎片，以有色菱形表示，滑鼠移過顯示中文名。"""
    return [html.Span("◆", title=jungle_data.SHARD_ZH.get(code, code),
                      style={"color": _SHARD_COLORS[min(i, 2)],
                             "fontSize": 10, "marginRight": 3})
            for i, code in enumerate(shards)]


def _clear_block(clear: dict, skill_icons: dict) -> html.Div:
    """單一種刷野：時間／技能／路徑／影片，下接示範者與版本備註。"""
    badges = [_skill_badge(slot, skill_icons.get(slot))
              for slot in clear.get("skills", [])]
    meta_parts = []
    if clear.get("player"):
        meta_parts.append(clear["player"])
    if clear.get("patch"):
        meta_parts.append(f'v{clear["patch"]}')
    footer_left = [
        html.Span(" · ".join(meta_parts),
                  style={"color": "#8A97AB", "fontSize": 10}),
        html.Span(_shard_diamonds(clear.get("shards", [])),
                  style={"marginLeft": 6, "display": "inline-flex",
                         "alignItems": "center"}),
    ]
    if clear.get("remark"):
        footer_left.append(html.Span(
            clear["remark"],
            style={"color": "#6EA8FE", "fontSize": 10, "marginLeft": 6}))
    return html.Div([
        html.Div([
            html.Span(clear["time"],
                      style={"width": 38, "fontSize": 12, "color": "#E6ECF5",
                             "fontFamily": "Consolas, monospace"}),
            html.Span(badges,
                      style={"display": "inline-flex", "gap": 3,
                             "width": 70, "flex": "none"}),
            html.Span(clear["path_zh"],
                      style={"fontSize": 11, "color": "#C7D2E4",
                             "flex": 1, "overflow": "hidden",
                             "textOverflow": "ellipsis",
                             "whiteSpace": "nowrap"}),
            (html.Span("過時", title="位於試算表 Outdated Clears 區段",
                       style={"fontSize": 9, "color": "#8A97AB",
                              "border": "1px solid #3a4660",
                              "borderRadius": 4, "padding": "1px 4px",
                              "marginRight": 6})
             if clear.get("outdated") else None),
            _play_button(clear["link"]) if clear.get("link") else None,
        ], style={"display": "flex", "alignItems": "center", "gap": 6}),
        html.Div(footer_left,
                 style={"margin": "3px 0 0 44px",
                        "display": "flex", "alignItems": "center",
                        "flexWrap": "wrap"}),
    ], style={"padding": "6px 8px", "marginBottom": 5,
              "background": "#0C1426", "border": "1px solid #17223a",
              "borderRadius": 6})


def _jungle_card(champion: dict) -> html.Div:
    """單一英雄刷野卡片：標題列＋所有刷野列。"""
    header = html.Div([
        html.Span([
            common.champ_img(champion["name"], 26),
            html.Span(champion["name"],
                      style={"fontSize": 13, "fontWeight": 700,
                             "color": "#E6ECF5", "marginLeft": 8}),
            html.Span(f'打野 {champion["clears_n"]} 場',
                      style={"fontSize": 11, "color": "#8A97AB",
                             "marginLeft": 8}),
        ], style={"display": "flex", "alignItems": "center"}),
        html.Span(f'最快 {champion["fastest"]}',
                  style={"fontSize": 12, "color": "#F5B945",
                         "fontWeight": 700}),
    ], style={"display": "flex", "justifyContent": "space-between",
              "alignItems": "center", "marginBottom": 8})
    rows = [_clear_block(c, champion.get("skill_icons", {}))
            for c in champion["clears"]]
    return html.Div([header, *rows],
                    style={"background": "#131A2A",
                           "border": "1px solid #1b2740",
                           "borderRadius": 10, "padding": 10,
                           "marginBottom": 12})


def _jungle_filter_clears(champion: dict, side: str) -> list:
    """依開局方過濾單卡刷野列。"""
    if side == "ALL":
        return champion["clears"]
    return [c for c in champion["clears"] if c["side"] == side]


def _jungle_grid(payload: dict, keyword: str = "", side: str = "ALL",
                 sort: str = "count") -> html.Div:
    """刷野卡片牆：搜尋／開局方／排序。"""
    views = []  # (排序鍵, 卡片)
    for champion in payload.get("champions", []):
        if keyword and keyword.lower() not in champion["name"].lower():
            continue
        clears = _jungle_filter_clears(champion, side)
        if not clears:
            continue
        view = dict(champion, clears=clears, fastest=clears[0]["time"])
        sort_key = clears[0]["seconds"] if sort == "time" else -view["clears_n"]
        views.append((sort_key, _jungle_card(view)))
    if sort == "time":
        views.sort(key=lambda item: item[0])
    return html.Div([card for _, card in views],
                    id="comp-jungle-grid",
                    style={"display": "grid",
                           "gridTemplateColumns":
                               "repeat(auto-fill,minmax(390px,1fr))",
                           "gap": "4px 12px"})


def _jungle_tab(payload: dict) -> html.Div:
    """刷野速度分頁：工具列＋資料來源說明＋卡片牆。"""
    summary = payload.get("summary", {})
    fetched = (payload.get("fetched_at", "")[:10] or "未知")
    banner = html.Div([
        html.Span(
            f'刷野速度 S16/2026｜每列＝一種刷野：時間・起手技能順序'
            f'（1-3 級）・刷野順序・示範影片・示範者・版本/符文碎片'
            f'｜共 {summary.get("champions", 0)} 隻英雄、'
            f'{summary.get("clears", 0)} 種刷野｜資料更新 {fetched}',
            style={"fontSize": 11, "color": "#8A97AB"}),
        html.Span("　"),
        html.A("Jungle Clear Compilation 試算表",
               href=payload.get("sheet_url", "#"), target="_blank",
               style={"color": "#4C8DFF", "fontSize": 11}),
        html.Span("・", style={"color": "#8A97AB"}),
        html.A("作者 Discord",
               href=payload.get("discord_url", "#"), target="_blank",
               style={"color": "#4C8DFF", "fontSize": 11}),
    ], style={"marginBottom": 10})
    toolbar = html.Div([
        dcc.Input(id="comp-jungle-search", type="text",
                  placeholder="搜尋英雄…",
                  style={"width": 180, "background": "#0E1526",
                         "color": "#E6ECF5", "border": "1px solid #22304A",
                         "borderRadius": 6, "padding": 7}),
        html.Span("開局：", style={"color": "#8A97AB", "fontSize": 12,
                                  "marginLeft": 14}),
        dcc.RadioItems(
            id="comp-jungle-side", inline=True, value="ALL",
            options=[{"label": "全部", "value": "ALL"},
                     {"label": "紅 B 開局", "value": "red"},
                     {"label": "藍 B 開局", "value": "blue"}],
            style={"color": "#E6ECF5", "fontSize": 12}),
        html.Span("排序：", style={"color": "#8A97AB", "fontSize": 12,
                                  "marginLeft": 14}),
        dcc.Dropdown(
            id="comp-jungle-sort", clearable=False, value="count",
            options=[{"label": "使用場數（多→少）", "value": "count"},
                     {"label": "最快時間（短→長）", "value": "time"}],
            style={"color": "#0B1020", "width": 190}),
    ], style={"display": "flex", "alignItems": "center",
              "flexWrap": "wrap", "gap": 6, "marginBottom": 8})
    return html.Div([banner, toolbar,
                     _jungle_grid(payload)], id="comp-jungle-pane")


def layout():
    tabs = [dcc.Tab(label="英雄圖鑑", value="champions"),
            dcc.Tab(label="版本更動", value="versions"),
            dcc.Tab(label="道具", value="items"),
            dcc.Tab(label="召喚師技能", value="summoners"),
            dcc.Tab(label="符文", value="runes"),
            dcc.Tab(label="刷野速度", value="jungle")]
    tabs += [dcc.Tab(label=zh, value=key)
             for key, (zh, _) in _SKELETON_TABS.items()]
    return html.Div([
        dcc.Tabs(id="comp-tabs", value="champions",
                 colors={"border": "#22304A", "primary": "#4C8DFF",
                         "background": "#0E1526"},
                 children=tabs),
        html.Div(id="comp-content", style={"paddingTop": 12}),
    ], className="page")


@callback(
    Output("comp-content", "children"),
    Input("comp-tabs", "value"),
    Input("global-filter", "data"),
)
def _render(tab, data):
    if tab in _SKELETON_TABS:
        title, note = _SKELETON_TABS[tab]
        return common.empty_state(f"{title}圖鑑開發中", note)
    if tab == "champions":
        return _champions_layout(data)
    if tab == "jungle":
        payload = jungle_data.load_clears()
        if not payload:
            return common.empty_state(
                "刷野資料暫時無法載入",
                "找不到本機 xlsx 副本（backend/data/jungle_clear_time/）"
                "且無 JSON 快取，請確認檔案存在後重新整理。")
        return _jungle_tab(payload)
    bundle = ddragon_data.load_bundle()
    if not bundle:
        return common.empty_state(
            "圖鑑資料無法載入",
            "Data Dragon 暫時無法連線且本機無快取，請稍後重試或執行"
            " python -m backend.pipeline assets。")
    if tab == "versions":
        return _versions_tab(bundle)
    if tab == "items":
        return _items_tab(bundle)
    if tab == "summoners":
        return _summoners_tab(bundle)
    if tab == "runes":
        return _runes_tab(bundle)
    return common.empty_state("開發中", "")


def _champions_layout(data) -> html.Div:
    years = [int(y) for y in (data or {}).get("years", [])] or None
    df = data_access.champion_wall(years)
    if df.empty:
        return common.empty_state("沒有資料", "請調整年份篩選")
    return html.Div([
        dcc.Input(id="comp-search", type="text", placeholder="搜尋英雄…",
                  style={"marginBottom": 10, "width": 240,
                         "background": "#0E1526", "color": "#E6ECF5",
                         "border": "1px solid #22304A", "borderRadius": 6,
                         "padding": 7}),
        html.Div(id="comp-wall",
                 children=_champion_cards(df, None),
                 style={"display": "flex", "flexWrap": "wrap"}),
    ])


# 英雄牆關鍵字過濾（不重查倉儲）
@callback(
    Output("comp-wall", "children"),
    Input("comp-search", "value"),
    State("global-filter", "data"),
    prevent_initial_call=True,
)
def _search_hero(keyword, data):
    years = [int(y) for y in (data or {}).get("years", [])] or None
    return _champion_cards(data_access.champion_wall(years), keyword)


# 道具牆關鍵字＋分類過濾（直接由快取重建，無網路請求）
@callback(
    Output("comp-content", "children", allow_duplicate=True),
    Input("comp-item-search", "value"),
    Input("comp-item-tag", "value"),
    prevent_initial_call=True,
)
def _filter_items(keyword, tag):
    bundle = ddragon_data.load_bundle()
    if not bundle:
        return common.empty_state("圖鑑資料無法載入", "請稍後重試")
    return _items_tab(bundle, keyword, tag)


# 刷野卡片牆：搜尋／開局方／排序（資料已 lru 快取，重建僅元件運算）
@callback(
    Output("comp-jungle-grid", "children"),
    Input("comp-jungle-search", "value"),
    Input("comp-jungle-side", "value"),
    Input("comp-jungle-sort", "value"),
    prevent_initial_call=True,
)
def _filter_jungle(keyword, side, sort):
    payload = jungle_data.load_clears()
    if not payload:
        return common.empty_state("刷野資料暫時無法載入", "請稍後重試")
    return _jungle_grid(payload, keyword or "", side or "ALL",
                        sort or "count").children
