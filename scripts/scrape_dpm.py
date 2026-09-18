"""DPM.LOL 職業積分爬蟲（cron 每 12 小時／右上角手動按鈕共用同一入口）。

流程五階段：
1. 各伺服器職業排行榜（isPro=true）翻頁 → accounts 快照
2. 六大聯賽官方登錄名單（esport leaderboard）→ 補上排行榜門檻外的
   低牌階選手帳號，其姓名一併送入階段 3
3. 職業選手檔 /v1/pros/{name} → pros 與合併帳號
4. 各帳號對戰清單（15 場/頁）→ matches 與本人統計；
   增量模式遇到已收錄的場景即停止往後翻頁
5. 最近 N 天未補的場景抓 10 人完整明細 → 對位人員身份

用法：
    python -m scripts.scrape_dpm                      # 完整增量更新
    python -m scripts.scrape_dpm --platforms kr       # 只爬韓服（測試）
    python -m scripts.scrape_dpm --limit-accounts 5   # 只處理前 5 個帳號
    python -m scripts.scrape_dpm --skip-details       # 跳過明細補抓
    python -m scripts.scrape_dpm --no-rosters         # 跳過聯賽名單補遺
"""
from __future__ import annotations

import argparse
import math
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from backend import config, dpm_client, dpm_store
from backend.pipeline.common import get_logger

logger = get_logger("scrape_dpm")

# 排行榜每頁 50 筆、對戰清單每頁 15 筆（dpm.lol 固定）
LB_PAGE_SIZE = 50
MH_PAGE_SIZE = 15
# 進度寫入 meta 的最小間隔秒數（避免頻繁開寫連線）
_PROGRESS_FLUSH_SEC = 5


class ScrapeRunner:
    """彙整五階段流程、計數與進度回報。"""

    def __init__(self, args):
        self.args = args
        # 全池共享退避：任一條線遇 429/CF，所有線一起冷卻
        self.backoff = dpm_client.SharedBackoff()
        self.client = dpm_client.DpmClient(backoff=self.backoff)
        # 各工作線獨立 DpmClient（獨立 session 與節流時鐘）
        self._tls = threading.local()
        self._counts_lock = threading.Lock()
        self.counts = {"accounts": 0, "rosters": 0, "pros": 0,
                       "matches": 0, "details": 0, "errors": 0}
        self._last_flush = 0.0

    # ------------------------------------------------------------------
    def _bump(self, key: str, amount: int = 1) -> None:
        """執行緒安全地累加計數。"""
        if not amount:
            return
        with self._counts_lock:
            self.counts[key] += amount

    def _progress(self, stage: str, index: int, total: int) -> None:
        """更新爬蟲進度到 meta（供儀表板按鈕輪詢）並寫 log。"""
        if time.time() - self._last_flush < _PROGRESS_FLUSH_SEC and index < total:
            return
        self._last_flush = time.time()
        dpm_store.set_meta(
            "stage_progress",
            f"{stage}|{index}|{total}")
        with self._counts_lock:
            errors = self.counts["errors"]
        logger.info("[%s] 進度 %d/%d（錯誤 %d）", stage, index, total, errors)

    def _safe(self, stage: str, fn, *fargs):
        """單一項目失敗不中斷整批，記錄錯誤數與 log（執行緒安全）。"""
        try:
            return fn(*fargs)
        except Exception as exc:  # noqa: BLE001
            self._bump("errors")
            with self._counts_lock:
                errors = self.counts["errors"]
            logger.warning("[%s] 單項失敗（累計 %d）：%s", stage, errors, exc)
            return None

    def _thread_client(self) -> dpm_client.DpmClient:
        """惰性建立「每條工作線一個」客戶端，請求間隔於區間隨機抖動。"""
        client = getattr(self._tls, "client", None)
        if client is None:
            client = dpm_client.DpmClient(
                jitter=tuple(config.DPM_JITTER_RANGE),
                backoff=self.backoff)
            self._tls.client = client
        return client

    def _run_pool(self, stage: str, tasks: list, worker) -> None:
        """以保守執行緒池逐項執行 worker(task)，單項例外互不影響。"""
        total = len(tasks)
        if not total:
            return
        workers = max(1, self.args.workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(worker, task): task for task in tasks}
            done = 0
            for fut in as_completed(futures):
                done += 1
                self._progress(stage, done, total)
                exc = fut.exception()
                if exc is not None:
                    self._bump("errors")
                    logger.warning("[%s] 工作項失敗：%s", stage, exc)
        self._progress(stage, total, total)

    # ------------------------------------------------------------------
    def run(self) -> dict:
        dpm_store.init_db()
        started = time.time()
        dpm_store.set_meta("stage", "running")
        try:
            names = self.stage_leaderboards()
            if not self.args.no_rosters:
                names = self.stage_teams(names)
            self.stage_pros(names)
            self.stage_matches()
            if not self.args.skip_details:
                self.stage_details()
        except Exception as exc:  # noqa: BLE001
            logger.error("爬取失敗：%s\n%s", exc, traceback.format_exc())
            dpm_store.set_meta("stage", "failed")
            dpm_store.set_meta("last_error", str(exc)[:500])
            raise
        elapsed = int(time.time() - started)
        dpm_store.set_meta("stage", "done")
        dpm_store.set_meta("last_error", "")
        dpm_store.set_meta("last_success_at", str(int(time.time())))
        dpm_store.set_meta("last_run_summary", str(self.counts))
        logger.info("完成，耗時 %d 秒：%s", elapsed, self.counts)
        return self.counts

    # ------------------------------------------------------------------
    def stage_leaderboards(self) -> set[str]:
        """階段一：翻完各平台職業排行榜，回傳 pro displayName 集合。"""
        names: set[str] = set()
        for platform in self.args.platforms:
            total, page = 0, 1
            while True:
                data = self._safe("leaderboard", self.client.leaderboard,
                                  platform, page, True)
                if not data:
                    break
                players = data.get("players") or []
                if page == 1:
                    total = data.get("total") or 0
                    logger.info("平台 %s 職業帳號共 %s 個", platform, total)
                self._bump("accounts",
                           dpm_store.save_leaderboard(
                               players, platform, data.get("total")))
                names.update(p["displayName"] for p in players
                             if p.get("displayName") and p.get("role") == "PRO")
                if len(players) < LB_PAGE_SIZE:
                    break
                page += 1
        dpm_store.set_meta("pro_name_count", str(len(names)))
        return names

    def stage_teams(self, names: set[str]) -> set[str]:
        """階段二：六大聯賽官方登錄名單補遺。

        職業排行榜每服僅約前 277 名，低牌階登錄選手（板凳／久未排位）
        不在其中；聯賽 leaderboard 是完整登錄源。新帳號插入 accounts
        後，階段 4 會自動回填對戰；姓名併入 names 讓階段 3 補 pro 檔。
        """
        for league in self.args.leagues:
            data = self._safe("esport-roster",
                              self.client.esport_rosters, league)
            if not data:
                continue
            added = self._safe("roster-save",
                               dpm_store.save_esport_rosters, data, league)
            self._bump("rosters", added or 0)
            names.update(p["displayName"] for p in data
                         if p.get("displayName") and p.get("puuid"))
            logger.info("聯賽 %s 名單 %d 人，新增帳號 %s",
                        league, len(data), added)
        dpm_store.set_meta("pro_name_count", str(len(names)))
        return names

    def stage_pros(self, names: set[str]) -> None:
        """階段三：逐個抓 pro 選手檔（保守多線程）。

        各工作線使用獨立 DpmClient（抖動節流、全池退避）；
        save_pro_profile 的寫入由 dpm_store 全域鎖序列化，
        HTTP 抓取與 DB 寫入互不長時間阻塞。
        """
        tasks = sorted(names)
        if self.args.limit_accounts:
            tasks = tasks[:self.args.limit_accounts]

        def worker(name: str):
            client = self._thread_client()
            # 抓取與存檔皆納入單項保護：單筆失敗不中斷整批
            pro = self._safe("pro", client.pro_profile, name)
            if pro and self._safe("pro-save",
                                  dpm_store.save_pro_profile, pro):
                self._bump("pros")

        self._run_pool("pros", tasks, worker)

    def stage_matches(self) -> None:
        """階段四：各帳號對戰清單（保守多線程）。

        - 活躍帳號每輪輪詢；沉寂逾 dormant_days 天者每 probe_interval_days
          才探一次（last_history_check_ts 節流）；
        - 新帳號回填至 max_games、舊帳號首頁全舊即增量停止；
        - 各工作線使用獨立 DpmClient（抖動節流），429/CF 由全池共享退避。
        """
        tasks = self._due_puuids()
        max_pages = math.ceil(self.args.max_games / MH_PAGE_SIZE)
        checked: list[str] = []
        lock = threading.Lock()

        def worker(item: tuple[str, bool]):
            puuid, is_dormant = item
            client = self._thread_client()
            ok = self._scrape_account_matches(client, puuid, max_pages)
            # 僅當探測本身成功才蓋戳記，避免失敗者被冷藏一週
            if is_dormant and ok:
                with lock:
                    checked.append(puuid)

        self._run_pool("matches", tasks, worker)
        marked = dpm_store.mark_history_checked(checked)
        if marked:
            logger.info("沉寂帳號已記錄本週探測：%d 個", marked)

    def _due_puuids(self) -> list[tuple[str, bool]]:
        """本輪到期的輪詢名單 (puuid, is_dormant)，套用測試筆數上限。"""
        rows = dpm_store.puuids_due_for_history(
            dormant_days=self.args.dormant_days,
            probe_every_days=self.args.probe_interval_days)
        dormant_n = sum(1 for _, d in rows if d)
        logger.info("對戰輪詢到期 %d 個帳號（其中沉寂 %d 個）",
                    len(rows), dormant_n)
        if self.args.limit_accounts:
            rows = rows[:self.args.limit_accounts]
        return rows

    def _scrape_account_matches(self, client: dpm_client.DpmClient,
                                puuid: str, max_pages: int) -> bool:
        """單一帳號翻頁：連續一頁無新場景視為已到位；全程成功回 True。"""
        for page in range(1, max_pages + 1):
            data = self._safe("match-history", client.match_history,
                              puuid, page)
            if data is None:
                return False  # 404 或重試失敗：不蓋沉寂戳記
            matches = data.get("matches") or []
            if not matches:
                return True
            new_ids = self._safe("match-save",
                                 dpm_store.save_match_list, puuid, matches)
            if new_ids is None:
                return False
            self._bump("matches", len(new_ids))
            if not new_ids:
                return True  # 本頁全為舊場景，後面更舊，無須再翻
        return True

    def stage_details(self) -> None:
        """階段五：近期未補的場景抓 10 人明細（同一場只抓一次，多線程）。"""
        pending = dpm_store.pending_detail_match_ids(self.args.detail_days)
        seen: set[str] = set()
        jobs: list[tuple[str, str, str]] = []
        for puuid, platform, game_id, _start_ts in pending:
            mid = f"{platform}_{game_id}"
            if mid in seen:
                continue
            seen.add(mid)
            jobs.append((puuid, platform, game_id))

        def worker(job: tuple[str, str, str]):
            puuid, platform, game_id = job
            client = self._thread_client()
            detail = self._safe("match-detail", client.match_detail,
                                puuid, platform, game_id)
            if detail and self._safe("detail-save",
                                     dpm_store.save_match_detail, detail):
                self._bump("details")

        self._run_pool("details", jobs, worker)


def _parse_args():
    parser = argparse.ArgumentParser(description="DPM.LOL 職業積分爬蟲")
    parser.add_argument("--platforms", default=",".join(config.DPM_PLATFORMS),
                        help="逗號分隔平台代碼，預設全部")
    parser.add_argument("--max-games", type=int,
                        default=config.DPM_BACKFILL_GAMES,
                        help="新帳號首次回填場數上限（預設 100）")
    parser.add_argument("--active-days", type=int, default=0,
                        help="已保留參數（不再過濾）：積分榜帳號一律輪詢")
    parser.add_argument("--detail-days", type=int,
                        default=config.DPM_DETAIL_DAYS,
                        help="補抓 10 人明細的回顧天數（預設 14）")
    parser.add_argument("--limit-accounts", type=int, default=0,
                        help="只處理前 N 個對象（測試用）")
    parser.add_argument("--workers", type=int, default=config.DPM_WORKERS,
                        help="對戰／明細階段執行緒數（保守預設 3；不穩時設 1）")
    parser.add_argument("--dormant-days", type=int,
                        default=config.DPM_DORMANT_DAYS,
                        help="逾此天數無場次視為沉寂帳號（預設 14）")
    parser.add_argument("--probe-interval-days", type=int,
                        default=config.DPM_PROBE_INTERVAL_DAYS,
                        help="沉寂帳號主動探測間隔天數（預設 7）")
    parser.add_argument("--leagues", default=",".join(config.DPM_ESPORT_LEAGUES),
                        help="逗號分隔官方聯賽代碼，用於登錄名單補遺")
    parser.add_argument("--no-rosters", action="store_true",
                        help="跳過官方聯賽登錄名單補遺階段")
    parser.add_argument("--skip-details", action="store_true",
                        help="跳過單場明細補抓")
    args = parser.parse_args()
    args.platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    args.leagues = [lg.strip() for lg in args.leagues.split(",") if lg.strip()]
    return args


def main() -> None:
    ScrapeRunner(_parse_args()).run()


if __name__ == "__main__":
    main()
