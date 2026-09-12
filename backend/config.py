"""全域設定：路徑常數、外部資料源、重試參數與評分權重。

所有模組皆應從此處取得路徑與可配置參數，避免散落的硬編碼值。
容器部署時可以環境變數 DATA_ROOT 覆蓋數據根目錄。
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 路徑設定
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 容器掛載場景：/data 優先；本機開發預設 backend/data
DATA_ROOT = Path(os.environ.get("DATA_ROOT", PROJECT_ROOT / "backend" / "data"))

RAW_DIR = DATA_ROOT / "OraclesElixir"          # Oracle's Elixir 原始 CSV
WAREHOUSE_DIR = DATA_ROOT / "warehouse"         # DuckDB 倉儲
PROCESSED_DIR = DATA_ROOT / "processed"         # JSON 聚合產物
ASSETS_DIR = DATA_ROOT / "assets"               # 遊戲圖示資源
CHAMP_ICON_DIR = ASSETS_DIR / "champions"
LOGS_DIR = DATA_ROOT / "logs"

WAREHOUSE_PATH = WAREHOUSE_DIR / "lol.duckdb"
LATEST_JSON = PROCESSED_DIR / "latest.json"
DD_META_PATH = ASSETS_DIR / "ddragon_meta.json"

# 數據片段（供儀表板分頁載入，避免每頁重掃全量）
SLICE_DIR = PROCESSED_DIR / "slices"

for _dir in (RAW_DIR, WAREHOUSE_DIR, PROCESSED_DIR, ASSETS_DIR,
             CHAMP_ICON_DIR, LOGS_DIR, SLICE_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Oracle's Elixir 來源設定
# ---------------------------------------------------------------------------
# 官方 Google Drive 資料夾（每年一個 CSV，每日更新一次）
DRIVE_FOLDER_ID = "1gLSw0RLjBbtaNy0dgnGQDAZOHIgCe-HH"
OE_FILE_TEMPLATE = "{year}_LoL_esports_match_data_from_OraclesElixir.csv"

CURRENT_YEAR = int(os.environ.get("OE_CURRENT_YEAR", datetime.now().year))
HISTORY_START_YEAR = 2014

# 原始 CSV 關鍵欄位（下載驗證用）
REQUIRED_COLUMNS = ("gameid", "date", "league", "year", "position", "result")

# ---------------------------------------------------------------------------
# Data Dragon 設定
# ---------------------------------------------------------------------------
DDRAGON_VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
DDRAGON_CHAMPION_FULL_URL = (
    "https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/championFull.json"
)
DDRAGON_SQUARE_URL = (
    "https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{champ_id}.png"
)

# 歷史遺留名稱 → DDragon 顯示名（自動映射失敗時的人工補充表）
CHAMPION_NAME_OVERRIDES: dict[str, str] = {}

# ---------------------------------------------------------------------------
# 網路重試參數
# ---------------------------------------------------------------------------
RETRY_ATTEMPTS = 3
RETRY_MULTIPLIER = 2.0      # 指數退避基數（秒）
RETRY_MIN_WAIT = 2.0
REQUEST_TIMEOUT = 60
REQUEST_INTERVAL = 2.0      # 相鄰請求最小間隔（秒），避免對目標伺服器造成負擔

# ---------------------------------------------------------------------------
# 數據口徑
# ---------------------------------------------------------------------------
POSITIONS = ("top", "jng", "mid", "bot", "sup")
POSITION_ZH = {"top": "上路", "jng": "打野", "mid": "中路",
               "bot": "下路", "sup": "輔助", "team": "戰隊"}
RATE_DECIMALS = 2           # 比率欄位保留小數位

# ---------------------------------------------------------------------------
# 模擬 BP 選角評分權重（合計 100%）
# 版本強弱/選手熟練/戰隊勝率/對線對位/隊友相合/陣容/禁用/可錯位/戰隊近況
# ---------------------------------------------------------------------------
DRAFT_SCORE_WEIGHTS = {
    "patch_strength": 0.20,
    "player_mastery": 0.20,
    "team_winrate": 0.10,
    "lane_matchup": 0.20,
    "synergy": 0.05,
    "composition": 0.05,
    "ban_value": 0.10,
    "flex_pick": 0.05,
    "team_form": 0.05,
}
DRAFT_NEUTRAL_SCORE = 50.0

# 英雄梯隊分位門檻（由高到低：S/A/B/C，其餘 D）
TIER_QUANTILES = {"S": 0.90, "A": 0.75, "B": 0.45, "C": 0.20}

# ---------------------------------------------------------------------------
# 服務設定
# ---------------------------------------------------------------------------
DASH_HOST = os.environ.get("DASH_HOST", "0.0.0.0")
DASH_PORT = int(os.environ.get("DASH_PORT", "8050"))
