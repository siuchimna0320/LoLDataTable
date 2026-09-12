"""管道共用工具：日誌、重試、原子寫入、檔案檢查。"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)

from backend import config

_LOGGER_CACHE: set[str] = set()


def get_logger(name: str) -> logging.Logger:
    """取得同時輸出終端與檔案的 logger。"""
    logger = logging.getLogger(name)
    if name in _LOGGER_CACHE:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    log_path = config.LOGS_DIR / "pipeline.log"
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.propagate = False
    _LOGGER_CACHE.add(name)
    return logger


class HttpRequestError(RuntimeError):
    """外部 HTTP 請求失敗。"""


@retry(
    retry=retry_if_exception_type((HttpRequestError, requests.RequestException)),
    stop=stop_after_attempt(config.RETRY_ATTEMPTS),
    wait=wait_exponential(multiplier=config.RETRY_MULTIPLIER,
                          min=config.RETRY_MIN_WAIT),
    reraise=True,
)
def http_get(url: str, *, timeout: int | None = None,
             headers: dict | None = None) -> requests.Response:
    """具重試機制的 GET，回傳前檢查狀態碼。"""
    resp = requests.get(
        url, timeout=timeout or config.REQUEST_TIMEOUT, headers=headers
    )
    if resp.status_code != 200:
        raise HttpRequestError(f"GET {url} 失敗：HTTP {resp.status_code}")
    return resp


def throttle(last_call_ts: float) -> float:
    """確保相鄰請求間隔符合規範，回傳本次呼叫時間戳。"""
    elapsed = time.time() - last_call_ts
    if elapsed < config.REQUEST_INTERVAL:
        time.sleep(config.REQUEST_INTERVAL - elapsed)
    return time.time()


def ensure_file_exists(path: Path) -> None:
    """檔案存在性檢查，避免 FileNotFoundError。"""
    if not path.exists():
        raise FileNotFoundError(f"找不到必要檔案：{path}")


def atomic_write_bytes(target: Path, data: bytes) -> None:
    """暫存檔＋原子取代，失敗不毀損既有檔案。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    with open(tmp_path, "wb") as fh:
        fh.write(data)
    os.replace(tmp_path, target)


def atomic_write_text(target: Path, text: str, encoding: str = "utf-8") -> None:
    """文字版本的原子寫入。"""
    atomic_write_bytes(target, text.encode(encoding))
