---
name: truenas-deploy
description: 用於將項目打包為 Docker 鏡像，並生成 TrueNAS 部署所需的配置文件和 Cron Job 指令。
---

## 使用場景
當用戶要求部署、容器化、設定定時任務時觸發。

## 指令
1. 為 backend 和 frontend 分別生成 Dockerfile，或使用統一的 docker-compose.yml。
2. 確保將 TrueNAS 數據集路徑（如 /mnt/pool/lol-data）掛載到容器內的 /data 目錄。
3. 生成 TrueNAS Cron Job 指令，頻率為每 12 小時執行一次（`0 0,12 * * *`）。
4. Cron Job 命令範例：
   docker exec <容器名> python /app/backend/scraper.py >> /mnt/pool/lol-data/logs/cron.log 2>&1
5. 提供 Tailscale 安裝和 Auth Key 配置步驟。
6. 提供 SMB 共享設定步驟，讓 PC 可透過 \\TrueNAS\lol-data 訪問數據。