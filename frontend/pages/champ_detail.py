"""英雄詳情頁（動態路由 /compendium/champion/<champion_id>）。

三個區塊：
1. 核心裝備與出裝流派：DPM.LOL 職業單／雙排（積分場）今年資料聚合。
2. 技能說明：Data Dragon zh_TW 目前版本技能數值。
3. 歷年改動：官方版本公告跨季快取，最新版本在前；BUFF 綠／NERF 紅／調整白。
"""
from __future__ import annotations

import re
from urllib.parse import quote

import dash
from dash import dcc, html

from backend import champ_detail, ddragon_data, patch_data
from frontend.components import common

dash.register_page(
    __name__,
    path_template="/compendium/champion/<champion_id>",
    title="英雄詳情",
)

# 配色（與圖鑑版本頁一致）
_C_PATCH = "#E08A3C"
_C_SKILL = "#4C8DFF"
_C_BUFF = "#3DDC84"
_C_NERF = "#F04444"
_C_ADJ = "#E6ECF5"
_C_GOLD = "#C8AA6E"
_C_NUM = "#F5B945"
_C_MUTED = "#8A97AB"
_C_BORDER = "#1b2740"
_C_PANEL = "#10192E"
_DIR_COLOR = {"buff": _C_BUFF, "nerf": _C_NERF,
              "adj": _C_ADJ, "note": _C_ADJ}
_CHAMP_TAG_ZH = {
    "Fighter": "戰士", "Mage": "法師", "Assassin": "刺客",
    "Tank": "坦克", "Marksman": "射手", "Support": "輔助",
}
# 官方 slug 與 DDragon ID 不同者（歷年改動「數據詳情」連結用）
_SLUG_OVERRIDE = {"MonkeyKing": "wukong", "Renata": "renataglasc"}
_NUM_RE = re.compile(r"([+-]?\d+(?:[./]\d+)*%?)")


# ---------------------------------------------------------------------------
# 小元件
# ---------------------------------------------------------------------------
def _dd_icon(group: str, filename: str, size: int = 24,
             title: str = "") -> html.Img:
    """本機代理的 Data Dragon 圖示。"""
    return html.Img(
        src=f"/dd-img/{group}/{quote(filename, safe='')}",
        width=size, height=size, title=title,
        className="champ-icon",
        style={"borderRadius": 5, "background": "#1b2740",
               "flex": "none"})


def _champ_icon(cid: str, size: int = 22) -> html.Img:
    return html.Img(src=f"/champ-img/{quote(cid)}.png",
                    width=size, height=size, className="champ-icon",
                    style={"borderRadius": 5, "flex": "none"})


def _winrate_span(rate: float) -> html.Span:
    """勝率著色：≥52 紅、≤48 藍（沿用參考站口語），其餘灰色。"""
    color = "#F05A6A" if rate >= 52 else ("#4C8DFF" if rate <= 48 else _C_MUTED)
    return html.Span(f"{rate:.0f}%", style={"color": color})


def _text_with_numbers(line: str, base_color: str = _C_ADJ) -> list:
    """把文案中的數值 token 染金，其餘維持內文色。"""
    parts = []
    for i, token in enumerate(_NUM_RE.split(line)):
        if not token:
            continue
        if i % 2 == 1:
            parts.append(html.Span(token, style={"color": _C_NUM}))
        else:
            parts.append(html.Span(token))
    return parts


def _section_panel(title: str, subtitle: str = "",
                   extra=None) -> html.Div:
    head_left = [html.Span("📄", style={"marginRight": 6}),
                 html.Span(title, style={"color": _C_SKILL,
                                         "fontWeight": 700,
                                         "fontSize": 13})]
    if subtitle:
        head_left.append(html.Span(f"｜{subtitle}",
                                   style={"color": _C_MUTED, "fontSize": 11}))
    return html.Div([
        html.Div([html.Span(head_left), extra],
                 style={"display": "flex", "justifyContent": "space-between",
                        "alignItems": "center", "marginBottom": 10}),
        html.Div(id="panel-body"),
    ], className="panel",
        style={"background": _C_PANEL, "border": f"1px solid {_C_BORDER}",
               "borderRadius": 10, "padding": "12px 14px",
               "marginTop": 14})


def _stat_line(icon: html.Img, games: int, win_rate: float,
               rate: float | None = None) -> html.Div:
    """「N 場 勝率%」一列；rate 為出裝率時加前綴。"""
    parts = [icon, html.Span(f" {games} 場",
                             style={"fontSize": 11, "marginLeft": 4}),
             html.Span(" ", style={"marginLeft": 6}),
             _winrate_span(win_rate)]
    if rate is not None:
        parts.insert(1, html.Span(f"{rate:.0f}%",
                                  style={"fontSize": 12,
                                         "color": "#E6ECF5",
                                         "marginRight": 6,
                                         "minWidth": 34,
                                         "display": "inline-block"}))
    return html.Div(parts,
                    style={"display": "flex", "alignItems": "center",
                           "margin": "5px 0", "minHeight": 26})


# ---------------------------------------------------------------------------
# 頁首：頭像／名稱／稱號／定位／加入日／歷代圖示
# ---------------------------------------------------------------------------
def _era_strip(eras: list[dict]) -> html.Div:
    """歷代圖示區：標題於左上，下方歷代頭像＋年份標籤等距排列。"""
    cols = []
    for era in eras:
        cols.append(html.Div([
            html.Img(src=era["src"], width=44, height=44,
                     className="champ-icon",
                     style={"borderRadius": 6}),
            html.Div(era["label"],
                     style={"fontSize": 10.5, "color": _C_MUTED,
                            "marginTop": 4, "textAlign": "center"}),
        ], style={"textAlign": "center"}, title=era["label"]))
    return html.Div(cols, style={"display": "flex",
                                 "alignItems": "flex-end", "gap": 20})


def _header(data: dict) -> html.Div:
    cid = data["cid"]
    tags = [html.Span(_CHAMP_TAG_ZH.get(t, t),
                      style={"fontSize": 12.5, "color": _C_MUTED,
                             "marginRight": 14})
            for t in data.get("tags", [])]
    meta_parts = tags
    if data.get("release_date"):
        meta_parts.append(html.Span(
            f'📅 加入日：{data["release_date"]}',
            style={"fontSize": 12.5, "color": _C_MUTED}))
    eras = []
    if data.get("release_date"):
        try:
            eras = champ_detail.era_icons(cid, data["release_date"])
        except Exception as exc:  # noqa: BLE001
            print(f"[champ_detail] 歷代圖示取得失敗 {cid}：{exc}")
    era_block = (html.Div([
        html.Div("歷代圖示",
                 style={"fontSize": 12, "color": _C_MUTED,
                        "marginBottom": 6}),
        _era_strip(eras),
    ], style={"flex": "none", "marginLeft": "14%",
              "alignSelf": "center"}) if eras else None)
    return html.Div([
        html.Div([
            html.Img(src=f"/champ-img/{quote(cid)}.png",
                     width=74, height=74, className="champ-icon",
                     style={"borderRadius": 10, "flex": "none"}),
            html.Div([
                html.Div([
                    html.Span(data["name"],
                              style={"fontSize": 22, "fontWeight": 700,
                                     "color": "#E6ECF5"}),
                    html.Span(f"  {data['title']}",
                              style={"fontSize": 13.5, "color": _C_GOLD}),
                ]),
                html.Div(meta_parts, style={"marginTop": 10}),
            ], style={"marginLeft": 16}),
        ], style={"display": "flex", "alignItems": "center",
                  "flex": "none", "minWidth": 0}),
        era_block,
    ], className="panel",
        style={"background": _C_PANEL,
               "border": f"1px solid {_C_BORDER}", "borderRadius": 10,
               "padding": "18px 22px", "display": "flex",
               "alignItems": "center"})


# ---------------------------------------------------------------------------
# 積分場出裝
# ---------------------------------------------------------------------------
def _rune_column(rows: list[dict], runes: dict) -> list:
    """符文排列（前三組）＋常對到英雄。"""
    blocks = []
    for row in rows:
        ids = row["key"]
        keystone = runes.get(ids[0], {})
        minors = [runes.get(rid) for rid in ids[1:] if rid]
        icons = [_dd_icon("perk", keystone.get("icon", ""), 30,
                          keystone.get("name", ""))]
        icons += [_dd_icon("perk", r.get("icon", ""), 18, r.get("name", ""))
                  for r in minors]
        blocks.append(html.Div([
            html.Div(icons, style={"display": "flex",
                                   "alignItems": "center", "gap": 3}),
            html.Div([
                html.Span(f'{row["games"]} 場',
                          style={"fontSize": 10.5, "color": _C_MUTED}),
                html.Span(" "),
                _winrate_span(row["win_rate"]),
            ], style={"marginLeft": 2, "marginTop": 2}),
        ], style={"margin": "6px 0"}))
    return [html.Div("符文排列（前三基石）",
                     style={"fontSize": 11.5, "color": _C_MUTED,
                            "marginBottom": 4}),
            *blocks]


def _matchup_rows(rows: list[dict]) -> list:
    chips = []
    for row in rows:
        chips.append(html.Span([
            _champ_icon(row["key"], 22),
        ], title=f'{row["key"]}｜{row["games"]} 場｜{row["win_rate"]:.0f}% 勝率',
            style={"display": "inline-flex", "margin": "0 5px 5px 0"}))
    return [html.Div("常對到",
                     style={"fontSize": 11.5, "color": _C_MUTED,
                            "margin": "10px 0 4px"}),
            html.Div(chips, style={"display": "flex", "flexWrap": "wrap"})]


def _summoner_rows(rows: list[dict], summoners: dict) -> list:
    out = [html.Div("召喚師技能",
                    style={"fontSize": 11.5, "color": _C_MUTED,
                           "marginBottom": 4})]
    for row in rows:
        a, b = row["key"]
        out.append(_stat_line(
            html.Span([_dd_icon("spell", summoners[a]["icon"], 22,
                                summoners[a]["name"]),
                       _dd_icon("spell", summoners[b]["icon"], 22,
                                summoners[b]["name"])],
                      style={"display": "inline-flex", "gap": 2}),
            row["games"], row["win_rate"], row["rate"]))
    return out


def _item_line(item_id: int, items: dict, games: int,
               win_rate: float | None = None, rate: float | None = None,
               size: int = 24) -> html.Div:
    meta = items.get(item_id, {})
    icon = _dd_icon("item", meta.get("icon", f"{item_id}.png"), size,
                    meta.get("name", str(item_id)))
    return _stat_line(icon, games, win_rate or 0.0, rate)


def _starter_rows(rows: list[dict], items: dict) -> list:
    out = [html.Div("起手裝",
                    style={"fontSize": 11.5, "color": _C_MUTED,
                           "margin": "10px 0 4px"})]
    for row in rows:
        icons = html.Span(
            [_dd_icon("item", items.get(i, {}).get("icon", f"{i}.png"),
                      22, items.get(i, {}).get("name", ""))
             for i in row["key"]],
            style={"display": "inline-flex", "gap": 2})
        out.append(_stat_line(icons, row["games"], row["win_rate"],
                              row["rate"]))
    return out


def _boots_rows(rows: list[dict], items: dict) -> list:
    out = [html.Div("鞋子",
                    style={"fontSize": 11.5, "color": _C_MUTED,
                           "margin": "10px 0 4px"})]
    for row in rows:
        out.append(_item_line(row["key"], items, row["games"],
                              row["win_rate"], row["rate"]))
    return out


def _other_rows(rows: list[dict], items: dict) -> list:
    out = [html.Div("剩餘適合裝備",
                    style={"fontSize": 11.5, "color": _C_MUTED,
                           "margin": "10px 0 4px"})]
    for row in rows:
        out.append(_item_line(row["key"], items, row["games"],
                              win_rate=None, rate=row["rate"]))
    return out


def _core_rows(rows: list[dict], items: dict,
               title: str = "核心裝（出裝率）") -> list:
    out = [html.Div(title,
                    style={"fontSize": 11.5, "color": _C_MUTED,
                           "marginBottom": 4})]
    for row in rows:
        meta = items.get(row["key"], {})
        out.append(html.Div([
            _dd_icon("item", meta.get("icon", f"{row['key']}.png"), 26,
                     meta.get("name", "")),
            html.Span(f'{row["rate"]:.0f}%',
                      style={"fontSize": 11.5, "marginLeft": 6,
                             "color": "#E6ECF5", "minWidth": 32,
                             "display": "inline-block"}),
        ], style={"display": "inline-flex", "flexDirection": "column",
                  "alignItems": "center", "margin": "0 10px 8px 0",
                  "width": 44},
            title=meta.get("name", "")))
    return [html.Div(out, style={"display": "flex", "flexWrap": "wrap"})]


def _path_rows(rows: list[dict], items: dict,
               title: str) -> list:
    out = [html.Div(title,
                    style={"fontSize": 11.5, "color": _C_MUTED,
                           "margin": "10px 0 4px"})]
    for i, row in enumerate(rows, 1):
        icons = [_dd_icon("item", items.get(pid, {}).get("icon",
                                                          f"{pid}.png"),
                          24, items.get(pid, {}).get("name", ""))
                 for pid in row["key"]]
        out.append(html.Div([
            html.Span(f"#{i}", style={"color": _C_MUTED, "fontSize": 11,
                                      "width": 22}),
            html.Span(icons, style={"display": "inline-flex", "gap": 3}),
            html.Span(f'{row["games"]} 場',
                      style={"fontSize": 10.5, "color": _C_MUTED,
                             "marginLeft": 8}),
            html.Span(" "),
            _winrate_span(row["win_rate"]),
        ], style={"display": "flex", "alignItems": "center",
                  "margin": "4px 0"}))
    return out


def _lane_summary(builds: dict) -> html.Div:
    total = builds["games"]
    parts = [html.Span(f'全部路線 {total} 場 ',
                       style={"color": "#E6ECF5", "fontSize": 12}),
             _winrate_span(builds["win_rate"])]
    lane_zh = common.POSITION_ZH
    for lane in builds["lanes"]:
        code = lane["key"]
        parts.append(html.Span(
            f'　{lane_zh.get(code, code)} {lane["games"]} 場',
            style={"color": _C_MUTED, "fontSize": 11.5}))
    return html.Div(parts, style={"marginBottom": 10})


def _synergy_rows(rows: list[dict]) -> list:
    """常配英雄列：一位英雄佔一列，頭像＋名稱靠左，場次／勝率靠右。"""
    out = []
    for row in rows:
        cid = row["key"]
        zh = (patch_data.load_champ_zh().get(cid) or {}).get("name", cid)
        out.append(html.Div([
            _champ_icon(cid, 22),
            html.Span(zh, style={"fontSize": 11.5, "lineHeight": 1.4,
                                 "marginLeft": 8, "color": "#C7D2E4",
                                 "minWidth": 80}),
            html.Span(f'{row["games"]} 場',
                      style={"fontSize": 11, "lineHeight": 1.4,
                             "color": _C_MUTED,
                             "flex": 1, "textAlign": "right",
                             "marginRight": 10}),
            _winrate_span(row["win_rate"]),
        ], style={"display": "flex", "alignItems": "center",
                  "width": "100%", "minWidth": 0,
                  "fontSize": 11.5, "lineHeight": 1.4,
                  "padding": "3px 0"}))
    return out


def _builds_panel(data: dict, maps: dict) -> html.Div:
    builds = data.get("builds")
    if not builds or not builds.get("games"):
        return _simple_panel("核心裝備與出裝流派",
                             "今年尚無足夠的單／雙排場次可聚合。")
    items, runes, summoners = maps["items"], maps["runes"], maps["summoners"]
    col1 = (_rune_column(builds["runes"], runes)
            + _matchup_rows(builds["matchups"]))
    col2 = (_summoner_rows(builds["summoners"], summoners)
            + _starter_rows(builds["starters"], items)
            + _boots_rows(builds["boots"], items)
            + _other_rows(builds["others"], items))
    col3 = (_core_rows(builds["core"], items)
            + _path_rows(builds["paths"], items,
                         "主要出裝流派（購買順序＋勝率）"))
    recent = builds.get("recent")
    if recent and recent.get("games"):
        label = builds.get("recent_label", "近兩版")
        col4 = (_core_rows(recent["core"], items,
                           f"近兩版核心裝（{label}）")
                + _path_rows(recent["paths"], items,
                             f"近兩版主要出裝流派（{label}）"))
    else:
        col4 = [html.Div("近兩版場次不足",
                         style={"color": _C_MUTED, "fontSize": 11.5})]
    panel = _section_panel(
        f'核心裝備與出裝流派（積分｜{builds["year"]} 年）',
        "資料來源：職業選手單／雙排對戰")
    panel.children[-1].children = [
        _lane_summary(builds),
        html.Div([
            html.Div(col1),
            html.Div(col2),
            html.Div(col3),
            html.Div(col4),
        ], style={"display": "grid",
                  "gridTemplateColumns": "1.1fr 1fr 1.2fr 1.2fr",
                  "gap": 18, "alignItems": "start"}),
        html.Div("常配英雄（前五）",
                 style={"fontSize": 11.5, "color": _C_MUTED,
                        "margin": "14px 0 6px"}),
        html.Div(_synergy_rows(builds["synergies"]),
                 style={"display": "flex", "flexDirection": "column",
                        "maxWidth": 560}),
    ]
    return panel


def _simple_panel(title: str, note: str) -> html.Div:
    panel = _section_panel(title)
    panel.children[-1].children = html.Div(
        note, style={"color": _C_MUTED, "fontSize": 12})
    return panel


# ---------------------------------------------------------------------------
# 技能說明
# ---------------------------------------------------------------------------
def _skill_block(slot: str, name: str, cooldown: str, cost: str,
                 lines: list[str]) -> html.Div:
    """單一技能／被動：藍色標題列＋金色數值內文。"""
    header_parts = [html.Span(slot, style={"color": _C_SKILL,
                                           "fontWeight": 700}),
                    html.Span(f"｜{name}",
                              style={"color": _C_SKILL, "fontWeight": 700,
                                     "marginLeft": 2})]
    if cooldown and cooldown != "0":
        header_parts.append(html.Span(f"　冷卻 {cooldown}",
                                      style={"color": _C_GOLD,
                                             "fontSize": 10.5}))
    if cost and cost != "0":
        header_parts.append(html.Span(f"　消耗 {cost}",
                                      style={"color": _C_GOLD,
                                             "fontSize": 10.5}))
    body = [html.Div(_text_with_numbers(line),
                     style={"fontSize": 11.5, "lineHeight": 1.65,
                            "marginTop": 2})
            for line in lines]
    return html.Div([
        html.Div(header_parts,
                 style={"fontSize": 12, "margin": "12px 0 3px"}),
        *body,
    ])


def _skills_panel(data: dict) -> html.Div:
    skills = data.get("skills")
    if not skills:
        return _simple_panel("技能說明", "目前版本技能文本尚未快取。")
    panel = _section_panel("技能說明",
                           f'{data["name"]}｜{data.get("title", "")}'
                           "｜目前版本數值")
    blocks = []
    passive = skills["passive"]
    blocks.append(_skill_block("被動", passive["name"], "", "",
                               passive.get("lines", [])))
    for spell in skills["spells"]:
        blocks.append(_skill_block(spell["slot"], spell["name"],
                                   spell.get("cooldown", ""),
                                   spell.get("cost", ""),
                                   spell.get("lines", [])))
    panel.children[-1].children = blocks
    return panel


# ---------------------------------------------------------------------------
# 歷年改動
# ---------------------------------------------------------------------------
def _history_block(patch_group: dict) -> html.Div:
    rows = []
    for group in patch_group.get("groups", []):
        title = group.get("title") or "基礎能力值"
        rows.append(html.Div(title, style={
            "color": _C_SKILL, "fontSize": 11.5, "fontWeight": 700,
            "margin": "8px 0 2px"}))
        for change in group["changes"]:
            text = change["text"].replace(" ：", "：").strip()
            rows.append(html.Div(
                text,
                style={"color": _DIR_COLOR.get(change["dir"], _C_ADJ),
                       "fontSize": 11, "lineHeight": 1.6,
                       "wordBreak": "break-word"}))
    return html.Div([
        html.Span(patch_group["patch"],
                  style={"background": "rgba(224,138,60,.14)",
                         "color": _C_PATCH, "fontSize": 11,
                         "fontWeight": 700, "borderRadius": 4,
                         "padding": "2px 8px", "display": "inline-block",
                         "marginTop": 10}),
        *rows,
    ])


def _history_panel(data: dict) -> html.Div:
    history = data.get("history")
    slug = _SLUG_OVERRIDE.get(data["cid"], data["cid"].lower())
    detail_link = html.A("數據詳情 →",
                         href=("https://www.leagueoflegends.com/zh-tw/"
                               f"champions/{slug}/"),
                         target="_blank", rel="noopener noreferrer",
                         style={"color": _C_SKILL, "fontSize": 11})
    if not history or not history.get("patches"):
        panel = _section_panel(f'{data["name"]} 歷年改動', extra=detail_link)
        panel.children[-1].children = html.Div(
            "官方公告快取中沒有此英雄的改動紀錄。",
            style={"color": _C_MUTED, "fontSize": 12})
        return panel
    count = len(history["patches"])
    panel = _section_panel(f'{data["name"]} 歷年改動',
                           f"{count} 個版本", extra=detail_link)
    children = []
    if data.get("release_date"):
        children.append(html.Span(
            f'● 加入 {data["release_date"][:4]} 年',
            style={"color": _C_BUFF, "fontSize": 11}))
    children += [_history_block(p) for p in history["patches"]]
    panel.children[-1].children = children
    return panel


# ---------------------------------------------------------------------------
# 頁面組裝
# ---------------------------------------------------------------------------
def layout(champion_id: str | None = None, **_kwargs):
    cid = champion_id
    try:
        data = champ_detail.detail(cid)
    except Exception as exc:  # noqa: BLE001
        data = None
        print(f"[champ_detail] 聚合失敗 {cid}：{exc}")
    if not data:
        return html.Div([
            dcc.Link("← 回英雄圖鑑", href="/compendium",
                     style={"color": _C_SKILL, "fontSize": 12}),
            common.empty_state("找不到此英雄",
                               f"英雄 ID：{cid}，請由圖鑑英雄頁點進。"),
        ], className="page")
    try:
        maps = champ_detail._meta_maps()  # noqa: SLF001
    except Exception:  # noqa: BLE001
        maps = {"items": {}, "runes": {}, "summoners": {}}
    return html.Div([
        dcc.Link("← 回英雄圖鑑", href="/compendium",
                 style={"display": "inline-block", "color": _C_SKILL,
                        "fontSize": 12, "margin": "2px 0 8px"}),
        _header(data),
        _builds_panel(data, maps),
        _skills_panel(data),
        _history_panel(data),
    ], className="page", style={"paddingBottom": 40})
