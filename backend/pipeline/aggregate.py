"""聚合產出 latest.json。

內含更新時間、涵蓋年份/場次、KPI、賽區、版本 meta 變化、連勝連敗、
英雄快照與圖鑑牆，供總覽頁快速載入；其餘頁面直接查詢 DuckDB。
"""
from __future__ import annotations

import json
from datetime import datetime

from backend import config, data_access
from backend.metrics.scoring import score_champions, score_players, score_teams
from backend.pipeline.common import atomic_write_text, get_logger

logger = get_logger("aggregate")


def _json_default(obj):
    """numpy/pandas 型別序列化兜底。"""
    if hasattr(obj, "item"):
        return obj.item()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _records(df, columns=None, limit=None):
    if df is None or df.empty:
        return []
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    if limit:
        df = df.head(limit)
    return json.loads(df.to_json(orient="records", force_ascii=False))


def build() -> dict:
    """計算完整聚合內容。"""
    options = data_access.get_options()
    current_year = config.CURRENT_YEAR
    year_filter = data_access.Filter(years=[current_year])

    games_per_year = data_access._run_df(
        "SELECT year, COUNT(*) games FROM games GROUP BY 1 ORDER BY 1"
    )
    games_per_year = {int(r["year"]): int(r["games"])
                      for _, r in games_per_year.iterrows()}

    kpi = data_access.overview_kpi(year_filter)
    last7 = data_access.games_by_day(year_filter)
    changes = data_access.patch_changes(year_filter)
    streaks = data_access.team_streaks(year_filter)

    champs = score_champions(
        data_access.champion_stats(year_filter, min_games=5)
    )
    wall = data_access.champion_wall(years=[current_year])
    players = score_players(
        data_access.player_stats(year_filter, min_games=10)
    ).sort_values("score", ascending=False)
    teams = score_teams(
        data_access.team_stats(year_filter, min_games=10)
    ).sort_values("score", ascending=False)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "years": options["years"],
        "games_per_year": games_per_year,
        "games": sum(games_per_year.values()),
        "date_range": {"min": options["date_min"], "max": options["date_max"]},
        "leagues": options["leagues"],
        "kpi": kpi,
        "last7": _records(last7),
        "patch_changes": changes,
        "streaks": {
            "win_streak": [[t, n] for t, n in streaks["win_streak"]],
            "lose_streak": [[t, n] for t, n in streaks["lose_streak"]],
        },
        "champions": _records(
            champs, ["champion", "games", "win_rate", "bp_rate",
                     "ban_rate", "kda", "attack", "defense", "score"], 50
        ),
        "champion_snapshot": _records(
            champs, ["champion", "games", "win_rate", "bp_rate", "score"], 24
        ),
        "players": _records(
            players, ["playername", "team", "position", "games",
                      "win_rate", "kda", "dpm", "score"], 50
        ),
        "teams": _records(
            teams, ["teamname", "games", "win_rate", "dragon_rate",
                    "tower_rate", "gold_mid", "score"], 50
        ),
        "champion_wall": _records(wall, limit=200),
    }
    return payload


def write_payload(payload: dict) -> None:
    """原子寫入 latest.json（UTF-8、縮排、非 ASCII 保留）。"""
    text = json.dumps(payload, ensure_ascii=False, indent=2,
                      default=_json_default)
    atomic_write_text(config.LATEST_JSON, text)
    size_kb = config.LATEST_JSON.stat().st_size / 1024
    logger.info("latest.json 寫入完成：%.0f KB、%d 場涵蓋",
                size_kb, payload["games"])


def run() -> dict:
    payload = build()
    write_payload(payload)
    return payload
