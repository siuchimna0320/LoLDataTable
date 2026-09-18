"""圖鑑頁：英雄圖鑑牆＋Data Dragon 版本/道具/召喚師/符文＋刷野速度。

賽事分頁保留誠實骨架（需賽程 API／逐幀事件資料）。
圖鑑靜態資料由 backend.ddragon_data 於伺服器端預取快取，
圖示走本機 /dd-img/ 代理路由，瀏覽器不需直連外網。
刷野卡片採分批渲染（每批 24 張）與圖片懶載入以控制首屏負擔。
"""
from __future__ import annotations

from urllib.parse import quote

import dash
from dash import Input, Output, State, callback, ctx, dcc, html

from backend import data_access, ddragon_data, jungle_data, patch_data
from frontend.components import common

dash.register_page(__name__, title="圖鑑", order=11)

# 刷野卡片牆每批次顯示數量（避免一次渲染 120 卡／700+ 圖示）
_JUNGLE_PAGE_SIZE = 24

# Data Dragon 英雄類型標籤英→中
_CHAMP_TAG_ZH = {
    "Fighter": "戰士", "Mage": "法師", "Assassin": "刺客",
    "Tank": "坦克", "Marksman": "射手", "Support": "輔助",
}
# 英雄牆每列固定卡片數（與設計稿一致）
_WALL_COLUMNS = 13

# 版本頁配色（依設計稿）
_C_PATCH = "#E08A3C"   # 版本號橙
_C_SKILL = "#4C8DFF"   # 技能標題藍
_C_BUFF = "#3DDC84"    # BUFF 綠
_C_NERF = "#F04444"    # NERF 紅
_C_ADJ = "#E6ECF5"     # 調整／一般白
_DIR_COLOR = {"buff": _C_BUFF, "nerf": _C_NERF,
              "adj": _C_ADJ, "note": _C_ADJ}

# 仍受限於資料源的骨架分頁
_SKELETON_TABS = {
    "objects": ("物件", "中立物件（史詩野怪／守衛／建築等）圖鑑資料待接入。"),
    "events": ("賽事", "官方賽程、賽事中繼與即時比分需接入 LoL Esports API。"),
}

# 分頁順序與中文名稱（與設計稿一致）
_SECTIONS = [
    ("versions", "版本"), ("champions", "英雄"), ("items", "道具"),
    ("runes", "符文"), ("summoners", "召喚師技能"),
    ("objects", "物件"), ("events", "賽事"), ("jungle", "刷野速度"),
]

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
# 金色小圖示（內嵌 SVG，無外部資源）
# ---------------------------------------------------------------------------
_GOLD = "#C8AA6E"


def _svg_img(svg: str, size: int) -> html.Img:
    """把內嵌 SVG 字串轉成 data URI 圖片元件（Dash 4 不支援 raw HTML）。"""
    uri = "data:image/svg+xml;utf8," + quote(svg, safe="")
    return html.Img(src=uri, width=size, height=size,
                    style={"display": "inline-block"})


def _gold_svg(kind: str, size: int = 11) -> html.Img:
    """選用（交叉劍）／禁用（戰槌）金色小圖示。"""
    if kind == "pick":
        path = (
            '<path d="M3 3l7 7m0-7L3 10" stroke="{g}" stroke-width="1.6" '
            'stroke-linecap="round"/>'
            '<path d="M10 10l2.2 2.2M2 12.2L4.2 10" stroke="{g}" '
            'stroke-width="1.6" stroke-linecap="round"/>')
    else:
        path = (
            '<path d="M11.5 2.5l2 2-3 3-2-2z" fill="{g}"/>'
            '<path d="M8.5 5.5l-1 1 2 2 1-1" fill="{g}"/>'
            '<path d="M7.2 7.8L3 12l1 1 4.2-4.2" stroke="{g}" '
            'stroke-width="1.5" stroke-linecap="round"/>')
    svg = (f'<svg viewBox="0 0 14 14" xmlns="http://www.w3.org/2000/svg">'
           f'{path.format(g=_GOLD)}</svg>')
    return _svg_img(svg, size)


def _stat_svg(kind: str, size: int = 16) -> html.Img:
    """版本頁彙總列的五個金色小圖示。"""
    paths = {
        "champ": '<circle cx="7" cy="5" r="2.6" fill="none" stroke="{g}" '
                 'stroke-width="1.5"/><path d="M2.5 12c.7-3 2.4-4.2 4.5-4.2S10.8 9 11.5 12" '
                 'fill="none" stroke="{g}" stroke-width="1.5" stroke-linecap="round"/>',
        "buff": '<path d="M7 2.5v9M3.5 6L7 2.5 10.5 6" fill="none" stroke="{g}" '
                'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
        "nerf": '<path d="M7 11.5v-9M3.5 8L7 11.5 10.5 8" fill="none" stroke="{g}" '
                'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
        "adj": '<path d="M3 4.5h8M5 4.5v3M9 6.5v3" fill="none" stroke="{g}" '
               'stroke-width="1.5" stroke-linecap="round"/>',
        "item": '<path d="M3 11L11 3l2-2-2 2-8 8-2 1z" fill="none" stroke="{g}" '
                'stroke-width="1.5" stroke-linejoin="round"/>',
    }
    svg = (f'<svg viewBox="0 0 14 14" xmlns="http://www.w3.org/2000/svg">'
           f'{paths[kind].format(g=_GOLD)}</svg>')
    return _svg_img(svg, size)


def _lane_tiles(codes: list) -> html.Div:
    """英雄卡底部路線方磚（深色底＋金色分路圖示，熱門路線在左）。"""
    tiles = [
        html.Span(
            html.Img(src=f"/pos-img/{code}.svg", width=17, height=17,
                     title=common.POSITION_ZH.get(code, code)),
            className="champ-lane-tile")
        for code in codes
    ]
    return html.Div(tiles, className="champ-tile-lanes")


# ---------------------------------------------------------------------------
# 英雄圖鑑牆
# ---------------------------------------------------------------------------
def _champ_zh_info(champ_zh: dict, cid: str) -> dict:
    """取繁中英雄資訊；缺料時降級英文 ID。"""
    info = champ_zh.get(cid) or {}
    return {
        "name": info.get("name") or cid,
        "tags": info.get("tags", []),
        "passive": info.get("passive", ""),
        "spells": info.get("spells", []),
    }


def _skill_tooltip(cid: str, info: dict) -> str:
    """頭像 hover 提示：被動與 Q/W/E/R 名稱。"""
    lines = [f'{info["name"]}（{cid}）']
    if info.get("passive"):
        lines.append(f'被動：{info["passive"]}')
    for slot, spell in zip(("Q", "W", "E", "R"), info.get("spells", [])):
        if spell:
            lines.append(f'{slot}：{spell}')
    return "\n".join(lines)


def _champion_cards(df, tag: str | None):
    """依設計稿產生英雄卡：頭像→名稱(連結)→選/禁次數→分路方磚，全面置中。"""
    champ_zh = patch_data.load_champ_zh()
    cards = []
    for _, r in df.iterrows():
        # 倉儲英雄名為賽事英文名（含空格／撇號），統一轉 DDragon 正式 ID
        raw_name = str(r["champion"])
        cid = common.champ_id(raw_name) or raw_name
        info = _champ_zh_info(champ_zh, cid)
        if tag and tag not in info["tags"]:
            continue
        cards.append(html.Div([
            html.Img(
                src=f"/champ-img/{quote(cid)}.png",
                width=68, height=68,
                className="champ-icon champ-tile-avatar",
                title=_skill_tooltip(cid, info)),
            dcc.Link(info["name"],
                     href=f"/compendium/champion/{quote(cid)}",
                     className="champ-name-link champ-tile-name"),
            html.Span([
                _gold_svg("pick"),
                html.Strong(f' 選 {int(r["picks"])}',
                            style={"fontWeight": 400}),
                html.Span("｜", style={"color": "#3a4660",
                                       "margin": "0 3px"}),
                _gold_svg("ban"),
                html.Strong(f' 禁 {int(r["bans"])}',
                            style={"fontWeight": 400}),
            ], className="champ-tile-bp"),
            _lane_tiles(list(r.get("positions") or [])),
        ], className="champ-tile"))
    return cards


# ---------------------------------------------------------------------------
# 版本更動頁（官方 Patch Notes 快取）
# ---------------------------------------------------------------------------
def _normalize_change_text(text: str) -> str:
    """修掉標的與冒號間的多餘空白，使其更接近設計稿排版。"""
    return text.replace(" ：", "：").strip()


def _patch_block(patch_group: dict) -> html.Div:
    """單一版本小節：橙色版本號＋各技能群組的彩色改動列。"""
    rows = []
    for group in patch_group.get("groups", []):
        title = group.get("title") or "基礎能力值"
        rows.append(html.Div(title, style={
            "color": _C_SKILL, "fontSize": 11.5, "fontWeight": 700,
            "margin": "6px 0 2px"}))
        for change in group["changes"]:
            rows.append(html.Div(
                _normalize_change_text(change["text"]),
                style={"color": _DIR_COLOR.get(change["dir"], _C_ADJ),
                       "fontSize": 10.5, "lineHeight": 1.5,
                       "wordBreak": "break-word"}))
    return html.Div([
        html.Div(patch_group["patch"], style={
            "color": _C_PATCH, "fontSize": 12, "fontWeight": 700,
            "margin": "8px 0 3px"}),
        *rows,
    ])


def _change_card(entry: dict) -> html.Div:
    """單一英雄改動卡：頭像＋名稱＋改動次數＋分版本明細。"""
    icon_src = f"/champ-img/{quote(entry['id'])}.png"
    touch_count = sum(len(g["changes"])
                      for p in entry["patches"] for g in p["groups"])
    blocks = [_patch_block(p) for p in entry["patches"]]
    return html.Div([
        html.Div([
            html.Img(src=icon_src, width=26, height=26,
                     className="champ-icon",
                     style={"borderRadius": 5, "objectFit": "cover",
                            "background": "#1b2740", "flex": "none"}),
            html.Span(entry["name"],
                      style={"fontSize": 13, "fontWeight": 700,
                             "color": "#E6ECF5", "marginLeft": 7,
                             "overflow": "hidden", "textOverflow": "ellipsis",
                             "whiteSpace": "nowrap", "flex": 1}),
            html.Span(f'{touch_count} 次改動',
                      style={"fontSize": 10.5, "color": "#8A97AB",
                             "flex": "none"}),
        ], style={"display": "flex", "alignItems": "center",
                  "marginBottom": 2}),
        *blocks,
    ], style={"background": "#10192E", "border": "1px solid #1b2740",
              "borderRadius": 8, "padding": "9px 11px",
              "minWidth": 0})


def _version_stats(entries: list[dict]) -> list[html.Div]:
    """四個金色彙總數字：改動英雄／BUFF 列／NERF 列／調整列。"""
    n_champ = n_buff = n_nerf = n_adj = 0
    for entry in entries:
        n_champ += 1
        for pg in entry["patches"]:
            for group in pg["groups"]:
                for change in group["changes"]:
                    direction = change["dir"]
                    if direction == "buff":
                        n_buff += 1
                    elif direction == "nerf":
                        n_nerf += 1
                    else:
                        n_adj += 1
    items = [
        ("champ", n_champ, "改動英雄數"),
        ("buff", n_buff, "BUFF 改動列數"),
        ("nerf", n_nerf, "NERF 改動列數"),
        ("adj", n_adj, "調整／說明列數"),
    ]
    return [html.Div([
        _stat_svg(kind),
        html.Span(f" {value}",
                  style={"fontSize": 17, "fontWeight": 700,
                         "color": _GOLD, "marginLeft": 5}),
    ], title=tip,
        style={"display": "flex", "alignItems": "center",
               "justifyContent": "center", "flex": 1, "minWidth": 0})
        for kind, value, tip in items]


def _season_year(season: str) -> int:
    """季號換西元年：26→2026；經典編號 14→2024。"""
    number = int(season)
    return 2000 + number if number >= 20 else 2010 + number


def _patch_year(patch: str) -> int:
    return _season_year(patch.split(".", 1)[0])


def _year_options(notes: dict) -> list[dict]:
    """年份篩選選項：最新一年在最前，外加「全部」。"""
    options = [{"label": f"{_season_year(s)} 年",
                "value": str(_season_year(s))}
               for s in notes.get("seasons", [])]
    return [{"label": "全部", "value": "ALL"}] + options


def _versions_pane(notes: dict, selected: str) -> html.Div:
    """版本更動頁主體：年份工具列＋彙總數字＋最新版本優先的英雄卡片牆。"""
    return html.Div([
        html.Div([
            html.Span("年度", style={"color": "#8A97AB", "fontSize": 12,
                                    "marginRight": 6}),
            dcc.Dropdown(id="comp-version-filter",
                         options=_year_options(notes),
                         value=selected, clearable=False,
                         style={"color": "#0B1020", "width": 150}),
        ], style={"display": "flex", "alignItems": "center",
                  "justifyContent": "flex-end", "margin": "4px 0 12px"}),
        html.Div(id="comp-versions-body",
                 children=_versions_body(notes, selected)),
    ])


def _versions_body(notes: dict, selected: str):
    """版本頁彙總列＋英雄卡片牆（供初始渲染與篩選 callback 共用）。

    只收錄召喚峽谷英雄改動：排除道具與經典模式區塊。
    """
    entries = []
    for entry in notes.get("entries", []):
        if entry.get("kind") != "champion":
            continue
        if selected != "ALL":
            year = int(selected)
            patches = [p for p in entry["patches"]
                       if _patch_year(p["patch"]) == year]
        else:
            patches = entry["patches"]
        if not patches:
            continue
        entries.append({**entry, "patches": patches})
    entries.sort(key=lambda e: e["patches"][0]["patch"], reverse=True)
    return [
        html.Div(_version_stats(entries),
                 style={"display": "flex", "marginBottom": 12}),
        html.Div([_change_card(e) for e in entries],
                 style={"display": "grid",
                        "gridTemplateColumns":
                            "repeat(auto-fill,minmax(250px,1fr))",
                        "gap": 10, "alignItems": "start"}),
    ]


def _versions_tab() -> html.Div:
    """版本頁入口：預設只顯示最新一年的召喚峽谷英雄改動。"""
    notes = patch_data.load_notes()
    if notes and notes.get("entries"):
        seasons = notes.get("seasons") or []
        latest = str(_season_year(seasons[0])) if seasons else "ALL"
        return _versions_pane(notes, latest)
    return common.empty_state(
        "版本改動資料尚未建立",
        "本機尚無官方版本更新快取，請於專案根目錄執行 "
        "python -m backend.patch_data 抓取後重新整理。")


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


_MORE_STYLE = {"margin": "6px 0 18px", "padding": "8px 28px",
               "background": "#1b2740", "color": "#E6ECF5",
               "border": "1px solid #2c4068", "borderRadius": 8,
               "fontSize": 13, "cursor": "pointer"}


def _more_button(remaining: int) -> html.Button:
    """卡片牆底部「顯示更多」按鈕（常駐 DOM，無更多時由 callback 隱藏）。"""
    return html.Button(
        f"顯示更多（還有 {max(remaining, 0)} 隻英雄）",
        id="comp-jungle-more", n_clicks=0,
        style={**_MORE_STYLE,
               "display": "none" if remaining <= 0 else "inline-block"})


def _jungle_views(payload: dict, keyword: str = "", side: str = "ALL",
                  sort: str = "count") -> list:
    """依搜尋／開局方／排序產生排序後的 (排序鍵, 卡片) 列表。"""
    views = []
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
    return views


def _jungle_grid(views: list, limit: int = _JUNGLE_PAGE_SIZE) -> html.Div:
    """刷野卡片牆：僅渲染前 limit 張卡片。"""
    cards = [card for _, card in views[:limit]]
    if not views:
        cards = [common.empty_state("找不到符合的英雄", "請調整搜尋或開局篩選")]
    return html.Div(cards,
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
    initial_views = _jungle_views(payload)
    initial_remaining = len(initial_views) - _JUNGLE_PAGE_SIZE
    return html.Div([
        banner, toolbar,
        # 目前已顯示卡片數（篩選條件變更時重置，點「顯示更多」時遞增）
        dcc.Store(id="comp-jungle-limit", data=_JUNGLE_PAGE_SIZE),
        _jungle_grid(initial_views),
        # 按鈕常駐 DOM（無更多時隱藏），避免 Dash Input 元件消失造成死鎖
        html.Div(_more_button(initial_remaining),
                 style={"textAlign": "center"}),
    ], id="comp-jungle-pane")


def layout():
    return html.Div([
        html.Div([
            html.Span("分區", className="comp-sections-label"),
            dcc.RadioItems(
                id="comp-tabs", value="champions", inline=True,
                options=[{"label": zh, "value": key}
                         for key, zh in _SECTIONS],
                className="comp-sections",
                inputClassName="comp-section-input",
                labelClassName="comp-section-pill"),
        ], className="comp-sections-bar"),
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
    if tab == "versions":
        # 版本頁只依賴官方公告快取，不需 Data Dragon bundle
        return _versions_tab()
    bundle = ddragon_data.load_bundle()
    if not bundle:
        return common.empty_state(
            "圖鑑資料無法載入",
            "Data Dragon 暫時無法連線且本機無快取，請稍後重試或執行"
            " python -m backend.pipeline assets。")
    if tab == "items":
        return _items_tab(bundle)
    if tab == "summoners":
        return _summoners_tab(bundle)
    if tab == "runes":
        return _runes_tab(bundle)
    return common.empty_state("開發中", "")


def _year_label(years: list[int] | None) -> str:
    """依全域年份篩選產生副標題年度文字。"""
    if not years:
        return "全部年度"
    if len(years) == 1:
        return f"{years[0]} 年"
    return f"{min(years)}–{max(years)} 年"


def _champ_tag_options() -> list[dict]:
    """類型下拉選項：以 patch_notes 快取中實際出現的 DDragon tags 為準。"""
    tags = sorted({tag for info in patch_data.load_champ_zh().values()
                   for tag in info.get("tags", [])})
    return [{"label": "全部類型", "value": "ALL"}] + [
        {"label": _CHAMP_TAG_ZH.get(tag, tag), "value": tag} for tag in tags]


def _champions_layout(data) -> html.Div:
    years = [int(y) for y in (data or {}).get("years", [])] or None
    df = data_access.champion_wall(years)
    if df.empty:
        return common.empty_state("沒有資料", "請調整年份篩選")
    subtitle = (f'{_year_label(years)}選用次數排序 ↑｜'
                f'滑上頭像看技能與被動數值')
    return html.Div([
        html.Div([
            html.Span([
                html.Span("👤", style={"fontSize": 12, "marginRight": 6}),
                html.Span("英雄圖鑑 ", style={"color": "#4C8DFF",
                                            "fontWeight": 700,
                                            "fontSize": 12}),
                html.Span(subtitle,
                          style={"color": "#8A97AB", "fontSize": 12}),
            ]),
            html.Span([
                html.Span("類型", style={"color": "#8A97AB", "fontSize": 12,
                                        "marginRight": 6}),
                dcc.Dropdown(id="comp-champ-tag",
                             options=_champ_tag_options(), value="ALL",
                             clearable=False,
                             style={"color": "#0B1020", "width": 150}),
            ], style={"display": "flex", "alignItems": "center"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "center", "margin": "2px 0 12px"}),
        html.Div(id="comp-wall",
                 children=_champion_cards(df, None),
                 style={"display": "grid",
                        "gridTemplateColumns":
                            f"repeat({_WALL_COLUMNS}, minmax(0, 1fr))",
                        "columnGap": 6, "rowGap": 12}),
    ])


# 英雄牆類型過濾（不重查倉儲）
@callback(
    Output("comp-wall", "children"),
    Input("comp-champ-tag", "value"),
    State("global-filter", "data"),
    prevent_initial_call=True,
)
def _filter_hero(tag, data):
    years = [int(y) for y in (data or {}).get("years", [])] or None
    selected = None if tag in (None, "ALL") else tag
    return _champion_cards(data_access.champion_wall(years), selected)


# 版本頁版本篩選（只由本機快取重建，無網路請求）
@callback(
    Output("comp-versions-body", "children"),
    Input("comp-version-filter", "value"),
    prevent_initial_call=True,
)
def _filter_versions(selected):
    notes = patch_data.load_notes()
    if not notes:
        return common.empty_state("版本改動資料尚未建立",
                                  "請執行 python -m backend.patch_data")
    return _versions_body(notes, selected or "ALL")


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


# 刷野卡片牆：搜尋/開局/排序重置分頁，「顯示更多」追加 24 張
@callback(
    Output("comp-jungle-grid", "children"),
    Output("comp-jungle-more", "children"),
    Output("comp-jungle-more", "style"),
    Output("comp-jungle-limit", "data"),
    Input("comp-jungle-search", "value"),
    Input("comp-jungle-side", "value"),
    Input("comp-jungle-sort", "value"),
    Input("comp-jungle-more", "n_clicks"),
    State("comp-jungle-limit", "data"),
    prevent_initial_call=True,
)
def _filter_jungle(keyword, side, sort, _more_clicks, limit):
    payload = jungle_data.load_clears()
    empty = common.empty_state("刷野資料暫時無法載入", "請稍後重試")
    if not payload:
        return empty, "", {**_MORE_STYLE, "display": "none"}, limit
    # 篩選條件變更 → 回到第一批；僅「顯示更多」→ 疊加
    if ctx.triggered_id == "comp-jungle-more":
        limit = int(limit or _JUNGLE_PAGE_SIZE) + _JUNGLE_PAGE_SIZE
    else:
        limit = _JUNGLE_PAGE_SIZE
    views = _jungle_views(payload, keyword or "", side or "ALL",
                          sort or "count")
    remaining = len(views) - limit
    grid = _jungle_grid(views, limit)
    label = f"顯示更多（還有 {max(remaining, 0)} 隻英雄）"
    style = {**_MORE_STYLE,
             "display": "none" if remaining <= 0 else "inline-block"}
    return grid.children, label, style, limit
