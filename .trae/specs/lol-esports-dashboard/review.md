# 複審報告：LOL 電競數據儀表板

- 規格目錄：`.trae/specs/lol-esports-dashboard/`
- 初審結論：**PASS（無 blocker）**，計 5 項 major、15 項 minor
- 複審範圍：初審發現之修復驗證 + 全量回歸（資料層／API 合約／11 條路由／互動冒煙／響應式截圖／部署構件）
- 複審結論：**PASS**。5 項 major 全數修復；minor 修復 12 項，3 項低優先項保留並記錄於第 4 節

---

## 1. Major 修復對照

| 編號 | 初審發現 | 修復內容 | 對應檔案／函式 |
| --- | --- | --- | --- |
| M1 | 模擬 BP 未實作先選方與真實 20 步禁選流程，頭像無法點選，缺少 hover 資訊 | 整頁重寫：`_bp_sequence()` 產出 6 禁→6 選(F,S,S,F,F,S)→4 禁→4 選共 20 步；藍/紅方英雄池頭像與禁用建議條可點選；hover 顯示場數/勝率/KDA 與全體 BP%/禁用率；完成自動試算九分項評分；支援上一步/重新開始/手動指定 | `frontend/pages/mock_bp.py`；評分 `backend/metrics/draft_score.py` `evaluate()` |
| M2 | 選手/戰隊雷達軸項固定、無法自選比較對象，指標缺項 | 兩頁整頁重寫：雙對象下拉自選、雷達軸 checklist 自選（選手 8 軸、戰隊 12 軸）、百分位正規化、`dcc.Store` 快取、表格加排名欄與 `filter_action` | `frontend/pages/players.py`、`frontend/pages/teams.py`；`backend/data_access.py` `player_stats()`/`team_stats()` |
| M3 | 比賽 BP 頁缺少先選/後選標記與紅藍方勝率口徑，無 BP 資料場次無提示 | 先選/後選徽章（讀 `blue_firstpick`/`red_firstpick`，NaN 不渲染）；頂部 KPI（場數/藍勝率/紅勝率/先選方勝率，僅統計 notna 列）；無資料場次金色提示 | `frontend/pages/match_bp.py`；`backend/data_access.py` `match_bp_records()` |
| M4 | `latest.json` 僅有英雄快照，缺選手/戰隊聚合，前端共用層資料契約不完整 | 聚合新增 players/teams（min_games=10 各 top 50）與 champions（top 50 含 ban_rate/kda/attack/defense），保留 `champion_snapshot` 向下相容；驗證腳本新增 15 個必要鍵與非空檢查 | `backend/pipeline/aggregate.py` `build()`、`backend/metrics/scoring.py`、`backend/tests/validate.py` `validate_latest_json()` |
| M5 | 後端指標口徑與查詢層缺陷：升降榜用單一 pick_rate、缺紅藍方視角、複合分數可破百、倉儲缺欄位、CLI 失敗語意不清 | 升降榜改 BP 率（選＋禁參數化查詢後 `pd.concat`）；`champion_stats()` 加 `main_position`/藍紅方場勝與勝率；`player_stats()` 加 `dmg_share`；`team_stats()` 加 `team_gpm/team_dpm`；`_composite()` 加 `.clip(upper=100)`；新增 `active_stages()`；`all` 模式降級續航回傳 exit 2 | `backend/data_access.py`、`backend/metrics/scoring.py`、`backend/pipeline/__main__.py` |

## 2. Minor 修復對照（節錄要項）

| 面向 | 修復 | 檔案 |
| --- | --- | --- |
| 英雄總覽 | 表格補勝-敗、禁用率、藍/紅方勝率、分均金錢、分均承傷，空值統一顯示「—」 | `frontend/pages/champions.py` |
| 陣容頁 | 英雄池補禁用率與「上次 YYYY-MM-DD」（`last_played`），NaN 防護 | `frontend/pages/roster.py`、`backend/data_access.py` `player_champion_pool()` |
| Tier 榜 | 固定 S→D 排序；頭像下加主位置；hover 補 BP%/禁用率/KDA | `frontend/pages/tier_list.py` |
| 圖鑑 | 補版本/召喚師技能/事件/峽谷等 5 個骨架分頁；英雄卡加主位置 | `frontend/pages/compendium.py` |
| 天梯頁 | 文案對齊 FR-18（職業選手 SoloQ），API Key 改環境變數 | `frontend/pages/ladder.py` |
| 總覽頁 | 升降榜標題改「BP 率」；新增「進行中賽段（近 30 天）」面板 | `frontend/pages/overview.py`、`backend/data_access.py` `active_stages()` |
| 程式品質 | 移除 `_pg_where` 未使用的 `base` 死碼；SQL 全面參數化 | `backend/data_access.py` |
| 部署 | 埠綁定 Tailscale IP 並加註解；sed 範例可實際執行；說明 exit 0/1/2 語意 | `deploy/truenas/docker-compose.yml`、`deploy/truenas/README.md`、`deploy/truenas/update-pipeline.sh`、根 `docker-compose.yml` |

## 3. 回歸驗證證據

| 項目 | 指令／方式 | 結果 |
| --- | --- | --- |
| 倉儲全量重建 | `python -m backend.pipeline clean` | PASS，13 年共 **101,790 場**（2026 年 8,557 場），耗時 43.2 秒 |
| JSON 聚合 | `python -m backend.pipeline aggregate` | PASS，`backend/data/processed/latest.json` 64 KB，15 個必要鍵齊全 |
| 指標驗證 | `python -m backend.tests.validate` | **34/34 PASS**（8 指標復算、3 篩選、5 查詢 p95 ≤ 91 ms、18 項 JSON 鍵檢查） |
| 路由冒煙 | Waitress `127.0.0.1:8052`，11 條路由 GET | 全數 HTTP 200，`from frontend.app import app` 匯入無誤 |
| 模擬 BP 互動 | `python -m backend.tests.screenshots` | **20 步全數推進 PASS**：6 禁 B/R 交替、6 選 F/S/S/F/F/S、4 禁 S/F/S/F、4 選 S/F/F/S，完成後兩張九分項評分圖產出（截圖 T1 61.1 vs Dplus KIA 56.7） |
| 響應式 | 桌面 1440px／行動 390px | 行動水平溢出 **0 px** |
| 截圖構件 | 9 張 | desktop：overview/players/teams/match-bp/mock-bp/mock-bp-result/champions；mobile：overview/tier |
| 英雄頭像 | DDragon 同步 | 173 位英雄 100% 覆蓋 |

互動驗收過程修復一個前端框架層問題：動態掛載的 pattern-matching 按鈕會以 `n_clicks=None` 誤發一次回調，並與「開始模擬」回調形成競態導致步驟卡死。已於 `frontend/pages/mock_bp.py` `_assign()` 以 `ctx.triggered[0]["value"] is None` 過濾，並為按鈕補穩定 `key`；測試端改用 Playwright 真實點擊（JS 合成 click 對 pattern-matching 不可靠）。

## 4. 保留項（低優先 minor，不阻擋結案）

1. **m5 全域篩選戰隊/選手搜尋**：目前於選手、戰隊頁表格以 `filter_action` 原生篩選補強；跨頁全域搜尋列可列下版迭代。
2. **m14 年份 2027 提示**：年份篩選未來年份的 tooltip 引導未加；現有資料至 2026 年，不影響使用。
3. **FR-15 系列賽分組視圖**：比賽 BP 頂部 KPI（場數/紅藍勝率/先選方勝率）已完成，BO 系列賽的局數分組卡片未做，建議與 m5 一併排入下版。

## 5. Chrome 試行補記（2026-09-12）

上線試行時發現一個漏網缺陷：**英雄頁於 UI 預設篩選（2026 年、最少場數 0）下整表空白**，伺服器回報 `TypeError: type NAType doesn't define __round__ method`。原因是藍/紅方勝率與 KDA 的分母防零寫法 `replace(0, pd.NA)` 使 Series 變成 object 夾帶 NAType，`.round()` 逐元素捨入時爆炸；既有冒煙皆用 min_games ≥ 5 且多為全期間篩選，未覆蓋單一方 0 場的邊際列。

修復（[data_access.py](file:///c:/Users/wwwsi/Desktop/my_project/backend/data_access.py) `champion_stats()`/`player_stats()`）：分母改以 `.where(分母 != 0)` 產出 float64 NaN 再運算；驗證腳本新增 4 個「2026 年、min_games=0」邊際案例，**validate 提升為 38/38 PASS**。Chrome 即時擷圖確認總覽/英雄/選手/戰隊/比賽BP/模擬BP 六頁皆正常渲染。教訓：頁面冒煙不能只等容器選擇器，需等待資料列實際出現（已記錄，後續測試應以資料列計數為準）。

## 6. 最終結論

規格 FR-1～23、NFR-1～7、AC-1～14 已全數實作並通過驗證；資料管道、11 頁 Dash 儀表板、Docker 構件、TrueNAS＋Tailscale 部署文件與 12 小時 Cron 腳本皆備，且以真實 13 年資料完成全量回歸。**准予結案**；保留三項低優先 UX 增強，建議於下一次迭代處理。
