"""DPM.LOL 積分資料的 DuckDB 倉儲層（獨立 dpm.duckdb，不動 OE 倉儲）。

資料表：
- accounts        排行榜帳號最新快照（一列一 Riot 帳號）
- pros            職業選手（合併多帳號、真實資料、所屬戰隊）
- matches         對戰表頭（日期／時長／是否已補 10 人明細）
- match_players   對戰參與者列（本人統計＋明細補齊後的對位身份）
- scrape_meta     爬蟲狀態鍵值（最後成功時間等）
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import duckdb

from backend import config
from backend.pipeline.common import get_logger

logger = get_logger("dpm_store")

# ---------------------------------------------------------------------------
# DPM 戰隊代碼 → 賽區的人工補充（自動映射失敗時使用，學院隊沿用母隊賽區）
# ---------------------------------------------------------------------------
_TEAM_REGION_OVERRIDE = {
    "LOUD": "CBLOL",
}
_ACADEMY_SUFFIX = (".C", ".EA", "C", "EA")

# Riot 佇列 id：420＝單／雙排積分（本平台只收這個佇列）
SOLOQ_QUEUE_ID = 420

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    puuid VARCHAR PRIMARY KEY,
    display_name VARCHAR,
    game_name VARCHAR,
    tag_line VARCHAR,
    platform VARCHAR,
    team VARCHAR,
    role VARCHAR,
    lane VARCHAR,
    lane_pct INTEGER,
    profile_icon INTEGER,
    tier VARCHAR,
    rank_tier VARCHAR,
    league_points INTEGER,
    wins INTEGER,
    losses INTEGER,
    kda DOUBLE,
    champion_ids VARCHAR,
    is_live BOOLEAN,
    lb_position INTEGER,
    last_match_ts BIGINT,
    last_history_check_ts BIGINT,
    updated_at DOUBLE
);
CREATE TABLE IF NOT EXISTS pros (
    display_name VARCHAR PRIMARY KEY,
    team VARCHAR,
    role VARCHAR,
    real_name VARCHAR,
    country VARCHAR,
    birthdate VARCHAR,
    links VARCHAR,
    accounts VARCHAR,
    updated_at DOUBLE
);
CREATE TABLE IF NOT EXISTS matches (
    match_id VARCHAR PRIMARY KEY,
    platform VARCHAR,
    game_id BIGINT,
    start_ts BIGINT,
    duration_sec INTEGER,
    queue_id INTEGER,
    detail_loaded BOOLEAN,
    first_seen DOUBLE
);
CREATE TABLE IF NOT EXISTS match_players (
    match_id VARCHAR,
    puuid VARCHAR,
    win BOOLEAN,
    team_id INTEGER,
    champion_name VARCHAR,
    lane VARCHAR,
    kills INTEGER,
    deaths INTEGER,
    assists INTEGER,
    kill_participation DOUBLE,
    gold_diff15 INTEGER,
    xp_diff15 INTEGER,
    cs_diff15 INTEGER,
    first_to_lvl2 BOOLEAN,
    summoner1_id INTEGER,
    summoner2_id INTEGER,
    primary_rune INTEGER,
    secondary_rune INTEGER,
    runes_json VARCHAR,
    items_json VARCHAR,
    start_items_json VARCHAR,
    item_actions_json VARCHAR,
    skill_ups_json VARCHAR,
    dpm_score DOUBLE,
    dpm_score_rank INTEGER,
    opponent_champion VARCHAR,
    duo_champion VARCHAR,
    duo_lane VARCHAR,
    duo_opponent_champion VARCHAR,
    lp_delta INTEGER,
    tier VARCHAR,
    game_name VARCHAR,
    tag_line VARCHAR,
    display_name VARCHAR,
    team VARCHAR,
    role VARCHAR,
    raw_json VARCHAR,
    PRIMARY KEY (match_id, puuid)
);
CREATE TABLE IF NOT EXISTS scrape_meta (
    key VARCHAR PRIMARY KEY,
    value VARCHAR,
    updated_at DOUBLE
);
"""

# 既有倉儲的增量遷移（新建庫已含於 _SCHEMA_SQL，此處冪等補欄）
_MIGRATION_SQL = (
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS "
    "last_history_check_ts BIGINT",
)


# 跨進程一寫多讀下，開連線瞬間可能撞上其他行程的檔案鎖，
# 短暫等待重試即可（IOException 非永久性錯誤）
_OPEN_RETRY_WAITS = (0.3, 0.7, 1.5, 3.0, 5.0, 8.0)
# 多執行緒高頻開關連線時，鴨子內部連線快取可能出現暫時性的
# 組態／attach 碰撞，皆屬對方短暫持有、重試即可復原的類型
_RETRYABLE_CONNECT_HINTS = (
    "different configuration",
    "already attached",
    "unique file handle conflict",
)


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """開啟 dpm 倉儲連線（用完請 close）。

    Web 行程固定唯讀；爬蟲以獨立子進程執行（見 backend/dpm_runner 與
    cron 指令 python -m scripts.scrape_dpm），DuckDB 允許跨行程
    一個可寫＋多個唯讀並存；開啟瞬間的鎖碰撞與連線快取競態以指數等待重試。
    """
    last_exc: Exception | None = None
    for wait in (0.0,) + _OPEN_RETRY_WAITS:
        if wait:
            time.sleep(wait)
        try:
            return duckdb.connect(
                str(config.DPM_WAREHOUSE_PATH), read_only=read_only)
        except duckdb.IOException as exc:
            last_exc = exc
        except (duckdb.ConnectionException, duckdb.BinderException) as exc:
            # 僅針對「開連線階段」的暫時性碰撞重試；其餘綁定錯誤照原樣拋出
            if any(h in str(exc).lower() for h in _RETRYABLE_CONNECT_HINTS):
                last_exc = exc
                continue
            raise
    raise last_exc


# 多執行緒高頻開關寫入連線時，鴨子內部連線快取在「關閉釋放／重新 attach」
# 的縫隙會偶發 BinderException（Cannot attach "dpm" ... already attached），
# 故以全行程 RLock 把寫入連線的「開啟→操作→關閉」序列化，根除該競態；
# RLock 容許同執行緒巢狀呼叫而不死鎖。（實測純 write+write 並存雖可，
# 但快取競態與唯讀／寫入跨組態混用仍會炸，統一以鎖最穩。）
_WRITE_LOCK = threading.RLock()


@contextmanager
def write_con():
    """提供全行程唯一的寫入連線；離開區塊時關閉並釋放鎖。

    僅包住純 DB 操作，HTTP 請求不得持鎖，避免多執行緒退化為串行。
    """
    with _WRITE_LOCK:
        con = connect(read_only=False)
        try:
            yield con
        finally:
            con.close()


def init_db() -> None:
    """建立資料表（冪等），並執行增量欄位遷移。"""
    with write_con() as con:
        con.execute(_SCHEMA_SQL)
        for statement in _MIGRATION_SQL:
            con.execute(statement)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _now() -> float:
    return time.time()


def _dumps(value) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def _ts_to_date(ts_ms: int | None) -> str | None:
    if not ts_ms:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def upsert_meta(con, key: str, value: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO scrape_meta VALUES (?, ?, ?)",
        [key, value, _now()])


# ---------------------------------------------------------------------------
# 寫入：排行榜快照
# ---------------------------------------------------------------------------
def _account_row(player: dict, platform: str) -> list:
    """將排行榜 player 物件轉成 accounts 列。"""
    rank = player.get("rank") or {}
    lane = player.get("lane") or {}
    return [
        player.get("puuid"), player.get("displayName"),
        player.get("gameName"), player.get("tagLine"),
        rank.get("platform") or platform, player.get("team"),
        player.get("role"), lane.get("value"), lane.get("percentage"),
        player.get("profileIcon"), rank.get("tier"), rank.get("rank"),
        rank.get("leaguePoints"), rank.get("wins"), rank.get("losses"),
        player.get("kda"), _dumps(player.get("championIds")),
        bool(player.get("isLive")), player.get("leaderboardPosition"),
        None, _now(),
    ]


_ACCOUNT_COLS = ("puuid, display_name, game_name, tag_line, platform, team, "
                 "role, lane, lane_pct, profile_icon, tier, rank_tier, "
                 "league_points, wins, losses, kda, champion_ids, is_live, "
                 "lb_position, last_match_ts, updated_at")


def save_leaderboard(players: list[dict], platform: str,
                     total: int | None = None) -> int:
    """寫入一頁排行榜快照，回傳列數。

    以 UPSERT 更新快照欄位，並刻意保留 last_match_ts（對戰／pro 檔來源）
    與 last_history_check_ts（沉寂探測戳記），避免每輪快照被清空。
    """
    if not players:
        return 0
    rows = [_account_row(p, platform) for p in players]
    update_cols = [c for c in _ACCOUNT_COLS.split(", ")
                   if c not in ("puuid", "last_match_ts")]
    set_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
    sql = (f"INSERT INTO accounts ({_ACCOUNT_COLS}) "
           f"VALUES ({','.join('?' * 21)}) "
           f"ON CONFLICT (puuid) DO UPDATE SET {set_clause}, "
           "last_history_check_ts = accounts.last_history_check_ts")
    with write_con() as con:
        con.executemany(sql, rows)
        if total is not None:
            upsert_meta(con, f"lb_total_{platform}", str(total))
    return len(rows)


# ---------------------------------------------------------------------------
# 寫入：官方聯賽登錄名單（補排行榜門檻外的選手）
# ---------------------------------------------------------------------------
def save_esport_rosters(players: list[dict], league: str) -> int:
    """把聯賽 leaderboard 球員併入 accounts，回傳新增帳號數。

    聯賽名單是權威登錄源（含未進職業排行榜的低牌階選手）；
    已存在的帳號一律不覆寫（排行榜快照的欄位較完整且較新），
    僅插入缺漏帳號，role 標 PRO 以便 all_tracked_puuids 納入輪詢。
    """
    fresh = [p for p in players if p.get("puuid")]
    if not fresh:
        return 0
    with write_con() as con:
        placeholders = ",".join("?" * len(fresh))
        rows = con.execute(
            f"SELECT puuid FROM accounts WHERE puuid IN ({placeholders})",
            [p["puuid"] for p in fresh]).fetchall()
        known = {r[0] for r in rows}
        new_rows = []
        for p in fresh:
            if p["puuid"] in known:
                continue
            new_rows.append([
                p["puuid"], p.get("displayName"),
                None, None, None,
                p.get("team"), "PRO", p.get("lane"), None,
                None, p.get("tier"), p.get("rank"),
                p.get("leaguePoints"), p.get("wins"), p.get("losses"),
                p.get("kda"), "[]", False, None, None, _now(),
            ])
        if new_rows:
            con.executemany(
                f"INSERT INTO accounts ({_ACCOUNT_COLS}) "
                f"VALUES ({','.join('?' * 21)})", new_rows)
        upsert_meta(con, f"roster_count_{league}", str(len(fresh)))
    return len(new_rows)


# ---------------------------------------------------------------------------
# 寫入：職業選手檔（合併帳號）
# ---------------------------------------------------------------------------
def save_pro_profile(pro: dict) -> bool:
    """寫入 pro 檔，並把帳號的 last_match_ts／真實戰隊補進 accounts。

    回傳 True 表示已寫入；False 表示檔案缺少可用名稱而略過。
    """
    info = pro.get("esportPlayer") or {}
    linked = pro.get("players") or []
    display_name = info.get("overviewPage") or (
        linked[0].get("displayName") if linked else None)
    if not display_name:
        return False
    with write_con() as con:
        con.execute(
            "INSERT OR REPLACE INTO pros VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [display_name, info.get("team"), info.get("role"),
             info.get("name"), info.get("country"),
             # birthdate 可能為 null，先轉空字串再截日期
             (info.get("birthdate") or "")[:10],
             _dumps((linked[0].get("links") or []) if linked else []),
             _dumps([a.get("puuid") for a in linked]), _now()])
        for acc in linked:
            _merge_pro_account(con, acc, display_name, info.get("team"))
    return True


def _merge_pro_account(con, acc: dict, display_name: str,
                       pro_team: str | None) -> None:
    """pro 檔帳號已在榜上則補 last_match_ts／戰隊；不在則插最小列。"""
    ranks = {r.get("queue"): r for r in (acc.get("ranks") or [])}
    solo = ranks.get("RANKED_SOLO_5x5", {})
    existing = con.execute(
        "SELECT puuid FROM accounts WHERE puuid = ?",
        [acc.get("puuid")]).fetchone()
    if existing:
        con.execute(
            "UPDATE accounts SET last_match_ts = ?, team = COALESCE(team, ?), "
            "display_name = COALESCE(display_name, ?) WHERE puuid = ?",
            [acc.get("lastMatchTimestamp"), pro_team, display_name,
             acc.get("puuid")])
        return
    con.execute(
        f"INSERT OR REPLACE INTO accounts ({_ACCOUNT_COLS}) "
        f"VALUES ({','.join('?' * 21)})",
        [acc.get("puuid"), display_name, acc.get("gameName"),
         acc.get("tagLine"), acc.get("platform"), pro_team, acc.get("role"),
         acc.get("lane"), None, acc.get("profileIcon"),
         solo.get("tier"), solo.get("rank"), solo.get("leaguePoints"),
         solo.get("wins"), solo.get("losses"), None, "[]", False, None,
         acc.get("lastMatchTimestamp"), _now()])


# ---------------------------------------------------------------------------
# 寫入：對戰清單與 10 人明細
# ---------------------------------------------------------------------------
def save_match_list(puuid: str, matches: list[dict]) -> list[str]:
    """寫入對戰表頭與本人統計列，回傳本次新見（待補明細）的 match_id 清單。"""
    new_ids: list[str] = []
    with write_con() as con:
        for m in matches:
            mid = f"{m.get('platformId')}_{m.get('gameId')}"
            known = con.execute(
                "SELECT 1 FROM matches WHERE match_id = ?", [mid]).fetchone()
            if not known:
                new_ids.append(mid)
            # 表頭不可因重複看到就覆寫 detail_loaded：已存在就完全保留，
            # 僅新場景以預設 FALSE 寫入，待 save_match_detail 補齊 10 人。
            con.execute(
                "INSERT OR IGNORE INTO matches VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?)",
                [mid, m.get("platformId"), m.get("gameId"),
                 m.get("gameCreation"), m.get("gameDuration"),
                 m.get("queueId"), False, _now()])
            parts = m.get("participants") or []
            me = next((p for p in parts if p.get("puuid") == puuid), None)
            if me:
                _upsert_match_player(con, mid, me)
    return new_ids


def save_match_detail(match: dict) -> bool:
    """寫入單場 10 人完整明細並標記 detail_loaded。"""
    mid = f"{match.get('platformId')}_{match.get('gameId')}"
    with write_con() as con:
        for p in match.get("participants") or []:
            _upsert_match_player(con, mid, p)
        con.execute(
            "UPDATE matches SET detail_loaded = TRUE WHERE match_id = ?",
            [mid])
    return True


def _upsert_match_player(con, match_id: str, p: dict) -> None:
    """參與者物件 → match_players 列（保留完整 raw JSON 供日後擴欄）。"""
    con.execute(
        "INSERT OR REPLACE INTO match_players VALUES ("
        + ",".join("?" * 37) + ")",
        [match_id, p.get("puuid"), bool(p.get("win")), p.get("teamId"),
         p.get("championName"), p.get("lane"),
         p.get("kills"), p.get("deaths"), p.get("assists"),
         p.get("killParticipation"), p.get("goldDiffAt15"),
         p.get("xpDiffAt15"), p.get("csDiffAt15"),
         p.get("isFirstToHitLevel2"),
         p.get("summoner1Id"), p.get("summoner2Id"),
         p.get("primaryRuneId"), p.get("secondaryRuneId"),
         _dumps({"p": p.get("primaryRuneId"), "p2": p.get("primaryRuneId2"),
                 "p3": p.get("primaryRuneId3"), "p4": p.get("primaryRuneId4"),
                 "s": p.get("secondaryRuneId"),
                 "s2": p.get("secondaryRuneId2"),
                 "s3": p.get("secondaryRuneId3"),
                 "st1": p.get("perksStat1"), "st2": p.get("perksStat2"),
                 "st3": p.get("perksStat3")}),
         _dumps(p.get("itemIds")), _dumps(p.get("startItems")),
         _dumps(p.get("itemActions")), _dumps(p.get("skillLevelUps")),
         p.get("dpmScore"), p.get("dpmScoreRank"),
         p.get("opponentChampionName"), p.get("duoChampionName"),
         p.get("duoLane"), p.get("duoOpponentChampionName"),
         p.get("lp"), p.get("tier"),
         p.get("gameName"), p.get("tagLine"),
         p.get("displayName"), p.get("team"), p.get("role"),
         _dumps(p)])


# ---------------------------------------------------------------------------
# 寫入／讀取：爬蟲狀態
# ---------------------------------------------------------------------------
def set_meta(key: str, value: str) -> None:
    with write_con() as con:
        upsert_meta(con, key, value)


def get_meta(key: str, default: str | None = None) -> str | None:
    if not config.DPM_WAREHOUSE_PATH.exists():
        return default
    con = connect(read_only=True)
    try:
        row = con.execute(
            "SELECT value FROM scrape_meta WHERE key = ?", [key]).fetchone()
        return row[0] if row else default
    finally:
        con.close()


def meta_fresh(key: str, within_sec: float = 900) -> bool:
    """指定 meta 鍵是否在最近 N 秒內被更新（跨進程爬蟲存活判斷）。"""
    if not config.DPM_WAREHOUSE_PATH.exists():
        return False
    con = connect(read_only=True)
    try:
        row = con.execute(
            "SELECT updated_at FROM scrape_meta WHERE key = ?",
            [key]).fetchone()
    finally:
        con.close()
    return bool(row and row[0] and time.time() - row[0] <= within_sec)


def pending_detail_match_ids(within_days: int) -> list[tuple[str, str, str]]:
    """找出最近 N 天、尚未補 10 人明細的單雙排場景（回傳 puuid, platform, gameid）。

    只收 queue 420：競技場／彈性／一般場次 UI 不顯示，無須浪費明細請求。
    """
    if not config.DPM_WAREHOUSE_PATH.exists():
        return []
    cutoff = (time.time() - within_days * 86400) * 1000
    con = connect(read_only=True)
    try:
        rows = con.execute(
            "SELECT mp.puuid, m.platform, m.game_id, MAX(m.start_ts) "
            "FROM matches m JOIN match_players mp "
            "ON mp.match_id = m.match_id "
            "WHERE m.detail_loaded = FALSE AND m.start_ts >= ? "
            "AND m.queue_id = ? "
            "GROUP BY mp.puuid, m.platform, m.game_id "
            "ORDER BY MAX(m.start_ts) DESC",
            [cutoff, SOLOQ_QUEUE_ID]).fetchall()
    finally:
        con.close()
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# 讀取：對戰輪詢名單（含沉寂帳號每週探一次的節流）
# ---------------------------------------------------------------------------
_DAY_MS = 86_400_000


def puuids_due_for_history(now_ms: int | None = None,
                           dormant_days: int = config.DPM_DORMANT_DAYS,
                           probe_every_days: int = config.DPM_PROBE_INTERVAL_DAYS
                           ) -> list[tuple[str, bool]]:
    """回傳本輪應輪詢對戰清單的 (puuid, is_dormant)。

    - 活躍帳號（近 dormant_days 天內有場次，或從未探測過的新帳號）：每輪輪詢；
    - 沉寂帳號（最後場次已逾 dormant_days 天）：若 probe_every_days 天內
      已探測過則跳過，一週只主動探一次，省下大量無謂請求。
    last_match_ts 為 NULL 的新入庫帳號一律納入（首次需回填歷史）。
    """
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    dormant_cutoff = now_ms - dormant_days * _DAY_MS
    probe_cutoff = now_ms - probe_every_days * _DAY_MS
    con = connect(read_only=True)
    try:
        rows = con.execute(
            """
            SELECT puuid, COALESCE(last_match_ts, 0) < ? AS is_dormant
            FROM accounts
            WHERE (role = 'PRO' OR display_name IS NOT NULL)
              AND NOT (COALESCE(last_match_ts, 0) < ?
                       AND COALESCE(last_history_check_ts, 0) >= ?)
            ORDER BY COALESCE(lb_position, 999999),
                     last_match_ts DESC NULLS LAST
            """,
            [dormant_cutoff, dormant_cutoff, probe_cutoff]).fetchall()
    finally:
        con.close()
    return [(r[0], bool(r[1])) for r in rows]


def mark_history_checked(puuids: list[str], ts_ms: int | None = None) -> int:
    """批次記錄沉寂帳號的對戰探測時間，回傳更新列數。"""
    if not puuids:
        return 0
    ts_ms = ts_ms if ts_ms is not None else int(time.time() * 1000)
    with write_con() as con:
        con.executemany(
            "UPDATE accounts SET last_history_check_ts = ? WHERE puuid = ?",
            [(ts_ms, p) for p in puuids])
    return len(puuids)


def all_tracked_puuids(active_since_ts: int = 0) -> list[str]:
    """已收錄帳號（供增量輪詢對戰清單）。

    凡有 display_name 的帳號都會出現在積分榜，故一律輪詢，
    不因最後出賽日較久而漏爬（排行榜可能仍掛著久未出賽的選手）；
    排序以排行榜名次優先、再計近期出賽，讓榜單前段儘快有資料。
    active_since_ts 參數保留相容性，目前不再過濾。
    """
    if not config.DPM_WAREHOUSE_PATH.exists():
        return []
    con = connect(read_only=True)
    try:
        rows = con.execute(
            "SELECT puuid FROM accounts "
            "WHERE role = 'PRO' OR display_name IS NOT NULL "
            "ORDER BY COALESCE(lb_position, 999999), "
            "last_match_ts DESC NULLS LAST").fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()
