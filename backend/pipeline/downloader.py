"""Oracle's Elixir Google Drive 下載器。

職責：
- 列出官方資料夾中的年度 CSV；
- 下載當前年度（或補齊缺漏歷史年度）；
- 暫存檔先驗證（可被 pandas 開啟且含必要欄位）再原子取代；
- 具備重試、超時、請求間隔與完整錯誤處理。
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import gdown
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from backend import config
from backend.pipeline.common import (HttpRequestError, atomic_write_bytes,
                                     ensure_file_exists, get_logger)

logger = get_logger("downloader")


def raw_csv_path(year: int) -> Path:
    """取得某年度原始 CSV 的預期路徑。"""
    return config.RAW_DIR / config.OE_FILE_TEMPLATE.format(year=year)


def _normalize_folder_listing(result) -> dict[str, str]:
    """將 gdown 資料夾清單結果正規化為 {檔名: file_id}。"""
    files: dict[str, str] = {}
    if not result:
        return files
    for item in result:
        # 不同 gdown 版本可能回傳 dict 或 GoogleDriveFileToDownload 物件
        if isinstance(item, dict):
            name = item.get("name") or item.get("path")
            item_id = item.get("id")
        else:
            name = (getattr(item, "name", None) or getattr(item, "path", None))
            item_id = getattr(item, "id", None)
        if name and item_id and str(name).endswith(".csv"):
            files[str(Path(name).name)] = str(item_id)
    return files


@retry(stop=stop_after_attempt(config.RETRY_ATTEMPTS),
       wait=wait_exponential(multiplier=config.RETRY_MULTIPLIER,
                             min=config.RETRY_MIN_WAIT),
       reraise=True)
def list_drive_files() -> dict[str, str]:
    """列出 Google Drive 資料夾內 CSV（不實際下載）。"""
    url = f"https://drive.google.com/drive/folders/{config.DRIVE_FOLDER_ID}"
    logger.info("解析 Google Drive 資料夾清單…")
    result = gdown.download_folder(
        url=url, skip_download=True, quiet=True, use_cookies=False
    )
    files = _normalize_folder_listing(result)
    if not files:
        raise HttpRequestError("Google Drive 資料夾清單為空，可能被限流或結構改變")
    logger.info("資料夾內發現 %d 個 CSV", len(files))
    return files


def _download_to_temp(file_id: str, tmp_path: Path) -> None:
    """以 gdown 下載單一檔案至暫存路徑。"""
    url = f"https://drive.google.com/uc?id={file_id}"
    output = str(tmp_path)
    ok = gdown.download(url=url, output=output, quiet=True,
                        use_cookies=False)
    if not ok or not tmp_path.exists():
        raise HttpRequestError(f"gdown 下載失敗：{file_id}")


def _validate_csv(path: Path) -> int:
    """驗證下載檔：可解析且含必要欄位，回傳列數。"""
    try:
        header = pd.read_csv(path, nrows=0, low_memory=False)
    except Exception as exc:  # 含 UnicodeDecodeError / EmptyDataError
        raise ValueError(f"下載檔無法以 pandas 開啟：{path.name}") from exc
    missing = [c for c in config.REQUIRED_COLUMNS if c not in header.columns]
    if missing:
        raise ValueError(f"{path.name} 缺少必要欄位：{missing}")
    return sum(1 for _ in open(path, "rb")) - 1


def _sha256(path: Path) -> str:
    """檔案 SHA-256（分塊讀取，避免大檔一次入憶體）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_year(year: int, file_id: str) -> tuple[Path, bool]:
    """下載並驗證單一年度檔，成功後原子取代舊檔。

    回傳 (目標路徑, 是否實際變更)；內容與現有檔相同則不取代。
    """
    target = raw_csv_path(year)
    tmp_path = target.with_suffix(".csv.tmp")
    logger.info("開始下載 %d 年度 CSV（file_id=%s）", year, file_id)

    @retry(stop=stop_after_attempt(config.RETRY_ATTEMPTS),
           wait=wait_exponential(multiplier=config.RETRY_MULTIPLIER,
                                 min=config.RETRY_MIN_WAIT),
           reraise=True)
    def _do() -> None:
        if tmp_path.exists():
            tmp_path.unlink()
        _download_to_temp(file_id, tmp_path)
        time.sleep(config.REQUEST_INTERVAL)

    _do()
    row_count = _validate_csv(tmp_path)

    # 與現有檔內容相同：保留舊檔，避免觸發無意義的全量重建
    if target.exists() and _sha256(tmp_path) == _sha256(target):
        tmp_path.unlink(missing_ok=True)
        logger.info("%d 年度內容未變更，沿用現有檔", year)
        return target, False

    atomic_write_bytes(target, tmp_path.read_bytes())
    tmp_path.unlink(missing_ok=True)
    size_mb = target.stat().st_size / 1024 / 1024
    logger.info("%d 年度下載完成：%d 列、%.1f MB", year, row_count, size_mb)
    return target, True


def run(all_years: bool = False) -> dict:
    """依條件下載 CSV。

    - all_years=False：只更新當前年度；
    - all_years=True：另補齊本地缺漏的歷史年度。
    """
    files = list_drive_files()
    wanted_years = [config.CURRENT_YEAR]
    if all_years:
        wanted_years = list(range(config.HISTORY_START_YEAR,
                                  config.CURRENT_YEAR + 1))

    summary = {"downloaded": [], "unchanged": [], "skipped": [], "failed": []}
    for year in wanted_years:
        name = config.OE_FILE_TEMPLATE.format(year=year)
        existing = raw_csv_path(year)
        drive_id = files.get(name)
        if drive_id is None:
            # 雲端清單無此年度：本機已有就沿用，否則記為失敗
            if existing.exists():
                logger.warning("清單未含 %s，沿用本機現有檔", name)
                summary["skipped"].append({"year": year, "reason": "local_only"})
            else:
                logger.error("清單與本機皆無 %s", name)
                summary["failed"].append({"year": year, "reason": "not_found"})
            continue
        try:
            _, changed = download_year(year, drive_id)
            (summary["downloaded"] if changed else summary["unchanged"]).append(
                year)
        except Exception as exc:  # 保留舊檔不中斷全流程
            logger.error("%d 年度下載失敗：%s", year, exc)
            summary["failed"].append({"year": year, "reason": str(exc)})

    # 最終檢查當前年度檔必須存在
    ensure_file_exists(raw_csv_path(config.CURRENT_YEAR))
    logger.info("下載摘要：%s", summary)
    return summary
