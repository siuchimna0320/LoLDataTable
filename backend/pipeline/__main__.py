"""管線單一入口：

    python -m backend.pipeline all                 # 下載→入倉→聚合→圖示
    python -m backend.pipeline download            # 僅更新當前年度
    python -m backend.pipeline download --all-years
    python -m backend.pipeline clean               # 全量清洗並重建倉儲
    python -m backend.pipeline aggregate           # 僅重建 latest.json
    python -m backend.pipeline assets [--force]    # 僅同步圖示
"""
from __future__ import annotations

import argparse
import sys
import time

from backend import config
from backend.pipeline import aggregate, assets_sync, downloader, warehouse
from backend.pipeline.common import get_logger

logger = get_logger("pipeline")


def _stage_download(all_years: bool) -> dict:
    return downloader.run(all_years=all_years)


def _stage_clean() -> dict:
    years = list(range(config.HISTORY_START_YEAR, config.CURRENT_YEAR + 1))
    return warehouse.rebuild(years)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LoL 電競數據管道")
    parser.add_argument(
        "stage", choices=["all", "download", "clean", "aggregate", "assets"]
    )
    parser.add_argument("--all-years", action="store_true",
                        help="下載階段一併補齊缺漏歷史年度")
    parser.add_argument("--force", action="store_true",
                        help="圖示階段強制重新下載")
    args = parser.parse_args(argv)
    started = time.time()

    degraded = False  # 下載有失敗但以舊數據續航完成（NFR-2，exit 2 區分）
    try:
        download_result = None
        if args.stage in ("all", "download"):
            logger.info("=== 階段 1/4：下載 ===")
            download_result = _stage_download(args.all_years)
            if download_result["failed"]:
                logger.error("下載存在失敗項目：%s", download_result["failed"])
                if args.stage == "download":
                    return 1
                degraded = True

        if args.stage == "clean":
            logger.info("=== 階段 2/4：清洗入倉 ===")
            _stage_clean()
        elif args.stage == "all":
            # 增量判斷：無檔案實際變更且倉儲已存在，跳過全量重建
            changed = bool(download_result and download_result["downloaded"])
            if changed or not config.WAREHOUSE_PATH.exists():
                logger.info("=== 階段 2/4：清洗入倉 ===")
                _stage_clean()
            else:
                logger.info("=== 階段 2/4：原始數據未變更，跳過倉儲重建 ===")

        if args.stage in ("all", "aggregate"):
            logger.info("=== 階段 3/4：聚合 JSON ===")
            aggregate.run()

        if args.stage in ("all", "assets"):
            logger.info("=== 階段 4/4：圖示同步 ===")
            assets_sync.run(force=args.force)
            # 圖鑑靜態資料（版本/道具/召喚師/符文）；失敗時沿用快取，不中斷管線
            try:
                from backend import ddragon_data
                ddragon_data.build_bundle()
            except Exception as exc:  # noqa: BLE001
                logger.warning("圖鑑靜態資料更新失敗（沿用快取）：%s", exc)
            # 社群刷野編纂表；失敗時沿用快取
            try:
                from backend import jungle_data
                jungle_data.build_cache()
            except Exception as exc:  # noqa: BLE001
                logger.warning("刷野編纂表更新失敗（沿用快取）：%s", exc)

    except Exception as exc:
        logger.exception("管線失敗：%s", exc)
        return 1

    elapsed = time.time() - started
    if degraded:
        logger.warning("管線完成但含下載失敗（以既有數據續航），耗時 %.1f 秒",
                       elapsed)
        return 2
    logger.info("管線完成，耗時 %.1f 秒", elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
