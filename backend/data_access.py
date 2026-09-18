"""DuckDB 唯讀查詢層：提供儀表板與聚合使用的參數化查詢。

所有函式接收 Filter，內部以參數綁定方式組 SQL，避免字串拼接注入。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import duckdb
import pandas as pd

from backend import config


@dataclass
class Filter:
    """頁面查詢篩選條件；None/空值代表不限制。"""
    leagues: list[str] | None = None
    years: list[int] | None = None
    patches: list[str] | None = None
    date_start: str | None = None
    date_end: str | None = None
    positions: list[str] | None = None
    teams: list[str] | None = None
    search: str | None = None
    # 全站搜尋列的英雄名／戰隊或選手關鍵字（模糊比對）
    champion: str | None = None
    member: str | None = None

    def cache_key(self) -> str:
        return repr(self.__dict__)


# 各區頂級聯賽（戰隊頁預設口徑，對照 DPM.LOL 六大賽區）
TOP_LEAGUES = ["LCP", "LPL", "LCK", "LEC", "LCS", "CBLOL"]
# 不計入頂級聯賽口徑的表演賽／對抗賽賽段
EXCLUDE_SPLITS = ("Versus",)


def latest_top_year() -> int:
    """頂級聯賽有數據的最新年份（2027 僅盃賽時自然回退 2026）。"""
    con = connect()
    try:
        row = con.execute(
            f"SELECT MAX(year) FROM team_games "
            f"WHERE league IN ({','.join('?' * len(TOP_LEAGUES))})",
            TOP_LEAGUES).fetchone()
    finally:
        con.close()
    return int(row[0]) if row and row[0] else 2026


def default_team_filter() -> Filter:
    """戰隊頁預設：最新賽季的六大頂級聯賽。"""
    return Filter(years=[latest_top_year()], leagues=list(TOP_LEAGUES))


# 戰隊官方縮寫／LOGO 對照（scripts.fetch_team_logos 產生）
TEAM_META_PATH = Path(__file__).parent / "data" / "team_meta.json"


@lru_cache(maxsize=1)
def load_team_meta() -> dict:
    """讀取隊名→縮寫／LOGO 對照；檔案不存在時降級為空。"""
    if not TEAM_META_PATH.exists():
        return {}
    try:
        return json.loads(TEAM_META_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[警告] 無法讀取 team_meta.json：{exc}")
        return {}


# 跨進程一寫多讀下，開連線瞬間可能撞上倉儲重建（pipeline 寫 .tmp 後
# os.replace 原子取代）或爬蟲寫入的檔案鎖，短暫等待重試即可復原
_OPEN_RETRY_WAITS = (0.3, 0.7, 1.5, 3.0, 5.0, 8.0)
# 鴨子內部連線快取暫時性的組態／attach 碰撞，同樣屬重試可恢復類型
_RETRYABLE_CONNECT_HINTS = (
    "different configuration",
    "already attached",
    "unique file handle conflict",
)


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """開啟倉儲連線（預設唯讀，用完請 close）。

    查詢層固定唯讀；倉儲重建（backend.pipeline）與 DPM 爬蟲皆為獨立
    行程，開連線瞬間的檔案鎖碰撞與連線快取競態以指數等待重試，
    其餘永久性錯誤（如檔案不存在、真綁定錯誤）照原樣拋出。
    """
    if not Path(config.WAREHOUSE_PATH).exists():
        raise FileNotFoundError(
            f"找不到倉儲 {config.WAREHOUSE_PATH}，請先執行 python -m backend.pipeline all"
        )
    last_exc: Exception | None = None
    for wait in (0.0,) + _OPEN_RETRY_WAITS:
        if wait:
            time.sleep(wait)
        try:
            return duckdb.connect(str(config.WAREHOUSE_PATH),
                                  read_only=read_only)
        except duckdb.IOException as exc:
            last_exc = exc
        except (duckdb.ConnectionException, duckdb.BinderException) as exc:
            # 僅針對「開連線階段」的暫時性碰撞重試；其餘綁定錯誤立即拋出
            if any(h in str(exc).lower() for h in _RETRYABLE_CONNECT_HINTS):
                last_exc = exc
                continue
            raise
    raise last_exc


def _game_where(f: Filter) -> tuple[str, list]:
    """組 games 表 WHERE 子句與參數。"""
    clauses, params = [], []
    if f.leagues:
        clauses.append(f"league IN ({','.join('?' * len(f.leagues))})")
        params.extend(f.leagues)
    if f.years:
        clauses.append(f"year IN ({','.join('?' * len(f.years))})")
        params.extend([int(y) for y in f.years])
    if f.patches:
        clauses.append(f"patch IN ({','.join('?' * len(f.patches))})")
        params.extend([float(p) for p in f.patches])
    if f.date_start:
        clauses.append("date_iso::DATE >= ?::DATE")
        params.append(f.date_start)
    if f.date_end:
        clauses.append("date_iso::DATE <= ?::DATE")
        params.append(f.date_end)
    if f.teams:
        clauses.append(
            f"(blue_team IN ({','.join('?' * len(f.teams))}) "
            f"OR red_team IN ({','.join('?' * len(f.teams))}))"
        )
        params.extend(f.teams)
    # 英雄／戰隊選手關鍵字：games 表無此欄，以對局子查詢縮限
    if f.champion:
        clauses.append(
            "gameid IN (SELECT gameid FROM player_games "
            "WHERE champion ILIKE ?)")
        params.append(f"%{f.champion}%")
    if f.member:
        clauses.append(
            "gameid IN (SELECT gameid FROM player_games "
            "WHERE teamname ILIKE ? OR playername ILIKE ?)")
        params.extend([f"%{f.member}%"] * 2)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _pg_where(f: Filter, include_position: bool = True) -> tuple[str, list]:
    """組 player_games 表 WHERE 子句。"""
    clauses, params = [], []
    if f.leagues:
        clauses.append(f"league IN ({','.join('?' * len(f.leagues))})")
        params.extend(f.leagues)
    if f.years:
        clauses.append(f"year IN ({','.join('?' * len(f.years))})")
        params.extend([int(y) for y in f.years])
    if f.patches:
        clauses.append(f"patch IN ({','.join('?' * len(f.patches))})")
        params.extend([float(p) for p in f.patches])
    if f.date_start:
        clauses.append("date_iso::DATE >= ?::DATE")
        params.append(f.date_start)
    if f.date_end:
        clauses.append("date_iso::DATE <= ?::DATE")
        params.append(f.date_end)
    if include_position and f.positions:
        placeholders = ",".join("?" * len(f.positions))
        clauses.append(f'"position" IN ({placeholders})')
        params.extend(f.positions)
    if f.teams:
        clauses.append(f"teamname IN ({','.join('?' * len(f.teams))})")
        params.extend(f.teams)
    if f.champion:
        clauses.append("champion ILIKE ?")
        params.append(f"%{f.champion}%")
    if f.member:
        clauses.append("(teamname ILIKE ? OR playername ILIKE ?)")
        params.extend([f"%{f.member}%"] * 2)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _run_df(sql: str, params: list | None = None) -> pd.DataFrame:
    with connect() as con:
        return con.execute(sql, params or []).fetchdf()


# ---------------------------------------------------------------------------
# 篩選選項
# ---------------------------------------------------------------------------
def get_options() -> dict:
    """取得全域篩選所需選項。

    排序口徑（全站下拉一致）：
    - 賽事：依樣本數（比賽場數）由多到少，同名再按字母；
    - 賽季／版本：時間倒序，較新者置頂。
    """
    leagues = _run_df(
        "SELECT league FROM games WHERE league IS NOT NULL "
        "GROUP BY league ORDER BY COUNT(*) DESC, league"
    )["league"].tolist()
    years = _run_df("SELECT DISTINCT year FROM games ORDER BY year DESC")
    years = [int(y) for y in years["year"].tolist()]
    patches = _run_df("SELECT DISTINCT patch FROM games WHERE patch IS NOT NULL")
    patches = sorted({f"{float(p):g}" for p in patches["patch"].tolist()},
                     key=lambda x: float(x), reverse=True)
    teams = _run_df(
        "SELECT DISTINCT blue_team AS t FROM games "
        "UNION SELECT DISTINCT red_team FROM games ORDER BY t"
    )["t"].dropna().tolist()
    date_range = _run_df("SELECT MIN(date_iso) a, MAX(date_iso) b FROM games")
    return {
        "leagues": leagues, "years": years, "patches": patches, "teams": teams,
        "date_min": str(date_range["a"][0]), "date_max": str(date_range["b"][0]),
    }


def count_games(f: Filter) -> int:
    where, params = _game_where(f)
    return int(_run_df(f"SELECT COUNT(*) c FROM games{where}", params)["c"][0])


# ---------------------------------------------------------------------------
# 總覽
# ---------------------------------------------------------------------------
def overview_kpi(f: Filter) -> dict:
    """總場數、藍紅方勝率、場均時長（分）。"""
    where, params = _game_where(f)
    row = _run_df(
        f"SELECT COUNT(*) games, "
        f"AVG(blue_result)*100 blue_win, AVG(red_result)*100 red_win, "
        f"AVG(gamelength)/60 avg_len FROM games{where}",
        params,
    ).iloc[0]
    return {
        "games": int(row["games"]),
        "blue_win_rate": round(float(row["blue_win"] or 0), config.RATE_DECIMALS),
        "red_win_rate": round(float(row["red_win"] or 0), config.RATE_DECIMALS),
        "avg_game_length": round(float(row["avg_len"] or 0), 1),
    }


def games_by_day(f: Filter, days: int = 7) -> pd.DataFrame:
    """近 N 日場次與賽區分布。"""
    where, params = _game_where(f)
    sql = (
        f"SELECT date_iso, league, COUNT(*) games FROM games{where} "
        f"GROUP BY 1,2 HAVING date_iso::DATE >= "
        f"(SELECT MAX(date_iso::DATE) FROM games) - INTERVAL '{int(days)}' DAY "
        f"ORDER BY 1"
    )
    return _run_df(sql, params)


def patch_changes(f: Filter, top_n: int = 8) -> dict:
    """相鄰兩個版本的英雄 BP%（選＋禁）升降榜。"""
    where, params = _pg_where(f)
    patch_cond = (" AND " if where else " WHERE ") + "patch IS NOT NULL"
    patches = _run_df(
        f"SELECT DISTINCT patch FROM player_games{where}{patch_cond} "
        "ORDER BY patch DESC LIMIT 2",
        params,
    )["patch"].tolist()
    if len(patches) < 2:
        return {"new_patch": None, "old_patch": None, "risers": [], "fallers": []}
    new_p, old_p = float(patches[0]), float(patches[1])
    total_new = count_games(Filter(**{**f.__dict__, "patches": [f"{new_p:g}"]}))
    total_old = count_games(Filter(**{**f.__dict__, "patches": [f"{old_p:g}"]}))

    # 選用次數（遵循賽區/年份等篩選）
    pick_conn = " AND " if where else " WHERE "
    picks = _run_df(
        f"SELECT champion, patch, COUNT(*) n FROM player_games{where}"
        f"{pick_conn} patch IN (?, ?) AND champion IS NOT NULL GROUP BY 1,2",
        params + [new_p, old_p],
    )
    # 禁用次數
    ban_where, ban_params = _pg_where(f, include_position=False)
    ban_conn = " AND " if ban_where else " WHERE "
    bans = _run_df(
        f"SELECT champion, patch, COUNT(*) n FROM draft_bans{ban_where}"
        f"{ban_conn} patch IN (?, ?) GROUP BY 1,2",
        ban_params + [new_p, old_p],
    )
    combined = pd.concat([picks, bans]).groupby(
        ["champion", "patch"], as_index=False)["n"].sum()
    if combined.empty:
        return {"new_patch": f"{new_p:g}", "old_patch": f"{old_p:g}",
                "risers": [], "fallers": []}
    pivot = combined.pivot_table(index="champion", columns="patch", values="n",
                                 fill_value=0)
    pivot["new_rate"] = pivot.get(new_p, 0) / max(total_new, 1) * 100
    pivot["old_rate"] = pivot.get(old_p, 0) / max(total_old, 1) * 100
    pivot["delta"] = pivot["new_rate"] - pivot["old_rate"]
    cols = {"new_rate": config.RATE_DECIMALS, "old_rate": config.RATE_DECIMALS,
            "delta": config.RATE_DECIMALS}

    def _pack(dd):
        out = dd.reset_index()
        for c, d in cols.items():
            out[c] = out[c].round(d)
        return out.to_dict("records")

    risers = _pack(pivot.sort_values("delta", ascending=False).head(top_n))
    fallers = _pack(pivot.sort_values("delta").head(top_n))
    return {"new_patch": f"{new_p:g}", "old_patch": f"{old_p:g}",
            "risers": risers, "fallers": fallers}


def active_stages(f: Filter, recent_days: int = 30) -> pd.DataFrame:
    """近 N 天內有比賽的賽區／賽段（進行中賽段面板用）。"""
    where, params = _game_where(f)
    conn = " AND " if where else " WHERE "
    return _run_df(
        f"SELECT league, split, MAX(date_iso) last_date, COUNT(*) games "
        f"FROM games{where}{conn} date_iso::DATE >= "
        f"(SELECT MAX(date_iso::DATE) FROM games) - INTERVAL "
        f"'{int(recent_days)}' DAY GROUP BY 1,2 ORDER BY games DESC",
        params,
    )


def team_streaks(f: Filter, top_n: int = 5) -> dict:
    """戰隊最長連勝／連敗（依篩選範圍內時間序）。"""
    where, params = _game_where(f)
    df = _run_df(
        f"SELECT date_iso, blue_team, red_team, blue_result, red_result "
        f"FROM games{where} ORDER BY date_iso, gameid", params
    )
    records = []
    for _, r in df.iterrows():
        if pd.isna(r["blue_result"]) or pd.isna(r["red_result"]):
            continue
        records.append((r["blue_team"], int(r["blue_result"])))
        records.append((r["red_team"], int(r["red_result"])))
    best_win, best_loss = {}, {}
    cur: dict[str, int] = {}
    for team, result in records:
        cur[team] = cur.get(team, 0) + 1 if result == 1 else 0
        best_win[team] = max(best_win.get(team, 0), cur[team])
    cur = {}
    for team, result in records:
        cur[team] = cur.get(team, 0) + 1 if result == 0 else 0
        best_loss[team] = max(best_loss.get(team, 0), cur[team])
    wins = sorted(best_win.items(), key=lambda x: x[1], reverse=True)[:top_n]
    losses = sorted(best_loss.items(), key=lambda x: x[1], reverse=True)[:top_n]
    return {"win_streak": wins, "lose_streak": losses}


# ---------------------------------------------------------------------------
# 積分榜（由對戰記錄推算；OE 無官方積分，大場＝同日同對手分組的系列賽）
# ---------------------------------------------------------------------------
def standings_groups(f: Filter) -> pd.DataFrame:
    """篩選範圍內可用的賽區/年份/賽段與場數。"""
    where, params = _game_where(f)
    return _run_df(
        "SELECT league, year, "
        "COALESCE(NULLIF(split, ''), '未分賽段') AS split, COUNT(*) games "
        f"FROM games{where} GROUP BY 1,2,3 "
        "ORDER BY year DESC, league, split", params
    )


def _series_rows(f: Filter) -> pd.DataFrame:
    """組出每個系列賽每隊一列：場數、勝場數。"""
    where, params = _game_where(f)
    sql = f"""
        WITH g AS (SELECT * FROM games{where}),
        team_rows AS (
            SELECT g.date_iso, g.league, g.year,
                   COALESCE(NULLIF(g.split, ''), '未分賽段') AS split,
                   g.playoffs,
                   t.teamname AS team,
                   CASE WHEN t.side='Blue' THEN g.red_team
                        ELSE g.blue_team END AS opponent,
                   t.result AS won, t.gameid
            FROM g JOIN team_games t ON t.gameid = g.gameid
        )
        SELECT date_iso, league, year, split, playoffs, team, opponent,
               COUNT(*) games_n, SUM(won) wins_n
        FROM team_rows
        GROUP BY 1,2,3,4,5,6,7
        ORDER BY date_iso
    """
    return _run_df(sql, params)


def _streak(rows: pd.DataFrame) -> tuple[int, str]:
    """依時間序計算當前連勝/連敗場數（大場）。"""
    count, kind = 0, ""
    for outcome in rows["outcome"]:
        if outcome == kind:
            count += 1
        else:
            count, kind = 1, outcome
    return count, kind


def standings(f: Filter, include_playoffs: bool = False) -> pd.DataFrame:
    """各賽區/賽段戰隊積分榜：大場勝負、小場勝負、淨勝場、當前連勢。"""
    series = _series_rows(f)
    if series.empty:
        return series
    if not include_playoffs:
        series = series[series["playoffs"].fillna(0).astype(int) == 0]
    # 系列賽勝負：勝場逾半即拿下大場（BO1 必有勝方）
    series["outcome"] = [
        "W" if w * 2 > g else ("L" if w * 2 < g else "D")
        for w, g in zip(series["wins_n"], series["games_n"])]
    records = []
    group_cols = ["league", "year", "split", "team"]
    for keys, grp in series.groupby(group_cols, sort=False):
        grp = grp.sort_values("date_iso")
        series_w = int((grp["outcome"] == "W").sum())
        series_l = int((grp["outcome"] == "L").sum())
        series_d = int((grp["outcome"] == "D").sum())
        game_w = int(grp["wins_n"].sum())
        game_l = int(grp["games_n"].sum() - game_w)
        streak_n, streak_type = _streak(grp)
        records.append({
            "league": keys[0], "year": int(keys[1]), "split": keys[2],
            "team": keys[3], "series_w": series_w, "series_l": series_l,
            "series_d": series_d, "game_w": game_w, "game_l": game_l,
            "game_diff": game_w - game_l,
            "win_rate": round(game_w / max(game_w + game_l, 1) * 100,
                              config.RATE_DECIMALS),
            "streak_n": streak_n, "streak_type": streak_type,
        })
    out = pd.DataFrame(records)
    out = out.sort_values(
        ["league", "year", "split", "series_w", "game_diff", "game_w"],
        ascending=[True, False, True, False, False, False]
    ).reset_index(drop=True)
    # 各賽段獨立排名
    out["rank"] = out.groupby(["league", "year", "split"]).cumcount() + 1
    return out


# ---------------------------------------------------------------------------
# 近況
# ---------------------------------------------------------------------------
def recent_series(f: Filter, limit: int = 50) -> list[dict]:
    """以日期＋兩隊分組的近期系列賽。"""
    where, params = _game_where(f)
    df = _run_df(
        f"SELECT date_iso, league, patch, blue_team, red_team, blue_result, "
        f"red_result, gamelength, game FROM games{where} "
        f"ORDER BY date_iso DESC, gameid LIMIT {int(limit) * 6}", params
    )
    series: dict[tuple, dict] = {}
    for _, r in df.iterrows():
        key = (r["date_iso"], r["league"], r["blue_team"], r["red_team"])
        item = series.setdefault(key, {
            "date": r["date_iso"], "league": r["league"],
            "blue": r["blue_team"], "red": r["red_team"],
            "blue_wins": 0, "red_wins": 0, "games": [],
        })
        if pd.isna(r["blue_result"]) or pd.isna(r["red_result"]):
            continue
        item["blue_wins"] += int(r["blue_result"])
        item["red_wins"] += int(r["red_result"])
        winner = r["blue_team"] if r["blue_result"] else r["red_team"]
        item["games"].append({
            "game": int(r["game"]) if pd.notna(r["game"]) else len(item["games"]) + 1,
            "patch": f"{float(r['patch']):g}" if pd.notna(r["patch"]) else None,
            "winner": winner, "length": round(float(r["gamelength"]) / 60, 1),
        })
    out = sorted(series.values(), key=lambda x: x["date"], reverse=True)
    return out[:limit]


# ---------------------------------------------------------------------------
# 英雄聚合
# ---------------------------------------------------------------------------
def champion_stats(f: Filter, min_games: int = 0) -> pd.DataFrame:
    """英雄出場、勝負、BP、KDA、金錢/傷害等聚合。"""
    pg_where, pg_params = _pg_where(f)
    total_games = count_games(f)
    picks_sql = f"""
        SELECT champion,
            COUNT(*) games,
            mode("position") main_position,
            SUM(result) wins,
            SUM(CASE WHEN side='Blue' THEN 1 ELSE 0 END) blue_games,
            SUM(CASE WHEN side='Red' THEN 1 ELSE 0 END) red_games,
            SUM(CASE WHEN side='Blue' THEN result ELSE 0 END) blue_wins,
            SUM(CASE WHEN side='Red' THEN result ELSE 0 END) red_wins,
            SUM(kills) kills, SUM(deaths) deaths, SUM(assists) assists,
            AVG((kills+assists)*1.0/NULLIF(teamkills,0))*100 kp,
            AVG(dpm) dpm,
            AVG(damagetakenperminute) dtaken,
            AVG(totalgold*60.0/NULLIF(gamelength,0)) gpm,
            AVG(COALESCE(golddiffat25-golddiffat10, golddiffat15)) gold_mid
        FROM player_games{pg_where}
        GROUP BY champion HAVING COUNT(*) >= ?
    """
    picks = _run_df(picks_sql, pg_params + [int(min_games)])
    if picks.empty:
        return picks
    ban_where, ban_params = _pg_where(f, include_position=False)
    bans = _run_df(
        f"SELECT champion, COUNT(*) bans FROM draft_bans{ban_where} "
        "GROUP BY champion", ban_params
    )
    df = picks.merge(bans, on="champion", how="left").fillna({"bans": 0})
    df["win_rate"] = (df["wins"] / df["games"] * 100).round(config.RATE_DECIMALS)
    # 藍/紅方勝率（該方有出賽才計算，0 場轉 NaN；勿用 pd.NA，否則 object.round 爆 NAType）
    blue_denom = df["blue_games"].where(df["blue_games"] != 0)
    red_denom = df["red_games"].where(df["red_games"] != 0)
    df["blue_win_rate"] = (df["blue_wins"] / blue_denom * 100).round(
        config.RATE_DECIMALS)
    df["red_win_rate"] = (df["red_wins"] / red_denom * 100).round(
        config.RATE_DECIMALS)
    df["ban_rate"] = (df["bans"] / max(total_games, 1) * 100).round(
        config.RATE_DECIMALS)
    df["pick_rate"] = (df["games"] / max(total_games, 1) * 100).round(
        config.RATE_DECIMALS)
    df["bp_rate"] = (df["pick_rate"] + df["ban_rate"]).round(config.RATE_DECIMALS)
    death_denom = df["deaths"].where(df["deaths"] != 0)
    df["kda"] = ((df["kills"] + df["assists"]) / death_denom).round(2).fillna(
        (df["kills"] + df["assists"])).round(2)
    df["wins"] = df["wins"].astype(int)
    df["bans"] = df["bans"].astype(int)
    return df.sort_values("games", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 英雄排行（對照 DPM.LOL：BP/禁用/勝負/贏線/後選/參與/KDA/分均數據/同路評分）
# ---------------------------------------------------------------------------
def _draft_scope(f: Filter, teams: list[str] | None = None
                 ) -> tuple[str, list]:
    """draft_bans/draft_picks 專用範圍（無位置欄；可限定戰隊）。"""
    where, params = _scope_without_position(f)
    if teams:
        placeholders = ",".join("?" * len(teams))
        where = f"{where} AND teamname IN ({placeholders})" if where else \
            f" WHERE teamname IN ({placeholders})"
        params.extend(teams)
    return where, params


def _resolve_member_teams(f: Filter, keyword: str) -> list[str]:
    """關鍵字若命中戰隊名，回傳相符戰隊（供 draft 表縮小範圍）。"""
    base_where, base_params = _scope_without_position(f)
    clause = f"{base_where} AND " if base_where else " WHERE "
    sql = (f"SELECT DISTINCT teamname FROM player_games{clause}"
           "teamname ILIKE ?")
    try:
        df = _run_df(sql, base_params + [f"%{keyword}%"])
        return df["teamname"].dropna().tolist()
    except Exception:  # noqa: BLE001
        return []


def champion_ranking(f: Filter, min_games: int = 0,
                     champ_search: str | None = None,
                     member_search: str | None = None) -> pd.DataFrame:
    """英雄排行聚合：BP 率、禁用率、勝負、贏線率、後選率、參團、KDA、分均數據。

    - champ_search：英雄名模糊比對；member_search：戰隊或選手模糊比對；
    - 贏線率以 15 分金差為正的比例代理（OE 無逐兵對位結果）；
    - 後選率＝該英雄被選時，所屬戰隊第 4/5 手拿的比例；
    - 路線分布回傳 positions 清單（由高至低，含佔比）。
    """
    where, params = _pg_where(f)
    extra, extra_params = [], []
    if champ_search:
        extra.append("champion ILIKE ?")
        extra_params.append(f"%{champ_search}%")
    if member_search:
        extra.append("(teamname ILIKE ? OR playername ILIKE ?)")
        extra_params.extend([f"%{member_search}%"] * 2)
    if extra:
        where = (where + " AND " if where else " WHERE ") + \
                " AND ".join(extra)

    denom = _run_df(
        f"SELECT COUNT(DISTINCT gameid) n FROM player_games{where}",
        params + extra_params)
    total_games = int(denom["n"].iloc[0]) if not denom.empty else 0

    picks_sql = f"""
        SELECT champion,
            COUNT(*) games,
            SUM(result) wins,
            SUM(kills) kills, SUM(deaths) deaths, SUM(assists) assists,
            AVG(kills*1.0) k_pg,
            AVG(deaths*1.0) d_pg,
            AVG(assists*1.0) a_pg,
            AVG((kills+assists)*1.0/NULLIF(teamkills,0))*100 kp,
            AVG(dpm) dpm,
            AVG(damagetakenperminute) dtaken,
            AVG(vspm) vspm,
            AVG(totalgold*60.0/NULLIF(gamelength,0)) gpm,
            AVG(golddiffat15) gold15,
            AVG(CASE WHEN COALESCE(golddiffat15,0) > 0 THEN 100.0 ELSE 0.0 END)
                lane_win
        FROM player_games{where}
        GROUP BY champion HAVING COUNT(*) >= ?
    """
    df = _run_df(picks_sql, params + extra_params + [int(min_games)])
    if df.empty:
        return df

    # 路線分布（每隻英雄取前三常見路線與佔比）
    pos = _run_df(
        f'SELECT champion, "position", COUNT(*) n FROM player_games{where} '
        f'GROUP BY champion, "position"', params + extra_params)
    pos = pos.dropna(subset=["position"])
    pos_lists = {}
    main_pos = {}
    for champion, grp in pos.groupby("champion"):
        grp = grp.sort_values("n", ascending=False)
        total = grp["n"].sum()
        pos_lists[champion] = [
            {"position": str(row["position"]),
             "share": float(round(row["n"] / total * 100,
                                 config.RATE_DECIMALS))}
            for _, row in grp.head(3).iterrows()]
        main_pos[champion] = grp.iloc[0]["position"]
    df["positions"] = df["champion"].map(pos_lists)
    df["main_position"] = df["champion"].map(main_pos)

    # 禁用與後選（draft 表無位置欄；戰隊/選手關鍵字僅在命中戰隊時縮範圍）
    teams = _resolve_member_teams(f, member_search) if member_search else None
    draft_where, draft_params = _draft_scope(f, teams)
    bans = _run_df(
        f"SELECT champion, COUNT(*) bans FROM draft_bans{draft_where} "
        "GROUP BY champion", draft_params)
    late = _run_df(
        f'SELECT champion, COUNT(*) picks, '
        f'SUM(CASE WHEN "order" >= 4 THEN 1 ELSE 0 END) late '
        f"FROM draft_picks{draft_where} GROUP BY champion", draft_params)

    df = df.merge(bans, on="champion", how="left").fillna({"bans": 0})
    df = df.merge(late, on="champion", how="left")
    df["bans"] = df["bans"].astype(int)
    df["wins"] = df["wins"].astype(int)

    rate = lambda col: (col / max(total_games, 1) * 100).round(
        config.RATE_DECIMALS)
    df["pick_rate"] = rate(df["games"].astype(float))
    df["ban_rate"] = rate(df["bans"].astype(float))
    df["bp_rate"] = (df["pick_rate"] + df["ban_rate"]).round(
        config.RATE_DECIMALS)
    late_denom = df["picks"].where(df["picks"] != 0)
    df["late_rate"] = (df["late"] / late_denom * 100).fillna(0).round(
        config.RATE_DECIMALS)
    df["win_rate"] = (df["wins"] / df["games"] * 100).round(
        config.RATE_DECIMALS)
    df["kp"] = df["kp"].round(config.RATE_DECIMALS)
    for col in ("dpm", "dtaken", "vspm", "gpm", "gold15", "lane_win"):
        df[col] = df[col].round(config.RATE_DECIMALS)
    for col in ("k_pg", "d_pg", "a_pg"):
        df[col] = df[col].round(1)
    death_denom = df["deaths"].where(df["deaths"] != 0)
    df["kda"] = ((df["kills"] + df["assists"]) / death_denom).round(2).fillna(
        (df["kills"] + df["assists"])).round(2)
    return df


# ---------------------------------------------------------------------------
# 選手聚合
# ---------------------------------------------------------------------------
def player_stats(f: Filter, min_games: int = 0) -> pd.DataFrame:
    """選手聚合（含最近效力戰隊、主位置、10/15 分金差等）。"""
    where, params = _pg_where(f)
    sql = f"""
        SELECT playername,
            arg_max(teamname, date_iso) team,
            arg_max("position", date_iso) AS "position",
            COUNT(*) games, SUM(result) wins,
            COUNT(DISTINCT champion) champ_pool,
            SUM(kills) kills, SUM(deaths) deaths, SUM(assists) assists,
            AVG((kills+assists)*1.0/NULLIF(teamkills,0))*100 kp,
            AVG(firstbloodkill)*100 fb_rate,
            AVG(damageshare)*100 dmg_share,
            AVG(dpm) dpm,
            AVG(damagetakenperminute) dtaken,
            AVG(totalgold*60.0/NULLIF(gamelength,0)) gpm,
            AVG(golddiffat10) gd10,
            AVG(COALESCE(golddiffat25-golddiffat10, golddiffat15)) gold_mid,
            AVG(vspm) vspm
        FROM player_games{where}
        GROUP BY playername HAVING COUNT(*) >= ?
        ORDER BY games DESC
    """
    df = _run_df(sql, params + [int(min_games)])
    if df.empty:
        return df
    df["win_rate"] = (df["wins"] / df["games"] * 100).round(config.RATE_DECIMALS)
    death_denom = df["deaths"].where(df["deaths"] != 0)
    df["kda"] = ((df["kills"] + df["assists"]) / death_denom).round(2).fillna(
        (df["kills"] + df["assists"])).round(2)
    df["wins"] = df["wins"].astype(int)
    return df


def _player_scope(f: Filter,
                  h2h_players: tuple[str, str] | None = None
                  ) -> tuple[str, list]:
    """player_games 表的選手頁範圍：聯賽/年份/位置等＋排除 Versus 表演賽。

    h2h_players：再限縮到兩位選手親自對戰（同局不同隊）的對局。
    """
    where, params = _pg_where(f)
    split_sql, split_params = _exclude_split_clause()
    where = (where + " AND " if where else " WHERE ") + split_sql
    params += split_params
    if h2h_players:
        where += (
            " AND gameid IN (SELECT gameid FROM player_games "
            "WHERE " + split_sql + " AND playername IN (?,?) "
            "GROUP BY gameid HAVING COUNT(DISTINCT teamname) = 2)")
        params += split_params + list(h2h_players)
    return where, params


def player_ranking(f: Filter, min_games: int = 0,
                   h2h_players: tuple[str, str] | None = None) -> pd.DataFrame:
    """選手數據總表聚合（對照 DPM.LOL 選手頁）。

    直接口徑欄位：場數/角色池/分均輸出(dpm)/分均承傷/分均視分。
    計算欄位（前端 tooltip 需說明）：勝率、參與率、KDA、KDA差@10、
    贏線率、輸出比、死亡比、首殺差、金轉傷、中期金差、分均金錢。
    後選率需 BP 選序資料，Oracle 回傳無此欄位，由前端補 123456。
    """
    where, params = _player_scope(f, h2h_players)
    # KDA差@10：同一局同位置對手的 @10 KDA 差值，跨局平均
    # 同一 WHERE 範圍在 CTE 與主查詢各使用一次，故參數重複綁定兩份
    sql = f"""
        WITH pg AS (
            SELECT gameid, side, position, playername,
                   killsat10 k, assistsat10 a, deathsat10 d
            FROM player_games{where}
        ),
        kda10 AS (
            SELECT p.playername pn,
                AVG((p.k+p.a)/COALESCE(NULLIF(p.d,0),1)
                    -(o.k+o.a)/COALESCE(NULLIF(o.d,0),1)) v
            FROM pg p JOIN pg o
                ON p.gameid=o.gameid AND p.position=o.position
                AND p.side<>o.side
            GROUP BY p.playername
        )
        SELECT playername,
            arg_max(teamname, date_iso) team,
            mode(league) league,
            arg_max("position", date_iso) AS "position",
            COUNT(*) games, SUM(result) wins,
            COUNT(DISTINCT champion) champ_pool,
            SUM(kills) kills, SUM(deaths) deaths, SUM(assists) assists,
            AVG((kills+assists)*1.0/NULLIF(teamkills,0))*100 kp,
            AVG(firstbloodkill)*100 fb_rate,
            (AVG(firstbloodkill)+AVG(firstbloodassist)
             -AVG(firstbloodvictim))*100 fb_diff,
            AVG(damageshare)*100 dmg_share,
            AVG(deaths*1.0/NULLIF(teamdeaths,0))*100 death_share,
            AVG(dpm) dpm,
            AVG(damagetakenperminute) dtaken,
            AVG(totalgold*60.0/NULLIF(gamelength,0)) gpm,
            AVG(COALESCE(golddiffat25-golddiffat10, golddiffat15)) gold_mid,
            AVG(CASE WHEN golddiffat15 > 0 THEN 100.0 ELSE 0.0 END) lane_win,
            AVG(golddiffat15) lane_gd15,
            AVG(vspm) vspm,
            (SELECT v FROM kda10 WHERE kda10.pn = player_games.playername)
                AS kda_diff10
        FROM player_games{where}
        GROUP BY playername HAVING COUNT(*) >= ?
    """
    df = _run_df(sql, params + params + [int(min_games)])
    if df.empty:
        return df
    df = df[df["playername"].notna() & (df["playername"].astype(str) != "")]

    df["wins"] = df["wins"].astype(int)
    df["win_rate"] = (df["wins"] / df["games"] * 100).round(
        config.RATE_DECIMALS)
    # KDA＝(擊殺+助攻)/死亡（死亡 0 時直接記 K+A）
    death_denom = df["deaths"].where(df["deaths"] != 0)
    df["kda"] = ((df["kills"] + df["assists"]) / death_denom).round(2).fillna(
        df["kills"] + df["assists"]).round(2)
    df["k_pg"] = (df["kills"] / df["games"]).round(1)
    df["d_pg"] = (df["deaths"] / df["games"]).round(1)
    df["a_pg"] = (df["assists"] / df["games"]).round(1)
    # 金轉傷＝分均輸出 / 分均金錢 × 100%
    df["gold_eff"] = (df["dpm"] / df["gpm"].where(df["gpm"] != 0) * 100
                      ).round(1)
    for col in ("kp", "dmg_share", "death_share", "lane_win", "fb_rate",
                "fb_diff"):
        df[col] = df[col].round(config.RATE_DECIMALS)
    df["gold_mid"] = df["gold_mid"].round(0)
    df["lane_gd15"] = df["lane_gd15"].round(0)
    df["gpm"] = df["gpm"].round(0)
    df["dpm"] = df["dpm"].round(0)
    df["dtaken"] = df["dtaken"].round(0)
    df["vspm"] = df["vspm"].round(1)
    df["kda_diff10"] = df["kda_diff10"].round(2)

    # 戰隊官方縮寫（選手欄需顯示「隊伍縮寫 選手名」）
    meta = load_team_meta()
    df["abbr"] = [meta.get(t, {}).get("abbr") or str(t)[:3].upper()
                  if t else None for t in df["team"]]
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 戰隊聚合
# ---------------------------------------------------------------------------
def team_stats(f: Filter, min_games: int = 0) -> pd.DataFrame:
    """戰隊聚合：勝率、目標物控制、金差、視野、角色池、選邊。"""
    t_where, t_params = _pg_where(f, include_position=False)
    # team_games 的聯賽/年份篩選（同 pg 但無位置）
    clauses, params = [], []
    if f.leagues:
        clauses.append(f"league IN ({','.join('?' * len(f.leagues))})")
        params.extend(f.leagues)
    if f.years:
        clauses.append(f"year IN ({','.join('?' * len(f.years))})")
        params.extend([int(y) for y in f.years])
    if f.patches:
        clauses.append(f"patch IN ({','.join('?' * len(f.patches))})")
        params.extend([float(p) for p in f.patches])
    if f.date_start:
        clauses.append("date_iso::DATE >= ?::DATE"); params.append(f.date_start)
    if f.date_end:
        clauses.append("date_iso::DATE <= ?::DATE"); params.append(f.date_end)
    tw = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT teamname, COUNT(*) games, SUM(result) wins,
            AVG(gamelength)/60 avg_len,
            AVG(team_kpm) team_kpm,
            AVG(totalgold*60.0/NULLIF(gamelength,0)) team_gpm,
            AVG(dpm) team_dpm,
            AVG(firstblood)*100 firstblood_rate,
            AVG(firstdragon)*100 dragon_rate,
            AVG(firstherald)*100 herald_rate,
            AVG(firstbaron)*100 baron_rate,
            AVG(firsttower)*100 tower_rate,
            AVG(CASE WHEN void_grubs >= 2 THEN 1 ELSE 0 END)*100 grub2_rate,
            AVG(golddiffat10) gd10,
            AVG(COALESCE(golddiffat25-golddiffat10, golddiffat15)) gold_mid,
            SUM(CASE WHEN first_pick=1 AND side='Blue' THEN 1 ELSE 0 END) fp_blue,
            SUM(CASE WHEN first_pick=1 AND side='Red' THEN 1 ELSE 0 END) fp_red,
            SUM(CASE WHEN first_pick=0 AND side='Blue' THEN 1 ELSE 0 END) sp_blue,
            SUM(CASE WHEN first_pick=0 AND side='Red' THEN 1 ELSE 0 END) sp_red
        FROM team_games{tw}
        GROUP BY teamname HAVING COUNT(*) >= ?
    """
    df = _run_df(sql, params + [int(min_games)])
    if df.empty:
        return df
    pool_scope, pool_params = _scope_without_position(f, True)
    pool = _run_df(
        f"SELECT teamname, COUNT(DISTINCT champion) champ_pool, "
        f"AVG(visionscore*60.0/NULLIF(gamelength,0)) vspm "
        f"FROM player_games{pool_scope} GROUP BY teamname", pool_params
    )
    df = df.merge(pool, on="teamname", how="left")
    df["win_rate"] = (df["wins"] / df["games"] * 100).round(config.RATE_DECIMALS)
    df["wins"] = df["wins"].astype(int)
    for c in ("dragon_rate", "herald_rate", "baron_rate", "tower_rate",
              "grub2_rate"):
        df[c] = df[c].round(config.RATE_DECIMALS)
    return df.sort_values("games", ascending=False).reset_index(drop=True)


def _exclude_split_clause(alias: str = "") -> tuple[str, list]:
    """排除表演賽賽段（Versus）的 SQL 片段；alias 為表別名前綴。"""
    col = f"{alias}split" if alias else "split"
    placeholders = ",".join("?" * len(EXCLUDE_SPLITS))
    return f"({col} IS NULL OR {col} NOT IN ({placeholders}))", list(
        EXCLUDE_SPLITS)


def _team_scope(f: Filter, h2h_teams: tuple[str, str] | None = None
                ) -> tuple[str, list]:
    """team_games 表專用 WHERE（無位置欄；關鍵字與對戰限縮走 gameid 子查詢）。"""
    # 先取不含冠軍/選手關鍵字的基礎範圍，再以對局子查詢補上
    base_f = Filter(leagues=f.leagues, years=f.years, patches=f.patches,
                    date_start=f.date_start, date_end=f.date_end,
                    teams=f.teams)
    where, params = _scope_without_position(base_f)
    split_sql, split_params = _exclude_split_clause()
    where = (where + " AND " if where else " WHERE ") + split_sql
    params += split_params
    extra, extra_params = [], []
    if f.champion:
        extra.append(
            "gameid IN (SELECT gameid FROM player_games WHERE champion ILIKE ?"
            f" AND {split_sql})")
        extra_params.append(f"%{f.champion}%")
        extra_params += split_params
    if f.member:
        extra.append(
            "gameid IN (SELECT gameid FROM player_games "
            "WHERE (teamname ILIKE ? OR playername ILIKE ?)"
            f" AND {split_sql})")
        extra_params.extend([f"%{f.member}%"] * 2)
        extra_params += split_params
    if h2h_teams:
        # 僅保留兩隊親自對戰的對局（同樣排除表演賽）
        scope2, p2 = _scope_without_position(base_f)
        cond = f"{scope2} AND " if scope2 else " WHERE "
        extra.append(
            f"gameid IN (SELECT gameid FROM team_games{cond}"
            f"{split_sql} AND teamname IN (?,?) GROUP BY gameid "
            "HAVING COUNT(DISTINCT teamname) = 2)")
        extra_params.extend(p2 + split_params + list(h2h_teams))
    if extra:
        where = where + " AND " + " AND ".join(extra)
    return where, params + extra_params


def team_ranking(f: Filter, min_games: int = 0,
                 h2h_teams: tuple[str, str] | None = None) -> pd.DataFrame:
    """戰隊數據總表聚合（對照 DPM.LOL 戰隊頁）。

    涵蓋場數/時長/勝率、藍紅勝率差、角色池、首塔/2+巢蟲/首龍/預示者/
    小龍/首巴龍、10 分與中期金差、分均金錢/輸出、平均 KD、視野分數。
    主動權/被動選序/被動選邊因 OE 無足夠口徑，由前端顯示佔位值。
    """
    where, params = _team_scope(f, h2h_teams)
    sql = f"""
        SELECT teamname,
            mode(league) league,
            COUNT(*) games,
            SUM(result) wins,
            AVG(gamelength)/60 avg_len,
            AVG(team_kpm) team_kpm,
            AVG(totalgold*60.0/NULLIF(gamelength,0)) team_gpm,
            AVG(dpm) team_dpm,
            SUM(teamkills)*1.0 kills,
            SUM(teamdeaths)*1.0 deaths,
            AVG(teamkills*1.0) k_pg,
            AVG(teamdeaths*1.0) d_pg,
            AVG(firstblood)*100 firstblood_rate,
            AVG(firstdragon)*100 dragon_rate,
            AVG(firstherald)*100 herald_rate,
            AVG(firstbaron)*100 baron_rate,
            AVG(firsttower)*100 tower_rate,
            AVG(CASE WHEN void_grubs >= 2 THEN 100.0 ELSE 0.0 END) grub2_rate,
            AVG(dragons) dragons,
            AVG(golddiffat10) gd10,
            AVG(COALESCE(golddiffat15, golddiffat10)) gold_mid,
            SUM(CASE WHEN side='Blue' THEN result ELSE 0 END)*100.0
                / NULLIF(SUM(CASE WHEN side='Blue' THEN 1 ELSE 0 END),0) blue_wr,
            SUM(CASE WHEN side='Red' THEN result ELSE 0 END)*100.0
                / NULLIF(SUM(CASE WHEN side='Red' THEN 1 ELSE 0 END),0) red_wr
        FROM team_games{where}
        GROUP BY teamname HAVING COUNT(*) >= ?
    """
    df = _run_df(sql, params + [int(min_games)])
    if df.empty:
        return df
    # 剔除空隊名（部份來源列缺隊名，會造成前端下拉 NaN 選項）
    df = df[df["teamname"].notna() & (df["teamname"].astype(str) != "")]

    # 角色池與團隊視野分（先把每場五人 vision/min 加總，再跨場平均）
    pool_scope, pool_params = _team_player_scope(f, h2h_teams)
    pool_cnt = _run_df(
        f"SELECT teamname, COUNT(DISTINCT champion) champ_pool "
        f"FROM player_games{pool_scope} GROUP BY teamname", pool_params)
    vspm_df = _run_df(
        "SELECT teamname, AVG(team_vspm) vspm FROM ("
        "SELECT gameid, teamname, "
        "SUM(visionscore*60.0/NULLIF(gamelength,0)) team_vspm "
        f"FROM player_games{pool_scope} GROUP BY gameid, teamname"
        ") GROUP BY teamname", pool_params)
    df = df.merge(pool_cnt, on="teamname", how="left")
    df = df.merge(vspm_df, on="teamname", how="left")

    df["wins"] = df["wins"].astype(int)
    df["win_rate"] = (df["wins"] / df["games"] * 100).round(
        config.RATE_DECIMALS)
    df["side_diff"] = (df["blue_wr"] - df["red_wr"]).round(
        config.RATE_DECIMALS)
    for c in ("firstblood_rate", "dragon_rate", "herald_rate", "baron_rate",
              "tower_rate", "grub2_rate", "blue_wr", "red_wr"):
        df[c] = df[c].round(config.RATE_DECIMALS)
    df["dragons"] = df["dragons"].round(2)
    df["gd10"] = df["gd10"].round(0)
    df["gold_mid"] = df["gold_mid"].round(0)
    df["team_gpm"] = df["team_gpm"].round(0)
    df["team_dpm"] = df["team_dpm"].round(0)
    df["vspm"] = df["vspm"].round(1)
    df["k_pg"] = df["k_pg"].round(2)
    df["d_pg"] = df["d_pg"].round(2)
    death_denom = df["deaths"].where(df["deaths"] != 0)
    df["kd"] = (df["kills"] / death_denom).round(2).fillna(df["kills"]).round(2)

    # 官方縮寫與本地 LOGO 檔名（缺 meta 時縮寫取隊名前 3 字、LOGO 留空）
    meta = load_team_meta()
    df["abbr"] = [meta.get(n, {}).get("abbr") or n[:3].upper()
                  for n in df["teamname"]]
    df["logo_file"] = [meta.get(n, {}).get("logo_file")
                       for n in df["teamname"]]
    return df


def _team_player_scope(f: Filter,
                       h2h_teams: tuple[str, str] | None = None
                       ) -> tuple[str, list]:
    """player_games 版範圍（含 playername 比對），供戰隊頁池/視野查詢。"""
    where, params = _scope_without_position(f, True)
    split_sql, split_params = _exclude_split_clause()
    where = (where + " AND " if where else " WHERE ") + split_sql
    params += split_params
    if h2h_teams:
        base_f = Filter(leagues=f.leagues, years=f.years, patches=f.patches,
                        date_start=f.date_start, date_end=f.date_end,
                        teams=f.teams)
        scope2, p2 = _scope_without_position(base_f)
        cond = f"{scope2} AND " if scope2 else " WHERE "
        clause = (
            f"gameid IN (SELECT gameid FROM team_games{cond}"
            f"{split_sql} AND teamname IN (?,?) GROUP BY gameid "
            "HAVING COUNT(DISTINCT teamname) = 2)")
        where = where + " AND " + clause
        params = params + p2 + split_params + list(h2h_teams)
    return where, params


def _scope_without_position(f: Filter, member_playername: bool = False
                            ) -> tuple[str, list]:
    """無位置條件的 WHERE 片段（一律參數綁定，回傳 SQL 與參數）。

    member_playername：查 player_games 時可連 playername 一起比；
    draft_bans/draft_picks 無 playername 欄，僅能比 teamname。
    """
    clauses, params = [], []
    if f.leagues:
        clauses.append(f"league IN ({','.join('?' * len(f.leagues))})")
        params.extend(f.leagues)
    if f.years:
        clauses.append(f"year IN ({','.join('?' * len(f.years))})")
        params.extend(int(y) for y in f.years)
    if f.patches:
        clauses.append(f"patch IN ({','.join('?' * len(f.patches))})")
        params.extend(float(p) for p in f.patches)
    if f.date_start:
        clauses.append("date_iso::DATE >= ?::DATE")
        params.append(f.date_start)
    if f.date_end:
        clauses.append("date_iso::DATE <= ?::DATE")
        params.append(f.date_end)
    if f.champion:
        clauses.append("champion ILIKE ?")
        params.append(f"%{f.champion}%")
    if f.member:
        if member_playername:
            clauses.append("(teamname ILIKE ? OR playername ILIKE ?)")
            params.extend([f"%{f.member}%"] * 2)
        else:
            clauses.append("teamname ILIKE ?")
            params.append(f"%{f.member}%")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


# ---------------------------------------------------------------------------
# 陣容、BP 紀錄、模擬 BP 輔助
# ---------------------------------------------------------------------------
def team_roster(team: str, f: Filter) -> dict[str, pd.DataFrame]:
    """戰隊五位選手，各自含英雄池。"""
    where, params = _pg_where(Filter(**{**f.__dict__, "teams": [team]}))
    players = _run_df(
        f"SELECT position, playername FROM player_games{where} "
        "GROUP BY 1,2", params
    )
    result = {}
    for _, row in players.iterrows():
        pos, name = row["position"], row["playername"]
        pf = Filter(**{**f.__dict__, "positions": [pos], "teams": [team]})
        pool = player_champion_pool(name, pf)
        result.setdefault(pos, []).append((name, pool))
    # 每位置取近期出賽最多者為當前登錄
    roster = {}
    for pos, candidates in result.items():
        name, pool = max(candidates, key=lambda x: len(x[1]))
        roster[pos] = {"player": name, "pool": pool}
    return roster


def player_champion_pool(player: str, f: Filter,
                         min_games: int = 0) -> pd.DataFrame:
    """單一選手的英雄池：場數、勝負、KDA、上次出場（預設按場數降冪）。"""
    where, params = _pg_where(f)
    connector = " AND " if where else " WHERE "
    sql = f"""
        SELECT champion, COUNT(*) games, SUM(result) wins,
            (SUM(kills)+SUM(assists))*1.0/NULLIF(SUM(deaths),0) kda,
            MAX(date_iso) last_played
        FROM player_games{where}{connector} playername = ?
        GROUP BY champion HAVING COUNT(*) >= ?
        ORDER BY games DESC, last_played DESC
    """
    df = _run_df(sql, params + [player, int(min_games)])
    if not df.empty:
        df["win_rate"] = (df["wins"] / df["games"] * 100).round(
            config.RATE_DECIMALS)
        df["kda"] = df["kda"].round(2)
        df["wins"] = df["wins"].astype(int)
    return df


def resolve_team(keyword: str) -> str | None:
    """戰隊縮寫或全名（不分大小寫）解析為標準隊名；無法唯一命中回傳 None。

    優先序：全名完全相等 → team_meta 縮寫完全相等 → 全名包含且唯一。
    """
    kw = (keyword or "").strip()
    if not kw:
        return None
    teams = _run_df(
        "SELECT DISTINCT teamname FROM player_games "
        "WHERE teamname IS NOT NULL ORDER BY 1")["teamname"].tolist()
    lower = {t.lower(): t for t in teams}
    if kw.lower() in lower:  # 全名完全相等（最可靠，避免 T1 命中 T1 Academy）
        return lower[kw.lower()]
    for team, meta in load_team_meta().items():
        if (meta.get("abbr") or "").upper() == kw.upper() \
                and team.lower() in lower:
            return lower[team.lower()]
    hits = [t for t in teams if kw.lower() in t.lower()]
    return hits[0] if len(hits) == 1 else None


ROSTER_POSITIONS = ["top", "jng", "mid", "bot", "sup"]


def roster_candidates(team: str, f: Filter) -> pd.DataFrame:
    """戰隊在篩選範圍內，五路各自的出賽選手候選。

    每位選手含上次出賽日、總場、勝場、角色池數、在隊年份跨度；
    同位置預設人選＝上次出賽日最新者（前端下拉可改選其餘選手）。
    """
    pf = Filter(**{**f.__dict__, "teams": [team]})
    where, params = _pg_where(pf)
    placeholders = ",".join("?" * len(ROSTER_POSITIONS))
    sql = f"""
        SELECT "position" AS position, playername,
            MAX(date_iso) last_date, COUNT(*) games, SUM(result) wins,
            COUNT(DISTINCT champion) pool_n,
            MIN(year) y0, MAX(year) y1
        FROM player_games{where}
        {"AND" if where else "WHERE"} "position" IN ({placeholders})
            AND playername IS NOT NULL AND playername <> ''
        GROUP BY 1, 2
        ORDER BY "position", last_date DESC, games DESC
    """
    return _run_df(sql, params + ROSTER_POSITIONS)


def roster_player_detail(team: str, position: str, player: str,
                         f: Filter, min_games: int = 0) -> tuple[dict, pd.DataFrame]:
    """陣容卡片所需：選手摘要＋英雄池明細。

    摘要：角色池／總場／勝率／在隊年份（職業生涯模式顯示）。
    """
    pf = Filter(**{**f.__dict__, "teams": [team], "positions": [position]})
    where, params = _pg_where(pf)
    connector = " AND " if where else " WHERE "
    summary_df = _run_df(
        f"""SELECT COUNT(*) games, SUM(result) wins,
                   COUNT(DISTINCT champion) pool_n,
                   MIN(year) y0, MAX(year) y1, MAX(date_iso) last_date
            FROM player_games{where}{connector} playername = ?""",
        params + [player])
    row = summary_df.iloc[0]
    games = int(row["games"] or 0)
    wins = int(row["wins"] or 0)
    summary = {
        "games": games, "wins": wins,
        "pool_n": int(row["pool_n"] or 0),
        "win_rate": round(wins / games * 100, config.RATE_DECIMALS)
        if games else 0.0,
        "y0": int(row["y0"]) if row["y0"] is not None else None,
        "y1": int(row["y1"]) if row["y1"] is not None else None,
        "last_date": row["last_date"],
    }
    pool = player_champion_pool(player, pf, min_games=min_games)
    return summary, pool


def match_bp_records(f: Filter, limit: int = 60) -> pd.DataFrame:
    """BP 紀錄用的比賽清單（含藍紅隊與結果）。"""
    where, params = _game_where(f)
    return _run_df(
        f"SELECT gameid, date_iso, league, patch, game, blue_team, red_team, "
        f"blue_result, red_result, gamelength, blue_firstpick, red_firstpick "
        f"FROM games{where} ORDER BY date_iso DESC, gameid LIMIT {int(limit)}",
        params,
    )


def match_draft(gameid: str) -> dict:
    """單場的藍/紅 bans/picks（含順序）。"""
    out = {}
    for side in ("Blue", "Red"):
        picks = _run_df(
            "SELECT \"order\", champion FROM draft_picks WHERE gameid=? "
            "AND side=? ORDER BY \"order\"", [gameid, side]
        )
        bans = _run_df(
            "SELECT \"order\", champion FROM draft_bans WHERE gameid=? "
            "AND side=? ORDER BY \"order\"", [gameid, side]
        )
        out[side] = {
            "picks": picks.set_index("order")["champion"].to_dict(),
            "bans": bans.set_index("order")["champion"].to_dict(),
        }
    return out


# ---------------------------------------------------------------------------
# 比賽 BP 頁：系列賽分組棋盤（一次展開、最新在上）
# ---------------------------------------------------------------------------
_ABBR_CACHE: dict[str, str] | None = None
_STAGE_PREFIX = {"Spring": 1, "Winter": 1, "Summer": 2}


def _ensure_abbr_cache() -> dict:
    """確保全名→縮寫對照表已建立並回傳。"""
    global _ABBR_CACHE
    if _ABBR_CACHE is None:
        try:
            _ABBR_CACHE = {
                k: (v.get("abbr") or k) for k, v in load_team_meta().items()}
        except Exception:  # noqa: BLE001
            _ABBR_CACHE = {}
    return _ABBR_CACHE


def team_abbr(name: str | None) -> str:
    """戰隊全名 → 短縮寫（T1／HLE／GEN…）；中繼資料缺時取首詞。"""
    if not name:
        return "—"
    return _ensure_abbr_cache().get(name, name.split()[0])


def _stage_label(split, playoffs) -> str:
    """split＋playoffs → S3／S3季後賽 之類的階段標籤。"""
    if split is None or (isinstance(split, float) and split != split):
        return "季後賽" if playoffs == 1 else ""
    import re
    digits = re.findall(r"\d+", str(split))
    if digits:
        n = int(digits[0])
    else:
        n = _STAGE_PREFIX.get(str(split), 2)
    return f"S{n}季後賽" if playoffs == 1 else f"S{n}"


def bp_drafts_bulk(gameids: list[str]) -> dict:
    """一次取多場的藍/紅 picks、bans（避免逐場查詢）。"""
    if not gameids:
        return {}
    placeholders = ",".join("?" * len(gameids))
    out = {g: {"Blue": {"picks": {}, "bans": {}},
               "Red": {"picks": {}, "bans": {}}} for g in gameids}
    for table, key in (("draft_picks", "picks"), ("draft_bans", "bans")):
        df = _run_df(
            f'SELECT gameid, side, "order", champion FROM {table} '
            f"WHERE gameid IN ({placeholders})", gameids)
        for r in df.itertuples(index=False):
            if r.gameid in out and r.side in out[r.gameid]:
                out[r.gameid][r.side][key][int(r.order)] = r.champion
    return out


def bp_positions_bulk(gameids: list[str]) -> dict:
    """批量取每場每邊每位英雄的實際上線位置（供 BP 按 role 排序）。

    回傳 dict[gameid][side][champion] = position（top/jng/mid/bot/sup）。
    若 player_games 缺某場，會自然漏掉，由呼叫端 fallback。
    """
    if not gameids:
        return {}
    placeholders = ",".join("?" * len(gameids))
    df = _run_df(
        f"SELECT gameid, side, champion, \"position\" FROM player_games "
        f"WHERE gameid IN ({placeholders}) AND champion IS NOT NULL "
        f"AND \"position\" IS NOT NULL", gameids)
    out: dict[str, dict[str, dict[str, str]]] = {}
    for r in df.itertuples(index=False):
        out.setdefault(r.gameid, {}).setdefault(r.side, {})[r.champion] = (
            r.position)
    return out


def _bp_where(f: Filter, blue_kw: str, red_kw: str,
              champ_kw: str) -> tuple[str, list]:
    """games 條件：全站篩選＋藍方/紅方隊名（全名或縮寫）＋BP 英雄。"""
    where, params = _game_where(f)
    extra = []

    def team_clause(col: str, kw: str):
        # 縮寫命中（HLE→Hanwha Life Esports）與全名子字串都算
        hits = [full for full, abbr in _ensure_abbr_cache().items()
                if kw.lower() in full.lower() or kw.lower() in abbr.lower()]
        if hits:
            placeholders = ",".join("?" * len(hits))
            extra.append(f"({col} IN ({placeholders}) OR {col} ILIKE ?)")
            params.extend(hits)
            params.append(f"%{kw}%")
        else:
            extra.append(f"{col} ILIKE ?")
            params.append(f"%{kw}%")

    if blue_kw:
        team_clause("blue_team", blue_kw)
    if red_kw:
        team_clause("red_team", red_kw)
    if champ_kw:
        extra.append(
            "gameid IN (SELECT gameid FROM draft_picks WHERE champion ILIKE ? "
            "UNION SELECT gameid FROM draft_bans WHERE champion ILIKE ?)")
        params.extend([f"%{champ_kw}%", f"%{champ_kw}%"])
    if extra:
        where = where + (" AND " if where else " WHERE ") + " AND ".join(extra)
    return where, params


# col_filters index → 語義（對應 _BP_COLS）：
# 0=局數, 1=時長, 2=勝, 3=藍方隊, 4=藍選擇(先/後),
# 5-9=藍禁用 ban1-5, 10-14=藍路線 top/jng/mid/bot/sup,
# 15=紅方隊, 16=紅選擇, 17-21=紅禁用 ban1-5, 22-26=紅路線 top/jng/mid/bot/sup


def _col_filter_game(game: dict, col_filters: dict[int, str]) -> bool:
    """檢查單場是否通過所有逐欄篩選（AND 邏輯：全部匹配才算命中）。"""
    if not col_filters:
        return True

    def match_kw(text: str | None, kw: str) -> bool:
        """不區分大小寫的子字串匹配。"""
        if not text:
            return False
        return kw.lower() in str(text).lower()

    def match_role_pick(slot: dict | None, kw: str) -> bool:
        """role pick 欄位（bp0-4 / rp0-4）：用英雄名比對。"""
        if not slot:
            return False
        return match_kw(slot.get("champion"), kw)

    def match_ban(name: str | None, kw: str) -> bool:
        return match_kw(name, kw)

    checks: list[bool] = []

    for idx, kw in col_filters.items():
        kw = (kw or "").strip()
        if not kw:
            continue
        if idx == 0:  # 局數（精確數字比對）
            checks.append(str(game["game_no"]) == kw)
        elif idx == 1:  # 時長
            checks.append(match_kw(game["length"], kw))
        elif idx == 2:  # 勝（勝方縮寫）
            checks.append(match_kw(game["winner_abbr"], kw))
        elif idx == 3:  # 藍方隊名（縮寫）
            checks.append(match_kw(game["blue_abbr"], kw)
                          or match_kw(game["blue_team"], kw))
        elif idx == 4:  # 藍選擇（先選/後選）
            kw_norm = kw.lower().replace(" ", "")
            is_fp = bool(game.get("blue_fp"))
            if kw_norm in ("先", "先選", "first"):
                checks.append(is_fp)
            elif kw_norm in ("後", "後選", "second", "sp"):
                checks.append(not is_fp)
            elif kw_norm in ("藍", "blue"):
                checks.append(is_fp)
            elif kw_norm in ("紅", "red"):
                checks.append(not is_fp)
            else:
                checks.append(False)
        elif 5 <= idx <= 9:  # 藍禁用 ban1-5（ban 先後順序）
            name = (game.get("blue_bans") or [None, None, None, None, None])[
                idx - 5]
            checks.append(match_ban(name, kw))
        elif 10 <= idx <= 14:  # 藍路線 top/jng/mid/bot/sup（按 role 排序）
            slot = (game.get("blue_picks") or [None, None, None, None, None])[
                idx - 10]
            checks.append(match_role_pick(slot, kw))
        elif idx == 15:  # 紅方隊名
            checks.append(match_kw(game["red_abbr"], kw)
                          or match_kw(game["red_team"], kw))
        elif idx == 16:  # 紅選擇（先選/後選）
            kw_norm = kw.lower().replace(" ", "")
            is_fp = not bool(game.get("blue_fp"))
            if kw_norm in ("先", "先選", "first"):
                checks.append(is_fp)
            elif kw_norm in ("後", "後選", "second", "sp"):
                checks.append(not is_fp)
            else:
                checks.append(False)
        elif 17 <= idx <= 21:  # 紅禁用 ban1-5
            name = (game.get("red_bans") or [None, None, None, None, None])[
                idx - 17]
            checks.append(match_ban(name, kw))
        elif 22 <= idx <= 26:  # 紅路線 top/jng/mid/bot/sup
            slot = (game.get("red_picks") or [None, None, None, None, None])[
                idx - 22]
            checks.append(match_role_pick(slot, kw))
        # 其餘 idx 忽略（保留 forward-compat）

    return all(checks) if checks else True


def _filter_series_col(series: list[dict],
                       col_filters: dict[int, str] | None) -> list[dict]:
    """對系列賽做逐欄後過濾：剔除沒有任何 game 通過的 series。"""
    if not col_filters:
        return series
    kept = []
    for s in series:
        kept_games = [g for g in s["games"]
                      if _col_filter_game(g, col_filters)]
        if kept_games:
            # 重新計分
            blue_fp_first = bool(kept_games[0].get("blue_fp"))
            wins_a = sum(1 for g in kept_games
                         if g["blue_abbr"] == s["abbr_a"] and g["blue_win"]
                         or g["red_abbr"] == s["abbr_a"] and not g["blue_win"])
            wins_b = len(kept_games) - wins_a
            kept.append({**s, "games": kept_games,
                         "score_a": wins_a, "score_b": wins_b,
                         "winner": "a" if wins_a > wins_b else "b"})
    return kept


def bp_board_data(f: Filter, blue_kw: str = "", red_kw: str = "",
                  champ_kw: str = "", render_games: int = 60,
                  col_filters: dict[int, str] | None = None) -> dict:
    """組 BP 頁所需資料：KPI 統計＋系列賽分列表（含每場 draft）。

    col_filters 是 27 欄逐欄篩選 dict：{欄位 index: 關鍵字}。
    提供時會覆蓋 blue_kw / red_kw / champ_kw（舊參數保留作向後相容）。
    """
    # 從 col_filters 提取舊式關鍵字（若有）
    if col_filters:
        blue_kw = col_filters.get(3, "")
        red_kw = col_filters.get(15, "")
        # champ_kw 不再單欄對應；各英雄分散在 10-14 / 22-26 欄
    where, params = _bp_where(
        f, (blue_kw or "").strip(), (red_kw or "").strip(),
        (champ_kw or "").strip())
    df = _run_df(
        f"SELECT gameid, date_iso, league, patch, game, split, playoffs, "
        f"blue_team, red_team, blue_result, red_result, gamelength, "
        f"blue_firstpick, red_firstpick FROM games{where} "
        f"ORDER BY date_iso DESC, game DESC, gameid",
        params)

    stats = _bp_stats(df)
    series = _bp_group_series(df.head(max(int(render_games), 1)))
    # Python 後過濾（picks 已按 role 排序，可精準匹配角色欄）
    series = _filter_series_col(series, col_filters)
    stats["series_total"] = _bp_count_series(df)
    return {"stats": stats, "series": series,
            "rendered_games": len(df.head(max(int(render_games), 1))),
            "total_games": len(df)}


def _bp_count_series(df: pd.DataFrame) -> int:
    """全量資料的系列賽數（同日同隊伍對視為一個系列）。"""
    if df.empty:
        return 0
    keys = df.apply(
        lambda r: (r["date_iso"],
                   tuple(sorted([str(r["blue_team"]), str(r["red_team"])]))),
        axis=1)
    return int(keys.nunique())


def _bp_stats(df: pd.DataFrame) -> dict:
    """KPI：場數／缺 draft／場均時長／藍紅勝率／先選比例與勝率／完整率。"""
    n = len(df)
    if n == 0:
        return {"games": 0, "missing": 0, "avg_length": "—",
                "blue_wr": 0, "red_wr": 0, "blue_wins": 0, "red_wins": 0,
                "fp_blue_pct": 0, "fp_red_pct": 0, "fp_blue_n": 0,
                "fp_red_n": 0, "fp_win_pct": None, "fp_den": 0,
                "complete_pct": 0, "complete_n": 0}
    gameids = df["gameid"].tolist()
    placeholders = ",".join("?" * len(gameids))
    pick_cnt = _run_df(
        "SELECT gameid, COUNT(*) c FROM draft_picks "
        f"WHERE gameid IN ({placeholders}) GROUP BY 1", gameids)
    complete_ids = set(pick_cnt.loc[pick_cnt["c"] >= 10, "gameid"])
    missing = n - len(
        set(pick_cnt["gameid"]))  # 完全無 pick 紀錄者計入缺場
    avg_sec = int(df["gamelength"].mean())
    blue_wins = int(df["blue_result"].sum())
    red_wins = int(df["red_result"].sum())
    fp = df[df["blue_firstpick"].notna()]
    fp_blue_n = int((fp["blue_firstpick"] == 1).sum())
    fp_red_n = len(fp) - fp_blue_n
    # 先選方（一樓選角方）的勝率
    fp_wins = int(((fp["blue_firstpick"] == 1) &
                   (fp["blue_result"] == 1)).sum())
    fp_wins += int(((fp["red_firstpick"] == 1) &
                    (fp["red_result"] == 1)).sum())
    fp_win_pct = round(fp_wins / len(fp) * 100) if len(fp) else None
    return {
        "games": n, "missing": missing,
        "avg_length": f"{avg_sec // 60}:{avg_sec % 60:02d}",
        "blue_wins": blue_wins, "red_wins": red_wins,
        "blue_wr": round(blue_wins / n * 100),
        "red_wr": round(red_wins / n * 100),
        "fp_blue_n": fp_blue_n, "fp_red_n": fp_red_n,
        "fp_blue_pct": round(fp_blue_n / len(fp) * 100) if len(fp) else 0,
        "fp_red_pct": round(fp_red_n / len(fp) * 100) if len(fp) else 0,
        "fp_win_pct": fp_win_pct,
        "fp_den": len(fp), "fp_wins": fp_wins,
        "complete_n": len(complete_ids),
        "complete_pct": round(len(complete_ids) / n * 100),
    }


# position 值 → 顯示順序（上路/打野/中路/下路/輔助）
_POSITION_ORDER = {"top": 0, "jng": 1, "mid": 2, "bot": 3, "sup": 4}


def _sort_picks_by_role(picks: list[dict], side: str,
                        positions: dict[str, dict[str, str]] | None,
                        gameid: str) -> list[dict]:
    """把 picks 按實際上線位置排序；缺 position 時退回既有順序。"""
    if not positions:
        return picks
    side_pos = positions.get(gameid, {}).get(side, {})
    has_role = any(p.get("champion") and p["champion"] in side_pos for p in picks)
    if not has_role:
        return picks
    return sorted(picks, key=lambda p: (
        _POSITION_ORDER.get(side_pos.get(p.get("champion"), ""), 99),
        p.get("order", 0),
    ))


def _bp_game_view(row: pd.Series, draft: dict,
                  positions: dict | None = None) -> dict:
    """單場給前端的視圖模型（含全選角順位 1–10；picks 按 role 排序）。"""
    blue_fp = row["blue_firstpick"]
    blue_fp = int(blue_fp) == 1 if pd.notna(blue_fp) else True  # 缺值預設藍先
    # 蛇形規則：一樓方為 1,3,5,7,9
    blue_orders = (1, 3, 5, 7, 9) if blue_fp else (2, 4, 6, 8, 10)
    red_orders = (2, 4, 6, 8, 10) if blue_fp else (1, 3, 5, 7, 9)

    def picks(side, orders):
        mp = draft[side]["picks"]
        raw = [{"champion": mp.get(i), "order": orders[i - 1]}
               for i in range(1, 6)]
        return _sort_picks_by_role(raw, side, positions, row["gameid"])

    def bans(side):
        mp = draft[side]["bans"]
        return [mp.get(i) for i in range(1, 6)]

    blue_win = int(row["blue_result"]) == 1
    return {
        "gameid": row["gameid"], "game_no": int(row["game"]),
        "length": f"{int(row['gamelength']) // 60}:"
                  f"{int(row['gamelength']) % 60:02d}",
        "blue_team": row["blue_team"], "red_team": row["red_team"],
        "blue_abbr": team_abbr(row["blue_team"]),
        "red_abbr": team_abbr(row["red_team"]),
        "blue_win": blue_win,
        "winner_abbr": team_abbr(row["blue_team"] if blue_win
                                 else row["red_team"]),
        "winner_side": "blue" if blue_win else "red",
        "blue_fp": blue_fp,
        "fp_known": bool(pd.notna(row["blue_firstpick"])),
        "blue_picks": picks("Blue", blue_orders),
        "red_picks": picks("Red", red_orders),
        "blue_bans": bans("Blue"), "red_bans": bans("Red"),
        "has_draft": bool(draft["Blue"]["picks"] or draft["Red"]["picks"]),
    }


def _bp_group_series(df: pd.DataFrame) -> list[dict]:
    """把明細依（日期＋隊伍對）分組成系列賽，最新日期在前。"""
    if df.empty:
        return []
    gameids = df["gameid"].tolist()
    drafts = bp_drafts_bulk(gameids)
    # 位置資訊（供 picks 按 role 排序；缺時 fallback 到原始順序）
    positions = bp_positions_bulk(gameids)
    df = df.sort_values(["date_iso", "game", "gameid"])
    groups: dict[tuple, list] = {}
    order: list[tuple] = []
    for _, row in df.iterrows():
        key = (row["date_iso"],
               tuple(sorted([str(row["blue_team"]), str(row["red_team"])])))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    series = []
    for key in reversed(order):  # 最新系列在上
        rows = groups[key]
        first = rows[0]
        t_a, t_b = key[1]
        # 比分隊伍順序：第一局藍方在前
        first_blue, first_red = str(first["blue_team"]), str(
            first["red_team"])
        wins_a = sum(1 for r in rows
                     if (str(r["blue_team"]) == first_blue and
                         int(r["blue_result"]) == 1) or
                     (str(r["red_team"]) == first_blue and
                      int(r["red_result"]) == 1))
        wins_b = len(rows) - wins_a
        empty_draft = {"Blue": {"picks": {}, "bans": {}},
                       "Red": {"picks": {}, "bans": {}}}
        games = [_bp_game_view(r, drafts.get(r["gameid"], empty_draft),
                               positions)
                 for r in rows]
        series.append({
            "date": first["date_iso"], "patch": first["patch"],
            "league": first["league"],
            "stage": _stage_label(first["split"], first["playoffs"]),
            "team_a": first_blue, "team_b": first_red,
            "abbr_a": team_abbr(first_blue), "abbr_b": team_abbr(first_red),
            "score_a": wins_a, "score_b": wins_b,
            "winner": "a" if wins_a > wins_b else "b",
            "games": games,
        })
    return series


def recent_team_form(team: str, f: Filter, last_n: int = 10) -> float:
    """戰隊近 N 場勝率（百分比），遵循賽區/年份/版本/日期篩選。"""
    clauses, params = [], []
    if f.leagues:
        clauses.append(f"league IN ({','.join('?' * len(f.leagues))})")
        params.extend(f.leagues)
    if f.years:
        clauses.append(f"year IN ({','.join('?' * len(f.years))})")
        params.extend(int(y) for y in f.years)
    if f.patches:
        clauses.append(f"patch IN ({','.join('?' * len(f.patches))})")
        params.extend(float(p) for p in f.patches)
    if f.date_start:
        clauses.append("date_iso::DATE >= ?::DATE")
        params.append(f.date_start)
    if f.date_end:
        clauses.append("date_iso::DATE <= ?::DATE")
        params.append(f.date_end)
    clauses.append("teamname = ?")
    params.append(team)
    where = " WHERE " + " AND ".join(clauses)
    df = _run_df(
        f"SELECT result FROM team_games{where} ORDER BY date_iso DESC, "
        "gameid DESC LIMIT ?", params + [int(last_n)]
    )
    if df.empty:
        return float("nan")
    return round(float(df["result"].mean() * 100), config.RATE_DECIMALS)


def champion_detail(champion: str, f: Filter, top_n: int = 5) -> dict:
    """單一英雄：分位置勝率、紅藍勝率、10/15/25 分金差、熟練選手榜。"""
    base, params = _pg_where(f)
    extra = (" AND " if base else " WHERE ") + "champion = ?"

    pos = _run_df(
        f'SELECT "position", COUNT(*) games, SUM(result) wins, '
        f"AVG(golddiffat10) gd10, AVG(golddiffat15) gd15, "
        f"AVG(golddiffat25) gd25, AVG(dpm) dpm, "
        f"(SUM(kills)+SUM(assists))*1.0/NULLIF(SUM(deaths),0) kda "
        f"FROM player_games{base}{extra} GROUP BY 1 ORDER BY games DESC",
        params + [champion],
    )
    side = _run_df(
        f"SELECT side, COUNT(*) games, SUM(result) wins "
        f"FROM player_games{base}{extra} GROUP BY 1", params + [champion]
    )
    masters = _run_df(
        f"SELECT playername, arg_max(teamname, date_iso) team, "
        f'arg_max("position", date_iso) AS "position", '
        f"COUNT(*) games, SUM(result) wins, "
        f"(SUM(kills)+SUM(assists))*1.0/NULLIF(SUM(deaths),0) kda, "
        f"MAX(date_iso) last_played "
        f"FROM player_games{base}{extra} GROUP BY playername "
        f"ORDER BY games DESC LIMIT {int(top_n)}",
        params + [champion],
    )
    for df in (pos, side, masters):
        if not df.empty:
            df["win_rate"] = (df["wins"] / df["games"] * 100).round(
                config.RATE_DECIMALS)
            df["wins"] = df["wins"].astype(int)
    return {"by_position": pos, "by_side": side, "masters": masters}


# 分路列入卡片的最低出場占比（避免罕見亂入位置造成誤導）
_WALL_POSITION_SHARE = 0.08
# 每張英雄卡最多顯示的分路圖示數
_WALL_MAX_POSITIONS = 3


def champion_wall(years: list[int] | None = None) -> pd.DataFrame:
    """圖鑑牆：各英雄選用、禁用次數與實際出賽分路清單（依年度篩選）。"""
    f = Filter(years=years)
    picks = _run_df(
        'SELECT champion, COUNT(*) picks, mode("position") main_position '
        "FROM player_games"
        + (_pg_where(f)[0]) + " GROUP BY champion", _pg_where(f)[1]
    )
    bans = _run_df(
        "SELECT champion, COUNT(*) bans FROM draft_bans"
        + (_pg_where(f, False)[0]) + " GROUP BY champion",
        _pg_where(f, False)[1]
    )
    # 各英雄分路出賽數，只保留顯著分路（占比達門檻、至多三路）
    pos_detail = _run_df(
        'SELECT champion, "position" AS position, COUNT(*) AS n '
        "FROM player_games" + _pg_where(f)[0]
        + ' GROUP BY champion, "position"',
        _pg_where(f)[1],
    )
    positions = (
        pos_detail.groupby("champion")
        .apply(lambda g: [
            row.position for row in g.sort_values("n", ascending=False).itertuples()
            if row.n / g["n"].sum() >= _WALL_POSITION_SHARE
        ][:_WALL_MAX_POSITIONS], include_groups=False)
        .rename("positions")
        .reset_index()
    )
    df = picks.merge(bans, on="champion", how="outer").fillna(0)
    df["picks"] = df["picks"].astype(int)
    df["bans"] = df["bans"].astype(int)
    df = df.merge(positions, on="champion", how="left")
    df["positions"] = df["positions"].apply(
        lambda x: x if isinstance(x, list) else [])
    return df.sort_values("picks", ascending=False)
