# 英雄聯盟電競數據平台 - Implementation Plan

> 任務以垂直切片、依賴排序。AC 對照見 `spec.md`（AC-1～AC-14）。
> 資料層任務（T1–T7）為前端基礎；部署任務（T17–T19）最後進行。

## Task 1：專案骨架與依賴管理
- **Status**：`completed`
- **Priority**：high
- **Depends On**：None
- **Description**：
  - 建立 `requirements.txt`（pandas、duckdb、dash、plotly、gdown、requests、tenacity 等，鎖定 Python 3.11 相容版本）。
  - 建立 `backend/` 套件結構（`__init__.py`、`pipeline/`、`config.py`、`data_access.py`）、`frontend/` 結構（`app.py`、`pages/`、`assets/`、`components/`）。
  - 建立 `backend/config.py`：集中路徑常數（原始 CSV、倉儲、latest.json、assets、logs）、Google Drive 資料夾/檔案設定、Data Dragon 版本 URL、重試參數、評分權重常數。
  - 建立 `.gitignore`（data 內大型產物、venv、__pycache__）；不寫入任何憑證。
- **Acceptance Criteria Addressed**：NFR-3、NFR-4、NFR-5
- **Test Requirements**：
  - `rule` TR-1.1：在乾淨 venv 執行 `pip install -r requirements.txt` 成功，且 `python -c "import pandas,duckdb,dash,plotly,gdown,requests"` 無錯誤；證據為安裝日誌。
  - `rule` TR-1.2：所有路徑常數由 `config.py` 彙整，原始碼掃描無散落硬編碼絕對路徑與密鑰；證據為 grep 結果。

## Task 2：Google Drive 下載器
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T1
- **Description**：
  - 實作 `backend/pipeline/downloader.py`：以 gdown 從 Oracle's Elixir Drive 資料夾解析年度 CSV，下載當前年度檔；偵測 `OraclesElixir/` 缺漏的歷史年度並可補齊（`--all-years`）。
  - 網路呼叫以 tenacity 指數退避重試 3 次、設定超時、請求間隔（≥2 秒）；全部 try/except；下載至暫存檔，驗證通過（可被 pandas 開啟、含關鍵欄位）後原子取代。
  - 讀取目標目錄前先檢查存在性，不存在則建立。
- **Acceptance Criteria Addressed**：AC-1、FR-1、NFR-2、NFR-3
- **Test Requirements**：
  - `rule` TR-2.1：對當前年度執行下載後，檔案可被 pandas 讀取且含 `gameid/date/league/position` 欄位；連續執行兩次結果冪等（大小一致、無 `.tmp` 殘留）；證據為執行日誌與檔案雜湊。
  - `rule` TR-2.2：人為模擬網路失敗（錯誤 URL/斷網）時可見重試日誌、非零結束狀態，且既有 CSV 未被毀損；證據為失敗模擬日誌。

## Task 3：清洗模組
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T1
- **Description**：
  - 實作 `backend/pipeline/cleaner.py`：逐（或批量）讀取 2014–2026 CSV，統一缺失值（空字串/NA）、日期解析、數值欄位型別、類別標準化；分離 player 列（position ∈ top/jng/mid/bot/sup）與 team 列。
  - 去除重複比賽列（同 gameid+participantid）；標註 `datacompleteness`；產生清洗摘要（各年度讀取/保留/剔除列數）。
  - 日期對外輸出採 ISO 8601（YYYY-MM-DD）。
- **Acceptance Criteria Addressed**：AC-2、FR-2、NFR-4
- **Test Requirements**：
  - `rule` TR-3.1：2026 年度清洗後 player＋team 列數合計與來源 97,788 一致（剔除項須於摘要逐項列出）；證據為清洗摘要輸出。
  - `rule` TR-3.2：以腳本驗證日期欄位可做日期比較、無空 teamname 的 player 列進入後續表、無重複 (gameid, participantid)；證據為驗證腳本輸出。

## Task 4：DuckDB 倉儲建置
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T3
- **Description**：
  - 實作 `backend/pipeline/warehouse.py`：建立 `backend/data/warehouse/lol.duckdb`，表格至少含 games、team_games、player_games、draft_bans、draft_picks（長格式，含 side、pick_order/ban_order）、champion_stats、player_stats、team_stats 聚合表。
  - 全量重建採先寫臨時表/檔再 swap 的安全方式；建立查詢所需索引（league、year、patch、teamid、champion）。
  - 實作 `backend/data_access.py` 提供儀表板使用的參數化查詢函式（篩選：賽區/年份/版本/日期/位置/最少場數）。
- **Acceptance Criteria Addressed**：AC-2、FR-3、AC-6、NFR-1
- **Test Requirements**：
  - `rule` TR-4.1：以 SQL 抽查遊戲數＝8,149（2026）、日期跨 2014–2026、draft_picks 每場恰 10 列、draft_bans 每場最多 10 列；證據為查詢輸出。
  - `rule` TR-4.2：重建過程中殺斷程序，既有 lol.duckib 仍可正常開啟查詢（原子替換驗證）；證據為中斷模擬與重連查詢日誌。

## Task 5：JSON 聚合產出
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T4
- **Description**：
  - 實作 `backend/pipeline/aggregate.py`：由倉儲計算 KPI 摘要、賽區清單、英雄/選手/戰隊聚合、相鄰版本 BP% 與出場率升降、戰隊連勝連敗；輸出 `backend/data/processed/latest.json`（UTF-8、暫存檔＋原子取代）。
  - 比率欄位（勝率、出場率、BP% 等）四捨五入至小數點後兩位；含 `generated_at`（ISO）、`years`、`games` 中繼。
  - 同步輸出供儀表板分頁查詢用的配套 JSON/Parquet 片段（champion、player、team、matches 等），避免前端重算。
- **Acceptance Criteria Addressed**：AC-3、FR-4、FR-20
- **Test Requirements**：
  - `rule` TR-5.1：latest.json 可被 `json.load` 解析，必要頂層鍵（generated_at、years、games、kpi、leagues、champions、players、teams、patch_changes）齊全；證據為驗證腳本。
  - `rule` TR-5.2：抽查 3 個比率欄位皆≤2 位小數，generated_at 符合 ISO；檔案寫入採原子取代（無中繼毀損）；證據為腳本輸出與中斷測試。

## Task 6：Data Dragon 圖示同步
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T1
- **Description**：
  - 實作 `backend/pipeline/assets_sync.py`：取得 DDragon 最新版本與 `championFull.json`，建立 CSV 英文名→DDragon id 映射（含 Wukong→MonkeyKing 等特例，以 full json 自動生成為主、人工補充表為輔）。
  - 下載方形頭像至 `backend/data/assets/champions/{id}.png`，含重試、超時、請求間隔；快取版本中繼；比對 CSV 英雄全集，未匹配項寫入日誌。
  - 另同步位置圖示與隊伍色塊所需的最小資源（若無官方戰隊 logo，以文字徽章替代）。
- **Acceptance Criteria Addressed**：AC-4、FR-5、NFR-7
- **Test Requirements**：
  - `rule` TR-6.1：CSV 英雄名集合對本地頭像檔覆蓋率≥99%，未覆蓋者全部出現在日誌清單；證據為集合比對腳本輸出。
  - `rule` TR-6.2：重複執行為快取冪等（已存在且版本相同不重複下載）；失敗重試可見於日誌；證據為執行日誌。

## Task 7：管線單一入口、日誌與摘要
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T2、T5、T6
- **Description**：
  - 實作 `backend/pipeline/__main__.py`（`python -m backend.pipeline`）：參數 `download|clean|aggregate|assets|all|--current-year|--all-years`，全流程採 download→clean→warehouse→aggregate→assets 順序。
  - 統一 logging（終端＋`backend/data/logs/pipeline_YYYYMMDD.log`），每階段列印讀取列數、清洗後列數、寫入筆數、輸出大小、耗時；失敗非零退出，不毀損既有產物。
  - 提供管線自我驗證（schema/列數對帳報告）。
- **Acceptance Criteria Addressed**：FR-6、FR-7、FR-23、AC-13
- **Test Requirements**：
  - `rule` TR-7.1：執行 `all` 全流程結束狀態 0，日誌含五階段摘要與耗時；再執行一次可成功（冪等）；證據為日誌。
  - `rule` TR-7.2：於任一階段注入失敗（如斷網），既有 latest.json 與倉儲仍可用於服務；證據為失敗模擬後的檔案檢查。

## Task 8：Dash 應用骨架、主題與全域篩選
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T5、T6
- **Description**：
  - 實作 `frontend/app.py`：Dash 多頁（pages/）、11 頁導航膠囊頁籤、頂部品牌「LOL 數據表」、資料時間標示、全域篩選列（賽事、年份、賽季、版本、日期區間、最少場數、位置、戰隊/選手搜尋），篩選狀態以 dcc.Store 共享。
  - 深色主題 CSS（#0B1020 底、#131A2A 卡片、藍/紅/金色碼、緊湊表格熱力樣式），全站繁體中文；本機頭像經 Dash 靜態路由提供（NFR-7）。
  - 載入 latest.json/配套產物的快取層；缺檔時顯示明確錯誤提示而非崩潰。
- **Acceptance Criteria Addressed**：AC-5、AC-11、FR-8、NFR-6、NFR-7
- **Test Requirements**：
  - `rule` TR-8.1：啟動後首頁回應 200，導航列 11 個入口皆可連到對應路由；不存在的產物檔案情境下顯示友善提示；證據為瀏覽器/HTTP 檢查。
  - `rubric` TR-8.2：主題與版面質感；1–5；錨點 1＝預設白版 Dash／3＝深色到位但粗糙／5＝與參考圖氣氛一致；門檻≥4；證據為首頁截圖對照參考圖。

## Task 9：總覽頁
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T8
- **Description**：
  - 實作 `frontend/pages/overview.py`：KPI 卡（總場數、藍/紅方勝率、場均時長）、近 7 天場次與賽區分布、進行中賽段、版本間英雄直升/暴跌榜（BP%、出場率）、戰隊連勝/連敗榜、英雄出場快照。
  - 指標由配套 JSON/倉儲查詢驅動，空樣本顯示「—」。
- **Acceptance Criteria Addressed**：FR-9、AC-7
- **Test Requirements**：
  - `rule` TR-9.1：各區塊皆有資料或空狀態；切換賽區/年份後 KPI 與榜單連動更新；證據為截圖與查詢比對。
  - `rule` TR-9.2：抽査版本升降榜至少 2 名英雄的前後版本 BP% 與獨立 pandas 計算相符；證據為驗證輸出。

## Task 10：近況頁
- **Status**：`completed`
- **Priority**：medium
- **Depends On**：T8
- **Description**：
  - 實作 `frontend/pages/recent.py`：依日期倒序的近期戰果（日期、賽區、版本、系列賽、比分、各場時長），賽區/日期篩選。
- **Acceptance Criteria Addressed**：FR-10
- **Test Requirements**：
  - `rule` TR-10.1：列表日期嚴格倒序、篩選後只含符合記錄，抽 5 列與 CSV game/result 相符；證據為比對記錄。

## Task 11：英雄頁
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T8
- **Description**：
  - 實作 `frontend/pages/champions.py`：英雄明細表（頭像、位置、場數、勝率(勝-敗)、BP%、禁用率、藍紅勝率、後選率、參與率、KDA、中期金差、分均金錢/輸出/承傷、攻擊/防禦、評分），可排序/搜尋/位置篩選/最少場數，數值紅綠熱力呈現。
  - 評分模組置於 `backend/metrics/scoring.py`（百分位加權，權重可配置）。
- **Acceptance Criteria Addressed**：FR-11、AC-6、AC-7、AC-11
- **Test Requirements**：
  - `rule` TR-11.1：任選一篩選範圍，出場率、勝率、BP%、KDA 四項與獨立 pandas 計算一致（兩位小數）；證據為驗證腳本。
  - `rubric` TR-11.2：表格熱力配色與密度；1–5；3＝可讀但 plain／5＝近似參考圖；門檻≥4；證據為截圖對照。

## Task 12：選手頁（雷達圖＋明細表）
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T11
- **Description**：
  - 實作 `frontend/pages/players.py`：雙選手選擇＋可自訂軸的 Plotly 雷達圖（預設 8 軸）、同位置選手明細表（KDA 差@10、單殺率、死亡比、首殺差、金轉率、中期金差、攻擊/防禦、評分、全聯盟名次）。
  - 雷達軸正規化與排名邏輯放 metrics 模組；圖表含完整標題/軸/圖例。
- **Acceptance Criteria Addressed**：FR-12、AC-6、AC-7
- **Test Requirements**：
  - `rule` TR-12.1：兩選手雷達各軸數值可由倉儲查詢復算，名次（如 #3）與明細表排序一致；切換位置後對照群正確變更；證據為復算輸出與截圖。

## Task 13：戰隊頁（雷達圖＋明細表）
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T12
- **Description**：
  - 實作 `frontend/pages/teams.py`：雙戰隊 12 軸雷達、戰隊明細表（主動權/被動選序以 `firstPick`+`side` 實作並標註僅 2026 新制、首龍/首塔/預示者/首巴龍率、中期金差、視野分數、攻擊/防禦/評分）。
- **Acceptance Criteria Addressed**：FR-13、AC-6
- **Test Requirements**：
  - `rule` TR-13.1：抽 2 戰隊，首龍率、首塔率、中期金差、主動權標註與 CSV 計算一致；2026 前年度主動權顯示「—」；證據為復算與截圖。

## Task 14：陣容頁
- **Status**：`completed`
- **Priority**：medium
- **Depends On**：T8
- **Description**：
  - 實作 `frontend/pages/roster.py`：選定戰隊後呈現五張選手卡（top/jng/mid/bot/sup），卡片含該選手英雄池（頭像、場數、勝率、禁用率、上次出場日期）。
- **Acceptance Criteria Addressed**：FR-14
- **Test Requirements**：
  - `rule` TR-14.1：抽 1 戰隊，五位選手與其英雄池前 5 筆和 player_games/draft 聚合一致，上次出場日期正確；證據為比對輸出。

## Task 15：比賽BP 頁
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T8
- **Description**：
  - 實作 `frontend/pages/match_bp.py`：BP 紀錄列表（系列賽分組、各場展開），藍/紅分欄 5 禁 5 選頭像、先選/後選與選邊標記、頂部摘要（場數、藍紅勝率、先選勝率、場均時長），賽區/版本/戰隊篩選。
- **Acceptance Criteria Addressed**：FR-15、AC-8
- **Test Requirements**：
  - `rule` TR-15.1：抽 3 場比對全部 ban1–5/pick1–5 共 20 欄位、順序、藍紅歸屬完全一致；證據為逐欄比對表。
  - `rule` TR-15.2：頂部三個勝率與獨立計算相符；證據為驗證輸出。

## Task 16：模擬BP 頁與選角評分引擎
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T13、T14
- **Description**：
  - 實作 `backend/metrics/draft_score.py`：九分項（版本強弱、選手熟練、戰隊勝率、對線對位、隊友相合、陣容、禁用、可錯位、戰隊近況），權重 20/20/10/20/5/5/10/5/5 集中於常數；樣本不足計中性 50。
  - 實作 `frontend/pages/mock_bp.py`：選兩隊與先選方、互動禁/選棋盤（兩輪節奏提示）、從選手英雄池點選填入、即時總分/分項面板、頭像 hover 卡（場數、勝率、BP%、KDA、慣用位置）。重新整理即重置（不持久化）。
- **Acceptance Criteria Addressed**：FR-16、AC-9
- **Test Requirements**：
  - `rule` TR-16.1：完成一場模擬，UI 總分＝九分項加權和（誤差≤0.1），權重合計 100% 且與常數檔一致；證據為截圖＋常數檔。
  - `rule` TR-16.2：hover 卡四項數值可由倉儲復算；禁用/選用操作有順序約束且 UI 阻擋非法步驟；證據為操作驗證與復算。

## Task 17：英雄Tier 頁
- **Status**：`completed`
- **Priority**：medium
- **Depends On**：T11
- **Description**：
  - 實作 `frontend/pages/tier_list.py`：依評分門檻（預設前 10%/25%/55%/80%）分 S/A/B/C/D 梯隊列，頭像＋位置圖示，hover/點選顯示場數、禁用、BP%、KDA、評分；門檻可於配置調整。
- **Acceptance Criteria Addressed**：FR-17
- **Test Requirements**：
  - `rule` TR-17.1：梯隊歸屬與配置門檻一致，抽 5 名英雄驗證分層正確；證據為分層比對輸出。

## Task 18：積分頁與圖鑑頁（可用部分＋誠實骨架）
- **Status**：`completed`
- **Priority**：medium
- **Depends On**：T8
- **Description**：
  - `frontend/pages/ladder.py`：完整篩選/表格骨架＋「SoloQ 資料源未接入（未來可接 Riot API）」空狀態，無任何假數據。
  - `frontend/pages/compendium.py`：英雄圖鑑牆（本地頭像、年度選用/禁用次數排序、位置圖示）為可用；版本/道具/符文/召喚師技能/物件/賽事/刷野速度分頁為骨架＋說明卡。
- **Acceptance Criteria Addressed**：FR-18、FR-19、AC-10
- **Test Requirements**：
  - `rule` TR-18.1：兩頁空狀態文案存在，原始碼與渲染 DOM 中無偽造排行/數值；英雄牆計數可復算；證據為截圖＋程式碼檢查。

## Task 19：資料正確性、篩選聯動與響應式/效能驗證
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T9、T10、T11、T12、T13、T14、T15、T16、T17、T18
- **Description**：
  - 撰寫 `backend/tests/` 驗證腳本：跨頁 5+ 項指標對 CSV 的獨立復算、篩選組合（≥3 組）正確性、空值與樣本不足呈現。
  - 響應式檢查（桌面/行動寬度），寬表格可橫滑；以全量數據對 5 個代表性回呼計時，最佳化查詢/快取至 p95≤3 秒；全量重建≤5 分鐘、增量≤90 秒。
- **Acceptance Criteria Addressed**：AC-6、AC-7、AC-14、NFR-1、NFR-6
- **Test Requirements**：
  - `rule` TR-19.1：驗證腳本全部通過（指標 5/5、篩選 3/3）；行動寬度無破版截圖；證據為腳本輸出與截圖。
  - `rubric` TR-19.2：效能表現；1–5；錨點 1=>10 秒／3＝3–5 秒／5＝<3 秒且增量<90 秒；門檻≥4；證據為計時記錄。

## Task 20：Dockerfile 與 docker-compose
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T19
- **Description**：
  - 單一映像 Dockerfile（Python 3.11-slim、裝 requirements、COPY backend/frontend、預設啟動 Dash 監聽 0.0.0.0:8050）；`.dockerignore`。
  - `docker-compose.yml`：服務埠 8050、`./backend/data:/data`（或指定路徑）持久化掛載、重啟原則、環境設定（資料路徑可由環境變數覆蓋）、健康檢查。
  - 提供容器內管線指令（`docker exec ... python -m backend.pipeline all`）。
- **Acceptance Criteria Addressed**：FR-21、AC-12
- **Test Requirements**：
  - `rule` TR-20.1：乾淨環境 `docker compose up --build` 後可開啟 8050 儀表板；重啟容器後 data 卷資料保留；健康檢查回 healthy；證據為建置/啟動日誌與截圖。

## Task 21：TrueNAS 部署配置、Cron Job、SMB 與 Tailscale
- **Status**：`completed`
- **Priority**：high
- **Depends On**：T20
- **Description**：
  - 產出 `deploy/truenas/`：dataset 掛載對應 `/mnt/pool/lol-data`（pool 名以變數標註）、compose 放置/啟用步驟、Cron Job（TrueNAS 系統排程）指令：每 12 小時 `0 0,12 * * *` 執行容器管線，輸出導向 `/mnt/pool/lol-data/logs/cron.log 2>&1`。
  - SMB 共享設定步驟（`\\TrueNAS\lol-data`）；Tailscale 安裝/Auth Key（以佔位變數呈現，不寫密鑰）與存取網址說明（預設僅 Tailnet，不用 Funnel）。
- **Acceptance Criteria Addressed**：FR-22、FR-23、AC-12、AC-13、NFR-3
- **Test Requirements**：
  - `rule` TR-21.1：部署文件每條指令具體可執行、路徑與 compose 一致；以本地模擬 cron 指令手動觸發一次，結束狀態 0 且 cron.log 含摘要、產物時間戳更新；證據為指令執行日誌。
  - `rule` TR-21.2：文件中 Auth Key 僅以佔位符呈現（grep 無真實密鑰）；失敗模擬後舊數據持續可服務；證據為文件檢查與失敗模擬記錄。
