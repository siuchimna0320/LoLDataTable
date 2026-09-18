"""積分頁前端共用輔助：圖示 URL、階級／路線中文化、戰隊賽區映射。

所有圖片均走本地／後端代理路由，瀏覽器不需直連外網。
"""
from __future__ import annotations

import json
from functools import lru_cache

from backend import config, data_access, ddragon_data

# DPM 路線代碼 → 本地 position icon 代碼
LANE_TO_POS = {"TOP": "top", "JUNGLE": "jng", "MIDDLE": "mid",
               "BOTTOM": "bot", "UTILITY": "sup"}
# 自己所在路線 → 右下角重疊的配對路線（圖3：上中下野輔連動）
DUO_OVERLAY_POS = {"BOTTOM": "sup", "UTILITY": "bot",
                   "MIDDLE": "jng", "JUNGLE": "mid", "TOP": None}

# 階級 → 中文名稱／主色（對照圖1 排名欄色字）
TIER_META = {
    "CHALLENGER": ("菁英", "#f2c14e"),
    "GRANDMASTER": ("宗師", "#f0767f"),
    "MASTER": ("大師", "#b18cff"),
    "DIAMOND": ("鑽石", "#5ec8f2"),
    "EMERALD": ("翡翠", "#5fd49a"),
    "PLATINUM": ("白金", "#57d6c8"),
    "GOLD": ("金牌", "#e0b357"),
    "SILVER": ("銀牌", "#b9c2cf"),
    "BRONZE": ("銅牌", "#c98b5e"),
    "IRON": ("鐵牌", "#8a8f99"),
}
# 可超過 100 LP 的階級（其餘顯示封頂 100）
_HIGH_TIERS = {"MASTER", "GRANDMASTER", "CHALLENGER"}


def tier_label(tier: str | None) -> str:
    return TIER_META.get(tier or "", (tier or "—", ""))[0]


def tier_color(tier: str | None) -> str:
    return TIER_META.get(tier or "", ("", "#8a97ab"))[1]


def display_lp(tier: str | None, lp: int | None) -> int:
    """大師以下 LP 上限顯示 100。"""
    if lp is None:
        return 0
    return lp if (tier in _HIGH_TIERS or lp <= 100) else 100


def score_color(score: float | None) -> str:
    """DPM 評分色帶：高分紅橙、中游金、低分綠（對照圖3）。"""
    if score is None:
        return "#8a97ab"
    if score >= 80:
        return "#ff6b57"
    if score >= 65:
        return "#f0a93c"
    if score >= 50:
        return "#cfc38a"
    return "#4caf7d"


# ---------------------------------------------------------------------------
# 圖示 URL
# ---------------------------------------------------------------------------
def champ_icon_url(name: str | None) -> str | None:
    """dpm 英雄名（Riot id 風格，如 DrMundo）→ 本地頭像路由。"""
    if not name:
        return None
    icon_file = config.CHAMP_ICON_DIR / f"{name}.png"
    if icon_file.exists():
        return f"/champ-img/{name}.png"
    # 少數為顯示名（Dr. Mundo）時走名稱→id 映射
    cid = _champ_name_to_id().get(name)
    if cid and (config.CHAMP_ICON_DIR / f"{cid}.png").exists():
        return f"/champ-img/{cid}.png"
    return None


@lru_cache(maxsize=1)
def _champ_name_to_id() -> dict:
    try:
        meta = json.loads(config.DD_META_PATH.read_text(encoding="utf-8"))
        return meta.get("name_to_id", {})
    except (OSError, json.JSONDecodeError):
        return {}


def position_img(pos: str | None) -> str:
    """五路 position icon 路由（top/jng/mid/bot/sup）。"""
    return f"/pos-img/{pos}.svg" if pos else ""


def rank_badge_url(tier: str | None) -> str:
    """dpm 階級小徽章（後端代理快取）。"""
    return f"/dpm-rank/{tier}.webp" if tier else ""


def team_logo_url(team_code: str | None) -> str:
    """戰隊隊徽：優先用 Leaguepedia 本地 logo，缺者退回 dpm 代理。"""
    if not team_code:
        return ""
    local = _local_team_logo().get(team_code)
    if local:
        return f"/assets/team_logos/{local}"
    from urllib.parse import quote
    return f"/dpm-team/{quote(team_code)}.webp"


@lru_cache(maxsize=1)
def _local_team_logo() -> dict:
    """隊伍全名／簡名 → 本地 logo 檔名（team_meta.json）。"""
    try:
        meta = data_access.load_team_meta()
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for full, info in meta.items():
        logo = info.get("logo_file")
        if not logo:
            continue
        out[full] = logo
        if info.get("abbr"):
            out[info["abbr"]] = logo
    return out


def item_icon_url(item_id: int | None) -> str:
    return f"/dd-img/item/{item_id}.png" if item_id else ""


def summoner_icon_url(spell_id: int | None) -> str:
    """召喚師技能數值 id（4＝閃現）→ 本地圖示。"""
    icon = _spell_key_icon().get(str(spell_id))
    return f"/dd-img/spell/{icon}" if icon else ""


def rune_icon_url(rune_id: int | None) -> str:
    """符文（主符文 keystone 為主）→ 本地圖示。"""
    icon = _rune_icon_map().get(rune_id)
    if not icon:
        return ""
    return f"/dd-img/perk/{icon}"


@lru_cache(maxsize=1)
def _spell_key_icon() -> dict:
    bundle = ddragon_data.load_bundle() or {}
    return {s.get("key"): s.get("icon") for s in bundle.get("summoners", [])
            if s.get("key")}


@lru_cache(maxsize=1)
def _rune_icon_map() -> dict:
    """扁平化 runesReforged 樹，建立符文 id→icon 路徑。"""
    bundle = ddragon_data.load_bundle() or {}
    out: dict[int, str] = {}
    for tree in bundle.get("runes", []):
        if tree.get("icon"):
            out[tree["id"]] = tree["icon"]
        for slot in tree.get("slots", []):
            for rune in slot:
                if rune.get("icon"):
                    out[rune["id"]] = rune["icon"]
    return out


# ---------------------------------------------------------------------------
# 戰隊 → 賽區
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def team_region_map() -> dict[str, str]:
    """戰隊全／簡名 → 六大賽區（LCK／LPL／LCP／LEC／LCS／CBLOL）。"""
    try:
        meta = data_access.load_team_meta()
        con = data_access.connect(read_only=True)
        try:
            # 只取六大賽區、各隊最新賽季所屬聯賽（arg_max 隨年份取最新）
            rows = con.execute(
                "SELECT teamname, arg_max(league, year) FROM team_games "
                "WHERE league IN ('LCK','LPL','LCP','LEC','LCS','CBLOL') "
                "GROUP BY teamname").fetchall()
        finally:
            con.close()
        full_to_league = {t: lg for t, lg in rows}
        result = {}
        for full, info in meta.items():
            league = full_to_league.get(full)
            if league:
                result[full] = league
                if info.get("abbr"):
                    result[info["abbr"]] = league
        result.update(_TEAM_REGION_OVERRIDE)
        return result
    except Exception:  # noqa: BLE001
        return dict(_TEAM_REGION_OVERRIDE)


# 自動映射失敗時的手工補充（dpm 代碼與 OE 簡名不一致者）
_TEAM_REGION_OVERRIDE = {
    "LOUD": "CBLOL",   # OE 全名可對，惟保留保險
    "GENG": "LCK",     # OE 簡名為 GEN
    "IG": "LPL",       # Invictus Gaming，近期不在頂級聯賽名單
    "BFX": "LCK",      # BNK FEARX 改名後的 dpm 代碼（OE 簡名 FOX）
    "T1A": "LCK",      # T1 青訓（無分隔字元版）
}
# 學院／青訓隊尾綴（剝除後回推母隊賽區）
_ACADEMY_TAILS = (".EA", ".SC", ".C", ".A", ".Y", ".J", ".R",
                  " C", " EA", " A", " Youth Team", " Academy",
                  " Challengers")


def resolve_region(team: str | None) -> str | None:
    """戰隊名（全或簡）解析賽區；學院隊試著回推母隊。"""
    if not team:
        return None
    mapping = team_region_map()
    if team in mapping:
        return mapping[team]
    for tail in _ACADEMY_TAILS:
        if team.endswith(tail):
            parent = team[: -len(tail)].strip()
            if parent in mapping:
                return mapping[parent]
    return None


def duo_overlay_pos(lane: str | None) -> str | None:
    """配對路線重疊 icon 代碼（回傳 None 表示上路不重疊）。"""
    return DUO_OVERLAY_POS.get(lane or "")
