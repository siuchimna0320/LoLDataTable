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
from backend.metrics.scoring import (score_champions, score_champions_lane,
                                     score_players, score_teams)
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
    # 擬真儀表板預設值（2026 年、最少場數 0）：會出現單一方 0 場的 NA 邊際案例
    ui_filter = data_access.Filter(years=[2026])
    edge_queries = {
        "champion_stats(min=0)": lambda: data_access.champion_stats(ui_filter, 0),
        "player_stats(min=0)": lambda: data_access.player_stats(ui_filter, 0),
        "team_stats(min=0)": lambda: data_access.team_stats(ui_filter, 0),
        "score_champions(min=0)":
            lambda: score_champions(data_access.champion_stats(ui_filter, 0)),
    }
    for name, fn in edge_queries.items():
        try:
            rows = len(fn())
            ok = rows > 0
        except Exception as exc:  # noqa: BLE001
            rows, ok = 0, False
            print(f"       例外：{type(exc).__name__}: {exc}")
        (_passed if ok else _failed).append(f"NA 邊際 {name}")
        print(f"[{'PASS' if ok else 'FAIL'}] {name}：{rows} 列")

    # 英雄排行（表格/散布圖/積分三視圖共用）
    rank_cols = {"champion", "games", "wins", "bp_rate", "ban_rate",
                 "win_rate", "lane_win", "late_rate", "kp", "kda",
                 "gold15", "gpm", "dpm", "dtaken", "vspm", "positions",
                 "main_position"}
    try:
        ranking = data_access.champion_ranking(ui_filter, 5)
        missing = rank_cols - set(ranking.columns)
        ranges_ok = (
            len(ranking) > 0 and not missing
            and ((ranking["bp_rate"] >= 0) & (ranking["bp_rate"] <= 100)).all()
            and ((ranking["win_rate"] >= 0) &
                 (ranking["win_rate"] <= 100)).all()
        )
        scored = score_champions_lane(ranking)
        ranges_ok = ranges_ok and ((scored["lane_score"] >= 0) &
                                   (scored["lane_score"] <= 100)).all()
    except Exception as exc:  # noqa: BLE001
        missing, ranges_ok = str(exc), False
    (_passed if ranges_ok else _failed).append("英雄排行欄位與值域")
    print(f"[{'PASS' if ranges_ok else 'FAIL'}] 英雄排行欄位與值域："
          f"{'齊全' if ranges_ok else missing}")

    # 戰隊/選手關鍵字過濾
    try:
        member_rows = len(data_access.champion_ranking(
            ui_filter, member_search="Chovy"))
        member_ok = member_rows > 0
    except Exception:  # noqa: BLE001
        member_ok = False
    (_passed if member_ok else _failed).append("戰隊選手關鍵字過濾")
    print(f"[{'PASS' if member_ok else 'FAIL'}] 戰隊選手關鍵字過濾："
          f"{member_rows if member_ok else '例外'} 隻")

    # 戰隊排行（戰隊頁雷達與 25 欄總表）
    team_cols = {"teamname", "league", "games", "wins", "avg_len",
                 "win_rate", "side_diff", "champ_pool", "tower_rate",
                 "grub2_rate", "dragon_rate", "herald_rate", "dragons",
                 "baron_rate", "gd10", "gold_mid", "team_gpm", "team_dpm",
                 "kd", "k_pg", "d_pg", "vspm", "abbr", "logo_file"}
    try:
        teams = score_teams(data_access.team_ranking(ui_filter, 5))
        missing_t = team_cols - set(teams.columns)
        team_ok = (
            len(teams) > 0 and not missing_t
            and ((teams["win_rate"] >= 0) &
                 (teams["win_rate"] <= 100)).all()
            and ((teams["attack"] >= 0) & (teams["attack"] <= 100)).all()
            and ((teams["defense"] >= 0) &
                 (teams["defense"] <= 100)).all()
            and ((teams["score"] >= 0) & (teams["score"] <= 100)).all()
            and teams["abbr"].notna().all()
        )
        # h2h 需取同聯賽前兩名（跨聯賽隊伍可能從未對戰）
        top_league = teams.groupby("league")["games"].sum().idxmax()
        top2 = teams[teams["league"] == top_league].sort_values(
            "games", ascending=False).head(2)["teamname"].tolist()
        league_filter = data_access.Filter(
            years=ui_filter.years, leagues=[top_league])
        h2h = data_access.team_ranking(league_filter, min_games=0,
                                       h2h_teams=(top2[0], top2[1]))
        team_ok = team_ok and len(h2h) == 2
    except Exception as exc:  # noqa: BLE001
        missing_t, team_ok = str(exc), False
    (_passed if team_ok else _failed).append("戰隊排行欄位與值域")
    print(f"[{'PASS' if team_ok else 'FAIL'}] 戰隊排行欄位與值域："
          f"{'齊全' if team_ok else missing_t}")

    # 戰隊頁預設口徑：最新賽季六大頂級聯賽（排除 Versus 表演賽）應為 64 隊
    try:
        default_n = len(data_access.team_ranking(
            data_access.default_team_filter()))
        default_ok = default_n == 64
    except Exception as exc:  # noqa: BLE001
        default_n, default_ok = str(exc), False
    (_passed if default_ok else _failed).append("戰隊預設口徑 64 隊")
    print(f"[{'PASS' if default_ok else 'FAIL'}] 戰隊預設口徑 64 隊："
          f"{default_n} 隊（最新賽季 {data_access.latest_top_year()}）")

    # 選手頁預設口徑：最新賽季六大頂級聯賽，22 欄口徑欄位齊全且值域合理
    try:
        pdf = data_access.player_ranking(data_access.default_team_filter())
        need_p = {
            "playername", "abbr", "league", "position", "games", "wins",
            "win_rate", "champ_pool", "kp", "kda", "k_pg", "d_pg", "a_pg",
            "kda_diff10", "lane_win", "lane_gd15", "dmg_share",
            "death_share", "fb_diff", "gold_eff", "gold_mid", "gpm",
            "dpm", "dtaken", "vspm"}
        missing_p = need_p - set(pdf.columns)
        pdf2 = score_players(pdf)
        player_ok = (
            len(pdf) >= 300 and not missing_p
            and ((pdf2["win_rate"] >= 0) &
                 (pdf2["win_rate"] <= 100)).all()
            and ((pdf2["attack"] >= 0) & (pdf2["attack"] <= 100)).all()
            and ((pdf2["defense"] >= 0) &
                 (pdf2["defense"] <= 100)).all()
            and ((pdf2["score"] >= 0) & (pdf2["score"] <= 100)).all()
            and pdf2["score"].max() >= 85
            and pdf2["abbr"].notna().all()
            # Versus 表演賽不應出現
            and (pdf2["league"] != "Versus").all()
        )
        # h2h：取同聯賽中單場數前兩名，對戰口徑應只回傳這兩人
        mid = pdf2[pdf2["position"] == "mid"].sort_values(
            "games", ascending=False)
        lg = mid.iloc[0]["league"]
        pair = mid[mid["league"] == lg].head(2)["playername"].tolist()
        lf = data_access.Filter(
            years=data_access.default_team_filter().years, leagues=[lg],
            positions=["mid"])
        ph2h = data_access.player_ranking(
            lf, min_games=0, h2h_players=(pair[0], pair[1]))
        player_ok = player_ok and len(ph2h) == 2
    except Exception as exc:  # noqa: BLE001
        missing_p, player_ok = str(exc), False
    (_passed if player_ok else _failed).append("選手排行欄位與值域")
    print(f"[{'PASS' if player_ok else 'FAIL'}] 選手排行欄位與值域："
          f"{'齊全' if player_ok else missing_p}")


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


def validate_standings_and_compendium() -> None:
    """積分榜推算一致性＋Data Dragon 圖鑑快取結構。"""
    print("\n== 5. 積分榜與圖鑑 ==")
    f = data_access.Filter(leagues=["LCK"], years=[2026])

    groups = data_access.standings_groups(f)
    ok = not groups.empty and {"league", "year", "split"} <= set(groups.columns)
    (_passed if ok else _failed).append("積分榜分組非空")
    print(f"[{'PASS' if ok else 'FAIL'}] 積分榜分組筆數：{len(groups)}")

    df = data_access.standings(f)
    if df.empty:
        _failed.append("積分榜非空")
        print("[FAIL] 積分榜無資料")
    else:
        # 淨勝場＝小場勝－敗
        diff_ok = bool((df["game_diff"]
                        == df["game_w"] - df["game_l"]).all())
        (_passed if diff_ok else _failed).append("積分榜淨勝場一致")
        print(f"[{'PASS' if diff_ok else 'FAIL'}] 淨勝場＝小場勝－敗")
        # 排名於各賽段內從 1 連續編號
        rank_ok = all(g["rank"].tolist() == list(range(1, len(g) + 1))
                      for _, g in df.groupby(["league", "year", "split"]))
        (_passed if rank_ok else _failed).append("積分榜排名連續")
        print(f"[{'PASS' if rank_ok else 'FAIL'}] 各賽段排名自 1 連續")
        # 大場與小場計數合理
        count_ok = bool(((df["series_w"] + df["series_l"]
                          + df["series_d"]) > 0).all()
                        and (df["game_w"] + df["game_l"] >= 1).all())
        (_passed if count_ok else _failed).append("積分榜計數合理")
        print(f"[{'PASS' if count_ok else 'FAIL'}] 大場/小場計數均 > 0")

    from backend import ddragon_data
    bundle = ddragon_data.load_bundle()
    if not bundle:
        _failed.append("圖鑑快取可載入")
        print("[FAIL] Data Dragon 圖鑑快取無法載入")
        return
    print(f"[PASS] 圖鑑快取可載入（版本 {bundle.get('version')}）")
    expectations = {
        "items": (len(bundle.get("items", [])), 100),
        "summoners": (len(bundle.get("summoners", [])), 5),
        "runes": (len(bundle.get("runes", [])), 5),
        "versions": (len(bundle.get("versions", [])), 10),
    }
    for key, (n, minimum) in expectations.items():
        ok = n >= minimum
        (_passed if ok else _failed).append(f"圖鑑 {key} 數量")
        print(f"[{'PASS' if ok else 'FAIL'}] {key} 筆數：{n}（≥{minimum}）")
    slots_ok = all(len(t.get("slots", [])) == 4 for t in bundle["runes"])
    (_passed if slots_ok else _failed).append("符文 5 系各 4 排")
    print(f"[{'PASS' if slots_ok else 'FAIL'}] 符文 5 系各 4 排")


def validate_jungle() -> None:
    """Jungle Clear Compilation 刷野編纂表快取結構檢查。"""
    print("\n== 6. 刷野編纂表 ==")
    xlsx_ok = config.JUNGLE_XLSX_PATH.exists()
    (_passed if xlsx_ok else _failed).append("刷野 xlsx 本機副本存在")
    print(f"[{'PASS' if xlsx_ok else 'FAIL'}] 本機副本："
          f"{config.JUNGLE_XLSX_PATH.name}")
    from backend import jungle_data
    payload = jungle_data.load_clears()
    if not payload:
        _failed.append("刷野快取可載入")
        print("[FAIL] 刷野編纂表無法載入（本機副本與 JSON 快取皆失敗）")
        return
    print(f"[PASS] 刷野快取可載入（更新於 {payload.get('fetched_at', '?')}）")
    champions = payload.get("champions", [])
    clears = [c for ch in champions for c in ch.get("clears", [])]
    checks = [
        ("英雄數 ≥ 100", len(champions) >= 100, len(champions)),
        ("刷野列 ≥ 300", len(clears) >= 300, len(clears)),
    ]
    for name, ok, n in checks:
        (_passed if ok else _failed).append(name)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}：實際 {n}")
    # 必要欄位完整
    required = {"time", "seconds", "skills", "shards", "path_zh",
                "side", "player", "link"}
    field_ok = all(required <= set(c) for c in clears)
    (_passed if field_ok else _failed).append("刷野列欄位完整")
    print(f"[{'PASS' if field_ok else 'FAIL'}] 每列含時間/技能/路徑/連結")
    # 連結皆為 YouTube http 連結
    link_ok = all(c["link"].startswith("http") for c in clears)
    (_passed if link_ok else _failed).append("刷野示範連結有效")
    print(f"[{'PASS' if link_ok else 'FAIL'}] 示範連結皆為 http(s)")
    # 路徑已在地化（不含英文營地 token）
    english_tokens = ("Raptors", "Krugs", "Gromp", "Wolves")
    path_ok = all(not any(t in c["path_zh"] for t in english_tokens)
                  for c in clears)
    (_passed if path_ok else _failed).append("刷野路徑中文化")
    print(f"[{'PASS' if path_ok else 'FAIL'}] 營地路徑皆已轉中文")
    # 技能圖示對應覆蓋率（允許少數英雄 JSON 失敗）
    with_icon = sum(1 for ch in champions
                    if {"Q", "W", "E"} <= set(ch.get("skill_icons", {})))
    coverage = with_icon / max(len(champions), 1)
    icon_ok = coverage >= 0.9
    (_passed if icon_ok else _failed).append("技能圖示覆蓋率 ≥ 90%")
    print(f"[{'PASS' if icon_ok else 'FAIL'}] Q/W/E 圖示覆蓋率："
          f"{with_icon}/{len(champions)}（{coverage:.0%}）")


def validate_roster() -> None:
    """陣容頁：戰隊解析、五路候選、選手英雄池口徑檢查。"""
    print("\n== 7. 陣容頁 ==")
    from backend import data_access as da

    # 戰隊縮寫／全名精確解析（避免 T1 誤命中 T1 Academy）
    checks = [
        ("resolve_team('T1') = T1", da.resolve_team("T1") == "T1",
         da.resolve_team("T1")),
        ("resolve_team('GEN') = Gen.G", da.resolve_team("GEN") == "Gen.G",
         da.resolve_team("GEN")),
        ("resolve_team 亂數回傳 None", da.resolve_team("不存在的戰隊xyz")
         is None, da.resolve_team("不存在的戰隊xyz")),
    ]
    # 最新賽季 T1 五位置皆有候選，首選＝該位置上次出賽日最新者
    f_latest = da.Filter(years=[da.latest_top_year()])
    cand = da.roster_candidates("T1", f_latest)
    five_ok = set(da.ROSTER_POSITIONS) <= set(cand["position"])
    checks.append(("T1 五路候選齊全", five_ok,
                   sorted(cand["position"].unique())))
    for pos in da.ROSTER_POSITIONS:
        grp = cand[cand["position"] == pos].sort_values(
            ["last_date", "games"], ascending=False)
        ok = (not grp.empty and grp.iloc[0]["last_date"]
              == grp["last_date"].max())
        checks.append((f"T1 {pos} 首選上次日期最新", bool(ok),
                       grp.iloc[0]["playername"] if not grp.empty else "無"))

    # Faker 職業生涯：年份跨度、英雄池按場數降冪
    s_all, pool_all = da.roster_player_detail(
        "T1", "mid", "Faker", da.Filter())
    checks += [
        ("Faker 職業生涯跨年（y0<y1）", s_all["y0"] < s_all["y1"],
         f"{s_all['y0']}-{s_all['y1']}"),
        ("Faker 職業生涯總場 ≥ 500", s_all["games"] >= 500,
         s_all["games"]),
        ("英雄池按場數降冪",
         bool(pool_all["games"].is_monotonic_decreasing),
         pool_all["games"].head(3).tolist()),
    ]
    # 場數門檻：min_games=5 後列數減少且皆 ≥ 5
    s5, pool5 = da.roster_player_detail(
        "T1", "mid", "Faker", da.Filter(), min_games=5)
    checks += [
        ("min_games=5 過濾低場數英雄",
         len(pool5) < len(pool_all) and (pool5["games"] >= 5).all(),
         f"{len(pool_all)} → {len(pool5)}"),
    ]
    for name, ok, actual in checks:
        (_passed if ok else _failed).append(name)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}：實際 {actual}")


def validate_bp() -> None:
    """比賽 BP 頁：系列賽分組、比分、蛇形選角順位與欄位篩選檢查。"""
    print("\n== 8. 比賽 BP 頁 ==")
    from backend import data_access as da

    # 驗證用全量明細（不受前端渲染上限影響）
    data = da.bp_board_data(da.Filter(years=[2026], leagues=["LCK"]),
                            render_games=100000)
    s = data["stats"]
    checks = [
        ("LCK 2026 有場次", s["games"] > 100, s["games"]),
        ("場均時長為 mm:ss", ":" in s["avg_length"], s["avg_length"]),
        ("藍紅勝場合計＝總場",
         s["blue_wins"] + s["red_wins"] == s["games"],
         f"{s['blue_wins']}+{s['red_wins']}/{s['games']}"),
        ("BP 完整率 100%（LCK）", s["complete_pct"] == 100,
         f"{s['complete_n']}/{s['games']}"),
        ("系列數 > 0", s["series_total"] > 0, s["series_total"]),
    ]
    # T1 2-3 HLE（2026-09-02，五局）
    t1 = [x for x in data["series"] if x["date"] == "2026-09-02"
          and {x["abbr_a"], x["abbr_b"]} == {"T1", "HLE"}]
    if t1:
        g = t1[0]
        checks += [
            ("T1-HLE 系列 5 局", len(g["games"]) == 5, len(g["games"])),
            ("比分 T1 2-3 HLE",
             (g["score_a"], g["score_b"], g["winner"]) == (2, 3, "b"),
             f"{g['abbr_a']} {g['score_a']}-{g['score_b']} {g['abbr_b']}"),
            ("階段標籤 S3季後賽", g["stage"] == "S3季後賽", g["stage"]),
        ]
        # 第 1 局紅方先選 → 紅方順位 1,3,5,7,9
        gm = g["games"][0]
        red_orders = [p["order"] for p in gm["red_picks"]]
        blue_orders = [p["order"] for p in gm["blue_picks"]]
        checks += [
            ("蛇形順位：紅先選紅為奇數",
             set(red_orders) == {1, 3, 5, 7, 9} and
             set(blue_orders) == {2, 4, 6, 8, 10},
             f"red={sorted(red_orders)} blue={sorted(blue_orders)}"),
            ("每邊各 5 選 5 禁",
             len(gm["red_picks"]) == 5 and len(gm["red_bans"]) == 5
             and len(gm["blue_picks"]) == 5 and len(gm["blue_bans"]) == 5,
             "ok"),
        ]
    else:
        checks.append(("找到 T1-HLE 09-02 系列", False, "缺"))
    # 英雄欄位篩選會縮小場次（LCK 場次為基準，全聯盟 Azir 仍應有資料）
    d2 = da.bp_board_data(da.Filter(years=[2026], leagues=["LCK"]),
                          champ_kw="Azir")
    checks.append(("英雄篩選（Azir）生效且縮小範圍",
                   0 < d2["stats"]["games"] < s["games"],
                   d2["stats"]["games"]))
    # 紅方欄輸入縮寫 HLE 應命中 Hanwha Life Esports
    d3 = da.bp_board_data(da.Filter(years=[2026], leagues=["LCK"]),
                          red_kw="HLE", render_games=100000)
    checks.append(("紅方縮寫篩選（HLE）命中", d3["stats"]["games"] > 0,
                   d3["stats"]["games"]))
    for name, ok, actual in checks:
        (_passed if ok else _failed).append(name)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}：實際 {actual}")


def main() -> int:
    validate_metrics()
    validate_filters()
    validate_performance()
    validate_latest_json()
    validate_standings_and_compendium()
    validate_jungle()
    validate_roster()
    validate_bp()
    print(f"\n結果：{len(_passed)} 通過 / {len(_failed)} 失敗")
    if _failed:
        print("失敗項目：", "、".join(_failed))
        return 1
    print("全部驗證通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
