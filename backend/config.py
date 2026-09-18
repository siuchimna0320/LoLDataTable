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
DD_STATIC_DIR = ASSETS_DIR / "ddragon"      # 圖鑑 JSON 與圖示快取
POSITION_ICON_DIR = DD_STATIC_DIR / "positions"
COMPENDIUM_JSON = DD_STATIC_DIR / "compendium.json"
JUNGLE_DIR = ASSETS_DIR / "jungle"          # 刷野編纂表快取
JUNGLE_JSON = JUNGLE_DIR / "jungle_clear.json"
# 官方版本更新公告解析快取（圖鑑－版本頁）
PATCH_NOTES_JSON = DD_STATIC_DIR / "patch_notes.json"
# 英雄發布日期快取（lol.fandom 列表頁，英雄詳情頁「加入日」使用）
CHAMP_RELEASE_JSON = DD_STATIC_DIR / "champ_releases.json"
CHAMP_ERA_DIR = DD_STATIC_DIR / "champ_eras"  # 歷代英雄頭像快取
# CommunityDragon 英雄 bin.json 快取（技能具名佔位的數值與系數來源）
CHAMP_BIN_DIR = DD_STATIC_DIR / "champ_bins"

# 數據片段（供儀表板分頁載入，避免每頁重掃全量）
SLICE_DIR = PROCESSED_DIR / "slices"

for _dir in (RAW_DIR, WAREHOUSE_DIR, PROCESSED_DIR, ASSETS_DIR,
             CHAMP_ICON_DIR, DD_STATIC_DIR, POSITION_ICON_DIR,
             CHAMP_ERA_DIR, CHAMP_BIN_DIR, JUNGLE_DIR, LOGS_DIR,
             SLICE_DIR):
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
# 繁中英雄全集：中文名、定位標籤與技能名稱（圖鑑英雄頁／版本頁使用）
DDRAGON_ZH_CHAMPION_URL = (
    "https://ddragon.leagueoflegends.com/cdn/{version}/data/zh_TW/championFull.json"
)
# 官方版本更新公告（zh-tw）；公告採年份制編號（2026 年＝26.xx），
# 與 DDragon 內部版本（16.xx）不同；{minor} 不補零（26-4、26-10）
PATCH_SEASON = os.environ.get("PATCH_SEASON", str(CURRENT_YEAR - 2000))
PATCH_INDEX_URL = (
    "https://www.leagueoflegends.com/zh-tw/news/game-updates/"
)
PATCH_NOTE_URL_TEMPLATE = (
    "https://www.leagueoflegends.com/zh-tw/news/game-updates/"
    "league-of-legends-patch-{season}-{minor}-notes/"
)
# 26 季以前的舊 slug 規則（無 league-of-legends- 前綴，如 patch-25-17）
PATCH_NOTE_LEGACY_URL_TEMPLATE = (
    "https://www.leagueoflegends.com/zh-tw/news/game-updates/"
    "patch-{season}-{minor}-notes/"
)
# 逐版本抓取禮貌間隔（秒，實際加 ±0.4 隨機抖動）
PATCH_REQUEST_INTERVAL = float(os.environ.get("PATCH_REQUEST_INTERVAL", "1.8"))
# 英雄發布日期列表頁（lol.fandom 官方 wiki，含發布日期與最後改動版本）
CHAMP_RELEASE_URL = (
    "https://wiki.leagueoflegends.com/en-us/List_of_champions"
)
# 歷代頭像代表版本：2013／2014／2015（現行頭像取本地 CHAMP_ICON_DIR）
CHAMP_ERA_VERSIONS = (("2013", "3.14.13"), ("2014", "4.13.1"),
                      ("2015", "5.24.2"))
DDRAGON_SQUARE_URL = (
    "https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{champ_id}.png"
)
# CommunityDragon 英雄技能 bin.json（DataValues 與 mSpellCalculations）
CDRAGON_CHAMP_BIN_URL = (
    "https://raw.communitydragon.org/latest/game/data/characters/"
    "{champ_lower}/{champ_lower}.bin.json"
)
# 圖鑑靜態資料（道具／召喚師技能／符文，皆無須 API Key）
DDRAGON_ITEM_URL = (
    "https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/item.json"
)
DDRAGON_SUMMONER_URL = (
    "https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/summoner.json"
)
DDRAGON_RUNES_URL = (
    "https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/runesReforged.json"
)
DDRAGON_ITEM_ICON_URL = (
    "https://ddragon.leagueoflegends.com/cdn/{version}/img/item/{icon}"
)
DDRAGON_SPELL_ICON_URL = (
    "https://ddragon.leagueoflegends.com/cdn/{version}/img/spell/{icon}"
)
# 符文圖示使用無版本的 /cdn/img/（版本化路徑在新版會 404）
DDRAGON_PERK_ICON_URL = (
    "https://ddragon.leagueoflegends.com/cdn/img/{icon}"
)
# 單一英雄技能資料（Q/W/E 圖示檔名由此取得）
DDRAGON_CHAMP_PAGE_URL = (
    "https://ddragon.leagueoflegends.com/cdn/{version}"
    "/data/en_US/champion/{champ_id}.json"
)
# 五路位置圖示（CommunityDragon champ-select SVG，DDragon 未提供）
COMMUNITY_POSITION_ICON_URL = (
    "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-champ-select"
    "/global/default/svg/position-{code}.svg"
)

# ---------------------------------------------------------------------------
# Jungle Clear Compilation 社群刷野編纂表（本機維護的 xlsx 副本）
# 原始試算表：gid=1938238048 為 S16 分頁
# ---------------------------------------------------------------------------
JUNGLE_SHEET_ID = "1jE8bnlnIJnmWv9pnVW9veMKRXJNaaJf5tneQB3xUkbI"
JUNGLE_SHEET_GID = "1938238048"
JUNGLE_XLSX_PATH = (
    DATA_ROOT / "jungle_clear_time"
    / "Jungle Clear Compilation S16_2026.xlsx"
)
JUNGLE_SHEET_PAGE_URL = (
    "https://docs.google.com/spreadsheets/d/"
    f"{JUNGLE_SHEET_ID}/edit?gid={JUNGLE_SHEET_GID}#gid={JUNGLE_SHEET_GID}"
)
JUNGLE_DISCORD_URL = "https://discord.com/invite/c9yzQWtYy2"

# 歷史遺留名稱 → DDragon 顯示名（自動映射失敗時的人工補充表）
CHAMPION_NAME_OVERRIDES: dict[str, str] = {}

# ---------------------------------------------------------------------------
# DPM.LOL 職業 SoloQ 積分資料來源（公開 JSON API，無需 API Key）
# ---------------------------------------------------------------------------
DPM_SITE_URL = os.environ.get("DPM_SITE_URL", "https://dpm.lol")
DPM_BASE_URL = os.environ.get("DPM_BASE_URL", "https://dpm.lol/v1")
DPM_CDN_URL = os.environ.get("DPM_CDN_URL", "https://cdn.dpm.lol")
# 韓服平台代碼為 kr，其餘伺服器為 euw1／br1 這類 Riot 平台碼；
# th2／ph2／sg2 目前 dpm.lol 回 422 Invalid platform，暫不列入
DPM_PLATFORMS = tuple(filter(None, os.environ.get(
    "DPM_PLATFORMS",
    "kr,euw1,na1,br1,eun1,jp1,la1,la2,oc1,tr1,ru,tw2,vn2",
).split(",")))
# 官方聯賽代碼：聯賽 leaderboard 含「完整登錄名單」（不受職業排行榜
# 僅前 277 名的門檻限制），是低牌階選手（如先發板凳）的權威名單來源
DPM_ESPORT_LEAGUES = tuple(filter(None, os.environ.get(
    "DPM_ESPORT_LEAGUES",
    "lck,lpl,lcp,lec,lcs,cblol",
).split(",")))
DPM_WAREHOUSE_PATH = WAREHOUSE_DIR / "dpm.duckdb"
DPM_ASSET_DIR = ASSETS_DIR / "dpm"          # 階級徽章／隊徽等 dpm 圖示
# 爬蟲禮儀與回填口徑（皆可由環境變數覆寫，供 cron／容器調整）
DPM_REQUEST_INTERVAL = float(os.environ.get("DPM_REQUEST_INTERVAL", "1.5"))
# 多線程時各線獨立節流，請求間隔於此區間隨機抖動（錯峰、避免脈衝式請求）
DPM_JITTER_RANGE = tuple(float(x) for x in os.environ.get(
    "DPM_JITTER_RANGE", "1.2,2.0").split(","))[:2]
# 爬取線程數（保守預設 3；遇不穩可用 --workers 1 退回單線）
DPM_WORKERS = int(os.environ.get("DPM_WORKERS", "3"))
# 近 14 天無場次的沉寂帳號，每 7 天才主動探測一次對戰清單
DPM_DORMANT_DAYS = int(os.environ.get("DPM_DORMANT_DAYS", "14"))
DPM_PROBE_INTERVAL_DAYS = int(os.environ.get("DPM_PROBE_INTERVAL_DAYS", "7"))
DPM_BACKFILL_GAMES = int(os.environ.get("DPM_BACKFILL_GAMES", "100"))
# 只對最近 N 天的新場景補抓 10 人完整明細（對位人員身份）
DPM_DETAIL_DAYS = int(os.environ.get("DPM_DETAIL_DAYS", "14"))

for _dpm_dir in (DPM_ASSET_DIR,):
    _dpm_dir.mkdir(parents=True, exist_ok=True)

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
