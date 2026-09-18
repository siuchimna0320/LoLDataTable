"""英雄詳情頁資料層：積分出裝、技能文本、歷年改動、發布日期。

單一事實源分工：
- 積分場出裝：dpm.duckdb 的 queue 420（單／雙排）對戰明細聚合，
  符文／道具／召喚師技能圖示中文化靠 ddragon_data bundle。
- 技能說明：patch_data 快取的 zh_TW championFull（目前版本數值）。
- 歷年改動：patch_data 跨季官方公告快取（最新版本在前）。
- 加入日：lol.fandom 英雄列表頁一次性快取。
所有網路請求皆走 http_get（重試＋禮貌間隔），讀檔先檢查存在。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import unquote

from bs4 import BeautifulSoup

from backend import config, ddragon_data, dpm_store, patch_data
from backend.pipeline.common import (HttpRequestError, atomic_write_bytes,
                                     atomic_write_text, get_logger, http_get)

logger = get_logger("champ_detail")

# DPM 對戰 lane 字串 → 本平台位置代碼
_LANE_MAP = {"TOP": "top", "JUNGLE": "jng", "MIDDLE": "mid",
             "BOTTOM": "bot", "UTILITY": "sup"}
# 視為「成型裝」的最小總金額（低於此值視為元件／起手裝）
_COMPLETED_GOLD = 1800
_BOOTS_TAG = "Boots"
_EXCLUDE_TAGS = {"Boots", "Trinket", "Consumable", "Jungle"}
# 各區塊取前 N 名
_RUNE_TOP_N = 3
_CORE_TOP_N = 6
_OTHER_TOP_N = 4
_BOOTS_TOP_N = 4
_SUMM_TOP_N = 2
_STARTER_TOP_N = 1
_PATH_TOP_N = 5
_PATH_MINIMUM = 5
_RECENT_CORE_TOP_N = 7
_RECENT_PATH_TOP_N = 7
_RECENT_PATH_MINIMUM = 4
_SYNERGY_TOP_N = 5
_MATCHUP_TOP_N = 9

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z])(\d+)\s*\}\}")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t　]+")
# 無法解析的殘留佔位（如本地化字串鍵 Spell_X_Tooltip）直接移除
_LEFTOVER_PH_RE = re.compile(r"\{\{[^}]*\}\}\s*")
# 客戶端圖示插入標記（如 %i:OnHit%），純文字模式下移除
_ICON_TOKEN_RE = re.compile(r"%[a-z]*:?[A-Za-z0-9_]+%")
_WIKI_HREF_RE = re.compile(r"^/en-us/([^/\"#?]+)$")


# ---------------------------------------------------------------------------
# Data Dragon bundle 對照表
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _meta_maps() -> dict:
    """由圖鑑 bundle 建立道具／符文／召喚師技能的 id→屬性對照。"""
    bundle = ddragon_data.load_bundle() or {}
    items = {int(it["id"]): {"name": it["name"], "icon": it["icon"],
                             "gold": it["gold"], "tags": set(it["tags"])}
             for it in bundle.get("items", [])}
    runes = {}
    for tree in bundle.get("runes", []):
        runes[int(tree["id"])] = {"name": tree["name"],
                                  "icon": tree["icon"], "keystone": False}
        for slot in tree.get("slots", []):
            for rune in slot:
                runes[int(rune["id"])] = {
                    "name": rune["name"], "icon": rune["icon"],
                    "keystone": False}
    # 第一排即基石符文
    for tree in bundle.get("runes", []):
        slots = tree.get("slots", [])
        if slots:
            for rune in slots[0]:
                runes[int(rune["id"])]["keystone"] = True
    summoners = {}
    for spell in bundle.get("summoners", []):
        try:
            summoners[int(spell["key"])] = {"name": spell["name"],
                                            "icon": spell["icon"]}
        except (TypeError, ValueError):
            continue
    return {"items": items, "runes": runes, "summoners": summoners}


def _is_completed(item_id: int, items: dict) -> bool:
    """元件判定：峽谷成型裝（排除鞋子／飾品／消耗品／打野裝）。"""
    meta = items.get(item_id)
    if not meta or meta["gold"] < _COMPLETED_GOLD:
        return False
    return not (meta["tags"] & _EXCLUDE_TAGS)


def _is_boots(item_id: int, items: dict) -> bool:
    return _BOOTS_TAG in items.get(item_id, {}).get("tags", set())


# ---------------------------------------------------------------------------
# 積分場出裝聚合（dpm.duckdb，queue 420）
# ---------------------------------------------------------------------------
def _bump(counter: dict, key, win: bool) -> None:
    slot = counter.setdefault(key, [0, 0])
    slot[0] += 1
    slot[1] += 1 if win else 0


def _rank(counter: dict, denom: int, top_n: int,
          minimum: int = 1) -> list[dict]:
    """計數表 → 依場次排序的比率明細（勝率四捨五入一位）。"""
    rows = []
    for key, (games, wins) in counter.items():
        if games < minimum:
            continue
        rows.append({"key": key, "games": games, "wins": wins,
                     "rate": round(games * 100.0 / denom, 1)
                     if denom else 0.0,
                     "win_rate": round(wins * 100.0 / games, 1)
                     if games else 0.0})
    rows.sort(key=lambda r: (-r["games"], -r["wins"]))
    return rows[:top_n]


def _aggregate_rows(rows: list[dict], items: dict, runes: dict,
                    summoners: dict, *,
                    core_top_n: int = _CORE_TOP_N,
                    path_top_n: int = _PATH_TOP_N,
                    path_minimum: int = _PATH_MINIMUM) -> dict:
    """把單一英雄的積分場參與列聚合成出裝流派結構。"""
    total = len(rows)
    wins = sum(1 for r in rows if r[1])
    lanes: dict[str, list] = {}

    rune_sigs: dict = {}
    summ_pairs: dict = {}
    starter_sigs: dict = {}
    boots_ct: dict = {}
    item_ct: dict = {}
    path_ct: dict = {}
    synergy_ct: dict = {}
    matchup_ct: dict = {}
    item_denom = 0

    for (_, win, lane, s1, s2, runes_raw, items_raw, start_raw,
         actions_raw, opp, duo) in rows:
        lane_code = _LANE_MAP.get(lane or "")
        if lane_code:
            _bump(lanes, lane_code, win)

        # 符文排列：以完整七符文簽名分組（基石＋三小符＋副系三符）
        try:
            rune_data = json.loads(runes_raw) if runes_raw else {}
        except json.JSONDecodeError:
            rune_data = {}
        if rune_data.get("p") in runes:
            sig = tuple(rune_data.get(k) for k in
                        ("p", "p2", "p3", "p4", "s", "s2", "s3"))
            if all(rid in runes for rid in sig if rid is not None):
                _bump(rune_sigs, sig, win)

        # 召喚師技能（無序 pair）
        if s1 in summoners and s2 in summoners:
            _bump(summ_pairs, tuple(sorted((s1, s2))), win)

        # 起手裝：以購買清單順序的去重 tuple 分組
        try:
            starters = [int(x) for x in json.loads(start_raw)] \
                if start_raw else []
        except (json.JSONDecodeError, TypeError, ValueError):
            starters = []
        if starters:
            _bump(starter_sigs, tuple(dict.fromkeys(starters)), win)

        # 對位／常配英雄
        if opp:
            _bump(matchup_ct, opp, win)
        if duo:
            _bump(synergy_ct, duo, win)

        # 最終道具欄
        try:
            final_ids = [int(x) for x in json.loads(items_raw)] \
                if items_raw else []
        except (json.JSONDecodeError, TypeError, ValueError):
            final_ids = []
        if not final_ids:
            continue
        item_denom += 1
        finalset = {x for x in final_ids if x > 0}
        for item_id in finalset:
            if _is_boots(item_id, items):
                _bump(boots_ct, item_id, win)
            elif _is_completed(item_id, items):
                _bump(item_ct, item_id, win)

        # 出裝流派：依購買時間取前三件成型裝（不含鞋子）
        try:
            actions = json.loads(actions_raw) if actions_raw else []
        except json.JSONDecodeError:
            actions = []
        purchased = [a for a in actions
                     if a.get("action") == "purchase"
                     and _is_completed(int(a.get("id", 0)), items)]
        purchased.sort(key=lambda a: int(a.get("timestamp", 0)))
        path = tuple(dict.fromkeys(int(a["id"]) for a in purchased))[:3]
        if len(path) == 3:
            _bump(path_ct, path, win)

    core = _rank(item_ct, item_denom, core_top_n)
    core_ids = {r["key"] for r in core}
    other_rows = [r for r in _rank(item_ct, item_denom, 200)
                  if r["key"] not in core_ids][:_OTHER_TOP_N]
    return {
        "games": total,
        "wins": wins,
        "win_rate": round(wins * 100.0 / total, 1) if total else 0.0,
        "lanes": _rank(lanes, total, 5),
        "runes": _rank(rune_sigs, total, _RUNE_TOP_N),
        "summoners": _rank(summ_pairs, total, _SUMM_TOP_N),
        "starters": _rank(starter_sigs, total, _STARTER_TOP_N),
        "boots": _rank(boots_ct, item_denom, _BOOTS_TOP_N),
        "core": core,
        "others": other_rows,
        "paths": _rank(path_ct, item_denom, path_top_n,
                       minimum=path_minimum),
        "synergies": _rank(synergy_ct, total, _SYNERGY_TOP_N, minimum=3),
        "matchups": _rank(matchup_ct, total, _MATCHUP_TOP_N, minimum=3),
    }


def _recent_window(notes: dict) -> tuple[str, str, int]:
    """最近兩版的（標籤、起算 ts 毫秒）。

    標籤例 26.17–26.18；起算日取第二新版本公告發布日，
    缺日期時退回 35 天前。
    """
    patches = notes.get("patches", [])
    newest, second = (patches + [None, None])[:2]
    released = notes.get("released", {})
    start = int(datetime.now(tz=timezone.utc).timestamp() * 1000) \
        - 35 * 86_400_000
    if second and released.get(second):
        dt = datetime.fromisoformat(released[second]).replace(
            tzinfo=timezone.utc)
        start = int(dt.timestamp() * 1000)
    label = f"{second}–{newest}" if second and newest else ""
    return label, start, 0


def build_stats(cid: str, year: int | None = None) -> dict | None:
    """聚合單一英雄今年的單／雙排出裝資料；倉儲不存在回 None。"""
    if not config.DPM_WAREHOUSE_PATH.exists():
        return None
    year = year or config.CURRENT_YEAR
    start_ms = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp()
                   * 1000)
    end_ms = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp()
                 * 1000)
    con = dpm_store.connect(read_only=True)
    try:
        rows = con.execute(
            """
            SELECT mp.champion_name, mp.win, mp.lane,
                   mp.summoner1_id, mp.summoner2_id,
                   mp.runes_json, mp.items_json, mp.start_items_json,
                   mp.item_actions_json,
                   mp.opponent_champion, mp.duo_champion
            FROM match_players mp
            JOIN matches m ON mp.match_id = m.match_id
            WHERE mp.champion_name = ? AND m.queue_id = 420
              AND m.start_ts >= ? AND m.start_ts < ?
            """,
            [cid, start_ms, end_ms]).fetchall()
    finally:
        con.close()
    maps = _meta_maps()
    stats = _aggregate_rows(rows, maps["items"], maps["runes"],
                            maps["summoners"])
    stats["year"] = year

    # 近兩版：以第二新公告發布日為起點重算
    notes = patch_data.load_notes()
    recent_label, recent_start, _ = _recent_window(notes or {})
    recent_rows = []
    if recent_label:
        con = dpm_store.connect(read_only=True)
        try:
            recent_rows = con.execute(
                """
                SELECT mp.champion_name, mp.win, mp.lane,
                       mp.summoner1_id, mp.summoner2_id,
                       mp.runes_json, mp.items_json, mp.start_items_json,
                       mp.item_actions_json,
                       mp.opponent_champion, mp.duo_champion
                FROM match_players mp
                JOIN matches m ON mp.match_id = m.match_id
                WHERE mp.champion_name = ? AND m.queue_id = 420
                  AND m.start_ts >= ? AND m.start_ts < ?
                """,
                [cid, recent_start, end_ms]).fetchall()
        finally:
            con.close()
    stats["recent_label"] = recent_label
    stats["recent"] = (_aggregate_rows(
        recent_rows, maps["items"], maps["runes"], maps["summoners"],
        core_top_n=_RECENT_CORE_TOP_N,
        path_top_n=_RECENT_PATH_TOP_N,
        path_minimum=_RECENT_PATH_MINIMUM)
        if recent_rows else None)
    return stats


# ---------------------------------------------------------------------------
# 技能說明（zh_TW championFull 快取＋CommunityDragon bin 數值）
# ---------------------------------------------------------------------------
# 舊式佔位：{{ e1 }}／{{ f1 }}
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z])(\d+)\s*\}\}")
# 新式具名佔位：{{ flatdamage }}、{{ healthdamage*100 }}、{{ shieldduration.1 }}
# 跨技能引用：{{ spell.naafiriq:totaldamagefirstcast }}
_NAMED_PH_RE = re.compile(
    r"\{\{\s*(?:spell\.([a-z0-9_]+):)?"
    r"([a-zA-Z0-9_]+)(\.\d)?(?:\s*\*\s*(-?\d+(?:\.\d+)?))?\s*\}\}")
# mStat 列舉 → 繁中屬性標籤（缺省即魔攻，依多位英雄 bin 實證）
_STAT_LABELS = {None: "魔攻", 0: "魔攻", 1: "護甲", 2: "物攻",
                4: "攻速", 6: "魔防", 7: "跑速", 12: "生命",
                18: "全能吸血"}
# CDragon 角色資料夾與 DDragon ID 不同者（實證：MonkeyKing→monkeyking、
# Renata→renata，皆與小寫 ID 相同，無需對照表；保留常數供日後例外使用）
_BIN_FOLDER_OVERRIDE: dict[str, str] = {}


def _fill_placeholders(text: str, effect_burn: list,
                       variables: list) -> str:
    """無 bin 時的舊式 fallback：取代 {{ e1 }}／{{ f1 }} 佔位。"""
    f_map = {}
    for var in variables or []:
        key = str(var.get("key", "")).strip()
        coeff = var.get("coeff")
        if isinstance(coeff, list):
            f_map[key] = "/".join(str(c) for c in coeff)
        elif coeff is not None:
            f_map[key] = str(coeff)

    def repl(match: re.Match) -> str:
        kind, index = match.group(1), int(match.group(2))
        if kind == "e":
            try:
                return str(effect_burn[index] or "")
            except (IndexError, TypeError):
                return ""
        return f_map.get(match.group(0).replace(" ", ""), "")

    return _PLACEHOLDER_RE.sub(repl, text)


def _rank_series(values: list) -> list[float]:
    """DataValues 數列 → 技能 1～5 級數值；末級不足時依末級差距線性外推。

    bin 數列索引 0 為未點級數值（常為 0 或負值），故取 index 1..5。
    """
    vals = [float(v) for v in (values or []) if v is not None]
    if not vals:
        return []
    out: list[float] = []
    for i in range(1, 6):
        if i < len(vals):
            out.append(vals[i])
        elif len(vals) >= 2:
            gap = vals[-1] - vals[-2]
            out.append(vals[-1] + gap * (i - (len(vals) - 1)))
        else:
            out.append(vals[-1])
    return out


def _fmt_num(value: float, precision: int | None = None) -> str:
    """數值格式化：消除浮點誤差，預設最多兩位小數。"""
    digits = precision if precision is not None else 2
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _series_text(vals: list[float], percent: bool,
                 add_symbol: bool = True) -> str:
    """五級數列文字：全相等只顯示一次，否則以斜線連接。

    percent 決定是否 ×100 換算；add_symbol 決定是否補上「%」。
    """
    if not vals:
        return ""
    rendered = [_fmt_num(v * 100 if percent else v) for v in vals]
    text = rendered[0] if len(set(rendered)) == 1 else "/".join(rendered)
    return text + ("%" if percent and add_symbol else "")


def _stat_label(stat, data_name: str) -> str:
    """mStat＋DataValue 名稱 → 繁中系數屬性（物攻／生命需細分加成基準）。"""
    label = _STAT_LABELS.get(stat, "")
    name = (data_name or "")
    low = name.lower()
    if stat in (None, 0):
        return "魔攻"
    if stat == 2:
        if "total" in low:
            return "總物攻"
        if "bad" in low or "bonus" in low or name.startswith("B"):
            return "額外物攻"
        return "物攻"
    if stat == 12:
        if "bonus" in low or "bhp" in low:
            return "額外生命"
        if "max" in low or "total" in low:
            return "最大生命"
        if "current" in low or "shield" in low:
            return "當前生命"
        return "最大生命"
    return label or "魔攻"


@lru_cache(maxsize=32)
def _spell_index(cid: str) -> dict | None:
    """讀取（必要時下載）英雄 bin.json，建立 spell_id → mSpell 對照。"""
    path = config.CHAMP_BIN_DIR / f"{cid}.json"
    raw = None
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = None
    if raw is None:
        folder = _BIN_FOLDER_OVERRIDE.get(cid, cid.lower())
        url = config.CDRAGON_CHAMP_BIN_URL.format(champ_lower=folder.lower())
        try:
            content = http_get(url).content.decode("utf-8-sig")
            raw = json.loads(content)
        except HttpRequestError as exc:
            logger.warning("bin.json 取得失敗 %s：%s", cid, exc)
            return None
        try:
            atomic_write_bytes(path, content.encode("utf-8"))
        except OSError as exc:
            logger.warning("bin.json 快取寫入失敗 %s：%s", cid, exc)
    # 同一技能可能有多個節點（飛彈／特效／本體），碰撞時保留資料量最多者
    candidates: dict[str, list[dict]] = {}
    for key, node in raw.items():
        if not (isinstance(node, dict)
                and isinstance(node.get("mSpell"), dict)):
            continue
        spell = node["mSpell"]
        basename = str(key).rstrip("/").rsplit("/", 1)[-1].lower()
        tokens = {basename}
        if spell.get("mAlternateName"):
            tokens.add(str(spell["mAlternateName"]).lower())
        for token in tokens:
            candidates.setdefault(token, []).append(spell)

    def _richness(spell: dict) -> int:
        return len(spell.get("DataValues") or []) + \
            len(spell.get("mSpellCalculations") or {})

    return {token: max(spells, key=_richness)
            for token, spells in candidates.items()}


def _eval_calc(calc: dict, values: dict, depth: int = 0) -> dict:
    """求值 GameCalculation，回傳 base 數列／系數清單／等級區間／顯示旗標。"""
    result = {"base": [0.0] * 5, "stats": [], "range": None}
    if depth > 4:
        return result
    for part in calc.get("mFormulaParts") or []:
        ptype = part.get("__type", "")
        if ptype == "NamedDataValueCalculationPart":
            series = _rank_series(values.get(part.get("mDataValue")))
            if series:
                result["base"] = [a + b for a, b in
                                  zip(result["base"], series)]
        elif ptype == "StatByNamedDataValueCalculationPart":
            series = _rank_series(values.get(part.get("mDataValue")))
            if series:
                result["stats"].append(
                    (_stat_label(part.get("mStat"), part.get("mDataValue")),
                     series))
        elif ptype == "StatByCoefficientCalculationPart":
            coeff = float(part.get("mCoefficient", 0) or 0)
            result["stats"].append(
                (_stat_label(part.get("mStat"), ""), [coeff] * 5))
        elif ptype == "NumberCalculationPart":
            number = float(part.get("mNumber", 0) or 0)
            result["base"] = [v + number for v in result["base"]]
        elif ptype == "ByCharLevelInterpolationCalculationPart":
            result["range"] = (float(part.get("mStartValue", 0) or 0),
                               float(part.get("mEndValue", 0) or 0))
    _apply_multiplier(result, calc.get("mMultiplier"), values)
    result["percent"] = bool(calc.get("mDisplayAsPercent"))
    result["precision"] = calc.get("mPrecision")
    return result


def _multiplier_values(multiplier: dict | None, values: dict) -> list[float]:
    """mMultiplier 部件 → 五級乘區數列；無乘區回全 1。"""
    if not isinstance(multiplier, dict):
        return [1.0] * 5
    mtype = multiplier.get("__type", "")
    if mtype == "NamedDataValueCalculationPart":
        series = _rank_series(values.get(multiplier.get("mDataValue")))
        return series or [1.0] * 5
    if mtype == "NumberCalculationPart":
        return [float(multiplier.get("mNumber", 0) or 0)] * 5
    return [1.0] * 5


def _apply_multiplier(result: dict, multiplier: dict | None,
                      values: dict) -> dict:
    """把乘區套到 base／系數／等級區間。"""
    mvals = _multiplier_values(multiplier, values)
    if mvals == [1.0] * 5:
        return result
    result["base"] = [b * m for b, m in zip(result["base"], mvals)]
    result["stats"] = [(label, [v * m for v, m in zip(series, mvals)])
                       for label, series in result["stats"]]
    if result["range"]:
        start, end = result["range"]
        result["range"] = (start * mvals[-1], end * mvals[-1])
    return result


def _resolve_named(name: str, multiplier: float | None,
                   calcs: dict, values: dict) -> str:
    """解析單一具名佔位為五級數值文字；查無對應回空字串。"""
    key = name.lower()
    calc = calcs.get(key)
    if calc is not None:
        # 修改型公式：以 mModifiedGameCalculation 為基底，再套本層乘區
        target = str(calc.get("mModifiedGameCalculation") or "").lower()
        if target and not calc.get("mFormulaParts") and target in calcs:
            evaluated = _eval_calc(calcs[target], values)
            evaluated = _apply_multiplier(evaluated,
                                          calc.get("mMultiplier"), values)
            evaluated["percent"] = bool(calc.get(
                "mDisplayAsPercent", evaluated["percent"]))
        else:
            evaluated = _eval_calc(calc, values)
        # {{ x*100 }}／{{ x*-100 }}：小數×乘區換算為百分數，
        # 「%」符號由模板自帶，不補；mDisplayAsPercent 則由我方補符號
        pct_mult = multiplier is not None and abs(multiplier) == 100
        if evaluated["range"] is not None:
            start, end = evaluated["range"]
            if multiplier and not pct_mult:
                start, end = start * multiplier, end * multiplier
            if pct_mult:
                start, end = start * multiplier, end * multiplier
                text = f"{_fmt_num(start)}–{_fmt_num(end)}"
            else:
                scale = 100 if evaluated["percent"] else 1
                text = f"{_fmt_num(start * scale)}–{_fmt_num(end * scale)}"
                text += "%" if evaluated["percent"] else ""
            return text
        base_vals = evaluated["base"]
        if multiplier:
            base_vals = [v * multiplier for v in base_vals]
        if any(abs(v) > 1e-9 for v in base_vals):
            # pct_mult 已自行換算，視為一般數列；符號同樣不補
            text = _series_text(base_vals, evaluated["percent"],
                                add_symbol=evaluated["percent"])
        else:
            text = ""
        for label, stat_series in evaluated["stats"]:
            text += f"（+{_series_text(stat_series, True)} {label}）"
        return text
    # 直接對 DataValue（如持續秒數、緩速百分比）
    raw_values = values.get(key)
    if raw_values is None:
        raw_values = next((v for n, v in values.items()
                           if n.lower() == key), None)
    series = _rank_series(raw_values)
    if not series:
        return ""
    if multiplier:
        series = [v * multiplier for v in series]
    # {{ x*100 }}／{{ x*-100 }} 已在此換算且不補「%」（模板自帶）
    return _series_text(series, False)


def _fill_named_placeholders(text: str, spell_bin: dict | None,
                             all_index: dict | None = None) -> str:
    """以 bin.json 的 DataValues／mSpellCalculations 填滿具名佔位。

    all_index 為同英雄全部技能節點，供 spell.xxx:yyy 跨技能引用使用。
    """
    if not spell_bin:
        return text

    def _maps(node: dict | None) -> tuple[dict, dict]:
        if not node:
            return {}, {}
        vals = {dv["name"]: dv.get("values", [])
                for dv in node.get("DataValues", []) if dv.get("name")}
        cs = {k.lower(): v
              for k, v in node.get("mSpellCalculations", {}).items()}
        return vals, cs

    values, calcs = _maps(spell_bin)

    def repl(match: re.Match) -> str:
        ref_spell, name = match.group(1), match.group(2)
        mult = float(match.group(4)) if match.group(4) else None
        ref_vals, ref_calcs = values, calcs
        if ref_spell and all_index is not None:
            target_node = all_index.get(ref_spell.lower())
            ref_vals, ref_calcs = _maps(target_node)
        try:
            return _resolve_named(name, mult, ref_calcs, ref_vals)
        except Exception as exc:  # noqa: BLE001
            logger.warning("技能佔位解析失敗 %s：%s", name, exc)
            return ""

    return _NAMED_PH_RE.sub(repl, text)


def _html_to_lines(text: str) -> list[str]:
    """Riot 技能 HTML → 純文字行（<br> 斷行，其餘標籤移除）。"""
    text = _BR_RE.sub("\n", text or "")
    text = _TAG_RE.sub("", text)
    text = _LEFTOVER_PH_RE.sub("", text)
    text = _ICON_TOKEN_RE.sub("", text)
    lines = []
    for line in text.split("\n"):
        line = _WS_RE.sub(" ", line.replace(" ", " ")).strip()
        if line:
            lines.append(line)
    return lines


def load_skills(cid: str) -> dict | None:
    """單一英雄的被動＋QWER 結構化文本；缺快取回 None。"""
    info = patch_data.load_champ_zh().get(cid)
    if not info or not info.get("spell_details"):
        return None
    index = _spell_index(cid)
    passive_lines = _html_to_lines(info.get("passive_desc", ""))
    spells = []
    for spell in info["spell_details"]:
        spell_bin = None
        if index is not None:
            spell_bin = index.get(str(spell.get("spell_id", "")).lower())
        tooltip = spell.get("tooltip", "")
        if spell_bin is not None:
            tooltip = _fill_named_placeholders(tooltip, spell_bin, index)
        else:
            tooltip = _fill_placeholders(tooltip,
                                         spell.get("effect_burn", []),
                                         spell.get("vars", []))
        spells.append({
            "slot": spell["slot"],
            "name": spell["name"],
            "icon": spell.get("icon", ""),
            "cooldown": spell.get("cooldown", ""),
            "cost": spell.get("cost", ""),
            "lines": _html_to_lines(tooltip),
        })
    return {
        "passive": {"name": info.get("passive", ""),
                    "icon": info.get("passive_icon", ""),
                    "lines": passive_lines},
        "spells": spells,
    }


# ---------------------------------------------------------------------------
# 歷年改動（官方公告快取，最新版本在前）
# ---------------------------------------------------------------------------
def load_history(cid: str) -> dict | None:
    """單一英雄跨季改動明細；無任何改動紀錄回 None。"""
    notes = patch_data.load_notes()
    if not notes:
        return None
    for entry in notes["entries"]:
        if entry.get("kind") == "champion" and entry.get("id") == cid:
            return {"name": entry["name"], "patches": entry["patches"]}
    return None


# ---------------------------------------------------------------------------
# 發布日期（lol.fandom 列表頁，一次性快取）
# ---------------------------------------------------------------------------
def _parse_release_table(html: str) -> dict:
    """解析 wiki 英雄列表：英文名→{發布日, 最後改動版本}。"""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        link = tds[0].find("a", href=_WIKI_HREF_RE)
        name = None
        if link is not None:
            href = link.get("href", "")
            m = _WIKI_HREF_RE.search(href)
            if m:
                name = m.group(1)
        date_text = tds[2].get_text(strip=True)
        if not name or not re.match(r"20\d\d-\d\d-\d\d$", date_text):
            continue
        last_patch = re.sub(r"[^V0-9.]", "", tds[3].get_text(strip=True))
        out[name] = {"release_date": date_text,
                     "last_patch": last_patch.lstrip("V") or None}
    return out


def fetch_releases() -> dict:
    """抓取並快取發布日期表，回傳 DDragon ID 鍵的字典。"""
    html = http_get(config.CHAMP_RELEASE_URL).text
    parsed = _parse_release_table(html)
    # wiki 用英文顯示名，藉 ddragon_meta 的 name_to_id 對回正式 ID
    name_to_id = {}
    if config.DD_META_PATH.exists():
        meta = json.loads(config.DD_META_PATH.read_text(encoding="utf-8"))
        name_to_id = meta.get("name_to_id", {})
    mapped = {}
    unmatched = []
    for raw_name, info in parsed.items():
        # wiki slug 會 URL 編碼並以底線取代空格（如 Dr._Mundo、Cho%27Gath）
        name = unquote(raw_name).replace("_", " ")
        cid = name_to_id.get(name)
        if cid:
            mapped[cid] = info
        else:
            unmatched.append(name)
    if unmatched:
        logger.warning("發布日期表 %d 隻英雄對不到 ID：%s",
                       len(unmatched), "、".join(unmatched[:12]))
    atomic_write_text(config.CHAMP_RELEASE_JSON,
                      json.dumps(mapped, ensure_ascii=False))
    return mapped


@lru_cache(maxsize=1)
def load_releases() -> dict:
    """讀取發布日期快取；不存在時嘗試連網補一次，再失敗回空 dict。"""
    if config.CHAMP_RELEASE_JSON.exists():
        try:
            return json.loads(
                config.CHAMP_RELEASE_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("發布日期快取毀損")
    try:
        return fetch_releases()
    except HttpRequestError as exc:
        logger.warning("發布日期取得失敗：%s", exc)
        return {}


# ---------------------------------------------------------------------------
# 歷代頭像（DDragon 舊版方形圖，按需下載去重）
# ---------------------------------------------------------------------------
def _file_sha(path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def era_icons(cid: str, release_date: str | None) -> list[dict]:
    """回傳與現行不同的歷代頭像（依發布年過濾、位元組去重）。

    最末追加現行頭像，標註其沿用起始年（例 2015–2026）。
    """
    current = config.CHAMP_ICON_DIR / f"{cid}.png"
    current_hash = _file_sha(current) if current.exists() else None
    release_year = None
    if release_date:
        release_year = int(release_date[:4])
    seen = {current_hash} if current_hash else set()
    eras = []
    for label, version in config.CHAMP_ERA_VERSIONS:
        if release_year and release_year > int(label):
            continue
        target = config.CHAMP_ERA_DIR / f"{version}_{cid}.png"
        if not target.exists():
            url = config.DDRAGON_SQUARE_URL.format(version=version,
                                                   champ_id=cid)
            try:
                resp = http_get(url)
            except HttpRequestError:
                continue  # 舊版缺圖（403/404）直接略過
            atomic_write_bytes(target, resp.content)
        digest = _file_sha(target)
        if digest in seen:
            continue
        seen.add(digest)
        eras.append({"label": label,
                     "src": f"/champ-era/{version}_{cid}.png"})
    # 現行圖的標籤：最後一個相異歷代版的次年起算
    if eras:
        start = int(eras[-1]["label"]) + 1
        current_label = f"{start}–{config.CURRENT_YEAR}"
    elif release_year:
        current_label = f"{release_year}–{config.CURRENT_YEAR}"
    else:
        current_label = "現行"
    eras.append({"label": current_label,
                 "src": f"/champ-img/{cid}.png"})
    return eras


# ---------------------------------------------------------------------------
# 彙整入口
# ---------------------------------------------------------------------------
@lru_cache(maxsize=64)
def detail(cid: str, year: int | None = None) -> dict | None:
    """英雄詳情頁完整資料：基本檔＋積分出裝＋技能＋歷年改動。"""
    champ_zh = patch_data.load_champ_zh()
    info = champ_zh.get(cid)
    if not info:
        return None
    releases = load_releases()
    release = releases.get(cid, {})
    stats = build_stats(cid, year)
    return {
        "cid": cid,
        "name": info.get("name") or cid,
        "title": info.get("title", ""),
        "tags": info.get("tags", []),
        "release_date": release.get("release_date"),
        "last_patch": release.get("last_patch"),
        "builds": stats,
        "skills": load_skills(cid),
        "history": load_history(cid),
    }
