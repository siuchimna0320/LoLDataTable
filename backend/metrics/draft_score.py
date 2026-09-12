"""模擬 BP 選角評分引擎（九分項加權）。

輸入兩隊完成的禁/選陣容，輸出各隊 0–100 的總分與分項。
樣本不足或無資料的項目以中性分 50 計（對應參考圖口徑）。
"""
from __future__ import annotations

import pandas as pd

from backend import config
from backend.data_access import Filter, _run_df, champion_stats, team_stats
from backend.metrics.scoring import pct_rank

# 各分項所需最小樣本
MIN_CHAMP_GAMES = 5
MIN_PAIR_GAMES = 5
MIN_PLAYER_CHAMP_GAMES = 3


def _scope_sql(f: Filter, table: str) -> tuple[str, list]:
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
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def champion_strength_map(f: Filter) -> dict[str, float]:
    """英雄版本強弱：BP% 與勝率雙百分位平均。"""
    df = champion_stats(f, min_games=MIN_CHAMP_GAMES)
    if df.empty:
        return {}
    strength = (pct_rank(df["bp_rate"]) + pct_rank(df["win_rate"])) / 2
    return dict(zip(df["champion"], strength.round(1)))


def _team_champion_rows(team: str, f: Filter) -> pd.DataFrame:
    """戰隊內選手-英雄使用紀錄（含位置、場數、勝場）。"""
    where, params = _scope_sql(f, "player_games")
    sql = f"""
        SELECT position, playername, champion, COUNT(*) games, SUM(result) wins
        FROM player_games{where} {'AND' if where else 'WHERE'} teamname = ?
        GROUP BY 1,2,3
    """
    return _run_df(sql, params + [team])


def _champ_position_table(f: Filter) -> pd.DataFrame:
    where, params = _scope_sql(f, "player_games")
    sql = f"""
        SELECT champion, position, COUNT(*) games, AVG(result)*100 win_rate
        FROM player_games{where}
        GROUP BY 1,2 HAVING COUNT(*) >= {MIN_CHAMP_GAMES}
    """
    return _run_df(sql, params)


def _prefixed_scope(f: Filter, prefix: str) -> tuple[str, list]:
    """組帶表別名前綴的 WHERE 片段（用於 JOIN 查詢）。"""
    clauses, params = [], []
    if f.leagues:
        clauses.append(f"{prefix}.league IN ({','.join('?' * len(f.leagues))})")
        params.extend(f.leagues)
    if f.years:
        clauses.append(f"{prefix}.year IN ({','.join('?' * len(f.years))})")
        params.extend([int(y) for y in f.years])
    if f.patches:
        clauses.append(f"{prefix}.patch IN ({','.join('?' * len(f.patches))})")
        params.extend([float(p) for p in f.patches])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _pair_winrate(f: Filter, c1: str, c2: str) -> float | None:
    """同隊兩英雄共同出場的勝率。"""
    scope, sparams = _prefixed_scope(f, "a")
    sql = (
        "SELECT COUNT(*) games, AVG(a.result)*100 win_rate "
        "FROM player_games a JOIN player_games b "
        "ON a.gameid=b.gameid AND a.side=b.side AND a.position<>b.position "
        f"WHERE a.champion=? AND b.champion=? "
        + scope.replace(" WHERE ", " AND ")
    )
    df = _run_df(sql, [c1, c2] + sparams)
    row = df.iloc[0]
    if row["games"] < MIN_PAIR_GAMES:
        return None
    return float(row["win_rate"])


def evaluate(team_a: str, team_b: str,
             picks: dict[str, dict[str, str]],
             bans: dict[str, list[str]], f: Filter) -> dict:
    """計算兩隊選角評分。

    picks: {戰隊: {position: champion}}（五個位置皆填入）
    bans:  {戰隊: [champion, ...]}
    """
    strength = champion_strength_map(f)
    pos_table = _champ_position_table(f)
    tdf = team_stats(f, min_games=1)
    team_wr = dict(zip(tdf["teamname"], tdf["win_rate"])) if not tdf.empty else {}

    result = {}
    roster_rows = {t: _team_champion_rows(t, f) for t in (team_a, team_b)}

    for team, opp in ((team_a, team_b), (team_b, team_a)):
        comps: dict[str, float] = {}
        own_picks = picks.get(team, {})
        champs = list(own_picks.values())

        # 1. 版本強弱
        vals = [strength.get(c) for c in champs if c in strength]
        comps["patch_strength"] = round(sum(vals) / len(vals), 1) if vals \
            else config.DRAFT_NEUTRAL_SCORE

        # 2. 選手熟練（該選手使用該英雄的勝率，樣本不足計中性）
        rows = roster_rows[team]
        mastery = []
        for pos, champ in own_picks.items():
            hit = rows[(rows["position"] == pos) & (rows["champion"] == champ)]
            if not hit.empty and int(hit.iloc[0]["games"]) >= MIN_PLAYER_CHAMP_GAMES:
                mastery.append(float(hit.iloc[0]["wins"]) /
                               int(hit.iloc[0]["games"]) * 100)
        comps["player_mastery"] = round(sum(mastery) / len(mastery), 1) \
            if mastery else config.DRAFT_NEUTRAL_SCORE

        # 3. 戰隊勝率
        comps["team_winrate"] = float(team_wr.get(team, config.DRAFT_NEUTRAL_SCORE))

        # 4. 對線對位（同位置英雄勝率差）
        opp_picks = picks.get(opp, {})
        lane_vals = []
        for pos, champ in own_picks.items():
            opp_champ = opp_picks.get(pos)
            if not opp_champ:
                continue
            own = pos_table[(pos_table["champion"] == champ) &
                            (pos_table["position"] == pos)]["win_rate"]
            enemy = pos_table[(pos_table["champion"] == opp_champ) &
                              (pos_table["position"] == pos)]["win_rate"]
            if not own.empty and not enemy.empty:
                lane_vals.append(50 + (float(own.iloc[0]) -
                                       float(enemy.iloc[0])) / 2)
        comps["lane_matchup"] = round(sum(lane_vals) / len(lane_vals), 1) \
            if lane_vals else config.DRAFT_NEUTRAL_SCORE

        # 5. 隊友相合（兩兩組合勝率）
        pair_vals = []
        for i in range(len(champs)):
            for j in range(i + 1, len(champs)):
                wr = _pair_winrate(f, champs[i], champs[j])
                if wr is not None:
                    pair_vals.append(wr)
        comps["synergy"] = round(sum(pair_vals) / len(pair_vals), 1) \
            if pair_vals else config.DRAFT_NEUTRAL_SCORE

        # 6. 陣容（五位置齊全且無重複英雄＝滿分）
        comps["composition"] = 100.0 if (
            len(own_picks) == 5 and len(set(champs)) == 5) else \
            config.DRAFT_NEUTRAL_SCORE

        # 7. 禁用（自己禁用英雄的平均強度）
        ban_vals = [strength.get(c) for c in bans.get(team, []) if c in strength]
        comps["ban_value"] = round(sum(ban_vals) / len(ban_vals), 1) \
            if ban_vals else config.DRAFT_NEUTRAL_SCORE

        # 8. 可錯位（戰隊內該英雄曾在 2+ 位置出賽的比例）
        flex_hits, flex_total = 0, 0
        for champ in champs:
            sub = rows[rows["champion"] == champ]
            if not sub.empty:
                flex_total += 1
                if sub["position"].nunique() >= 2:
                    flex_hits += 1
        comps["flex_pick"] = round(flex_hits / flex_total * 100, 1) \
            if flex_total else config.DRAFT_NEUTRAL_SCORE

        # 9. 戰隊近況
        from backend.data_access import recent_team_form
        form = recent_team_form(team, f)
        comps["team_form"] = float(form) if pd.notna(form) \
            else config.DRAFT_NEUTRAL_SCORE

        weights = config.DRAFT_SCORE_WEIGHTS
        total = sum(comps[k] * w for k, w in weights.items())
        result[team] = {"total": round(total, 1), "components": comps}
    return result
