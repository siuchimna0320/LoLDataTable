"""響應式截圖與互動驗證（python -m backend.tests.screenshots）。

以 Playwright 在桌面 1440px 與行動 390px 寬度下擷圖、檢查行動無水平溢出，
並對新版雙人雷達頁與模擬 BP 20 步流程做互動冒煙。
截圖存放於 backend/tests/artifacts/。
前置：儀表板需已運行（預設 http://127.0.0.1:8050，可以 LOL_DASH_URL 覆寫）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ARTIFACT_DIR = Path(__file__).parent / "artifacts"
BASE_URL = os.environ.get("LOL_DASH_URL", "http://127.0.0.1:8050")


def _shot(page, name: str):
    page.screenshot(path=str(ARTIFACT_DIR / name), full_page=True)


def _capture_pages(page) -> None:
    """重點改動頁桌面截圖。"""
    for path, name, selector in [
        ("/players", "desktop-players.png", "#player-table"),
        ("/teams", "desktop-teams.png", "#team-table"),
        ("/match-bp", "desktop-match-bp.png", "#bp-match-table"),
        ("/mock-bp", "desktop-mock-bp.png", "#mb-start"),
        ("/champions", "desktop-champions.png", "#champ-table"),
    ]:
        page.goto(f"{BASE_URL}{path}", wait_until="networkidle")
        page.wait_for_selector(selector, timeout=20000)
        page.wait_for_timeout(1200)
        _shot(page, name)


def _select_team(page, dropdown_id: str, team: str) -> None:
    """Dash 新版 dash-dropdown 選隊伍（精確匹配，避免 T1 誤選 SK Telecom T1）。"""
    page.click(f"#{dropdown_id}")
    page.wait_for_timeout(300)
    page.locator("input.dash-dropdown-search").fill(team)
    page.wait_for_timeout(400)
    clicked = page.evaluate(
        """(team) => {
            const opts = [...document.querySelectorAll('[role="option"]')];
            const hit = opts.find(
                o => o.textContent.trim() === team);
            if (hit) { hit.click(); return true; }
            return false;
        }""",
        team,
    )
    if not clicked:
        raise RuntimeError(f"選單找不到選項：{team}")
    page.wait_for_timeout(200)


def _mark_current_step_target(page) -> None:
    """依目前步驟把第一個可用頭像標記到 window.__target。

    ban 用禁用建議條按鈕（pos 為空）、pick 用英雄池頭像；
    僅做標記不做 JS click，之後由 Playwright 真實點擊。
    """
    found = page.evaluate(
        """() => {
            window.__target = null;
            const banner = document.querySelector('#mb-step h3');
            if (!banner) return false;
            const text = banner.textContent;
            const side = text.includes('藍') ? 'blue' : 'red';
            const phase = text.includes('禁') ? 'ban' : 'pick';
            for (const b of document.querySelectorAll('#mb-pools button')) {
                if (b.disabled) continue;
                let id;
                try { id = JSON.parse(b.id); } catch { continue; }
                if (id.type !== 'mb-pool' || id.side !== side) continue;
                if (phase === 'pick' && !id.pos) continue;
                if (phase === 'ban' && id.pos) continue;
                b.scrollIntoView({block: 'center'});
                window.__target = b;
                return true;
            }
            return false;
        }"""
    )
    if not found:
        raise RuntimeError("目前步驟找不到可點選頭像")


def _wait_step_advance(page, before: str) -> None:
    """等待 banner 推進到下一步，或 20 步完成（h3 被完成卡片取代）。"""
    page.wait_for_function(
        """([old]) => {
            const box = document.querySelector('#mb-step');
            if (!box) return false;
            if (box.textContent.includes('BP 完成')) return true;
            const h3 = box.querySelector('h3');
            return h3 && h3.textContent !== old;
        }""",
        arg=[before], timeout=10000)


def _mock_bp_flow(page) -> None:
    """模擬 BP：選兩隊→開始→走滿 20 步→驗證評分產出。"""
    page.goto(f"{BASE_URL}/mock-bp", wait_until="networkidle")
    page.wait_for_selector("#mb-start", timeout=20000)
    _select_team(page, "mb-team-blue", "T1")
    _select_team(page, "mb-team-red", "Dplus Kia")
    page.click("#mb-start")
    page.wait_for_selector("#mb-step h3", timeout=20000)
    page.wait_for_timeout(800)
    for step in range(20):
        before = page.text_content("#mb-step h3")
        _mark_current_step_target(page)
        # Playwright 真實點擊（JS 合成 click 對 pattern-matching 不可靠）
        page.evaluate_handle("window.__target").as_element().click()
        _wait_step_advance(page, before)
    page.wait_for_selector("#mb-result .js-plotly-plot", timeout=30000)
    page.wait_for_selector("#mb-result .js-plotly-plot >> nth=1",
                           timeout=30000)
    page.wait_for_timeout(800)
    _shot(page, "desktop-mock-bp-result.png")
    print("模擬 BP 20 步互動：PASS（評分圖已產出）")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("請先安裝：pip install playwright 並執行 playwright install chromium")
        return 2

    ARTIFACT_DIR.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(BASE_URL, wait_until="networkidle")
        page.wait_for_selector(".kpi-value", timeout=20000)
        page.wait_for_timeout(1500)
        _shot(page, "desktop-overview.png")
        _capture_pages(page)
        _mock_bp_flow(browser.new_page(viewport={"width": 1440,
                                                  "height": 1000}))

        # 行動寬度
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(BASE_URL, wait_until="networkidle")
        page.wait_for_selector(".kpi-value", timeout=20000)
        page.wait_for_timeout(1200)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth")
        _shot(page, "mobile-overview.png")

        page.goto(f"{BASE_URL}/tier-list", wait_until="networkidle")
        page.wait_for_timeout(1500)
        _shot(page, "mobile-tier.png")
        browser.close()

    print(f"水平溢出像素：{overflow}（須 ≤ 0）")
    print("截圖：", *[f"  - {p.name}" for p in sorted(
        ARTIFACT_DIR.glob("*.png"))], sep="\n")
    return 0 if overflow <= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
