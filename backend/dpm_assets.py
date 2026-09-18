"""DPM.LOL 靜態圖示（階級徽章、戰隊隊徽）按需下載與本地快取。

由 Flask 路由呼叫：首次存取時從 dpm.lol 下載，之後走本地檔案，
TrueNAS 離網環境亦可瀏覽（圖片已常駐 backend/data/assets/dpm）。
"""
from __future__ import annotations

import re
from pathlib import Path

from backend import config
from backend.dpm_client import DpmClient, DpmError
from backend.pipeline.common import get_logger

logger = get_logger("dpm_assets")

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+$")
_client = DpmClient()


def _http() -> DpmClient:
    """共用客戶端（模組級單例）。"""
    return _client


def _cached(subdir: str, safe_name: str, remote_url: str) -> Path | None:
    """取本地快取檔；不存在則下載，失敗回 None。"""
    if not _SAFE_NAME.match(safe_name):
        return None
    target = config.DPM_ASSET_DIR / subdir / safe_name
    if target.exists() and target.stat().st_size > 0:
        return target
    try:
        data = _http().get_bytes(remote_url)
    except DpmError as exc:
        logger.warning("圖示下載失敗 %s：%s", remote_url, exc)
        data = None
    if not data:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def rank_path(tier: str) -> Path | None:
    """階級小徽章（CHALLENGER_SMALL.webp 這類）。"""
    return _cached("rank", f"{tier}.webp",
                   f"{config.DPM_SITE_URL}/rank/{tier}_SMALL.webp")


def team_path(code: str) -> Path | None:
    """戰隊隊徽（esport/teams/{CODE}.webp）；隊名含空格時安全化檔名。"""
    from urllib.parse import quote
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", code)
    return _cached("team", f"{safe}.webp",
                   f"{config.DPM_SITE_URL}/esport/teams/{quote(code)}.webp")
