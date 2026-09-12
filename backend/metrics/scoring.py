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


def score_players(df: pd.DataFrame) -> pd.DataFrame:
    """選手評分。"""
    if df.empty:
        return df
    df = df.copy()
    df["deaths_per_game"] = df["deaths"] / df["games"].clip(lower=1)
    return _composite(
        df,
        attack_cols=["win_rate", "kda", "kp", "dpm", "fb_rate"],
        defense_cols=["win_rate", "gold_mid", "vspm", "deaths_per_game"],
    )


def score_teams(df: pd.DataFrame) -> pd.DataFrame:
    """戰隊評分。"""
    if df.empty:
        return df
    return _composite(
        df,
        attack_cols=["win_rate", "team_kpm", "firstblood_rate", "tower_rate"],
        defense_cols=["win_rate", "gold_mid", "dragon_rate", "baron_rate"],
    )


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
