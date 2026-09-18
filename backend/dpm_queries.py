"""DPM 積分頁的唯讀查詢模型：積分榜列、選手詳情、逐場明細。

回傳皆為 list[dict]（JSON 友好），供 Dash server callback 送進 dcc.Store，
再由 clientside callback 以 innerHTML 高效渲染（避開大型元件樹）。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

from backend import config, data_access, dpm_store, dpm_ui

WEEK_MS = 7 * 86400 * 1000
# 官方路線（dpm pro 檔的 role 字串）→ 本地 position code
OFFICIAL_ROLE = {"top": "top", "jungle": "jng", "mid": "mid",
                 "bot": "bot", "adc": "bot", "support": "sup",
                 "utility": "sup"}
_SOLOQ_QUEUE = 420


def _server_label(platform: str | None) -> str:
    """KR／EUW1→EUW／BR1→BR 這類顯示名。"""
    if not platform:
        return ""
    return platform.rstrip("0123456789").upper()


def _mmdd(ts_ms: int | None) -> str:
    if not ts_ms:
        return ""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%m/%d")


def _official_pos(pro_role: str | None, lane: str | None) -> str:
    """優先用職業比賽路線，沒有才退回積分主修路線。"""
    if pro_role:
        pos = OFFICIAL_ROLE.get(pro_role.lower())
        if pos:
            return pos
    return dpm_ui.LANE_TO_POS.get(lane or "", "")


# ---------------------------------------------------------------------------
# 積分榜
# ---------------------------------------------------------------------------
_WEEK_SQL = """
SELECT mp.puuid,
       ROUND(AVG(mp.dpm_score), 1) AS wk_score,
       SUM(CASE WHEN mp.win THEN 1 ELSE 0 END) AS wk_wins,
       COUNT(*) AS wk_games,
       ROUND(SUM(mp.kills + mp.assists) * 1.0 / NULLIF(SUM(mp.deaths), 0), 1)
         AS wk_kda
FROM match_players mp JOIN matches m ON m.match_id = mp.match_id
WHERE m.queue_id = {queue} AND m.start_ts >= ?
GROUP BY mp.puuid
"""

_RECENT_SQL = """
SELECT puuid, STRING_AGG(token, '|' ORDER BY rn) FROM (
    SELECT mp.puuid,
           mp.champion_name || ':' || CASE WHEN mp.win THEN '1' ELSE '0' END
             AS token,
           ROW_NUMBER() OVER (PARTITION BY mp.puuid
                              ORDER BY m.start_ts DESC) AS rn
    FROM match_players mp JOIN matches m ON m.match_id = mp.match_id
    WHERE m.queue_id = {queue}
) WHERE rn <= 10 GROUP BY puuid
"""

_LADDER_SQL = """
SELECT a.puuid, a.display_name, a.game_name, a.tag_line,
       a.platform, COALESCE(a.team, p.team) AS team,
       p.role AS official_role, a.lane AS solo_lane, a.lane_pct,
       a.tier, a.league_points, a.wins, a.losses, a.kda,
       a.last_match_ts
FROM accounts a LEFT JOIN pros p ON p.display_name = a.display_name
WHERE a.display_name IS NOT NULL
ORDER BY a.league_points DESC NULLS LAST, a.last_match_ts DESC
"""


def ladder_rows(region: str | None = None,
                position: str | None = None) -> list[dict]:
    """組出圖1 積分榜全部列（不含 #，前端依排序即時編號）。"""
    if not _db_exists():
        return []
    con = dpm_store.connect(read_only=True)
    try:
        week_cut = int(time.time() * 1000) - WEEK_MS
        week = {r[0]: r for r in con.execute(
            _WEEK_SQL.format(queue=_SOLOQ_QUEUE), [week_cut]).fetchall()}
        recent = {r[0]: _parse_recent(r[1]) for r in con.execute(
            _RECENT_SQL.format(queue=_SOLOQ_QUEUE)).fetchall()}
        rows = con.execute(_LADDER_SQL).fetchall()
    finally:
        con.close()

    result = []
    for r in rows:
        row = _row_from_tuple(r)
        row["pos"] = _official_pos(row.pop("official_role", None),
                                   row.pop("solo_lane", None))
        row.pop("lane_pct", None)
        row["region"] = dpm_ui.resolve_region(row["team"])
        if region and row["region"] != region:
            continue
        if position and row["pos"] != position:
            continue
        _enrich_ladder_row(row, week.get(r[0]), recent.get(r[0], []))
        result.append(row)
    return result


def _parse_recent(blob: str | None) -> list[dict]:
    """『英雄:1/0』聚合字串 → [{champ, win}]（最近在前）。"""
    out = []
    for token in (blob or "").split("|"):
        if ":" not in token:
            continue
        champ, win_flag = token.rsplit(":", 1)
        out.append({"champ": champ, "win": win_flag == "1"})
    return out


def _row_from_tuple(r) -> dict:
    keys = ("puuid", "display_name", "game_name", "tag_line", "platform",
            "team", "official_role", "solo_lane", "lane_pct", "tier",
            "league_points", "wins", "losses", "kda", "last_match_ts")
    return dict(zip(keys, r))


def _enrich_ladder_row(row: dict, week: tuple | None,
                       recent_champs: list[str]) -> None:
    """補齊顯示欄位：分數封頂、勝率、一週統計、英雄圖與超連結。"""
    wins, losses = row.get("wins") or 0, row.get("losses") or 0
    games = wins + losses
    row["lp"] = dpm_ui.display_lp(row.get("tier"), row.get("league_points"))
    row["winrate"] = round(wins * 100.0 / games, 1) if games else None
    row["server"] = _server_label(row.get("platform"))
    row["tier_zh"] = dpm_ui.tier_label(row.get("tier"))
    row["tier_color"] = dpm_ui.tier_color(row.get("tier"))
    row["rank_icon"] = dpm_ui.rank_badge_url(row.get("tier"))
    row["team_logo"] = dpm_ui.team_logo_url(row.get("team"))
    row["profile_url"] = _dpm_profile(row)
    row["recent_champs"] = [c for c in recent_champs
                            if dpm_ui.champ_icon_url(c["champ"])][:10]
    row["last_date"] = _mmdd(row.get("last_match_ts"))
    if week:
        _, score, w_wins, w_games, w_kda = week
        row["wk_score"] = float(score) if score is not None else None
        row["wk_wins"] = w_wins
        row["wk_losses"] = (w_games or 0) - (w_wins or 0)
        row["wk_kda"] = float(w_kda) if w_kda is not None else None
    else:
        row.update(wk_score=None, wk_wins=0, wk_losses=0, wk_kda=None)


def _dpm_profile(row: dict) -> str | None:
    from backend.dpm_client import profile_url
    return profile_url(row.get("game_name"), row.get("tag_line"))


def _db_exists() -> bool:
    from backend import config
    return config.DPM_WAREHOUSE_PATH.exists()


# ---------------------------------------------------------------------------
# 每日戰況（各隊選手單雙排逐日戰況）
# ---------------------------------------------------------------------------
# 賽區優先序：LCK→LPL→LCP（含 PCS）→LEC→LCS→CBLOL
_REGION_ORDER = {"LCK": 0, "LPL": 1, "LCP": 2, "LEC": 3, "LCS": 4,
                 "CBLOL": 5}
_POS_ORDER = {"top": 0, "jng": 1, "mid": 2, "bot": 3, "sup": 4}
_KR_TZ = timezone(timedelta(hours=9))  # 戰況日界以韓服時間為準

_DAILY_GAMES_SQL = """
SELECT mp.puuid, m.start_ts, mp.champion_name, mp.win
FROM match_players mp JOIN matches m ON m.match_id = mp.match_id
WHERE m.queue_id = {queue} AND m.start_ts >= ?
ORDER BY m.start_ts ASC
""".format(queue=_SOLOQ_QUEUE)


def _day_bounds(now_kst: datetime) -> dict:
    """三個時間窗的毫秒邊界（KST 日界）：昨天／近3天／近7天。"""
    midnight = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)

    def ms(dt: datetime) -> int:
        return int(dt.timestamp() * 1000)

    y_start = midnight - timedelta(days=1)
    d3_start = midnight - timedelta(days=2)
    d7_start = midnight - timedelta(days=6)

    def md(dt: datetime) -> str:
        return f"{dt.month}/{dt.day}"

    return {
        "y_start": ms(y_start), "y_end": ms(midnight),
        "d3_start": ms(d3_start),
        "d7_start": ms(d7_start),
        "now": ms(now_kst),
        "y_label": md(y_start),
        "d3_label": f"{md(d3_start)}–{md(now_kst)}",
        "d7_label": f"{md(d7_start)}–{md(now_kst)}",
    }


def daily_payload() -> dict:
    """組每日戰況資料：隊伍卡片→選手→近 7 天逐場（時間過濾在前端）。

    一次取回近 7 天全部單雙排，前端依 y/d3/d7 邊界切窗，避免三個分頁各查一次。
    """
    if not _db_exists():
        return {"bounds": None, "teams": []}
    now_kst = datetime.now(_KR_TZ)
    bounds = _day_bounds(now_kst)
    con = dpm_store.connect(read_only=True)
    try:
        pros = con.execute(
            "SELECT display_name, team, role, accounts FROM pros "
            "WHERE team IS NOT NULL").fetchall()
        # 全部帳號快照（1900 列左右，一次進記憶體）
        acct_rows = con.execute(
            "SELECT puuid, display_name, game_name, tag_line, team, role, "
            "lane, tier, league_points FROM accounts").fetchall()
        games = con.execute(_DAILY_GAMES_SQL, [bounds["d7_start"]]).fetchall()
    finally:
        con.close()

    accounts = {r[0]: _acct_snapshot(r) for r in acct_rows}
    games_by_puuid: dict[str, list] = {}
    for puuid, ts, champ, win in games:
        games_by_puuid.setdefault(puuid, []).append([ts, champ, bool(win)])

    groups: dict[str, dict] = {}

    def get_group(full_name: str) -> dict:
        grp = groups.get(full_name)
        if grp is None:
            grp = {"team": full_name, "region": dpm_ui.resolve_region(full_name),
                   "codes": {}, "players": [], "names": set()}
            groups[full_name] = grp
        return grp

    # 1) pros 檔內的職業選手（主力資料源；即使目前無排行榜帳號也保留名單）
    for raw_name, full_team, pro_role, acct_json in pros:
        name = _short_name(raw_name)
        puuids = json.loads(acct_json or "[]")
        # pros.role 可能是 Streamer/None，退回其帳號的主修路線判位置
        lane = next((accounts[pid]["lane"] for pid in puuids
                     if accounts.get(pid) and accounts[pid].get("lane")), None)
        pos = _official_pos(pro_role, lane)
        if not name or not pos:
            continue
        grp = get_group(full_team)
        if name in grp["names"]:
            continue
        grp["names"].add(name)
        for pid in puuids:
            snap = accounts.get(pid)
            if snap and snap["team"]:
                grp["codes"][snap["team"]] = \
                    grp["codes"].get(snap["team"], 0) + 1
        grp["players"].append(_daily_player(name, pos, puuids,
                                            accounts, games_by_puuid))

    # 先解出各職業隊簡名，供步驟 2 歸隊使用
    code_of = {full: _team_code(full, grp["codes"])
               for full, grp in groups.items()}
    code_index: dict[str, str] = {}
    for full, code in code_of.items():
        code_index.setdefault(code.upper(), full)
    owned_puuids = {pid for grp in groups.values() for pl in grp["players"]
                    for pid in pl["puuids"]}

    # 2) 排行榜有戰隊、但無 pros 檔的帳號（依 dpm 隊名自成卡或併入同簡名主隊）
    for pid, acct in accounts.items():
        if not acct["team"] or pid in owned_puuids:
            continue
        raw_team = acct["team"]
        if dpm_ui.resolve_region(raw_team) not in _REGION_ORDER:
            continue
        name = _short_name(acct["display_name"]) or acct["game_name"]
        pos = _official_pos(acct["role"], acct["lane"])
        if not name or not pos:
            continue
        target_full = code_index.get(raw_team.upper(), raw_team)
        grp = groups.get(target_full)
        if grp is None:
            grp = get_group(raw_team)
            code_of[raw_team] = _team_code(raw_team, {raw_team: 1})
            code_index.setdefault(raw_team.upper(), raw_team)
        if name in grp["names"]:
            continue
        grp["names"].add(name)
        grp["codes"][raw_team] = grp["codes"].get(raw_team, 0) + 1
        grp["players"].append(_daily_player(
            name, pos, [pid], accounts, games_by_puuid))

    # 3) 官方隊伍頁登錄名單快照：補上未進職業排行榜的登錄選手
    #    （如 Teddy／GIDEON 這類低牌階選手不在 pro 榜前 277 名內）
    snapshot = _load_roster_snapshot()
    if snapshot:
        _merge_roster_snapshot(snapshot, groups, code_of, code_index,
                               get_group, games_by_puuid)

    result = []
    for full_name, grp in groups.items():
        if grp["region"] not in _REGION_ORDER or not grp["players"]:
            continue
        players = sorted(grp["players"],
                         key=lambda p: (_POS_ORDER.get(p["pos"], 9),
                                        -p["game_count"], -(p["lp"] or -1),
                                        p["name"]))
        code = code_of.get(full_name) or _team_code(full_name, grp["codes"])
        is_academy = _is_academy(full_name, code)
        # 二隊若沒拿到 dpm 短碼（如 "EDward"），改掛母隊 abbr＋.A/.C
        if is_academy and not _looks_like_code(code):
            code = _academy_code(full_name)
        result.append({
            "team": code, "team_full": full_name,
            "region": grp["region"], "academy": is_academy,
            "players": [{k: v for k, v in p.items() if k != "puuids"}
                        for p in players],
        })
    # 排序：所有主隊先依賽區優先序（LCK→LPL→LCP→LEC→LCS→CBLOL）排完，
    # 二隊／青訓一律墊在主隊之後（同樣依賽區序）
    result.sort(key=lambda t: (t["academy"], _REGION_ORDER[t["region"]],
                               t["team"].upper()))
    return {"bounds": bounds, "teams": result}


def _short_name(raw: str | None) -> str:
    """pros.display_name 可能是 'Clear (Song Hyeon-min)'，剝掉本名後綴。"""
    if not raw:
        return ""
    return raw.split(" (", 1)[0].strip()


def _is_academy(full_name: str, code: str) -> bool:
    """二隊判定：全名關鍵字或簡碼尾碼（.A/.C/.SC/.EA…）。"""
    low = full_name.lower()
    if any(k in low for k in ("academy", "challenger", "rookie", "youth")):
        return True
    tail = code.upper().rsplit(".", 1)[-1] if "." in code else ""
    return tail in {"A", "C", "SC", "EA", "LA", "CB"}


def _looks_like_code(code: str) -> bool:
    """是否像 dpm 短代碼：無空格、全大寫英數（BFX／EDG／OMG.A）。"""
    return bool(code) and " " not in code and code.upper() == code \
        and any(c.isalpha() for c in code)


def _academy_code(full_name: str) -> str:
    """二隊無短碼時：母隊 abbr ＋ .C（challengers）或 .A（academy/youth）。"""
    suffix = ".C" if "challenger" in full_name.lower() else ".A"
    base = re.sub(r"(challengers?|youth team|youth|academy|rookies?)",
                  " ", full_name, flags=re.IGNORECASE).strip()
    base_code = _team_code(base, {}) if base else ""
    return (base_code or full_name.split()[0]) + suffix


def _acct_snapshot(r) -> dict:
    """accounts 列 → 每日戰況用快照。"""
    keys = ("puuid", "display_name", "game_name", "tag_line", "team",
            "role", "lane", "tier", "league_points")
    return dict(zip(keys, r))


def _daily_player(name: str, pos: str, puuids: list[str],
                  accounts: dict, games_by_puuid: dict) -> dict:
    """合併多帳號：代表牌階取最高 LP 帳號，近 7 天場次跨帳號合併排序。"""
    known = [p for p in puuids if p in accounts]
    snap = None
    if known:
        snap = accounts[max(known,
                            key=lambda p: accounts[p]["league_points"] or -1)]
    merged: list = []
    for pid in puuids:
        merged.extend(games_by_puuid.get(pid, ()))
    merged.sort(key=lambda g: g[0])
    tier = snap["tier"] if snap else None
    return {
        "name": name,
        "pos": pos,
        "tier_zh": dpm_ui.tier_label(tier) if tier else "",
        "tier_color": dpm_ui.tier_color(tier) if tier else "#8b97ab",
        "lp": dpm_ui.display_lp(tier, snap["league_points"]) if snap else None,
        "profile_url": _dpm_profile(snap) if snap else "",
        "games": merged,
        "game_count": len(merged),
        "puuids": list(puuids),
    }


# 隊伍頁登錄名單快照（scripts.fetch_dpm_rosters 產出）
_ROSTER_FILENAME = "dpm_rosters.json"


def _load_roster_snapshot() -> dict | None:
    """唯讀載入名單快照；檔案不存在或毀損時靜默退回 None（不影響主流程）。"""
    path = config.WAREHOUSE_DIR / _ROSTER_FILENAME
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data.get("teams"), dict) else None


def _merge_roster_snapshot(snapshot: dict, groups: dict, code_of: dict,
                           code_index: dict, get_group,
                           games_by_puuid: dict) -> None:
    """把官方登錄名單併入分組：只補不刪，puuid 相同者視為同一人。

    排行榜管線看不到的登錄選手才會在這裡新增（牌階/LP 取自快照 ranks，
    近 7 天場次待爬蟲正式追蹤其帳號後自動出現）。
    """
    for code, team_data in snapshot.get("teams", {}).items():
        full_name = team_data.get("team_full")
        if not full_name:
            continue
        grp = groups.get(full_name) or groups.get(code_index.get(code.upper()))
        if grp is None:
            # 本地完全無資料的隊伍也建卡；非六大賽區者於輸出階段過濾
            grp = get_group(full_name)
            code_of[full_name] = code
            code_index.setdefault(code.upper(), full_name)
        for pro in team_data.get("players", []):
            if pro.get("role") != "PRO":
                continue
            accs = [a for a in pro.get("accounts", []) if a.get("puuid")]
            if not accs:
                continue
            puuids = [a["puuid"] for a in accs]
            pos = next((dpm_ui.LANE_TO_POS.get(a.get("lane") or "")
                        for a in accs
                        if dpm_ui.LANE_TO_POS.get(a.get("lane") or "")), "")
            name = _short_name(pro.get("display_name"))
            if not pos or not name:
                continue
            existing = next((p for p in grp["players"]
                             if set(p["puuids"]) & set(puuids)), None)
            if existing is not None:
                _attach_extra_puuids(existing, puuids, games_by_puuid)
                continue
            if name in grp["names"]:
                continue
            grp["names"].add(name)
            grp["players"].append(_roster_player(name, pos, accs,
                                                 games_by_puuid))


def _attach_extra_puuids(player: dict, puuids: list[str],
                         games_by_puuid: dict) -> None:
    """名單揭露的額外帳號（如跨服小號）補進既有選手，一併合併本地場次。"""
    extra_games = []
    for pid in puuids:
        if pid not in player["puuids"]:
            player["puuids"].append(pid)
            extra_games.extend(games_by_puuid.get(pid, ()))
    if extra_games:
        player["games"].extend(extra_games)
        player["games"].sort(key=lambda g: g[0])
        player["game_count"] = len(player["games"])


def _roster_player(name: str, pos: str, accs: list[dict],
                   games_by_puuid: dict) -> dict:
    """名單快照選手 → 每日戰況選手結構（accs 已依 LP 排序）。"""
    rep = accs[0]
    tier = rep.get("tier")
    merged: list = []
    for acc in accs:
        merged.extend(games_by_puuid.get(acc["puuid"], ()))
    merged.sort(key=lambda g: g[0])
    return {
        "name": name,
        "pos": pos,
        "tier_zh": dpm_ui.tier_label(tier) if tier else "",
        "tier_color": dpm_ui.tier_color(tier) if tier else "#8b97ab",
        "lp": dpm_ui.display_lp(tier, rep.get("league_points"))
              if tier else None,
        "profile_url": _dpm_profile(
            {"game_name": rep.get("game_name"),
             "tag_line": rep.get("tag_line")}),
        "games": merged,
        "game_count": len(merged),
        "puuids": [a["puuid"] for a in accs],
    }


def _team_code(full_name: str, code_votes: dict) -> str:
    """卡片頭顯示的隊伍簡名：優先採計「短代碼」票（無空格、全大寫、異於全名）。"""
    low_full = full_name.strip().lower()
    short = {}
    for k, v in code_votes.items():
        code = (k or "").strip()
        # 需無空格、與全名不同、全大寫（BFX/EDG/DK.C），過濾 "EDward" 類名字片段
        if code and " " not in code and code.lower() != low_full \
                and code.upper() == code and any(c.isalpha() for c in code):
            short[code] = v
    if short:
        # 票數高者勝，同票取較短（BFX 勝過其他同票碼）
        return min(short, key=lambda k: (-short[k], len(k)))
    # 退回 team_meta 的 abbr，再退回全名第一個詞
    info = data_access.load_team_meta().get(full_name)
    if info and info.get("abbr"):
        return info["abbr"]
    return full_name.split()[0] if " " in full_name else full_name


# ---------------------------------------------------------------------------
# 選手詳情頁（圖4／圖5）
# ---------------------------------------------------------------------------
def pro_detail(display_name: str) -> dict | None:
    """選手基本資料＋合併帳號＋同隊隊友（圖4 隊友列、圖5 帳號列）。"""
    if not _db_exists():
        return None
    con = dpm_store.connect(read_only=True)
    try:
        pro = con.execute(
            "SELECT display_name, team, role, real_name, country, "
            "accounts FROM pros WHERE display_name = ?",
            [display_name]).fetchone()
        if not pro:
            return None
        name, team, role, real_name, country, accounts_json = pro
        puuids = json.loads(accounts_json or "[]")
        accounts = _account_cards(con, puuids)
        teammates = _teammates(con, team, name)
    finally:
        con.close()
    return {
        "display_name": name,
        "team": team,
        "role": role,
        "pos": _official_pos(role, None),
        "real_name": real_name,
        "country": country,
        "team_logo": dpm_ui.team_logo_url(team),
        "region": dpm_ui.resolve_region(team),
        "accounts": accounts,
        "teammates": teammates,
    }


def _account_cards(con, puuids: list[str]) -> list[dict]:
    """圖5「合併 N 帳號」pill 所需的各帳號摘要。"""
    if not puuids:
        return []
    placeholders = ",".join("?" * len(puuids))
    rows = con.execute(
        f"SELECT game_name, tag_line, platform, tier, league_points, "
        f"last_match_ts, puuid FROM accounts WHERE puuid IN ({placeholders})",
        puuids).fetchall()
    cards = []
    for game, tag, platform, tier, lp, last_ts, puuid in rows:
        cards.append({
            "game_name": game, "tag_line": tag,
            "server": _server_label(platform),
            "tier_zh": dpm_ui.tier_label(tier),
            "tier_color": dpm_ui.tier_color(tier),
            "lp": dpm_ui.display_lp(tier, lp),
            "profile_url": _dpm_profile({"game_name": game, "tag_line": tag}),
            "last_date": _mmdd(last_ts), "puuid": puuid,
        })
    cards.sort(key=lambda c: (c["last_date"], c["lp"]), reverse=True)
    return cards


def _teammates(con, team: str | None, current: str) -> list[dict]:
    """同戰隊全部選手（圖4 頂列 chips）。"""
    if not team:
        return []
    rows = con.execute(
        "SELECT display_name, role FROM pros WHERE team = ? "
        "AND display_name <> ? ORDER BY display_name",
        [team, current]).fetchall()
    return [{"display_name": n, "pos": _official_pos(role, None)}
            for n, role in rows]


# ---------------------------------------------------------------------------
# 逐場明細（圖3）
# ---------------------------------------------------------------------------
def pro_matches(display_name: str, limit: int = 100) -> list[dict]:
    """該選手所有合併帳號的逐場列（圖3 表格）。"""
    if not _db_exists():
        return []
    con = dpm_store.connect(read_only=True)
    try:
        raw = con.execute(
            "SELECT accounts FROM pros WHERE display_name = ?",
            [display_name]).fetchone()
        if not raw:
            return []
        puuids = json.loads(raw[0] or "[]")
        if not puuids:
            return []
        placeholders = ",".join("?" * len(puuids))
        col_list = ("match_id,puuid,win,team_id,champion_name,lane,kills,deaths,"
                    "assists,kill_participation,gold_diff15,xp_diff15,cs_diff15,"
                    "first_to_lvl2,summoner1_id,summoner2_id,primary_rune,"
                    "secondary_rune,runes_json,items_json,start_items_json,"
                    "item_actions_json,skill_ups_json,dpm_score,dpm_score_rank,"
                    "opponent_champion,duo_champion,duo_lane,duo_opponent_champion,"
                    "lp_delta,tier,game_name,tag_line,display_name,team,role,raw_json")
        cols_prefixed = ", ".join(f"mp.{c}" for c in col_list.split(","))
        rows = con.execute(
            f"SELECT {cols_prefixed}, m.start_ts, m.duration_sec, m.detail_loaded "
            f"FROM match_players mp JOIN matches m ON m.match_id = mp.match_id "
            f"WHERE mp.puuid IN ({placeholders}) AND m.queue_id = "
            f"{_SOLOQ_QUEUE} ORDER BY m.start_ts DESC LIMIT ?",
            [*puuids, limit]).fetchall()
        keys = col_list.split(",") + ["start_ts", "duration_sec",
                                      "detail_loaded"]
        out, detail_cache = [], {}
        for r in rows:
            item = dict(zip(keys, r))
            if item["detail_loaded"]:
                opp = _opponent_identity(con, item, detail_cache)
                item["opponent"] = opp
            else:
                item["opponent"] = None
            out.append(_present_match(item))
    finally:
        con.close()
    return out


def _opponent_identity(con, item: dict, cache: dict) -> dict | None:
    """從 10 人明細找同路敵方：職業選手帶戰隊與超連結，其餘為路人。"""
    if item["match_id"] not in cache:
        parts = con.execute(
            "SELECT lane, team_id, display_name, team, role, puuid, "
            "game_name, tag_line FROM match_players WHERE match_id = ?",
            [item["match_id"]]).fetchall()
        cache[item["match_id"]] = parts
    enemy_team = 100 if item["team_id"] == 200 else 200
    for lane, team_id, name, team, role, puuid, game, tag in cache[item["match_id"]]:
        if team_id == enemy_team and lane == item["lane"] and puuid != item["puuid"]:
            if role == "PRO" and name:
                return {"name": name, "team": team,
                        "is_pro": True,
                        "href": f"/ladder/p/{name}"}
            return {"name": "路人", "is_pro": False}
    return None


def _present_match(m: dict) -> dict:
    """把資料庫列轉成圖3 表格需要的呈現結構（含圖示 URL）。"""
    items = [i for i in json.loads(m.get("items_json") or "[]") if i]
    k, d, a = m.get("kills") or 0, m.get("deaths") or 0, m.get("assists") or 0
    kda_val = round((k + a) / d, 1) if d else round(float(k + a), 1)
    own_pos = dpm_ui.LANE_TO_POS.get(m.get("lane") or "", "")
    dt = datetime.fromtimestamp((m["start_ts"] or 0) / 1000, tz=timezone.utc)
    return {
        "match_id": m["match_id"],
        "win": bool(m["win"]),
        "date": f"{dt.month}/{dt.day}",
        "time": dt.strftime("%H:%M"),
        "duration": _mmss(m.get("duration_sec")),
        "champ_icon": dpm_ui.champ_icon_url(m.get("champion_name")),
        "champion": m.get("champion_name"),
        "duo_champ_icon": dpm_ui.champ_icon_url(m.get("duo_champion")),
        "duo_pos": dpm_ui.duo_overlay_pos(m.get("lane")),
        "opp_champion": m.get("opponent_champion"),
        "opp_champ_icon": dpm_ui.champ_icon_url(
            m.get("opponent_champion")),
        "opponent": m.get("opponent"),
        "k": k, "d": d, "a": a, "kda_val": kda_val,
        "kp": round(m["kill_participation"]) if m.get(
            "kill_participation") is not None else None,
        "first2": m.get("first_to_lvl2"),
        "gd15": m.get("gold_diff15"),
        "xd15": m.get("xp_diff15"),
        "spell1": dpm_ui.summoner_icon_url(m.get("summoner1_id")),
        "spell2": dpm_ui.summoner_icon_url(m.get("summoner2_id")),
        "rune": dpm_ui.rune_icon_url(m.get("primary_rune")),
        "skill_order": _skill_order(json.loads(m.get("skill_ups_json") or "[]")),
        "items": [dpm_ui.item_icon_url(i) for i in items],
        "item_actions": json.loads(m.get("item_actions_json") or "[]"),
        "score": round(m["dpm_score"]) if m.get("dpm_score") is not None else None,
        "score_color": dpm_ui.score_color(m.get("dpm_score")),
        "score_rank": m.get("dpm_score_rank"),
        "lp_delta": m.get("lp_delta"),
        "own_pos": own_pos,
    }


def _skill_order(levels: list[int]) -> str:
    """skillLevelUps [1,3,2,1...] → QEQQ 這種六等前點亮順序字串。"""
    letter = {1: "Q", 2: "W", 3: "E", 4: "R"}
    return "".join(letter.get(x, "?") for x in levels[:6])


def _mmss(seconds: int | None) -> str:
    if not seconds:
        return ""
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------
# 爬取狀態（SCRAPE 按鈕輪詢）
# ---------------------------------------------------------------------------
def scrape_status() -> dict:
    """回傳最近一次爬蟲執行狀態。"""
    keys = ("stage", "stage_progress", "last_success_at", "last_error",
            "last_run_summary", "pro_name_count")
    return {k: (dpm_store.get_meta(k) if _db_exists() else None) for k in keys}
