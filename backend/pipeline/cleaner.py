"""CSV 清洗模組。

- 統一缺失值與型別（日期、數值、類別）；
- 去除 (gameid, participantid) 重複列；
- 分離選手列與戰隊列；
- 產出供入倉的標準 DataFrame 與清洗摘要。
"""
from __future__ import annotations

import pandas as pd

from backend import config
from backend.pipeline.common import ensure_file_exists, get_logger

logger = get_logger("cleaner")

# 明確保留為文字的欄位（其餘欄位嘗試數值化）
TEXT_COLUMNS = {
    "gameid", "datacompleteness", "url", "league", "split", "date", "side",
    "position", "playername", "playerid", "teamname", "teamid", "champion",
    "ban1", "ban2", "ban3", "ban4", "ban5",
    "pick1", "pick2", "pick3", "pick4", "pick5",
}

BP_COLUMNS = [f"ban{i}" for i in range(1, 6)] + [f"pick{i}" for i in range(1, 6)]


def clean_dataframe(df: pd.DataFrame, year: int) -> tuple[pd.DataFrame, dict]:
    """清洗單一年度 DataFrame，回傳 (清洗後資料, 摘要)。"""
    raw_rows = len(df)
    summary = {"year": year, "raw_rows": raw_rows,
               "dropped_duplicates": 0, "dropped_bad_date": 0}

    # 類別文字標準化：去除空白、空字串轉遺漏
    for col in TEXT_COLUMNS:
        if col in df.columns:
            df[col] = df[col].astype("string").str.strip()
            df[col] = df[col].replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})

    # 日期解析（原始格式如 2026-01-08 17:08:27）
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    bad_date = int(df["date"].isna().sum())
    if bad_date:
        logger.warning("%d 年有 %d 列日期無法解析，將保留但標記", year, bad_date)
    summary["dropped_bad_date"] = bad_date

    # 其餘欄位嘗試數值化（ID 與文字欄除外）
    for col in df.columns:
        if col in TEXT_COLUMNS or col in ("date", "date_iso"):
            continue
        converted = pd.to_numeric(df[col], errors="coerce")
        # 僅在多數值可轉換時才採納，避免把異常文字欄全數清成 NaN
        if converted.notna().sum() >= df[col].notna().sum() * 0.5:
            df[col] = converted

    # 去重：同一比賽同一參與者僅保留一列（複製一次以消除 DataFrame 碎片化）
    before = len(df)
    df = df.drop_duplicates(
        subset=["gameid", "participantid"], keep="last"
    ).copy()
    summary["dropped_duplicates"] = before - len(df)

    df["year"] = df["year"].fillna(year).astype("Int64")
    # 統一 ISO 8601 日期字串（YYYY-MM-DD），物件型別確保入倉為 VARCHAR
    df["date_iso"] = df["date"].dt.strftime("%Y-%m-%d")
    summary["clean_rows"] = len(df)
    summary["games"] = int(df["gameid"].nunique())
    return df, summary


def read_year(year: int) -> tuple[pd.DataFrame, dict]:
    """讀取並清洗單一年度 CSV。"""
    path = config.RAW_DIR / config.OE_FILE_TEMPLATE.format(year=year)
    ensure_file_exists(path)
    logger.info("讀取 %s", path.name)
    df = pd.read_csv(path, low_memory=False, dtype={"gameid": "string"})
    return clean_dataframe(df, year)


def split_players_teams(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """分離選手列（五個位置）與戰隊列。"""
    players = df[df["position"].isin(config.POSITIONS)].copy()
    teams = df[df["position"].eq("team")].copy()
    return players, teams
