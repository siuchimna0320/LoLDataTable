"""DuckDB 唯讀查詢層：提供儀表板與聚合使用的參數化查詢。

所有函式接收 Filter，內部以參數綁定方式組 SQL，避免字串拼接注入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
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

    def cache_key(self) -> str:
        return repr(self.__dict__)


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """開啟倉儲連線（預設唯讀）。"""
    if not Path(config.WAREHOUSE_PATH).exists():
        raise FileNotFoundError(
            f"找不到倉儲 {config.WAREHOUSE_PATH}，請先執行 python -m backend.pipeline all"
        )
    return duckdb.connect(str(config.WAREHOUSE_PATH), read_only=read_only)


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
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _run_df(sql: str, params: list | None = None) -> pd.DataFrame:
    with connect() as con:
        return con.execute(sql, params or []).fetchdf()


# ---------------------------------------------------------------------------
# 篩選選項
# ---------------------------------------------------------------------------
def get_options() -> dict:
    """取得全域篩選所需選項。"""
    leagues = _run_df(
        "SELECT DISTINCT league FROM games WHERE league IS NOT NULL "
        "ORDER BY league"
    )["league"].tolist()
    years = _run_df("SELECT DISTINCT year FROM games ORDER BY year")
    years = [int(y) for y in years["year"].tolist()]
    patches = _run_df("SELECT DISTINCT patch FROM games WHERE patch IS NOT NULL")
    patches = sorted({f"{float(p):g}" for p in patches["patch"].tolist()},
                     key=lambda x: float(x))
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
    pool_scope, pool_params = _scope_without_position(f)
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


def _scope_without_position(f: Filter) -> tuple[str, list]:
    """無位置條件的 WHERE 片段（一律參數綁定，回傳 SQL 與參數）。"""
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


def player_champion_pool(player: str, f: Filter) -> pd.DataFrame:
    """單一選手的英雄池：場數、勝負、KDA、上次出場。"""
    where, params = _pg_where(f)
    connector = " AND " if where else " WHERE "
    sql = f"""
        SELECT champion, COUNT(*) games, SUM(result) wins,
            (SUM(kills)+SUM(assists))*1.0/NULLIF(SUM(deaths),0) kda,
            MAX(date_iso) last_played
        FROM player_games{where}{connector} playername = ?
        GROUP BY champion ORDER BY games DESC
    """
    df = _run_df(sql, params + [player])
    if not df.empty:
        df["win_rate"] = (df["wins"] / df["games"] * 100).round(
            config.RATE_DECIMALS)
        df["kda"] = df["kda"].round(2)
        df["wins"] = df["wins"].astype(int)
    return df


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


def champion_wall(years: list[int] | None = None) -> pd.DataFrame:
    """圖鑑牆：各英雄選用與禁用次數（依年度篩選）。"""
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
    df = picks.merge(bans, on="champion", how="outer").fillna(0)
    df["picks"] = df["picks"].astype(int)
    df["bans"] = df["bans"].astype(int)
    return df.sort_values("picks", ascending=False)
