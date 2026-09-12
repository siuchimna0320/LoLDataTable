---
name: oracle-data-pipeline
description: 用於從 Oracle‘s Elixir 下載比賽數據、進行清洗並輸出標準化 JSON 的完整數據管道。
---

## 使用場景
當用戶要求更新數據、運行爬蟲、或處理 Oracle CSV 數據時觸發。

## 指令
1. 檢查 backend/data/ 目錄下是否存在最新的 Oracle CSV 文件。
2. 如果不存在，從 Oracle's Elixir 官方 Google Drive 下載最新的 CSV。
3. 使用 Pandas 讀取 CSV，處理缺失值和數據類型轉換。
4. 篩選需要的字段（如賽區、戰隊、英雄、勝負等）。
5. 計算聚合指標：戰隊勝率、英雄出場率、選手 KDA 等。
6. 輸出為 backend/data/processed/latest.json，使用 UTF-8 編碼。
7. 在終端打印處理摘要：讀取行數、清洗後行數、輸出文件大小。