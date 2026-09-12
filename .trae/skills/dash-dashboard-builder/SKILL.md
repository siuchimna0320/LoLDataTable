---
name: dash-dashboard-builder
description: 用於根據參考圖片和數據結構，自動生成 Plotly Dash 互動式儀表板代碼。
---

## 使用場景
當用戶要求建立或修改前端儀表板、添加圖表、調整佈局時觸發。

## 指令
1. 讀取 frontend/reference/ 目錄下的參考圖片，理解設計風格和佈局。
2. 讀取 backend/data/processed/latest.json，了解可用數據字段。
3. 使用 Plotly Dash 生成 app.py，包含：
   - 頂部 KPI 卡片區域
   - 中間折線圖區域（戰隊勝率變化）
   - 底部柱狀圖區域（英雄出場率比較）
   - 下拉選單篩選器（按賽區、日期範圍）
4. 使用 dcc.Interval 設定自動刷新，或每次頁面載入時讀取最新 JSON。
5. 啟動本地測試：python frontend/app.py，確認圖表正常渲染。