"""補抓 dpm.lol 官方隊伍頁的完整登錄名單，輸出 JSON 快照供查詢層合併。

背景：dpm 的職業排行榜只回各平台前約 277 名，低牌階但已登錄的選手
（例：Teddy 51 LP、GIDEON 667 LP）不在榜內，現行爬蟲管線永遠看不到，
導致每日戰況卡片缺路線／缺人。官網隊伍頁
https://dpm.lol/pro/teams/{CODE} 為 Next.js RSC 頁面，其 flight payload
內嵌完整 players 陣列（puuid／lane／牌階／LP／勝敗）。

本腳本刻意不寫 DuckDB、也不修改現行爬蟲（爬取期間避免鎖競爭），
僅把名單寫成 dpm_rosters.json，由 backend.dpm_queries 唯讀合併。
日後爬蟲空檔再把名單來源正式併入 scrape_dpm（帳號入庫後對戰才會自動補抓）。

用法：
    python -m scripts.fetch_dpm_rosters [--interval 2.0] [--only DNS,BRO]
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timezone

from backend import config
from backend.dpm_client import DpmClient
from backend.dpm_queries import daily_payload
from backend.pipeline.common import get_logger

logger = get_logger("fetch_dpm_rosters")

_OUTPUT_PATH = config.WAREHOUSE_DIR / "dpm_rosters.json"


def team_codes(only: set[str] | None) -> list[tuple[str, str]]:
    """從每日戰況現有分組取（全名, dpm 短碼），短碼即官網隊伍頁路徑。"""
    payload = daily_payload()
    pairs = []
    seen: set[str] = set()
    for team in payload.get("teams", []):
        code = team.get("team")
        if not code or code.upper() in seen:
            continue
        if only and code.upper() not in only:
            continue
        seen.add(code.upper())
        pairs.append((team["team_full"], code))
    return pairs


def fetch_all(interval: float, only: set[str] | None) -> dict:
    """逐隊抓取（禮貌間隔，單隊失敗不影響其他隊）。"""
    client = DpmClient(interval=interval)
    snapshot = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "teams": {},
    }
    pairs = team_codes(only)
    total = len(pairs)
    ok = empty = 0
    for i, (full_name, code) in enumerate(pairs, 1):
        logger.info("[%d/%d] %s（%s）", i, total, code, full_name)
        players = []
        try:
            players = client.team_roster_page(code)
        except Exception as exc:  # 任何單隊異常都不中斷整批
            logger.warning("抓取失敗 %s：%s", code, exc)
        if not players:
            empty += 1
            logger.warning("%s 頁面未取得名單（短碼可能不符）", code)
            continue
        ok += 1
        snapshot["teams"][code] = {"team_full": full_name, "players": players}
    logger.info("完成 %d 隊：成功 %d、無名單/失敗 %d",
                total, ok, empty)
    return snapshot


def save_snapshot(snapshot: dict) -> None:
    """先寫暫存檔再原子取代，避免查詢層讀到半寫檔。"""
    out_dir = _OUTPUT_PATH.parent
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="dpm_rosters.", suffix=".tmp",
                                    dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, _OUTPUT_PATH)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    size_kb = round(os.path.getsize(_OUTPUT_PATH) / 1024, 1)
    logger.info("名單快照已寫入 %s（%s KB）", _OUTPUT_PATH, size_kb)


def main() -> None:
    parser = argparse.ArgumentParser(description="補抓 dpm.lol 隊伍登錄名單")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="相鄰請求最小間隔秒數（預設 2.0，避免造成伺服器負擔）")
    parser.add_argument("--only", default="",
                        help="只抓指定短碼，逗號分隔（例：DNS,BRO）")
    args = parser.parse_args()
    only = {c.strip().upper() for c in args.only.split(",") if c.strip()} or None
    started = time.time()
    snapshot = fetch_all(args.interval, only)
    save_snapshot(snapshot)
    logger.info("耗時 %.0f 秒", time.time() - started)


if __name__ == "__main__":
    main()
