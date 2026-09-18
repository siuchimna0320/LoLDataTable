"""Oracle's Elixir 職業比賽管線的手動／排程共用入口。

與 `python -m backend.pipeline all` 相同流程（下載→清洗入倉→聚合→圖示），
外包一層狀態檔寫入，供網站右上角按鈕輪詢與日後 cron 排程共用：

    python -m scripts.update_pipeline        # 完整增量更新
    python -m scripts.update_pipeline        # 未來 cron 每 12 小時同一指令

狀態檔：backend/data/warehouse/pipeline_state.json
日誌：  backend/data/warehouse/pipeline.log

結束狀態：done（成功）／degraded（exit 2，部分下載失敗但以舊資料續航）／failed。
"""
from __future__ import annotations

import json
import time
import traceback

from backend import config
from backend.pipeline.__main__ import main as pipeline_main
from backend.pipeline.common import get_logger

logger = get_logger("update_pipeline")

STATE_PATH = config.WAREHOUSE_DIR / "pipeline_state.json"


def _write_state(state: dict) -> None:
    """原子寫入狀態檔（先寫暫存再取代），避免 web 端讀到半寫檔。"""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(STATE_PATH)


def _read_latest_summary() -> dict:
    """聚合完成後讀 latest.json 的資料時間與總場次；檔案缺失不視為致命。"""
    if not config.LATEST_JSON.exists():
        return {"data_time": None, "games": None}
    try:
        payload = json.loads(
            config.LATEST_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("latest.json 讀取失敗：%s", exc)
        return {"data_time": None, "games": None}
    return {"data_time": payload.get("generated_at"),
            "games": payload.get("games")}


def main() -> int:
    """執行完整管線並保證寫入終態（即使例外也不留下懸空 running）。"""
    started = time.time()
    _write_state({
        "stage": "running", "started_at": started,
        "finished_at": None, "exit_code": None,
        "data_time": None, "games": None, "error": None,
    })
    exit_code = 1
    try:
        exit_code = pipeline_main(["all"])
        summary = _read_latest_summary()
        # 0 成功；2 有下載失敗但以既有資料續航；其餘視為失敗
        stage = {0: "done", 2: "degraded"}.get(exit_code, "failed")
        error = (None if stage != "failed"
                 else f"管線回傳碼 {exit_code}，詳見 pipeline.log")
        _write_state({
            "stage": stage, "started_at": started,
            "finished_at": time.time(), "exit_code": exit_code,
            "error": error, **summary,
        })
    except Exception as exc:  # noqa: BLE001 任何例外都要落終態
        logger.error("管線執行失敗：%s\n%s", exc, traceback.format_exc())
        _write_state({
            "stage": "failed", "started_at": started,
            "finished_at": time.time(), "exit_code": exit_code,
            "data_time": None, "games": None,
            "error": str(exc)[:500],
        })
        return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
