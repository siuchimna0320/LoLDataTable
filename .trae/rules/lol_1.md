---
alwaysApply: true
---

## 數據處理規範

- Oracle CSV 數據存放於 backend/data/ 目錄，文件名格式為 oracle_matches.csv。
- 使用 Pandas 讀取 CSV 時，必須處理缺失值和編碼問題。
- 數據清洗後的輸出格式為 JSON，存放於 backend/data/processed/latest.json。
- 所有日期字段統一使用 ISO 8601 格式（YYYY-MM-DD）。
- 數值字段（如勝率、出場率）保留小數點後兩位。