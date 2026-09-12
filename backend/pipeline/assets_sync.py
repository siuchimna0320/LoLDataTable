"""Data Dragon 英雄頭像同步。

- 取最新版本 championFull.json，建立「CSV 英文名 → DDragon 資源 ID」映射；
- 下載方形頭像至 backend/data/assets/champions/；
- 版本相同時快取跳過（冪等）；
- 與倉儲實際英雄名比對，未匹配項寫入日誌。
"""
from __future__ import annotations

import json

from backend import config, data_access
from backend.pipeline.common import (atomic_write_bytes, atomic_write_text,
                                     get_logger, http_get, throttle)

logger = get_logger("assets_sync")


def _latest_version() -> str:
    versions = http_get(config.DDRAGON_VERSIONS_URL).json()
    return versions[0]


def _fetch_champion_catalog(version: str) -> dict:
    url = config.DDRAGON_CHAMPION_FULL_URL.format(version=version)
    return http_get(url).json()


def build_name_mapping(catalog: dict) -> dict[str, str]:
    """顯示名（CSV 用名）→ 資源 ID。"""
    mapping = {info["name"]: champ_id
               for champ_id, info in catalog["data"].items()}
    # 人工補充表優先
    mapping.update(config.CHAMPION_NAME_OVERRIDES)
    return mapping


def _download_icon(version: str, champ_id: str, last_call: float) -> float:
    target = config.CHAMP_ICON_DIR / f"{champ_id}.png"
    if target.exists() and target.stat().st_size > 0:
        return last_call
    url = config.DDRAGON_SQUARE_URL.format(version=version, champ_id=champ_id)
    last_call = throttle(last_call)
    resp = http_get(url)
    atomic_write_bytes(target, resp.content)
    return last_call


def _warehouse_champion_names() -> set[str] | None:
    """從倉儲收集所有出現過的英雄名（選用＋禁用）。"""
    try:
        picks = data_access._run_df(
            "SELECT DISTINCT champion FROM player_games WHERE champion IS NOT NULL"
        )["champion"].tolist()
        bans = data_access._run_df(
            "SELECT DISTINCT champion FROM draft_bans WHERE champion IS NOT NULL"
        )["champion"].tolist()
        return set(picks + bans)
    except Exception as exc:
        logger.warning("無法讀取倉儲進行覆蓋率比對：%s", exc)
        return None


def run(force: bool = False) -> dict:
    """同步頭像並回傳摘要。"""
    version = _latest_version()
    meta = {"version": version}
    if config.DD_META_PATH.exists() and not force:
        try:
            old = json.loads(config.DD_META_PATH.read_text(encoding="utf-8"))
            if old.get("version") == version:
                logger.info("圖示版本 %s 已同步，跳過下載", version)
        except json.JSONDecodeError:
            pass

    catalog = _fetch_champion_catalog(version)
    mapping = build_name_mapping(catalog)
    champ_ids = sorted({info["id"] for info in catalog["data"].values()})

    last_call = 0.0
    downloaded = 0
    for champ_id in champ_ids:
        target = config.CHAMP_ICON_DIR / f"{champ_id}.png"
        existed = target.exists() and target.stat().st_size > 0
        last_call = _download_icon(version, champ_id, last_call)
        if not existed:
            downloaded += 1
    logger.info("頭像同步完成：%d 個英雄、本次新下載 %d",
                len(champ_ids), downloaded)

    # 覆蓋率比對
    csv_names = _warehouse_champion_names()
    unmatched = []
    coverage = None
    if csv_names:
        unmatched = sorted(n for n in csv_names if n not in mapping)
        coverage = round((len(csv_names) - len(unmatched)) /
                         max(len(csv_names), 1) * 100, 2)
        if unmatched:
            logger.warning("未匹配英雄 %d 個：%s", len(unmatched), unmatched)
        logger.info("英雄名覆蓋率：%.2f%%", coverage)

    meta.update({"champion_count": len(champ_ids), "coverage": coverage,
                 "unmatched": unmatched, "name_to_id": mapping})
    atomic_write_text(config.DD_META_PATH,
                      json.dumps(meta, ensure_ascii=False, indent=2))
    return {"version": version, "icons": len(champ_ids),
            "downloaded": downloaded, "coverage": coverage,
            "unmatched": unmatched}
