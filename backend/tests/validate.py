"""數據正確性、篩選與效能驗證（可直接執行：python -m backend.tests.validate）。

驗證內容：
1. 以 pandas 從原始 CSV 獨立復算 5 項指標，比對查詢層結果
2. 3 組篩選條件冒煙測試
3. 5 個關鍵查詢各跑 5 次，p95 延遲須 ≤ 3 秒
"""
from __future__ import annotations

import time
import warnings

import numpy as np
import pandas as pd

from backend import config, data_access
from backend.metrics.scoring import score_champions
from backend.pipeline.cleaner import read_year

warnings.filterwarnings("ignore")

RATE_TOLERANCE = 0.6          # 比率容差（百分點）
COUNT_TOLERANCE_RATIO = 0.0   # 計數須完全一致
PERF_LIMIT_SEC = 3.0
PERF_ROUNDS = 5

_passed: list[str] = []
_failed: list[str] = []


def _check(name: str, actual, expected, tolerance: float = 0.0):
    ok = abs(float(actual) - float(expected)) <= tolerance
    (_passed if ok else _failed).append(name)
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}：實際={actual}｜預期≈{expected}（容差 {tolerance}）")


def _load_raw(league: str, year: int) -> pd.DataFrame:
    """獨立資料源：直接讀原始 CSV（不經倉儲）。"""
    df, _ = read_year(year)
    return df[df["league"] == league].copy()


def validate_metrics() -> None:
    """5+ 項指標以 pandas 獨立復算比對。"""
    print("\n== 1. 指標正確性（LCK 2026，pandas 獨立復算） ==")
    raw = _load_raw("LCK", 2026)
    teams_raw = raw[raw["position"] == "team"]
    players_raw = raw[raw["position"].isin(list(config.POSITIONS))]
    f = data_access.Filter(leagues=["LCK"], years=[2026])

    # 1.1 比賽場數
    _check("比賽場數", data_access.count_games(f),
           teams_raw["gameid"].nunique())

    # 1.2 英雄勝率（取出場量最高者）
    stats = score_champions(data_access.champion_stats(f, 5))
    champ = stats.iloc[0]["champion"]
    sub = players_raw[players_raw["champion"] == champ]
    expected_wr = round(sub["result"].mean() * 100, 2)
    actual_wr = float(stats.iloc[0]["win_rate"])
    _check(f"英雄勝率（{champ}）", actual_wr, expected_wr, RATE_TOLERANCE)

    # 1.3 英雄場數
    _check(f"英雄場數（{champ}）", int(stats.iloc[0]["games"]), len(sub))

    # 1.4 戰隊首龍控制率（取場數最多戰隊）
    tstats = data_access.team_stats(f, 5)
    tstats = tstats.sort_values("games", ascending=False)
    team = tstats.iloc[0]["teamname"]
    tsub = teams_raw[teams_raw["teamname"] == team]
    expected_dragon = round(tsub["firstdragon"].mean() * 100, 2)
    _check(f"戰隊小龍控制率（{team}）",
           float(tstats.iloc[0]["dragon_rate"]), expected_dragon,
           RATE_TOLERANCE)

    # 1.5 選手 KDA（選死亡數 >0 的選手比對）
    pstats = data_access.player_stats(f, 10)
    picked = None
    for _, row in pstats.iterrows():
        psub = players_raw[players_raw["playername"] == row["playername"]]
        if psub["deaths"].sum() > 0 and len(psub) >= 10:
            picked = (row, psub)
            break
    if picked is not None:
        row, psub = picked
        expected_kda = round((psub["kills"].sum() + psub["assists"].sum())
                             / psub["deaths"].sum(), 2)
        _check(f"選手 KDA（{row['playername']}）", float(row["kda"]),
               expected_kda, 0.05)

    # 1.6 禁用計數（ban1..5 獨立展開）
    ban_cols = [f"ban{i}" for i in range(1, 6)]
    ban_series = teams_raw[ban_cols].melt(value_name="champion").dropna()
    ban_counts = ban_series["champion"].value_counts()
    merged = stats.merge(
        ban_counts.rename("expected_bans"), left_on="champion",
        right_index=True, how="left").fillna({"expected_bans": 0})
    diff = int((merged["bans"] - merged["expected_bans"]).abs().sum())
    _check("全英雄禁用計數總誤差", diff, 0)

    # 1.7 模擬 BP 總分＝九分項加權和
    from backend.metrics.draft_score import evaluate
    picks = {
        "T1": {"top": "Aatrox", "jng": "Jarvan IV", "mid": "Ryze",
               "bot": "Ezreal", "sup": "Nautilus"},
        "Dplus Kia": {"top": "Gnar", "jng": "Vi", "mid": "Ahri",
                      "bot": "Kai'Sa", "sup": "Thresh"},
    }
    bans = {"T1": ["Lee Sin"], "Dplus Kia": ["Zed"]}
    draft = evaluate("T1", "Dplus Kia", picks, bans, f)
    for team, out in draft.items():
        weighted = round(sum(v * config.DRAFT_SCORE_WEIGHTS[k]
                             for k, v in out["components"].items()), 1)
        _check(f"BP 加權總分（{team}）", out["total"], weighted, 0.15)


def validate_filters() -> None:
    """3 組篩選條件冒煙測試。"""
    print("\n== 2. 三組篩選條件 ==")
    combos = [
        data_access.Filter(leagues=["LPL", "LCK"], years=[2026]),
        data_access.Filter(leagues=["LEC"], years=[2025], positions=["mid"]),
        data_access.Filter(years=[2020], date_start="2020-06-01",
                           date_end="2020-08-31"),
    ]
    for i, f in enumerate(combos, 1):
        kpi = data_access.overview_kpi(f)
        rows = len(data_access.champion_stats(f, 20))
        ok = kpi["games"] > 0 and rows > 0
        (_passed if ok else _failed).append(f"篩選組合 {i}")
        print(f"[{'PASS' if ok else 'FAIL'}] 組合 {i}：{kpi['games']} 場、"
              f"{rows} 個英雄")


def validate_performance() -> None:
    """關鍵查詢 p95 延遲。"""
    print(f"\n== 3. 效能（每查詢 {PERF_ROUNDS} 次，p95 ≤ {PERF_LIMIT_SEC}s） ==")
    f = data_access.Filter(leagues=["LCK", "LPL", "LEC", "LCS"],
                           years=[2026])
    queries = {
        "overview_kpi": lambda: data_access.overview_kpi(f),
        "champion_stats": lambda: data_access.champion_stats(f, 10),
        "player_stats": lambda: data_access.player_stats(f, 10),
        "team_stats": lambda: data_access.team_stats(f, 5),
        "recent_series": lambda: data_access.recent_series(f, 30),
    }
    for name, fn in queries.items():
        samples = []
        for _ in range(PERF_ROUNDS):
            start = time.perf_counter()
            fn()
            samples.append((time.perf_counter() - start) * 1000)
        p95 = float(np.percentile(samples, 95))
        ok = p95 / 1000 <= PERF_LIMIT_SEC
        (_passed if ok else _failed).append(f"效能 {name}")
        print(f"[{'PASS' if ok else 'FAIL'}] {name}：p95={p95:.0f}ms")


def validate_latest_json() -> None:
    """FR-4：latest.json 必含鍵與 players/teams 聚合區塊檢查。"""
    print("\n== 4. latest.json 結構 ==")
    import json
    path = config.LATEST_JSON
    if not path.exists():
        _failed.append("latest.json 存在性")
        print("[FAIL] latest.json 不存在")
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = ["generated_at", "years", "games_per_year", "games",
                "date_range", "leagues", "kpi", "last7", "patch_changes",
                "streaks", "champions", "champion_snapshot", "players",
                "teams", "champion_wall"]
    for key in required:
        ok = key in payload
        (_passed if ok else _failed).append(f"latest.json 鍵 {key}")
        print(f"[{'PASS' if ok else 'FAIL'}] 鍵存在：{key}")
    for key in ("champions", "players", "teams"):
        ok = key in payload and len(payload[key]) > 0
        (_passed if ok else _failed).append(f"latest.json {key} 非空")
        n = len(payload.get(key, []))
        print(f"[{'PASS' if ok else 'FAIL'}] {key} 聚合筆數：{n}")


def main() -> int:
    validate_metrics()
    validate_filters()
    validate_performance()
    validate_latest_json()
    print(f"\n結果：{len(_passed)} 通過 / {len(_failed)} 失敗")
    if _failed:
        print("失敗項目：", "、".join(_failed))
        return 1
    print("全部驗證通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
