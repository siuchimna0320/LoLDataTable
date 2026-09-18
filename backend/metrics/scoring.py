"""0–100 百分位評分模組。

口徑：各維度先在同儕集合內取百分位（0–100），再依攻擊/防禦面向平均，
整體評分採攻擊 0.7、防禦 0.3（對應參考圖工具提示），另加少量場數穩定性。
所有加權集中於此檔，方便日後調整。
"""
from __future__ import annotations

import pandas as pd

from backend import config

ATTACK_WEIGHT = 0.7
DEFENSE_WEIGHT = 0.3
SAMPLE_BONUS_MAX = 5.0          # 場數穩定性加分上限
# 選手評分專用：百分位平均後開 γ 次方「精英化拉伸」，
# 使頂尖選手落在 90+（對齊參考站分佈）；戰隊/英雄評分維持線性
PLAYER_STRETCH = 0.5


def pct_rank(series: pd.Series) -> pd.Series:
    """同儕百分位（0–100），缺值以中位數 50 計。"""
    ranked = series.rank(pct=True) * 100
    return ranked.fillna(50.0)


def _composite(df: pd.DataFrame, attack_cols: list[str],
               defense_cols: list[str]) -> pd.DataFrame:
    """依攻擊/防禦指標清單計算三個 0–100 分數欄位。"""
    attack = sum(pct_rank(df[c]) for c in attack_cols) / len(attack_cols)
    defense = sum(pct_rank(df[c]) for c in defense_cols) / len(defense_cols)
    df["attack"] = attack.round(1)
    df["defense"] = defense.round(1)
    sample_bonus = df["games"].rank(pct=True) * SAMPLE_BONUS_MAX
    # 樣本加分可能使頂端者超過 100，統一封頂維持分數語意
    df["score"] = (attack * ATTACK_WEIGHT + defense * DEFENSE_WEIGHT
                   + sample_bonus).clip(upper=100).round(1)
    return df


def score_champions(df: pd.DataFrame) -> pd.DataFrame:
    """英雄評分。攻擊：勝率/KDA/參與/輸出；防禦：勝率/中期金差/承傷。"""
    if df.empty:
        return df
    return _composite(
        df,
        attack_cols=["win_rate", "kda", "kp", "dpm"],
        defense_cols=["win_rate", "gold_mid", "dtaken"],
    )


# 英雄同路評分權重（滑鼠移到表頭可看；合計 1.00）
LANE_SCORE_WEIGHTS = [
    ("win_rate", 0.25, "勝率"),
    ("lane_win", 0.10, "贏線率"),
    ("kda", 0.15, "KDA"),
    ("bp_rate", 0.10, "BP率"),
    ("gold15", 0.15, "中期金差"),
    ("dpm", 0.10, "分均輸出"),
    ("gpm", 0.05, "分均金錢"),
    ("dtaken", 0.05, "分均承傷"),
    ("vspm", 0.05, "分均視分"),
]


def score_champions_lane(df: pd.DataFrame) -> pd.DataFrame:
    """評分＝各指標在同一路線內的百分位加權（0–100）。

    缺值以第 50 百分位計；無主路線者各項皆依中位數。
    """
    if df.empty:
        return df
    df = df.copy()
    total = pd.Series(0.0, index=df.index)
    for col, weight, _ in LANE_SCORE_WEIGHTS:
        pct = df.groupby("main_position")[col].rank(pct=True) * 100
        total = total + pct.fillna(50) * weight
    df["lane_score"] = total.round(1)
    return df


def lane_score_tooltip() -> str:
    """產生表頭 tooltip 文字（權重清單）。"""
    return "評分＝同路百分位加權\n" + "\n".join(
        f"{label} {int(weight * 100)}%" for col, weight, label
        in LANE_SCORE_WEIGHTS)


def score_players(df: pd.DataFrame) -> pd.DataFrame:
    """選手評分。

    攻擊：勝率/KDA/參與率/分均輸出/首殺率；防禦：勝率/中期金差/分均視分/
    場均死亡（反向）。各面向百分位平均後再開根號拉伸（精英化）。
    """
    if df.empty:
        return df
    df = df.copy()
    df["deaths_per_game"] = df["deaths"] / df["games"].clip(lower=1)

    def _stretch(mean_pct: pd.Series) -> pd.Series:
        return 100 * (mean_pct.clip(lower=0) / 100) ** PLAYER_STRETCH

    attack = _stretch(sum(pct_rank(df[c]) for c in
                          ["win_rate", "kda", "kp", "dpm", "fb_rate"]) / 5)
    defense = _stretch(sum(pct_rank(df[c]) for c in
                           ["win_rate", "gold_mid", "vspm",
                            "deaths_per_game"]) / 4)
    df["attack"] = attack.round(1)
    df["defense"] = defense.round(1)
    sample_bonus = df["games"].rank(pct=True) * SAMPLE_BONUS_MAX
    df["score"] = (attack * ATTACK_WEIGHT + defense * DEFENSE_WEIGHT
                   + sample_bonus).clip(upper=100).round(1)
    return df


# 戰隊攻擊/防禦面向的組成指標（tooltip 用，需與 score_teams 一致）
TEAM_ATTACK_COLS = [
    ("win_rate", "勝率"), ("team_kpm", "團隊節奏（KPM）"),
    ("firstblood_rate", "首殺率"), ("tower_rate", "首塔率")]
TEAM_DEFENSE_COLS = [
    ("win_rate", "勝率"), ("gold_mid", "中期金差"),
    ("dragon_rate", "首龍率"), ("baron_rate", "首巴龍率")]


def score_teams(df: pd.DataFrame) -> pd.DataFrame:
    """戰隊評分。攻擊：勝率/節奏/首殺/首塔；防禦：勝率/中期金差/首龍/首巴。"""
    if df.empty:
        return df
    return _composite(
        df,
        attack_cols=[c for c, _ in TEAM_ATTACK_COLS],
        defense_cols=[c for c, _ in TEAM_DEFENSE_COLS],
    )


def team_attack_tooltip() -> str:
    """攻擊值表頭說明。"""
    return "攻擊值＝攻擊面向指標的同儕百分位平均\n" + "、".join(
        label for _, label in TEAM_ATTACK_COLS)


def team_defense_tooltip() -> str:
    """防禦值表頭說明。"""
    return "防禦值＝防禦面向指標的同儕百分位平均\n" + "、".join(
        label for _, label in TEAM_DEFENSE_COLS)


def team_score_tooltip() -> str:
    """整體評分表頭說明。"""
    return (
        f"評分＝攻擊值×{int(ATTACK_WEIGHT * 100)}%"
        f"＋防禦值×{int(DEFENSE_WEIGHT * 100)}%\n"
        f"另加場數穩定性加分（最多 {SAMPLE_BONUS_MAX:.0f} 分），最高 100 分")


def radar_table(df: pd.DataFrame, axes: list[str]) -> pd.DataFrame:
    """將雷達軸原始值正規化為 0–100 百分位，回傳 name/axis/value 長表。"""
    if df.empty:
        return pd.DataFrame(columns=["name", "axis", "value"])
    norm = pd.DataFrame(index=df.index)
    for axis in axes:
        norm[axis] = pct_rank(df[axis]) if axis in df.columns else 50.0
    label_col = "playername" if "playername" in df.columns else "teamname"
    norm["name"] = df[label_col].values
    return norm.melt(id_vars="name", var_name="axis", value_name="value")


def assign_tiers(df: pd.DataFrame, score_col: str = "score") -> pd.DataFrame:
    """依分位門檻把英雄分入 S/A/B/C/D。"""
    if df.empty:
        df["tier"] = []
        return df
    df = df.copy()
    thresholds = config.TIER_QUANTILES
    quantiles = df[score_col].quantile(
        [thresholds["S"], thresholds["A"], thresholds["B"], thresholds["C"]]
    ).to_dict()
    def _tier(v):
        if v >= quantiles[thresholds["S"]]:
            return "S"
        if v >= quantiles[thresholds["A"]]:
            return "A"
        if v >= quantiles[thresholds["B"]]:
            return "B"
        if v >= quantiles[thresholds["C"]]:
            return "C"
        return "D"
    df["tier"] = df[score_col].apply(_tier)
    return df
