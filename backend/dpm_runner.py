"""DPM.LOL 爬蟲背景執行（供 UI 按鈕與 cron 共用）。

UI 按鈕以「獨立子進程」執行 `python -m scripts.scrape_dpm`，
與 cron 完全同一路徑：子進程以可寫模式開 DuckDB，web 行程保持唯讀，
符合 DuckDB 跨行程鎖定規則，且 waitress 重啟不會中斷爬取。
同時間僅允許一個爬取任務，重複點擊直接略過。
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from backend import config, dpm_queries, dpm_store

logger = logging.getLogger(__name__)

# 進度 meta 超過此秒數未更新即視同無爬蟲運行（最長退避 40 秒，900 秒足寬鬆）
_HEARTBEAT_SEC = 900

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 子進程輸出導到倉儲旁的 scrape.log，供事後排查（不進 web 主控台）
_LOG_PATH = config.DPM_WAREHOUSE_PATH.parent / "scrape.log"

# 全域：目前由本 web 行程啟動的爬取子進程
_proc: subprocess.Popen | None = None


def _own_proc_alive() -> bool:
    """本 web 行程自己啟動的子進程是否仍存活。"""
    global _proc
    if _proc is None:
        return False
    if _proc.poll() is not None:
        _proc = None
        return False
    return True


def is_running() -> bool:
    """是否有爬蟲運行：本行程子進程，或 cron／外部指令的活躍心跳。

    階段已標記 failed／done 時，即便心跳時間仍新也視為未運行，
    避免中斷後 15 分鐘內無法手動重啟。
    """
    if _own_proc_alive():
        return True
    return (dpm_store.get_meta("stage") == "running"
            and dpm_store.meta_fresh("stage_progress", _HEARTBEAT_SEC))


def start_scrape() -> bool:
    """啟動獨立爬取子進程；已在執行中（含 cron 行程）則回 False。"""
    global _proc
    if is_running():
        return False
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_fh = _LOG_PATH.open("a", encoding="utf-8")
    popen_kwargs: dict = {}
    if sys.platform == "win32":
        # 分離到新进程群，web 服務重啟不帶走爬蟲
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008)
    else:
        popen_kwargs["start_new_session"] = True
    _proc = subprocess.Popen(
        [sys.executable, "-m", "scripts.scrape_dpm"],
        cwd=str(_PROJECT_ROOT),
        stdout=log_fh, stderr=subprocess.STDOUT,
        **popen_kwargs)
    logger.info("已啟動 DPM 爬取子進程 PID=%s", _proc.pid)
    return True


def status() -> dict:
    """爬取狀態（含子進程存活旗標），供 UI 輪詢。"""
    data = dpm_queries.scrape_status()
    data["thread_alive"] = is_running()
    return data
