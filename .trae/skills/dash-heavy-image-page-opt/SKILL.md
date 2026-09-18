---
name: "dash-heavy-image-page-opt"
description: "Optimizes slow Plotly Dash tabs that render hundreds of cards/images via batched rendering, clientside lazy-loading, and mtime cache short-circuit. Invoke when a Dash page/tab shows long LOADING with many DOM nodes or image requests."
---

# Dash 大量圖片/卡片頁面載入優化

適用場景：Plotly Dash（本工作區 Waitress + use_pages）頁面或分頁切換後 LOADING 很久，
頁面一次渲染上百張卡片／數百張 `<img>`。

## 0. 先量測，禁止憑感覺改

- 後端計時：冷啟動重建快取、`lru_cache` 命中、讀磁碟 JSON、Dash 元件樹建置、
  `json.dumps(..., cls=PlotlyJSONEncoder)` 序列化與回應大小，五個階段分別計時。
- 前端計時：切 tab 前存 `window.__t0 = performance.now()`，輪詢目標容器
  `querySelectorAll(...)` 首次非空的耗時。
- 統計 DOM：卡片數、`img` 總數、`performance.getEntriesByType('resource')`
  中圖片請求數。後端若僅毫秒級，瓶頸幾乎必在 DOM 建構 + 並發圖片請求
  （瀏覽器對同一 host 約 6 連線上限會排隊）。

## 1. 後端：本機檔案快取加 mtime 短路

- 查詢函式維持 `@lru_cache(maxsize=1)`（每個伺服器進程只算一次）。
- 進入後先讀既有 JSON 快取，比對來源檔 `stat().st_mtime`（統一用
  `datetime.isoformat(timespec="seconds")` 寫入/比對），未變更直接回傳快取；
  變更才重新解析，失敗回退快取。讀檔前一律先檢查存在性。

## 2. 前端：卡片分批渲染（顯示更多）

- 常數 `_PAGE_SIZE = 24`，抽兩個函式：
  - `_xxx_views(payload, 篩選...)`：只做篩選/排序，回傳 `(排序鍵, 卡片)` 列表；
  - `_xxx_grid(views, limit)`：只切 `views[:limit]`，空結果給 `empty_state`。
- `dcc.Store(id=...-limit, data=_PAGE_SIZE)` 記錄目前顯示數量。
- Callback：篩選條件 Input 觸發時 limit 重置為 `_PAGE_SIZE`；
  「顯示更多」按鈕觸發時（用 `ctx.triggered_id` 判斷）limit 疊加。
- **關鍵坑（Dash 回呼死鎖）**：按鈕必須**永遠留在 DOM 中**，沒有更多時只能
  `style.display='none'`，絕對不能直接不 render。Input 元件一旦從 DOM 消失，
  該 callback 會報錯且之後所有觸發都失效（包含搜尋/篩選）。按鈕的文字與
  display 都作為 callback Output 動態更新。
- 篩選結果為空或資料載入失敗時，回傳值仍需包含全部 Output（含按鈕 style）。

## 3. 圖片懶載入：不要傳 loading= 給 html.Img

- 本工作區 Dash 4.4.1 的 `html.Img` **不接受 `loading` 參數**，直接寫會
  `TypeError: unexpected keyword argument: loading`（Python 端 500）。
- 解法：在根佈局元件加 `id="app-shell"`，註冊一個 clientside_callback，
  Input 為 app-shell 的 children，函式內以 `window.__lazyImgInstalled` 防重入，
  先用 `querySelectorAll('img')` 補標記，再用 `MutationObserver`
  （`document.body, {childList:true, subtree:true}`）對所有動態新增節點
  `setAttribute('loading','lazy')`，Output 寫一個無害的 `data-*` 屬性。
- 效果驗證：全展開時 `img[loading="lazy"]` 數量等於圖片總數，但 resource
  entries 明顯較少（視窗外不發請求）。

## 4. 驗證清單

- 首屏卡片數 = _PAGE_SIZE；連點「顯示更多」卡片數等差遞增，最後一批後按鈕隱藏、
  全數卡片皆已顯示。
- 搜尋/排序/單選篩選後分頁重置回首批；清空篩選後恢復。
- 無 404/500；伺服器 log 無 Traceback。console 的 `net::ERR_ABORTED` 在自動化
  快速連點情境下是 Dash 作廢過期 callback 請求的正常現象，人工正常操作不會出現。
- 跑 `python -m backend.tests.validate` 確認既有檢查全數通過。
- Windows + Waitress：改完必須 StopCommand 舊背景工作再重新啟動才會載入新代碼。
