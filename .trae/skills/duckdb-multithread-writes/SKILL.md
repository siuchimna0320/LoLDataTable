---
name: duckdb-multithread-writes
description: 在同一個 Python 行程以多執行緒並發寫入 DuckDB 檔案的安全配方（全域寫入鎖＋連線重試）。用於把爬蟲或批次作業改成 ThreadPoolExecutor 後出現 Unique file handle conflict、already attached、different configuration 等錯誤時；不適用於跨機器共用或純唯讀查詢場景。
---

# DuckDB 多執行緒寫入安全模式

適用場景：單機、同一個 Python 行程內，多條執行緒各自開關 DuckDB 連線寫入**同一個 .duckdb 檔案**（典型：爬蟲 ThreadPoolExecutor ＋ 倉儲層）。本工作區實測版本 DuckDB 1.5.5 / Python 3.11 / Windows。

## 先判斷症狀（務必看完整例外與階段）

多執行緒上線後在**寫入階段**偶發或必發以下錯誤，且錯誤點常落在「開連線」或 UPSERT 當下：

- `Binder Error: Unique file handle conflict: Cannot attach "dpm" - ... already attached by database "dpm"`
- `ConnectionException: Can't open a connection to same database file with a different configuration than existing connections`
- Windows 跨行程：`IOException: 程序無法存取檔案，因為檔案正由另一個程序使用`

注意：不要只看例外名稱就臆測。先記錄「哪個階段、第幾項、哪些執行緒同時在寫」，可用最小重現確認規則（見「驗證」）。

## 實測到的 DuckDB 連線規則（1.5.5）

- 同組態可並存：多條 write 連線同時存在不會立即報錯；多條 read_only 亦同。
- 跨組態互斥：write 與 read_only 連線只要同時存在，後開者立刻擲 `different configuration`（這不是短暫碰撞，重試沒用，必須等對方關閉）。
- 高頻開關寫入連線時，鴨子內部連線快取在「關閉釋放／重新 attach」縫隙會偶發 `already attached` BinderException——人為很難穩定重現，但真實高頻爬取會中招。
- 跨行程：一寫多讀在鎖允許範圍可共存，但開連線瞬間可能撞到 IOException；Windows 上另一行程持有連線期間可能持續被擋。

## 配方一：全域寫入鎖序列化（核心）

在倉儲模組（如 `dpm_store.py`）放模組級 RLock 與上下文管理器，所有寫入路徑**只能**經由它取得連線：

```python
import threading
from contextlib import contextmanager

_WRITE_LOCK = threading.RLock()

@contextmanager
def write_con():
    with _WRITE_LOCK:                 # 序列化「開啟→操作→關閉」整段
        con = connect(read_only=False)
        try:
            yield con
        finally:
            con.close()
```

鐵律：

1. **模組內所有寫入函式一律改用 `with write_con() as con:`**，不得再直接 `connect(read_only=False)`；用 grep 確認零遺漏。
2. **鎖內只做 DB 操作**。HTTP 請求、time.sleep、大量 CPU 計算不得持鎖，否則多線退化為串行。正確分工：執行緒先在鎖外抓資料，再進鎖快速寫入。
3. 用 RLock 而非 Lock，容許同執行緒巢狀呼叫（如 `set_meta()` 被其他寫入函式內部呼叫）。
4. 計數器／共享 list 用獨立的小鎖（`threading.Lock`），不要借用寫入鎖。
5. UPSERT 優於 INSERT OR REPLACE：快照類更新若用 REPLACE 會清掉其他管線維護的欄位（如時間戳記），改 `INSERT ... ON CONFLICT DO UPDATE` 並顯式保留不該覆寫的欄。

## 配方二：connect() 對暫時性碰撞重試

只捕 `IOException` 不夠。對「開連線階段」的暫時性錯誤加白名單指數重試，其餘永久錯誤立即拋出：

```python
_OPEN_RETRY_WAITS = (0.3, 0.7, 1.5, 3.0, 5.0, 8.0)
_RETRYABLE_HINTS = ("different configuration", "already attached",
                    "unique file handle conflict")

for wait in (0.0,) + _OPEN_RETRY_WAITS:
    if wait:
        time.sleep(wait)
    try:
        return duckdb.connect(str(path), read_only=read_only)
    except duckdb.IOException:
        continue                      # 跨行程檔案鎖，重試
    except (duckdb.ConnectionException, duckdb.BinderException) as exc:
        if any(h in str(exc).lower() for h in _RETRYABLE_HINTS):
            continue                  # 快取競態／對方短持連線，重試
        raise                         # 真綁定錯誤不要吞
```

注意：白名單只放「對方短暫持有、重試可恢復」的訊息；跨組態互斥若對方是**常駐不關的連線**（例如 web 行程外洩的全域連線），重試只會拖時間——那代表有連線生命週期 bug，要修呼叫端。

## 配方三：執行緒池與用戶端組織

- `ThreadPoolExecutor(max_workers=N)`；保守預設 N=3，`--workers 1` 可退回單線。
- **每條執行緒一個獨立 HTTP client/session**（threading.local 惰性建立），各自節流時鐘；請求間隔在區間隨機抖動（如 1.2–2.0s）錯峰。
- 限流（429/Cloudflare/5xx）用**跨執行緒共享退避**：任一線觸發，全部線暫停並逐級加長（8→20→40→60s），冷卻後降級。
- 單一任務失敗只計錯不中斷整批；但「階段級」未保護的寫入（如主執行緒定期寫進度 meta）也要走 `write_con()`，否則例外會直竄頂層讓整輪失敗。
- 進度寫 meta 加最小間隔（如 5 秒），避免高頻開寫連線。
- 只在「成功」後更新節流戳記之類的狀態，失敗者下一輪要能重試。

## 驗證順序（離線先行，再小規模真機）

1. 編譯：`python -m py_compile <store_module> <runner_script>`
2. 臨時倉高壓測試：N≥8 條執行緒、屏障或同步啟動，交錯呼叫各寫入函式＋主緒持續 set_meta，斷言 0 錯誤且列數正確。
3. 對照測試確認跨組態行為：先開一條 read_only 持住，另一緒開 write，驗證重試邏輯在前者關閉後成功。
4. 完全隔離網路的端到端：假 client（連主 runner 的 client 欄位也換掉）跑執行緒池，確認恰好開 N 個每線客戶端。
5. 真機小規模：`--platforms <一區> --limit-accounts 8 --skip-details --no-rosters`，確認原崩潰階段 0 錯誤。
6. 跑專案 validate 保持既有基準（本工作區為 84/0）。

常見測試陷阱：假 client 工廠若每次呼叫都 new，會誤判「每線獨立客戶端」斷言；A/B 計速前先確認沒有其他行程（含 web 服務、殘留爬蟲）持著同一個倉，污染數據會讓加速比失真。

## 何時不適用

- **單線批次重建**（逐年讀 CSV→單一連線大量 INSERT→tmp 檔原子取代）：沒有併發就不需要鎖，維持單連線最快。
- 純唯讀查詢服務：每查詢開一條短生命週期 read_only 連線即可；若同一行程內另有寫入者，再評論配方二。
- 跨機器／網路共用檔案：DuckDB 檔案型鎖不適用，應改用 Motherduck 或服務層。

## 本工作區參考實作

- 倉儲層：`backend/dpm_store.py`（`write_con()`、`connect()` 重試、`_WRITE_LOCK`）
- 爬蟲：`scripts/scrape_dpm.py`（`_run_pool()`、`_thread_client()`、`_bump()`、SharedBackoff 在用戶端層 `backend/dpm_client.py`）
- 可複製範本：[references/write_con_template.py](references/write_con_template.py)

效能實測錨點（2026-09-18，3 線 vs 舊單線）：matches 階段 3.36→0.72 秒/帳（4.66x），details 1.20→0.96 秒/場（1.26x，寫入佔比高故提速有限屬預期）。
