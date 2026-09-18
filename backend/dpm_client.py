"""DPM.LOL 公開 JSON API 客戶端（職業 SoloQ 積分資料）。

特色：
- 無需 API Key；以瀏覽器標頭請求，自訂最小請求間隔避免造成伺服器負擔。
- 指數退避重試，並辨識 Cloudflare 挑戰頁（HTTP 200 但回 HTML）。
- 404 回傳 None（選手／場景不存在），422 直接拋錯不重試（參數錯誤）。
"""
from __future__ import annotations

import json
import random
import threading
import time
from urllib.parse import quote

import requests

from backend import config
from backend.pipeline.common import get_logger

logger = get_logger("dpm_client")

_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/138.0.0.0 Safari/537.36"),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://dpm.lol/",
}
# 遇 CF 挑戰／429／5xx 時的退避秒數（循序拉長，共四個機會）
_RETRY_WAITS = (0, 8, 20, 40)


class SharedBackoff:
    """跨執行緒共享的全池退避：任一條線遇到 429/CF/5xx，全部線暫停。

    以 monotonic 時鐘記錄全池恢復時間，觸發時等級遞增（最長 60 秒）；
    全池順利度過冷卻期後等級歸零，避免一次抖動造成永久長退避。
    """

    WAITS = (8, 20, 40, 60)

    def __init__(self):
        self._cond = threading.Condition()
        self._until = 0.0
        self._level = 0

    def wait_if_clear(self) -> None:
        """若全池處於退避冷卻中則阻塞至恢復。"""
        with self._cond:
            while self._until > time.monotonic():
                self._cond.wait(self._until - time.monotonic())

    def trigger(self) -> float:
        """任一條線遭遇限流時呼叫，全池進入下一級退避，回傳等待秒數。"""
        with self._cond:
            wait_s = self.WAITS[min(self._level, len(self.WAITS) - 1)]
            self._level += 1
            self._until = max(self._until, time.monotonic() + wait_s)
            self._cond.notify_all()
        return wait_s

    def relax(self) -> None:
        """請求成功且冷卻已過時，降低退避等級。"""
        with self._cond:
            if self._level and self._until <= time.monotonic():
                self._level = 0


class DpmError(RuntimeError):
    """DPM.LOL 請求失敗（重試後仍未成功）。"""


class DpmValidationError(DpmError):
    """API 回覆 422 參數驗證錯誤，不應重試。"""


def profile_url(game_name: str | None, tag_line: str | None) -> str | None:
    """帳號 Riot ID → dpm.lol 帳號頁超連結。"""
    if not game_name or not tag_line:
        return None
    return f"{config.DPM_SITE_URL}/{quote(game_name)}-{quote(tag_line)}"


def match_id(platform_id: str, game_id: int | str) -> str:
    """組合單場明細使用的 ID（平台前綴需大寫，如 KR_8382205147）。"""
    return f"{str(platform_id).upper()}_{game_id}"


class DpmClient:
    """對 dpm.lol 的所有 HTTP 呼叫統一由此客戶端發出。"""

    def __init__(self, interval: float | None = None,
                 jitter: tuple[float, float] | None = None,
                 backoff: "SharedBackoff | None" = None):
        self._session = requests.Session()
        self._session.headers.update(_BROWSER_HEADERS)
        self._interval = (config.DPM_REQUEST_INTERVAL if interval is None
                          else interval)
        # 多線程時每條線各自在區間內隨機間隔，錯峰避免同步請求
        self._jitter = jitter
        self._backoff = backoff
        self._last_call = 0.0

    # ------------------------------------------------------------------
    def _throttle(self) -> None:
        """先配合全池退避，再維持本線相鄰請求的最小（隨機）間隔。"""
        if self._backoff is not None:
            self._backoff.wait_if_clear()
        gap = (random.uniform(*self._jitter) if self._jitter
               else self._interval)
        elapsed = time.time() - self._last_call
        if elapsed < gap:
            time.sleep(gap - elapsed)
        self._last_call = time.time()

    def _raw_get(self, url: str, params: dict | None = None):
        """單次 GET，回傳 requests.Response。"""
        self._throttle()
        logger.debug("GET %s params=%s", url, params)
        return self._session.get(url, params=params,
                                 timeout=config.REQUEST_TIMEOUT)

    def _pause_before_retry(self, attempt_wait: float) -> None:
        """重試前等待：有共享退避則全池一起冷卻，否則僅本線 sleep。"""
        if self._backoff is not None:
            self._backoff.trigger()
            self._backoff.wait_if_clear()
        elif attempt_wait:
            time.sleep(attempt_wait)

    def _get_json(self, path: str, params: dict | None = None):
        """具 CF 辨識與退避重試的 JSON GET；404 回傳 None。"""
        url = f"{config.DPM_BASE_URL}{path}"
        last_exc: Exception | None = None
        for attempt, wait in enumerate(_RETRY_WAITS):
            if wait and self._backoff is None:
                time.sleep(wait)
            elif wait:
                self._backoff.wait_if_clear()
            try:
                resp = self._raw_get(url, params)
            except requests.RequestException as exc:  # 網路異常則重試
                last_exc = exc
                logger.warning("第 %d 次請求異常 %s：%s", attempt + 1, path, exc)
                self._pause_before_retry(wait)
                continue
            if resp.status_code == 404:
                return None
            if resp.status_code == 422:
                raise DpmValidationError(
                    f"參數錯誤 {url}：{resp.text[:200]}")
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = DpmError(f"HTTP {resp.status_code}")
                logger.warning("第 %d 次收到 %s，全池準備退避", attempt + 1,
                               resp.status_code)
                self._pause_before_retry(wait)
                continue
            if self._is_challenge(resp):
                last_exc = DpmError("Cloudflare 挑戰頁")
                logger.warning("第 %d 次遇到 CF 挑戰，全池準備退避",
                               attempt + 1)
                self._pause_before_retry(wait)
                continue
            if resp.status_code != 200:
                raise DpmError(f"GET {url} 失敗：HTTP {resp.status_code}")
            if self._backoff is not None:
                self._backoff.relax()
            return _safe_json(resp, url)
        raise DpmError(f"重試耗盡 {url}：{last_exc}")

    @staticmethod
    def _is_challenge(resp) -> bool:
        """Cloudflare 挑戰頁為 HTML 且標題含 Just a moment。"""
        ctype = resp.headers.get("Content-Type", "")
        if "application/json" in ctype:
            return False
        return "just a moment" in resp.text[:500].lower()

    # ------------------------------------------------------------------
    # 公開端點
    # ------------------------------------------------------------------
    def leaderboard(self, platform: str, page: int = 1,
                    is_pro: bool = True) -> dict:
        """單一伺服器的（職業）排行榜頁，每頁 50 筆。"""
        return self._get_json("/leaderboards/soloq", {
            "platform": platform, "page": page, "isPro": str(is_pro).lower(),
        })

    def pro_profile(self, display_name: str) -> dict | None:
        """職業選手檔（合併帳號清單與基本資料）。"""
        return self._get_json(f"/pros/{quote(display_name)}")

    def match_history(self, puuid: str, page: int = 1) -> dict | None:
        """帳號的單雙排（queue 420）對戰清單，每頁 15 場。

        不帶 queue 參數時 dpm.lol 會混入競技場（1750）、彈性（440）、
        一般（400）場次，會排擠單雙排回填額度，故固定過濾 420。
        """
        return self._get_json(f"/players/{puuid}/match-history",
                              {"page": page, "queue": 420})

    def match_detail(self, puuid: str, platform_id: str,
                     game_id: int | str) -> dict | None:
        """單場 10 人完整明細；match id 格式為大寫平台_遊戲序號。"""
        mid = match_id(platform_id, game_id)
        return self._get_json(f"/players/{puuid}/match/{mid}")

    def esport_rosters(self, league: str) -> list[dict] | None:
        """官方聯賽完整登錄名單（含未進職業排行榜的低牌階選手）。

        回傳球員扁平清單，欄位含 puuid／displayName／team 短碼／lane／
        tier／leaguePoints／勝敗；404 回 None。
        """
        return self._get_json(
            f"/esport/soloq/leagues/{quote(league)}/leaderboard")

    def team_roster_page(self, code: str) -> list[dict]:
        """抓官網隊伍頁 RSC 並解析登錄選手（見 parse_team_roster_html）。"""
        url = f"{config.DPM_SITE_URL}/pro/teams/{quote(code)}"
        content = self.get_bytes(url)
        if content is None:
            return []
        return parse_team_roster_html(
            content.decode("utf-8", errors="replace"))

    def get_bytes(self, url: str) -> bytes | None:
        """下載圖位元組（用於 dpm CDN／階級／隊徽在地化）。"""
        last_exc: Exception | None = None
        for attempt, wait in enumerate(_RETRY_WAITS[:3]):
            if wait:
                time.sleep(wait)
            try:
                resp = self._raw_get(url)
            except requests.RequestException as exc:
                last_exc = exc
                continue
            if resp.status_code == 404:
                return None
            if resp.status_code == 200 and not self._is_challenge(resp):
                return resp.content
            last_exc = DpmError(f"HTTP {resp.status_code}")
        logger.warning("圖片下載失敗 %s：%s", url, last_exc)
        return None


def _safe_json(resp, url: str):
    """解析 JSON，遇到空回應或壞 JSON 擲 DpmError。"""
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise DpmError(f"回應非 JSON {url}：{resp.text[:120]}") from exc


# ---------------------------------------------------------------------------
# 官網隊伍頁（Next.js RSC）名單解析
# ---------------------------------------------------------------------------
# RSC flight 中名單陣列的起手式（頁面中第一次出現者即隊伍登錄名單）
_ROSTER_TOKEN_ESC = r'\"players\":['
_ROSTER_TOKEN_PLAIN = '"players":['
_SOLOQ_QUEUE_NAME = "RANKED_SOLO_5x5"


def parse_team_roster_html(html: str) -> list[dict]:
    """從隊伍頁 RSC 原始 HTML 抽出 players，正規化為選手清單。

    同一位選手可能有多個帳號（跨服／小號），以 displayName 分組，
    各帳號的 puuid／lane／rank 全數保留；代表牌階取單雙排最高 LP。
    """
    raw_objs = _extract_roster_objects(html)
    grouped: dict[str, dict] = {}
    for obj in raw_objs:
        name = obj.get("displayName") or obj.get("gameName")
        puuid = obj.get("puuid")
        if not name or not puuid:
            continue
        solo = next((r for r in (obj.get("ranks") or [])
                     if r.get("queue") == _SOLOQ_QUEUE_NAME), None)
        acc = {
            "puuid": puuid,
            "game_name": obj.get("gameName"),
            "tag_line": obj.get("tagLine"),
            "platform": (obj.get("platform") or "").lower() or None,
            "lane": obj.get("lane"),
            "tier": (solo or {}).get("tier"),
            "league_points": (solo or {}).get("leaguePoints"),
            "wins": (solo or {}).get("wins"),
            "losses": (solo or {}).get("losses"),
        }
        pro = grouped.setdefault(name, {
            "display_name": name, "role": obj.get("role"),
            "accounts": []})
        pro["accounts"].append(acc)
    players = list(grouped.values())
    for pro in players:
        pro["accounts"].sort(key=lambda a: a["league_points"] or -1,
                             reverse=True)
    return players


def _extract_roster_objects(html: str) -> list[dict]:
    """定位第一個 players 陣列（注意轉義），逐筆 json.loads 成物件。"""
    idx = html.find(_ROSTER_TOKEN_ESC)
    escaped = True
    if idx < 0:
        idx = html.find(_ROSTER_TOKEN_PLAIN)
        escaped = False
    if idx < 0:
        return []
    seg = html[idx + len(_ROSTER_TOKEN_PLAIN) - 1:]  # 保留開頭 '['
    if escaped:
        # RSC 把 JSON 放在 JS 字串裡，雙引號被轉義
        seg = seg.replace('\\"', '"').replace("\\n", "\n")
    depth, end = 0, None
    for k, ch in enumerate(seg):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = k
                break
    if end is None:
        return []
    try:
        return json.loads(seg[:end + 1])
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("RSC players 區塊解析失敗：%s", exc)
        return []
