"""從 Leaguepedia 抓取頂級聯賽戰隊的官方縮寫與 LOGO（本地快取）。

- 資料來源：lol.fandom.com Cargo API（Teams 表 Name/Short/Image）
- 僅需執行一次（更新賽季名單時再跑）；輸出 team_meta.json 與本地 PNG
- 含重試與請求間隔，避免對目標伺服器造成負擔
用法：
    python -m scripts.fetch_team_logos --probe   # 只查詢對應關係
    python -m scripts.fetch_team_logos           # 查詢並下載圖檔
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

from backend import data_access

API = "https://lol.fandom.com/api.php"
# 圖檔改用 Fandom 靜態 CDN（獨立主機，不受 Cargo API 限流影響）
CDN_BASE = "https://static.wikia.nocookie.net/lolesports_gamepedia_en/images"
FILE_BASE = "https://lol.fandom.com/wiki/Special:FilePath/"
META_PATH = Path("backend/data/team_meta.json")
SNAPSHOT_PATH = Path("backend/data/team_cargo_snapshot.json")
LOGO_DIR = Path("frontend/assets/team_logos")

# OE 隊名 → Leaguepedia 隊名（精確查不到時的手工別名）
NAME_ALIASES = {
    "Dplus Kia": "Dplus KIA",
    "QT DIG∞": "QT DIG Infinity",
    "Fukuoka SoftBank HAWKS gaming": "Fukuoka SoftBank HAWKS gaming",
    # 下列於批量 IN 查詢偶發漏網，以獨立請求精確重查
    "Vivo Keyd Stars": "Vivo Keyd Stars",
    "GiantX": "GIANTX",
}

# OE 隊名 → 縮寫覆寫（優先於 Leaguepedia Short，對照 DPM 顯示習慣）
ABBR_OVERRIDES = {
    "Bilibili Gaming": "BLG",
    "Gen.G": "GEN",
    "Hanwha Life Esports": "HLE",
    "Team Secret Whales": "TSW",
    "FURIA": "FUR",
    "G2 Esports": "G2",
    "Karmine Corp": "KC",
    "Team Liquid": "TL",
    "CTBC Flying Oyster": "CFO",
    "Deep Cross Gaming": "DCG",
    "DetonatioN FocusMe": "DFM",
    "Fukuoka SoftBank HAWKS gaming": "SHG",
    "GAM Esports": "GAM",
    "Ground Zero Gaming": "GZ",
    "MVK Esports": "MVK",
    "Chiefs Esports Club": "CHF",
    "Inferno Esports": "INF",
    "Saigon Dino": "SGD",
    "MGN Vikings Esports": "MGN",
    "Saving OCE": "SAV",
    "QT DIG∞": "QTD",
    "Vivo Keyd Stars": "VKS",
    "RED Canids": "RED",
    "paiN Gaming": "PNG",
    "Fluxo W7M": "W7M",
    "BNK FEARX": "FOX",
    "DN SOOPers": "DNS",
    "HANJIN BRION": "BRO",
    "KT Rolster": "KT",
    "Kiwoom DRX": "DRX",
    "Nongshim RedForce": "NS",
    "Cloud9": "C9",
    "Shopify Rebellion": "SR",
    "Movistar KOI": "KOI",
    "Natus Vincere": "NAVI",
    "GiantX": "GX",
    "SK Gaming": "SK",
    "Anyone's Legend": "AL",
    "JD Gaming": "JDG",
    "EDward Gaming": "EDG",
    "LGD Gaming": "LGD",
    "LNG Esports": "LNG",
    "Ninjas in Pyjamas": "NIP",
    "Oh My God": "OMG",
    "Team WE": "WE",
    "ThunderTalk Gaming": "TT",
    "Top Esports": "TES",
    "Ultra Prime": "UP",
    "Weibo Gaming": "WBG",
    "Invictus Gaming": "iG",
}


# OE 隊名 → 圖檔名手工修正（Cargo Image 與 CDN 實際路徑不一致時）
IMAGE_FIXES = {
    "LGD Gaming": "LGD Gaminglogo square.png",
}


def _request(url: str, retries: int = 5, delay: float = 2.0):
    """含重試的 HTTP GET；遇限流時指數退避（最長 60 秒）。"""
    import json as _json
    last_exc = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "LOL-data-tool/1.0"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                raw = resp.read()
            data = _json.loads(raw)
            if isinstance(data, dict) and data.get("error", {}).get(
                    "code") == "ratelimited":
                wait = min(60, 15 * (attempt + 1))
                print(f"……限流，等待 {wait}s")
                time.sleep(wait)
                continue
            return raw
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(delay * (attempt + 1))
    raise RuntimeError(f"請求失敗（{retries} 次）：{url}") from last_exc


def _cargo(where: str, limit: int = 200) -> list[dict]:
    """執行 Cargo 查詢並回傳 title 列。"""
    url = (f"{API}?action=cargoquery&format=json&tables=Teams"
           f"&fields=Name,Short,Image&where="
           f"{urllib.parse.quote(where)}&limit={limit}")
    data = json.loads(_request(url))
    return [i["title"] for i in data.get("cargoquery", [])]


def _sql_str(value: str) -> str:
    """Cargo SQL 單引號跳脫。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def cargo_query_batch(names: list[str]) -> dict[str, dict]:
    """一次 Name IN(...) 批量查詢，回傳 Name→row。"""
    in_list = ",".join(f'"{_sql_str(n)}"' for n in names)
    rows = _cargo(f"Name IN ({in_list})", limit=max(len(names), 1))
    return {r["Name"]: r for r in rows if r.get("Name")}


def cargo_query_team(name: str) -> dict | None:
    """以精確隊名補查單一隊伍。"""
    rows = _cargo(f'Name="{_sql_str(name)}"', limit=1)
    return rows[0] if rows else None


def _norm(text: str) -> str:
    """大小寫／空格正規化，供隊名比對。"""
    return " ".join((text or "").lower().split())


def _distinct_token(name: str) -> str | None:
    """取隊名中最具區別性的單字（過濾 gaming/esports/team 等泛用詞）。"""
    stop = {"gaming", "esports", "team", "the", "of", "de", "e", "club"}
    tokens = [t for t in name.replace("-", " ").split()
              if len(t) >= 4 and t.lower() not in stop]
    return max(tokens, key=len) if tokens else None


def _pick_candidate(oe_name: str, rows: list[dict]) -> dict | None:
    """自 LIKE 候選中挑選最匹配者：正規化全等 > 包含區別詞 > 最短路徑。"""
    if not rows:
        return None
    target = _norm(oe_name)
    token = _distinct_token(oe_name)
    token = _norm(token) if token else ""

    def score(row: str) -> tuple[int, int]:
        cand = _norm(row)
        if cand == target:
            return (3, -len(cand))
        if token and token in cand:
            # 共享區別詞者優先，名稱越短越可能是母隊而非分部
            return (2, -len(cand))
        if cand.startswith(target) or target.startswith(cand):
            return (1, -len(cand))
        return (0, -len(cand))

    best = max(rows, key=lambda r: score(r.get("Name", "")))
    return best if score(best.get("Name", ""))[0] > 0 else None


def safe_file_stem(name: str) -> str:
    """檔名安全化。"""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def cdn_url(image_name: str, width: int = 64) -> str:
    """以檔名 md5 推算 Fandom CDN 圖檔位址（MediaWiki 慣例）。"""
    base = image_name.strip().replace(" ", "_")
    digest = hashlib.md5(base.encode("utf-8")).hexdigest()
    return (f"{CDN_BASE}/{digest[0]}/{digest[:2]}/"
            f"{urllib.parse.quote(base)}/revision/latest/"
            f"scale-to-width-down/{width}")


def load_snapshot() -> dict:
    """Cargo 不可用時的本地名稱對應快照（OE 名 → row）。"""
    if not SNAPSHOT_PATH.exists():
        return {}
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


# Leaguepedia 圖檔常見字尾，供 CDN 離線探測
_LOGO_TAILS = ["logo square.png", "logo profile.png", "_logo.png"]


def probe_logo_cdn(name_variants: list[str]) -> str | None:
    """不經 Cargo，直接對 CDN 嘗試候選圖檔名，回傳首個存在者。"""
    for nm in name_variants:
        for tail in _LOGO_TAILS:
            candidate = f"{nm}{tail}"
            try:
                req = urllib.request.Request(
                    cdn_url(candidate),
                    headers={"User-Agent": "LOL-data-tool/1.0"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    if resp.status == 200:
                        return candidate
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    time.sleep(0.3)
            except Exception:  # noqa: BLE001
                time.sleep(0.3)


def http_get_bytes(url: str, retries: int = 4) -> bytes | None:
    """下載原始位元組；404 明確回 None，其餘錯誤重試（線性退避）。"""
    last_exc = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "LOL-data-tool/1.0"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            last_exc = exc
            time.sleep(2 * (attempt + 1))
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(2 * (attempt + 1))
    print(f"[下載失敗] {url}：{last_exc}")
    return None


def resolve_thumb_url(image_name: str, width: int = 64) -> str | None:
    """以 MediaWiki imageinfo 解析真實縮圖位址。

    Cargo 的 Image 可能是「重新導向頁」標題，其 md5 與實體檔路徑不同，
    會讓 cdn_url() 推算結果 404；imageinfo 會跟隨重新導向給出真實 URL。
    """
    query = (f"{API}?action=query&format=json&titles=File:"
             f"{urllib.parse.quote(image_name.strip())}&prop=imageinfo"
             f"&iiprop=url|mime&iiurlwidth={width}")
    try:
        data = json.loads(_request(query))
    except Exception as exc:  # noqa: BLE001
        print(f"[imageinfo 失敗] {image_name}：{exc}")
        return None
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        return info.get("thumburl") or info.get("url")
    return None


def save_logo_png(payload: bytes, out: Path) -> bool:
    """以 Pillow 解碼（相容 webp/png）並轉存 PNG；失敗回 False。"""
    try:
        img = Image.open(io.BytesIO(payload))
        img.save(out, format="PNG")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[解碼失敗] {out.name}：{exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--offline", action="store_true",
                        help="不查 Cargo，直接用本地快照＋CDN 探測／下載")
    args = parser.parse_args()

    df = data_access.team_ranking(data_access.default_team_filter())
    oe_names = sorted(df["teamname"].tolist())
    meta: dict[str, dict] = {}

    # 第一段：一次批量查全部 OE 名；--offline 或 Cargo 失效時降級本地快照
    cargo_ok = not args.offline
    if args.offline:
        print("[離線模式] 跳過 Cargo，讀取本地快照")
        batch = load_snapshot()
    else:
        try:
            batch = cargo_query_batch(oe_names)
        except Exception as exc:  # noqa: BLE001
            print(f"[警告] Cargo 批量查詢失敗，改用本地快照：{exc}")
            cargo_ok = False
            batch = load_snapshot()
    for oe in oe_names:
        row = batch.get(oe)
        if row:
            meta[oe] = {"abbr": ABBR_OVERRIDES.get(
                oe, row.get("Short") or oe[:3].upper()),
                "image": row.get("Image") or "",
                "lp_name": row.get("Name", oe)}

    # 第二段：未命中者以別名批量補查（Cargo 健康時才執行）
    rest = [n for n in oe_names if n not in meta]
    alias_map = {NAME_ALIASES[n]: n for n in rest if n in NAME_ALIASES}
    if alias_map and cargo_ok:
        time.sleep(2)
        try:
            batch2 = cargo_query_batch(list(alias_map))
        except Exception as exc:  # noqa: BLE001
            print(f"[警告] 別名補查失敗：{exc}")
            batch2 = {}
        for lp_name, oe in alias_map.items():
            row = batch2.get(lp_name)
            if row:
                meta[oe] = {"abbr": ABBR_OVERRIDES.get(
                    oe, row.get("Short") or oe[:3].upper()),
                    "image": row.get("Image") or "",
                    "lp_name": row["Name"]}

    # 第三段：仍缺者逐個 Cargo 補查（限流時略過，改由第四段 CDN 探測）
    missing = [n for n in oe_names if n not in meta]
    if cargo_ok:
        for oe in missing[:]:
            time.sleep(2)
            rows = []
            try:  # 先以精確名獨立重查（批量 IN 偶發漏網）
                rows = _cargo(f'Name="{_sql_str(oe)}"', limit=1)
            except Exception as exc:  # noqa: BLE001
                print(f"[錯誤] {oe}（精確）: {exc}")
            if not rows:
                try:
                    rows = _cargo(f'Name LIKE "{_sql_str(oe[:8])}%"', limit=8)
                except Exception as exc:  # noqa: BLE001
                    print(f"[錯誤] {oe}: {exc}")
                    continue
            if not rows:  # 首 8 字無果，改用區別性關鍵字再試
                try:
                    token = _distinct_token(oe)
                    if token:
                        time.sleep(2)
                        rows = _cargo(
                            f'Name LIKE "%{_sql_str(token)}%"', limit=8)
                except Exception as exc:  # noqa: BLE001
                    print(f"[錯誤] {oe}（關鍵字補查）: {exc}")
            cand = _pick_candidate(oe, rows)
            print(f"[候選] {oe}: {[(r['Name'], r.get('Short')) for r in rows]}"
                  f" → 採用 {cand['Name'] if cand else None}")
            if cand:
                meta[oe] = {"abbr": ABBR_OVERRIDES.get(
                    oe, cand.get("Short") or oe[:3].upper()),
                    "image": cand.get("Image") or "",
                    "lp_name": cand["Name"]}

    # 第四段：仍缺者以 CDN 候選圖檔名離線探測（不經 Cargo）
    for oe in [n for n in oe_names if n not in meta]:
        variants = [oe]
        if oe in NAME_ALIASES:
            variants.insert(0, NAME_ALIASES[oe])
        found = probe_logo_cdn(variants)
        if found:
            print(f"[CDN 探測] {oe} → {found}")
            meta[oe] = {"abbr": ABBR_OVERRIDES.get(oe, oe[:3].upper()),
                        "image": found, "lp_name": variants[0]}
        else:
            print(f"[CDN 探測] {oe}：無候選圖檔，稍後以縮寫徽章降級")

    # 圖檔名手工修正（覆寫 Cargo／探測結果）
    for oe, image in IMAGE_FIXES.items():
        if oe in meta:
            meta[oe]["image"] = image

    for oe in oe_names:
        if oe in meta:
            print(f"{oe:32s} → {meta[oe]['abbr']:5s} {meta[oe]['image']}")
    still = [n for n in oe_names if n not in meta]
    print(f"\n對應成功 {len(meta)}/{len(oe_names)}；缺：{still}")
    if args.probe:
        return

    # 下載 LOGO（CDN 預設回 webp；重新導向頁或 PNG 來源一律以 Pillow 轉存 PNG）
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    for oe, info in meta.items():
        if not info.get("image"):
            print(f"[略過] {oe}：無 Image 欄位")
            continue
        stem = safe_file_stem(oe)
        out = LOGO_DIR / f"{stem}.png"
        if not out.exists():
            payload = http_get_bytes(cdn_url(info["image"]))
            # md5 路徑 404（Cargo 標題為重新導向頁）時，改解析真實縮圖位址
            if payload is None:
                real_url = resolve_thumb_url(info["image"])
                if real_url:
                    time.sleep(1.0)  # 對 API 保持禮貌間隔
                    payload = http_get_bytes(real_url)
                if payload is None:
                    print(f"[略過] {oe}：找不到可用圖檔")
                    continue
            if not save_logo_png(payload, out):
                continue
            print(f"[下載] {oe} → {out.name}（{len(payload)} bytes）")
            time.sleep(0.25)  # 對 CDN 保持禮貌間隔
        info["logo_file"] = f"{stem}.png"
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    got_logo = sum(1 for i in meta.values() if i.get("logo_file"))
    print(f"已寫入 {META_PATH}（{got_logo}/{len(meta)} 個 LOGO）→ {LOGO_DIR}")


if __name__ == "__main__":
    main()
