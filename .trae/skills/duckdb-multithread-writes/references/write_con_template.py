"""DuckDB 多執行緒寫入安全配方 —— 可複製範本。

內含三塊可直接搬進倉儲模組的元件：
1. connect()：跨行程鎖與連線快取競態的暫時性錯誤重試
2. write_con()：全行程 RLock 序列化寫入連線生命週期
3. SharedBackoff：跨執行緒限流全池退避（放在 HTTP 客戶端層）

使用前提：單機、單一 .duckdb 檔、Python 3.11+、DuckDB 1.x。
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import duckdb

DB_PATH = "warehouse.duckdb"

# ---------------------------------------------------------------------------
# 元件一、二：倉儲層
# ---------------------------------------------------------------------------
_OPEN_RETRY_WAITS = (0.3, 0.7, 1.5, 3.0, 5.0, 8.0)
# 只列「對方短暫持有、重試可恢復」的訊息片段；永久錯誤不可列入
_RETRYABLE_CONNECT_HINTS = (
    "different configuration",
    "already attached",
    "unique file handle conflict",
)


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """開啟倉儲連線（呼叫端負責 close）。"""
    last_exc: Exception | None = None
    for wait in (0.0,) + _OPEN_RETRY_WAITS:
        if wait:
            time.sleep(wait)
        try:
            return duckdb.connect(DB_PATH, read_only=read_only)
        except duckdb.IOException as exc:
            last_exc = exc  # 跨行程檔案鎖
        except (duckdb.ConnectionException, duckdb.BinderException) as exc:
            if any(h in str(exc).lower() for h in _RETRYABLE_CONNECT_HINTS):
                last_exc = exc
                continue
            raise
    raise last_exc


# RLock：容許同執行緒巢狀（如寫入函式內部再呼叫 set_meta）
_WRITE_LOCK = threading.RLock()


@contextmanager
def write_con():
    """全行程唯一寫入連線；鎖內僅可做純 DB 操作，不可含 HTTP/sleep。"""
    with _WRITE_LOCK:
        con = connect(read_only=False)
        try:
            yield con
        finally:
            con.close()


# ---- 所有寫入函式的標準寫法（grep 確認沒有殘留 connect(read_only=False)）----
def upsert_example(rows: list[tuple]) -> None:
    sql = (
        "INSERT INTO t (id, v) VALUES (?, ?) "
        "ON CONFLICT (id) DO UPDATE SET v = excluded.v"
    )
    with write_con() as con:
        con.executemany(sql, rows)


def set_meta(key: str, value: str) -> None:
    with write_con() as con:
        con.execute(
            "INSERT INTO meta(k, v) VALUES (?, ?) "
            "ON CONFLICT (k) DO UPDATE SET v = excluded.v",
            [key, value])


# ---------------------------------------------------------------------------
# 元件三：HTTP 客戶端層的全池共享退避
# ---------------------------------------------------------------------------
class SharedBackoff:
    """任一執行緒遇 429/5xx/挑戰頁，全部執行緒一起冷卻。"""

    WAITS = (8, 20, 40, 60)

    def __init__(self):
        self._cond = threading.Condition()
        self._until = 0.0
        self._level = 0

    def wait_if_clear(self) -> None:
        with self._cond:
            while self._until > time.monotonic():
                self._cond.wait(self._until - time.monotonic())

    def trigger(self) -> float:
        with self._cond:
            wait_s = self.WAITS[min(self._level, len(self.WAITS) - 1)]
            self._level += 1
            self._until = max(self._until, time.monotonic() + wait_s)
            self._cond.notify_all()
        return wait_s

    def relax(self) -> None:
        with self._cond:
            if self._level and self._until <= time.monotonic():
                self._level = 0


# ---------------------------------------------------------------------------
# 執行緒池標準骨架
# ---------------------------------------------------------------------------
def run_pool_example(tasks: list, workers: int = 3) -> None:
    """每線獨立 client（threading.local），計數用獨立小鎖。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    # import random  # 實務上請求間隔用 random.uniform(1.2, 2.0) 抖動

    backoff = SharedBackoff()
    tls = threading.local()
    counts = {"ok": 0, "err": 0}
    counts_lock = threading.Lock()

    def thread_client():
        client = getattr(tls, "client", None)
        if client is None:
            client = object()  # 實務：建立帶獨立 session 的 HTTP client
            tls.client = client
        return client

    def worker(task):
        backoff.wait_if_clear()
        client = thread_client()
        try:
            payload = None  # 實務：client.get(task) —— 鎖外完成 HTTP
            with write_con() as con:      # 進鎖只做快速寫入
                con.execute("SELECT 1")
            with counts_lock:
                counts["ok"] += 1
        except Exception:
            with counts_lock:
                counts["err"] += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker, t) for t in tasks]
        for fut in as_completed(futures):
            fut.result()
