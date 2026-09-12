"""DuckDB 倉儲建置。

逐年讀取清洗結果，寫入以下表格：
- games        每場一列（藍/紅隊、比分、版本、時長、首選）
- team_games   每戰隊每場一列（目標物、塔殺、結果）
- player_games 每選手每場一列（KDA、金錢、傷害、視野、10/15/20/25 分鐘差）
- draft_bans   BP 禁用長格式
- draft_picks  BP 選用長格式

重建採「寫臨時檔 → 原子取代」，中斷不毀損既有倉儲。
"""
from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd

from backend import config
from backend.pipeline.cleaner import read_year, split_players_teams
from backend.pipeline.common import get_logger

logger = get_logger("warehouse")

# 含空白/駝峰欄位統一改名，方便 SQL 使用
COLUMN_RENAME = {
    "team kpm": "team_kpm",
    "earned gpm": "earned_gpm",
    "total cs": "total_cs",
    "firstPick": "first_pick",
    "dragons (type unknown)": "dragons_unknown",
}

TEAM_COLUMNS = [
    "gameid", "datacompleteness", "league", "year", "split", "playoffs",
    "date_iso", "patch", "game", "side", "teamname", "teamid", "first_pick",
    "result", "gamelength", "teamkills", "teamdeaths", "team_kpm", "ckpm",
    "firstblood", "firstdragon", "dragons", "opp_dragons", "elementaldrakes",
    "infernals", "mountains", "clouds", "oceans", "chemtechs", "hextechs",
    "elders", "opp_elders", "firstherald", "heralds", "opp_heralds",
    "void_grubs", "opp_void_grubs", "firstbaron", "barons", "opp_barons",
    "atakhans", "opp_atakhans", "firsttower", "towers", "opp_towers",
    "firstmidtower", "firsttothreetowers", "turretplates", "opp_turretplates",
    "inhibitors", "opp_inhibitors", "damagetochampions", "dpm",
    "damagetotowers", "totalgold",
    "goldat10", "golddiffat10", "goldat15", "golddiffat15",
    "goldat20", "golddiffat20", "goldat25", "golddiffat25",
]

PLAYER_COLUMNS = [
    "gameid", "datacompleteness", "league", "year", "split", "playoffs",
    "date_iso", "patch", "side", "position", "playername", "playerid",
    "teamname", "teamid", "champion", "result", "gamelength",
    "kills", "deaths", "assists", "teamkills", "teamdeaths",
    "doublekills", "triplekills", "quadrakills", "pentakills",
    "firstblood", "firstbloodkill", "firstbloodassist", "firstbloodvictim",
    "team_kpm", "ckpm", "damagetochampions", "dpm", "damageshare",
    "damagetakenperminute", "damagemitigatedperminute", "damagetotowers",
    "wardsplaced", "wpm", "wardskilled", "wcpm", "controlwardsbought",
    "visionscore", "vspm", "totalgold", "earnedgold", "earned_gpm",
    "earnedgoldshare", "goldspent", "gspd", "gpr", "total_cs",
    "minionkills", "monsterkills", "monsterkillsownjungle",
    "monsterkillsenemyjungle", "cspm",
    "goldat10", "xpat10", "csat10", "golddiffat10", "xpdiffat10",
    "csdiffat10", "killsat10", "assistsat10", "deathsat10",
    "goldat15", "xpat15", "csat15", "golddiffat15", "xpdiffat15",
    "csdiffat15", "killsat15", "assistsat15", "deathsat15",
    "goldat20", "xpat20", "csat20", "golddiffat20", "xpdiffat20",
    "csdiffat20", "killsat20", "assistsat20", "deathsat20",
    "goldat25", "xpat25", "csat25", "golddiffat25", "xpdiffat25",
    "csdiffat25", "killsat25", "assistsat25", "deathsat25",
]


def _select_existing(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """只取存在的目標欄位，缺欄補 NA 以保 schema 一致。"""
    out = pd.DataFrame(index=df.index)
    for col in columns:
        out[col] = df[col] if col in df.columns else pd.NA
    return out


def _build_games(teams: pd.DataFrame) -> pd.DataFrame:
    """由戰隊列組出每場一列的 games 表。"""
    cols = ["gameid", "league", "year", "split", "playoffs", "date_iso",
            "patch", "game", "gamelength", "side", "teamname", "teamid",
            "first_pick", "result", "datacompleteness"]
    t = _select_existing(teams, cols)
    blue = t[t["side"] == "Blue"].drop(columns=["side"]).rename(
        columns={"teamname": "blue_team", "teamid": "blue_teamid",
                 "result": "blue_result", "first_pick": "blue_firstpick"})
    red = t[t["side"] == "Red"].drop(columns=[
        "league", "year", "split", "playoffs", "date_iso", "patch", "game",
        "gamelength", "datacompleteness"]).rename(
        columns={"teamname": "red_team", "teamid": "red_teamid",
                 "result": "red_result", "first_pick": "red_firstpick",
                 "gameid": "gameid_r"})
    games = blue.merge(red, left_on="gameid", right_on="gameid_r", how="inner")
    return games.drop(columns=["gameid_r"])


def _build_draft_long(teams: pd.DataFrame, kind: str) -> pd.DataFrame:
    """展開 ban1..5 / pick1..5 為長格式。"""
    base_cols = ["gameid", "league", "year", "date_iso", "patch", "side",
                 "teamname", "teamid"]
    frames = []
    for order in range(1, 6):
        col = f"{kind}{order}"
        if col not in teams.columns:
            continue
        part = _select_existing(teams, base_cols + [col]).dropna(subset=[col])
        part = part.rename(columns={col: "champion"})
        part["order"] = order
        frames.append(part)
    if not frames:
        return pd.DataFrame(columns=base_cols + ["champion", "order"])
    return pd.concat(frames, ignore_index=True)


def _write_frame(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame,
                 first: bool) -> None:
    """首年建表，後續年份追加。"""
    con.register("_staging_df", df)
    if first:
        con.execute(f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM _staging_df')
    else:
        con.execute(f'INSERT INTO "{table}" SELECT * FROM _staging_df')
    con.unregister("_staging_df")


def rebuild(years: list[int] | None = None) -> dict:
    """全量重建倉儲。"""
    years = years or list(range(config.HISTORY_START_YEAR,
                                config.CURRENT_YEAR + 1))
    tmp_path = config.WAREHOUSE_PATH.with_suffix(".duckdb.tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    summaries: list[dict] = []
    con = duckdb.connect(str(tmp_path))
    try:
        for index, year in enumerate(years):
            df, clean_summary = read_year(year)
            df = df.rename(columns=COLUMN_RENAME)
            players, teams = split_players_teams(df)
            games = _build_games(teams)
            bans = _build_draft_long(teams, "ban")
            picks = _build_draft_long(teams, "pick")

            first = index == 0
            _write_frame(con, "games", games, first)
            _write_frame(con, "team_games",
                         _select_existing(teams, TEAM_COLUMNS), first)
            _write_frame(con, "player_games",
                         _select_existing(players, PLAYER_COLUMNS), first)
            _write_frame(con, "draft_bans", bans, first)
            _write_frame(con, "draft_picks", picks, first)
            summaries.append(clean_summary)
            logger.info("入倉 %d：%d 場、%d 選手列",
                        year, clean_summary["games"], len(players))

        # 常用查詢索引
        for stmt in (
            "CREATE INDEX idx_games_league ON games(league, year, patch)",
            "CREATE INDEX idx_pg_champ ON player_games(champion)",
            "CREATE INDEX idx_pg_player ON player_games(playername, year)",
            "CREATE INDEX idx_pg_team ON player_games(teamname, year)",
        ):
            try:
                con.execute(stmt)
            except duckdb.Error:
                # DuckDB 對 min/max 索引支援隨版本不同，忽略建立失敗
                pass
    finally:
        con.close()

    os.replace(tmp_path, config.WAREHOUSE_PATH)
    total_games = sum(s.get("games", 0) for s in summaries)
    result = {"years": years, "total_games": total_games, "details": summaries}
    logger.info("倉儲重建完成：%d 年、合計 %d 場 → %s",
                len(years), total_games, config.WAREHOUSE_PATH)
    return result
