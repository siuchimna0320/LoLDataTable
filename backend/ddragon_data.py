"""Data Dragon 靜態資料：版本清單、道具、召喚師技能、符文。

資料源皆為 Riot 官方公開 JSON（無須 API Key）：
- 整包圖鑑 JSON 首次存取時下載、裁剪後快取至 assets/ddragon/；
- 圖示由前端經本機 Flask 路由請求，伺服器端按需下載一次並落盤，
  瀏覽器不需直連外網（NFR-7）；
- 所有網路請求走 http_get（內含重試），離線時回退最近快取。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from backend import config
from backend.pipeline.common import (atomic_write_bytes, atomic_write_text,
                                     get_logger, http_get)

logger = get_logger("ddragon_data")

# 圖示群組 →（URL 模板、快取子目錄）
_ICON_GROUPS = {
    "item": (config.DDRAGON_ITEM_ICON_URL, "items"),
    "spell": (config.DDRAGON_SPELL_ICON_URL, "spells"),
    "perk": (config.DDRAGON_PERK_ICON_URL, "perks"),
}
_ICON_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-/]+$")


def _strip_html(text: str | None) -> str:
    """移除描述文字中的 HTML 標籤與多餘空白。"""
    if not text:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()


def _synced_version() -> str | None:
    """優先沿用英雄頭像同步時記錄的版本，保持圖鑑與頭像一致。"""
    if config.DD_META_PATH.exists():
        try:
            meta = json.loads(config.DD_META_PATH.read_text(encoding="utf-8"))
            return meta.get("version")
        except json.JSONDecodeError:
            return None
    return None


def synced_version() -> str:
    """對外公開：同步版本，無快取時取線上最新版。"""
    return _synced_version() or _online_json(versions=True)[0]


def _online_json(url_template: str | None = None, version: str | None = None,
                 *, versions: bool = False):
    """具重試的 JSON 下載。"""
    url = config.DDRAGON_VERSIONS_URL if versions else url_template.format(
        version=version)
    return http_get(url).json()


def _trim_items(raw: dict) -> list[dict]:
    """裁剪道具 JSON：只留峽谷可用、商店可購、有合成價值的項目。"""
    items = []
    for item_id, info in raw.get("data", {}).items():
        gold = info.get("gold", {})
        maps = info.get("maps", {})
        if not maps.get("11"):
            continue
        if gold.get("in_store") is False or not gold.get("purchasable", False):
            continue
        if int(gold.get("total", 0)) <= 0:
            continue
        items.append({
            "id": int(item_id),
            "name": info.get("name", ""),
            "icon": info.get("image", {}).get("full", ""),
            "gold": int(gold.get("total", 0)),
            "tags": info.get("tags", []),
            "plaintext": info.get("plaintext", ""),
            "stats": _strip_html(info.get("description", ""))[:280],
        })
    return sorted(items, key=lambda x: x["gold"], reverse=True)


def _trim_summoners(raw: dict) -> list[dict]:
    """裁剪召喚師技能 JSON：只留經典召喚峽谷可用，並依顯示名去重。"""
    spells, seen = [], set()
    for info in raw.get("data", {}).values():
        modes = info.get("modes", [])
        name = info.get("name", "")
        if "CLASSIC" not in modes or name in seen:
            continue
        seen.add(name)
        spells.append({
            "id": info.get("id", ""),
            "name": name,
            "icon": info.get("image", {}).get("full", ""),
            "cooldown": info.get("cooldownBurn", ""),
            "description": _strip_html(info.get("description", ""))[:280],
        })
    return sorted(spells, key=lambda x: x["name"])


def _trim_runes(raw: list) -> list[dict]:
    """裁剪符文 JSON：保留系別、各排符文名稱與圖示。"""
    trees = []
    for tree in raw:
        slots = []
        for slot in tree.get("slots", []):
            slots.append([{
                "id": r.get("id"),
                "name": r.get("name", ""),
                "icon": r.get("icon", ""),
                "desc": _strip_html(r.get("longDesc", ""))[:200],
            } for r in slot.get("runes", [])])
        trees.append({
            "id": tree.get("id"),
            "name": tree.get("name", ""),
            "icon": tree.get("icon", ""),
            "slots": slots,
        })
    return trees


def build_bundle(force: bool = False) -> dict:
    """下載並裁剪圖鑑資料，成功後原子寫入快取。"""
    version = _synced_version() or _online_json(versions=True)[0]
    raw_items = _online_json(config.DDRAGON_ITEM_URL, version)
    raw_spells = _online_json(config.DDRAGON_SUMMONER_URL, version)
    raw_runes = _online_json(config.DDRAGON_RUNES_URL, version)
    versions = _online_json(versions=True)
    bundle = {
        "version": version,
        "versions": versions[:40],
        "items": _trim_items(raw_items),
        "summoners": _trim_summoners(raw_spells),
        "runes": _trim_runes(raw_runes),
    }
    atomic_write_text(config.COMPENDIUM_JSON,
                      json.dumps(bundle, ensure_ascii=False))
    logger.info("圖鑑靜態資料已更新：版本 %s、道具 %d、召喚師 %d、符文系 %d",
                version, len(bundle["items"]), len(bundle["summoners"]),
                len(bundle["runes"]))
    return bundle


@lru_cache(maxsize=1)
def load_bundle() -> dict | None:
    """載入圖鑑資料：優先快取，快取不存在才連網；全失敗回傳 None。"""
    if config.COMPENDIUM_JSON.exists():
        try:
            return json.loads(
                config.COMPENDIUM_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("圖鑑快取毀損，重新下載")
    try:
        return build_bundle()
    except Exception as exc:  # noqa: BLE001
        logger.error("圖鑑靜態資料無法取得：%s", exc)
        return None


def icon_path(group: str, filename: str) -> Path | None:
    """取得圖示本機路徑；不存在則按需下載一次（含重試）。

    群組白名单與檔名檢查防止路徑穿越。
    """
    if group not in _ICON_GROUPS or not _ICON_NAME_RE.match(filename or ""):
        return None
    url_template, subdir = _ICON_GROUPS[group]
    # 符文圖示帶子目錄，扁平化檔名避免碰撞與穿越
    safe_name = filename.replace("/", "_").replace("\\", "_")
    target = config.DD_STATIC_DIR / subdir / safe_name
    if target.exists() and target.stat().st_size > 0:
        return target
    try:
        resp = http_get(url_template.format(version=_bundle_version(),
                                            icon=filename))
        atomic_write_bytes(target, resp.content)
        return target
    except Exception as exc:  # noqa: BLE001
        logger.warning("圖示下載失敗 %s/%s：%s", group, filename, exc)
        return None


def _bundle_version() -> str:
    """圖示下載用版本：快取版本 → 同步版本 → 最新版。"""
    bundle = load_bundle()
    if bundle and bundle.get("version"):
        return bundle["version"]
    return _synced_version() or _online_json(versions=True)[0]
