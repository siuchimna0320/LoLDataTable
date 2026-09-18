"""Oracle's Elixir 比賽管線的背景執行（UI 按鈕與未來 cron 共用）。

與 dpm_runner 相同模式：UI 按鈕以「獨立子進程」執行
`python -m scripts.update_pipeline`，與日後 cron 完全同一路徑；
waitress 重啟不會中斷更新，同時間僅允許一個更新任務。
狀態由子進程原子寫入 pipeline_state.json，web 端只讀。
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

from backend import config

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_STATE_PATH = config.WAREHOUSE_DIR / "pipeline_state.json"
_LOG_PATH = config.WAREHOUSE_DIR / "pipeline.log"
# running 狀態超過此秒數仍無終態，視同孤兒（行程被強殺／斷電），
# 允許重新啟動；完整管線含圖示下載正常在十幾分鐘內完成
_STALE_SEC = 2 * 60 * 60

# 全域：目前由本 web 行程啟動的更新子進程
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


def read_state() -> dict | None:
    """讀取管線狀態檔；檔案不存在或毀損回 None（讀取端寬容）。"""
    if not _STATE_PATH.exists():
        return None
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("pipeline_state.json 讀取失敗：%s", exc)
        return None


def is_running() -> bool:
    """是否有更新運行：本行程子進程，或狀態檔新鮮的 running（cron 啟動）。"""
    if _own_proc_alive():
        return True
    state = read_state()
    if not state or state.get("stage") != "running":
        return False
    started = float(state.get("started_at") or 0)
    return time.time() - started < _STALE_SEC


def start_update() -> bool:
    """啟動獨立管線子進程；已在執行中（含 cron 行程）則回 False。"""
    global _proc
    if is_running():
        return False
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_fh = _LOG_PATH.open("a", encoding="utf-8")
    popen_kwargs: dict = {}
    if sys.platform == "win32":
        # 分離到新进程群，web 服務重啟不帶走更新行程
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008)
    else:
        popen_kwargs["start_new_session"] = True
    _proc = subprocess.Popen(
        [sys.executable, "-m", "scripts.update_pipeline"],
        cwd=str(_PROJECT_ROOT),
        stdout=log_fh, stderr=subprocess.STDOUT,
        **popen_kwargs)
    logger.info("已啟動 Oracle 管線更新子進程 PID=%s", _proc.pid)
    return True


def status() -> dict:
    """更新狀態（含 running 旗標），供 UI 每 5 秒輪詢。"""
    state = read_state() or {}
    state["running"] = is_running()
    return state
