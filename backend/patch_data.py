"""官方版本更新公告（zh-tw）抓取、解析與本地快取。

資料源：Riot 官方版本更新公告 HTML（無須 API Key），逐版本禮貌抓取，
以 BeautifulSoup 解析「英雄／道具」兩個章節，輸出結構化改動明細：

- 每則改動保留原始文案（label＋⇒ 新值），BUFF／NERF／調整由數值方向判定
  （冷卻、消耗等反向屬性往下調視為 BUFF；新舊值無法對齊視為調整）
- 英雄以官方連結內的 DDragon ID 為鍵，名稱直接取繁中頁面
- 道具以公告圖片網址中的道具 ID 為鍵，圖示走既有 /dd-img/item 代理

另抓取 Data Dragon zh_tw championFull.json，提供英雄中文名、定位標籤
與技能名稱（英雄頁名稱與 tooltip 使用）。

僅供離線快取讀取的介面：load_notes()／load_champ_zh()；
手動更新：python -m backend.patch_data
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from datetime import datetime
from functools import lru_cache

from bs4 import BeautifulSoup

from backend import config
from backend.pipeline.common import (HttpRequestError, atomic_write_text,
                                     get_logger, http_get)

logger = get_logger("patch_data")

# 只解析這兩個章節（其餘：經典模式／隨機單中／競技場／系統／造型…）
_WANTED_SECTIONS = ("英雄", "道具")
# 改動方向箭號（官方頁面為 ⇒，一併容許 →）
_ARROW_RE = re.compile(r"\s*[⇒→]\s*")
_NUM_RE = re.compile(r"[-−+]?\d+(?:\.\d+)?")
_WS_RE = re.compile(r"\s+")
# 數值向下反而對玩家有利的屬性關鍵字（出現在標的名稱即反向判定）
_INVERSE_KEYWORDS = ("冷卻", "消耗", "魔力", "法力", "能量", "延遲",
                     "飛行", "減傷", "減免", "降速")
# 基礎屬性「基礎值＋每級成長」同列一升一降時，官方立場以基礎值為準
_BASE_STAT_KEYWORDS = ("生命", "回血", "回魔", "魔力", "護甲", "護防",
                       "魔防", "物攻")
# 道具圖片網址中的 DDragon 道具 ID
_ITEM_ID_RE = re.compile(r"/item/(\d+)\.png")
# 英雄官方連結中的 DDragon ID（如 aurelionsol）
_CHAMP_HREF_RE = re.compile(r"/champions/([a-z0-9'_.\-]+)/?$")

_BUFF, _NERF, _ADJ, _NOTE = "buff", "nerf", "adj", "note"


# ---------------------------------------------------------------------------
# 改動方向判定
# ---------------------------------------------------------------------------
def _to_float(token: str) -> float | None:
    """把文案中的數字權杖轉 float；全形減號與正號先正規化。"""
    token = token.replace("−", "-").replace("+", "")
    try:
        return float(token)
    except ValueError:
        return None


def classify_change(label: str, before: str, after: str) -> str:
    """比對新舊數值序列判定 buff／nerf；無法對齊或漲跌互見則為調整。

    label：屬性名稱（用於識別冷卻／消耗等反向屬性）。
    """
    old_nums = [v for v in (_to_float(t) for t in _NUM_RE.findall(before))
                if v is not None]
    new_nums = [v for v in (_to_float(t) for t in _NUM_RE.findall(after))
                if v is not None]
    if not old_nums or not new_nums:
        return _ADJ
    # 一側為單一數值、另一側為各級數列時，視為全級同值再比對
    if len(old_nums) == 1 and len(new_nums) > 1:
        old_nums = old_nums * len(new_nums)
    elif len(new_nums) == 1 and len(old_nums) > 1:
        new_nums = new_nums * len(old_nums)
    if len(old_nums) != len(new_nums):
        return _ADJ
    inverse = any(kw in label for kw in _INVERSE_KEYWORDS)
    moves = set()
    for old, new in zip(old_nums, new_nums):
        if new > old:
            moves.add("up")
        elif new < old:
            moves.add("down")
    if not moves:
        return _ADJ
    if len(moves) > 1:
        # 又升又降：基礎屬性（基礎值＋每級成長）以首個數字方向為準
        if any(kw in label for kw in _BASE_STAT_KEYWORDS):
            going_up = new_nums[0] > old_nums[0]
            if new_nums[0] == old_nums[0]:
                return _ADJ
        else:
            return _ADJ
    else:
        going_up = moves.pop() == "up"
    if inverse:
        return _NERF if going_up else _BUFF
    return _BUFF if going_up else _NERF


# ---------------------------------------------------------------------------
# HTML 解析
# ---------------------------------------------------------------------------
def _clean_text(node) -> str:
    """節點文字壓成單行。"""
    return _WS_RE.sub(" ", node.get_text(" ", strip=True)).strip()


def _parse_change_li(li) -> dict:
    """解析單條改動 <li>：標的名稱、完整文案、方向。"""
    text = _clean_text(li)
    strong = li.find("strong")
    label = strong.get_text(strip=True) if strong else ""
    direction = _NOTE
    arrow = _ARROW_RE.search(text)
    if arrow:
        left, after = text[:arrow.start()], text[arrow.end():].strip()
        before = left
        if label and label in before:
            before = before.split(label, 1)[1]
        before = before.lstrip("：: ").strip()
        direction = classify_change(label, before, after)
    return {"label": label, "text": text, "dir": direction}


def _parse_block(block, section: str) -> dict | None:
    """解析單一英雄／道具區塊為結構化條目。"""
    h3 = block.find("h3")
    if h3 is None:
        return None
    name = _clean_text(h3)
    if not name:
        return None

    # 依 h4 技能標題分組；道具沒有 h4 時歸入空標題群組
    groups: list[dict] = []
    current: dict | None = None
    for node in block.find_all(["h4", "ul", "ol"], recursive=True):
        if node.name == "h4":
            current = {"title": _clean_text(node), "changes": []}
            groups.append(current)
        else:
            lis = node.find_all("li", recursive=False) or node.find_all("li")
            changes = [_parse_change_li(li) for li in lis]
            changes = [c for c in changes if c["text"]]
            if changes:
                if current is None:
                    current = {"title": "", "changes": []}
                    groups.append(current)
                current["changes"].extend(changes)
    groups = [g for g in groups if g["changes"]]
    if not groups:
        return None

    if section == "英雄":
        link = block.select_one("a[href*='/champions/']")
        if link is None:
            return None
        match = _CHAMP_HREF_RE.search(link.get("href", ""))
        if not match:
            return None
        return {"kind": "champion", "id": match.group(1), "name": name,
                "groups": groups}

    # 道具：ID 取自公告內嵌的 DDragon 道具圖
    img = block.find("img", src=_ITEM_ID_RE)
    if img is None:
        return None
    item_id = _ITEM_ID_RE.search(img["src"]).group(1)
    return {"kind": "item", "id": item_id, "name": name, "groups": groups}


def _parse_old_format(soup: BeautifulSoup) -> list[dict]:
    """解析 26.09 以前的扁平格式：h3 切條目、h4 切技能群、ul 接改動。"""
    entries: list[dict] = []
    entry: dict | None = None
    group: dict | None = None
    section = None
    last_item_id = None

    def flush():
        nonlocal entry, group
        if entry is not None:
            entry["groups"] = [g for g in entry["groups"] if g["changes"]]
            if entry["groups"]:
                entries.append(entry)
        entry, group = None, None

    for el in soup.find_all(["h2", "h3", "h4", "ul", "ol"]):
        if el.name == "h2":
            flush()
            section = _clean_text(el)
            continue
        if section not in _WANTED_SECTIONS:
            continue
        if el.name == "h3":
            flush()
            link = el.select_one("a[href*='/champions/']")
            if link is not None:
                match = _CHAMP_HREF_RE.search(link.get("href", ""))
                if match:
                    entry = {"kind": "champion", "id": match.group(1),
                             "name": _clean_text(el), "groups": []}
            elif section == "道具":
                # 26 季舊格式道具圖示在 h3 之前，向回找最近的 DDragon 道具圖
                img = el.find_previous("img", src=_ITEM_ID_RE)
                item_id = (_ITEM_ID_RE.search(img["src"]).group(1)
                           if img is not None else None)
                if item_id and item_id != last_item_id:
                    last_item_id = item_id
                    entry = {"kind": "item", "id": item_id,
                             "name": _clean_text(el), "groups": []}
            continue
        if entry is None:
            continue
        if el.name == "h4":
            # 25 季以前道具章節以 h4 為道具名（英雄章節 h4 為技能群組）
            if section == "道具":
                flush()
                img = el.find_next("img", src=_ITEM_ID_RE)
                item_id = (_ITEM_ID_RE.search(img["src"]).group(1)
                           if img is not None else None)
                if item_id and item_id != last_item_id:
                    last_item_id = item_id
                    entry = {"kind": "item", "id": item_id,
                             "name": _clean_text(el), "groups": []}
                continue
            title = _clean_text(el)
            group = {"title": title, "changes": []} if title else None
            if group is not None:
                entry["groups"].append(group)
        else:
            lis = el.find_all("li", recursive=False) or el.find_all("li")
            changes = [c for c in (_parse_change_li(li) for li in lis)
                       if c["text"]]
            if not changes:
                continue
            if group is None:
                group = {"title": "", "changes": []}
                entry["groups"].append(group)
            group["changes"].extend(changes)
    flush()
    return entries


def _section_of(node) -> str | None:
    """回傳該節點所屬章節：文件中最近的前一個 h2（minified HTML 亦適用）。"""
    previous = node.find_all_previous("h2")
    return _clean_text(previous[0]) if previous else None


def _parse_system_squads(soup: BeautifulSoup) -> list[dict]:
    """解析「系統」章節的名單式改動（一組數值套用多名英雄）。

    例：26.16 ADC 魔防，一個 li 列出受影響英雄、後續 li 為共用改動；
    列首帶特定英雄名的 li（如「崔絲塔娜魔防」）視為該英雄專屬覆蓋。
    """
    squads = []
    for li in soup.find_all("li"):
        strong = li.find("strong")
        if strong is None:
            continue
        label = strong.get_text(strip=True)
        if "受影響" not in label or "英雄" not in label:
            continue
        if _section_of(li) != "系統":
            continue
        text = _clean_text(li)
        tail = text.split("：", 1)[-1].split(":", 1)[-1].strip()
        names = [n for n in re.split(r"[、,，\s]+", tail) if n]
        if not names:
            continue
        changes = []
        for sib in li.find_next_siblings("li"):
            change = _parse_change_li(sib)
            if change["text"]:
                changes.append(change)
        if changes:
            squads.append({"kind": "squad", "names": names,
                           "changes": changes})
    return squads


def parse_patch_html(html: str, patch: str) -> list[dict]:
    """解析單一版本公告，回傳英雄／道具條目（含該版本分組）。

    26.10 起為 div.patch-change-block 卡片格式；更早版本為扁平格式。
    """
    soup = BeautifulSoup(html, "html.parser")
    blocks = soup.find_all("div", class_="patch-change-block")
    if blocks:
        # 新格式：以 h2 章節歸屬各卡片
        section_of_block: dict[int, str] = {}
        section = None
        for el in soup.find_all(["h2", "div"]):
            classes = el.get("class") or []
            if el.name == "h2":
                section = _clean_text(el)
            elif "patch-change-block" in classes:
                section_of_block[id(el)] = section
        entries = []
        for block in blocks:
            block_section = section_of_block.get(id(block))
            if block_section not in _WANTED_SECTIONS:
                continue
            parsed = _parse_block(block, block_section)
            if parsed:
                parsed["patch"] = patch
                entries.append(parsed)
        entries.extend(_parse_system_squads(soup))
        return entries

    entries = _parse_old_format(soup)
    entries.extend(_parse_system_squads(soup))
    for entry in entries:
        entry["patch"] = patch
    return entries


# ---------------------------------------------------------------------------
# 抓取與快取
# ---------------------------------------------------------------------------
def _fetch_zh_champions(version: str) -> dict:
    """zh_tw 英雄全集：中文名、稱號、定位標籤與完整技能文本。

    除名稱外保留被動與 Q/W/E/R 的 tooltip／冷卻／消耗／數列等欄位，
    供英雄詳情頁「技能說明」區塊渲染（目前版本數值）。
    """
    data = http_get(
        config.DDRAGON_ZH_CHAMPION_URL.format(version=version)).json()["data"]
    result = {}
    for cid, info in data.items():
        passive = info.get("passive", {})
        spell_details = []
        for slot, spell in enumerate(info.get("spells", [])[:4]):
            spell_details.append({
                "slot": "QWER"[slot],
                "spell_id": spell.get("id", ""),
                "name": spell.get("name", ""),
                "icon": spell.get("image", {}).get("full", ""),
                "tooltip": spell.get("tooltip", ""),
                "description": spell.get("description", ""),
                "cooldown": spell.get("cooldownBurn", ""),
                "cost": spell.get("costBurn", ""),
                "range": spell.get("rangeBurn", ""),
                "resource": spell.get("resource", ""),
                "effect_burn": spell.get("effectBurn", []),
                "vars": spell.get("vars", []),
            })
        result[cid] = {
            "name": info.get("name", ""),
            "title": info.get("title", ""),
            "tags": info.get("tags", []),
            "passive": passive.get("name", ""),
            "passive_desc": passive.get("description", ""),
            "passive_icon": passive.get("image", {}).get("full", ""),
            "spells": [s["name"] for s in spell_details],
            "spell_details": spell_details,
        }
    return result


# 公告 JSON-LD 中的發布時間（ISO，例 2026-06-23T18:00:00.000Z）
_DATE_PUBLISHED_RE = re.compile(r'"datePublished"\s*:\s*"([^"]+)"')


def _extract_published(html: str) -> str | None:
    """由公告頁內嵌 JSON-LD 取出發布日期（YYYY-MM-DD）；失敗回 None。"""
    match = _DATE_PUBLISHED_RE.search(html)
    if not match:
        return None
    return match.group(1)[:10]


def _merge_entries(merged: dict, entries: list[dict]) -> None:
    """把單一版本條目併入以 ID 為鍵的累積表（新版本插在最前）。"""
    for entry in entries:
        key = (entry["kind"], entry["id"])
        slot = merged.get(key)
        new_groups = entry["groups"]
        if slot is None:
            merged[key] = {**{k: v for k, v in entry.items()
                              if k not in ("groups", "patch")},
                           "patches": [{"patch": entry["patch"],
                                        "groups": new_groups}]}
            continue
        same_patch = next((p for p in slot["patches"]
                           if p["patch"] == entry["patch"]), None)
        if same_patch is None:
            # 抓取順序由新到舊，較舊版本追加在後
            slot["patches"].append({"patch": entry["patch"],
                                    "groups": new_groups})
        else:
            # 同版本（如個人改動＋系統名單展開）群組併存
            same_patch["groups"].extend(new_groups)


def _change_with_label(change: dict, new_label: str) -> dict:
    """以覆蓋後的屬性名重建改動文案並重算方向。"""
    values = change["text"]
    if "：" in values:
        values = values.split("：", 1)[1]
    elif ":" in values:
        values = values.split(":", 1)[1]
    direction = _NOTE
    arrow = _ARROW_RE.search(values)
    if arrow:
        direction = classify_change(new_label, values[:arrow.start()],
                                    values[arrow.end():])
    return {"label": new_label,
            "text": f"{new_label}：{values.strip()}", "dir": direction}


# 官方連結 slug 與 DDragon ID 不單純是大小寫差異者
_SLUG_ALIASES = {"wukong": "MonkeyKing", "renataglasc": "Renata"}


def _slug_to_cid_map(champions: dict) -> dict[str, str]:
    """官方公告 slug（全小寫）→ DDragon 正式 ID（對齊頭像檔名）。"""
    mapping = {cid.lower(): cid for cid in champions}
    mapping.update(_SLUG_ALIASES)
    return mapping


def _expand_squads(squad_entries: list[dict],
                   name_to_id: dict[str, str]) -> list[dict]:
    """把系統名單式改動展開成個別英雄條目（群組統一歸「基礎能力值」）。"""
    known = sorted(name_to_id, key=len, reverse=True)
    expanded: list[dict] = []
    for squad in squad_entries:
        generic, overrides = [], {}
        for change in squad["changes"]:
            hit = next((n for n in known
                        if change["label"].startswith(n)
                        and len(change["label"]) > len(n)), None)
            if hit is None:
                generic.append(change)
            else:
                sub_label = change["label"][len(hit):].strip()
                overrides.setdefault(hit, []).append(
                    _change_with_label(change, sub_label))
        for name in squad["names"]:
            cid = name_to_id.get(name)
            if cid is None:
                logger.warning("系統名單找不到英雄 ID：%s", name)
                continue
            changes = list(generic)
            changes.extend(overrides.get(name, []))
            if changes:
                expanded.append(
                    {"kind": "champion", "id": cid, "name": name,
                     "groups": [{"title": "基礎能力值",
                                 "changes": changes}]})
        for name, changes in overrides.items():
            cid = name_to_id.get(name)
            if cid is None or name in squad["names"]:
                continue
            expanded.append(
                {"kind": "champion", "id": cid, "name": name,
                 "groups": [{"title": "基礎能力值", "changes": changes}]})
    return expanded


def _discover_minors(season: str) -> list[int] | None:
    """從官方遊戲更新總覽頁取得本年度有公告的全部補丁號（新→舊）。

    官方網址早期編號不補零（26-4）、兩位數原樣（26-10）；以總覽頁
    實際連結為準可避開不存在版本的 404。
    """
    try:
        html = http_get(config.PATCH_INDEX_URL).text
    except HttpRequestError as exc:
        logger.warning("版本總覽頁抓取失敗：%s", exc)
        return None
    minors = sorted({int(m) for s, m in
                     re.findall(r"league-of-legends-patch-(\d+)-(\d+)-notes",
                                html) if s == season}, reverse=True)
    return minors or None


def _note_url(season: str, minor: int, legacy: bool = False) -> str:
    """組官方公告網址：補丁號不補零（官方實際 slug 規則）。

    26 季起為 league-of-legends-patch-XX-N；更早季為舊式 patch-XX-N。
    """
    template = (config.PATCH_NOTE_LEGACY_URL_TEMPLATE if legacy
                else config.PATCH_NOTE_URL_TEMPLATE)
    return template.format(season=season, minor=minor)


def _season_minors(season: str) -> list[int]:
    """該季要抓取的補丁號（新→舊）。

    當季以官方總覽頁實際連結為準（避開不存在版本的 404）；
    舊季無總覽頁可翻，直接循序探 1..26，404 記為 skipped 不中止。
    """
    discovered = _discover_minors(season)
    if discovered:
        return discovered
    return list(range(26, 0, -1))


def _season_of(patch: str) -> str:
    """版本標籤的季號（'26.13' → '26'）。"""
    return patch.split(".", 1)[0]


def build(seasons: list[str] | None = None, *, quiet: bool = False) -> dict:
    """抓取各季版本公告並輸出快取 payload（新季→舊季，各季新→舊）。

    採增量合併：本次未請求的季別沿用舊快取，請求中的季別整季重抓覆蓋。
    """
    if seasons is None:
        current = int(config.PATCH_SEASON)
        seasons = [str(s) for s in range(current, current - 2, -1)]
    season_set = set(seasons)
    # 與總覽頁請求保持禮貌間隔
    time.sleep(max(0.6, config.PATCH_REQUEST_INTERVAL
                   + random.uniform(-0.4, 0.4)))

    # 繁中英雄資料先取：系統名單展開需以繁中名對回 DDragon ID
    dd_version = http_get(config.DDRAGON_VERSIONS_URL).json()[0]
    try:
        champions = _fetch_zh_champions(dd_version)
    except HttpRequestError as exc:
        logger.warning("繁中英雄資料取得失敗（版本頁仍可運作）：%s", exc)
        champions = {}
    name_to_id = {info["name"]: cid for cid, info in champions.items()
                  if info.get("name")}
    slug_to_cid = _slug_to_cid_map(champions)
    unmatched_slugs = []

    # 載入舊快取：保留非本次季別的版本與改動條目
    merged: dict[tuple[str, str], dict] = {}
    released: dict[str, str] = {}
    kept_patches, kept_skipped, kept_failed = [], [], []
    prior = load_notes()
    if prior:
        for entry in prior.get("entries", []):
            kept_groups = [g for g in entry.get("patches", [])
                           if _season_of(g["patch"]) not in season_set]
            if kept_groups:
                slot = {k: v for k, v in entry.items() if k != "patches"}
                slot["patches"] = kept_groups
                merged[(entry["kind"], entry["id"])] = slot
        released = {p: d for p, d in prior.get("released", {}).items()
                    if _season_of(p) not in season_set}
        kept_patches = [p for p in prior.get("patches", [])
                        if _season_of(p) not in season_set]
        kept_skipped = [p for p in prior.get("skipped", [])
                        if _season_of(p) not in season_set]
        kept_failed = [p for p in prior.get("failed", [])
                       if _season_of(p) not in season_set]
    fetched, skipped, failed = [], [], []
    for season in seasons:
        # 當季走總覽頁新式 slug；舊季走舊式 slug 並循序探號
        legacy = season != config.PATCH_SEASON
        # 年份制（≥20 季）補零兩位數；經典制（如 14.1）不補零
        pad_minor = int(season) >= 20
        for minor in _season_minors(season):
            time.sleep(max(0.6, config.PATCH_REQUEST_INTERVAL
                           + random.uniform(-0.4, 0.4)))
            patch = (f"{season}.{minor:02d}" if pad_minor
                     else f"{season}.{minor}")
            url = _note_url(season, minor, legacy=legacy)
            try:
                html = http_get(url).text
            except HttpRequestError as exc:
                # 部分版本無獨立公告（404）；其餘錯誤記錄後續跳，不中止整輪
                (skipped if "404" in str(exc) else failed).append(patch)
                logger.warning("版本 %s 抓取失敗：%s", patch, exc)
                continue
            published = _extract_published(html)
            if published:
                released[patch] = published
            parsed = parse_patch_html(html, patch)
            squads = [e for e in parsed if e["kind"] == "squad"]
            entries = [e for e in parsed if e["kind"] != "squad"]
            # 官方 slug 統一轉成 DDragon 正式 ID（頭像檔名與牆資料共用）
            for entry in entries:
                if entry["kind"] != "champion":
                    continue
                cid = slug_to_cid.get(entry["id"].lower())
                if cid:
                    entry["id"] = cid
                elif entry["id"] not in unmatched_slugs:
                    unmatched_slugs.append(entry["id"])
                    logger.warning("版本 %s 英雄 slug 無法對回 DDragon ID：%s",
                                   patch, entry["id"])
            for entry in _expand_squads(squads, name_to_id):
                entry["patch"] = patch
                entries.append(entry)
            _merge_entries(merged, entries)
            fetched.append(patch)
            if not quiet:
                print(f"[patch_data] {patch}：{len(entries)} 個條目")
    def _patch_key(label: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in label.split("."))
        except ValueError:
            return (0,)

    all_patches = sorted(set(kept_patches + fetched),
                         key=_patch_key, reverse=True)
    # 跨季增量合併後，統一確保各條目版本群組維持新→舊
    for slot in merged.values():
        slot["patches"].sort(key=lambda g: _patch_key(g["patch"]),
                             reverse=True)
    all_seasons = sorted({_season_of(p) for p in all_patches},
                         key=lambda s: int(s), reverse=True)
    payload = {
        "generated_at": datetime.now().date().isoformat(),
        "season": all_seasons[0] if all_seasons else seasons[0],
        "seasons": all_seasons,
        "version": dd_version,
        "latest": all_patches[0] if all_patches else None,
        "patches": all_patches,
        "released": released,
        "skipped": kept_skipped + skipped,
        "failed": kept_failed + failed,
        "entries": list(merged.values()),
        "champions": champions,
    }
    atomic_write_text(config.PATCH_NOTES_JSON,
                      json.dumps(payload, ensure_ascii=False))
    logger.info("版本快取已更新：%d 版本、%d 個英雄／道具",
                len(fetched), len(merged))
    return payload


@lru_cache(maxsize=1)
def load_notes() -> dict | None:
    """讀取版本快取；不存在或毀損回 None（不自動連網，避免拖慢頁面）。"""
    if not config.PATCH_NOTES_JSON.exists():
        return None
    try:
        return json.loads(config.PATCH_NOTES_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("版本快取無法讀取：%s", exc)
        return None


@lru_cache(maxsize=1)
def load_champ_zh() -> dict:
    """DDragon ID → 繁中英雄資訊；無快取時回空 dict（呼叫端降級英文）。"""
    notes = load_notes()
    return notes.get("champions", {}) if notes else {}


def refresh_champions() -> dict:
    """只刷新冠軍全集（zh_TW championFull），保留公告與改動條目。"""
    prior = load_notes() or {}
    dd_version = http_get(config.DDRAGON_VERSIONS_URL).json()[0]
    champions = _fetch_zh_champions(dd_version)
    payload = {**prior,
               "generated_at": datetime.now().date().isoformat(),
               "version": dd_version,
               "champions": champions}
    atomic_write_text(config.PATCH_NOTES_JSON,
                      json.dumps(payload, ensure_ascii=False))
    load_notes.cache_clear()
    load_champ_zh.cache_clear()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取官方版本更新公告")
    parser.add_argument("--seasons", default=None,
                        help="賽季清單（逗號分隔，如 26,25,14；預設近三季）")
    parser.add_argument("--champions-only", action="store_true",
                        help="只刷新 zh_TW 英雄全集，不重抓公告")
    args = parser.parse_args()
    if args.champions_only:
        payload = refresh_champions()
        print(f"完成：英雄全集 {len(payload['champions'])} 隻")
        return 0
    seasons = ([s.strip() for s in args.seasons.split(",") if s.strip()]
               if args.seasons else None)
    payload = build(seasons)
    print(f"完成：抓取 {len(payload['patches'])} 版本、"
          f"{len(payload['entries'])} 個條目；跳過 {len(payload['skipped'])}、"
          f"失敗 {len(payload['failed'])}")
    if payload["failed"]:
        print("失敗版本：", "、".join(payload["failed"]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
