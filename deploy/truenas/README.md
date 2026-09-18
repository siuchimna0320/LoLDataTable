# TrueNAS SCALE 部署指南（LOL 電競數據平台）

本指南覆蓋：Dataset 掛載 → 映像建置 → 啟動 → 每 12 小時 Cron Job →
SMB 共享 → Tailscale 遠程訪問。所有指令在 TrueNAS SCALE Shell 執行。

> 將下方 `<POOL>` 取代為你的 pool 名稱（例如 `tank`）。

## 1. 建立 Dataset 與目錄

```bash
# 數據 dataset（會掛載進容器 /data）
zfs create <POOL>/lol-data
mkdir -p /mnt/<POOL>/lol-data/{scripts,logs}

# 放置原始碼（用於建置映像）
mkdir -p /mnt/<POOL>/apps
cd /mnt/<POOL>/apps
git clone <你的倉庫網址> lol-dashboard
cd lol-dashboard
```

## 2. 建置映像

```bash
cd /mnt/<POOL>/apps/lol-dashboard
docker build -t lol-dashboard:latest .
```

## 3. 首次啟動與全量灌數

```bash
# 複製 TrueNAS 專用 compose，並取代 pool 名稱與 Tailscale IP
# （下方範例假設 pool 為 tank、Tailscale IP 為 100.64.0.1，請換成實際值）
cp deploy/truenas/docker-compose.yml /mnt/<POOL>/lol-data/docker-compose.yml
sed -i -e "s#<POOL>#tank#g" \
       -e "s#<TAILSCALE_IP>#100.64.0.1#g" \
       /mnt/tank/lol-data/docker-compose.yml

cd /mnt/<POOL>/lol-data
docker compose up -d

# 首次灌入 2014–今年全量歷史（約 1 GB，視網速需數十分鐘）
docker exec lol-dashboard python -m backend.pipeline all --all-years

# 首次回填 dpm.lol 職業積分（全部伺服器，每請求間隔 1.5 秒，約 1–3 小時）
# 之後由排程增量更新；亦可於積分頁右上角按「⟳ SCRAPE」手動觸發
docker exec lol-dashboard python -m scripts.scrape_dpm
```

完成後開啟 `http://<TrueNAS-IP>:8050` 確認儀表板。
容器健康狀態：`docker ps` 應顯示 `healthy`。

## 4. 每 12 小時自動更新（Cron Job）

```bash
# 安裝更新腳本
cp /mnt/<POOL>/apps/lol-dashboard/deploy/truenas/update-pipeline.sh \
   /mnt/<POOL>/lol-data/scripts/update-pipeline.sh
chmod +x /mnt/<POOL>/lol-data/scripts/update-pipeline.sh
```

在 TrueNAS Web UI：**System → Advanced → Cron Jobs → Add**：

- Description：`LoL 數據 12 小時更新`
- User：`root`
- Command：
  ```
  /mnt/<POOL>/lol-data/scripts/update-pipeline.sh >> /mnt/<POOL>/lol-data/logs/cron.log 2>&1
  ```
- Schedule：Custom → `0 0,12 * * *`（每天 00:00 與 12:00）
- 取消勾選 Hide Standard Output/Error，方便日誌排查

手動模擬驗證：

```bash
/mnt/<POOL>/lol-data/scripts/update-pipeline.sh
echo "離開碼：$?"          # 0=成功；2=下載有失敗但已用舊數據續航完成
tail -n 20 /mnt/<POOL>/lol-data/logs/cron.log
ls -l /mnt/<POOL>/lol-data/processed/latest.json   # 時間戳已更新
```

管線具增量機制：原始 CSV 內容未變更時自動跳過倉儲重建，
一般無更新週期僅需數十秒。

## 5. SMB 共享（選配，方便查看數據檔）

1. **Shares → Windows (SMB) Shares → Add**
2. Path：`/mnt/<POOL>/lol-data`
3. Name：`lol-data`
4. 於 **Services** 啟用 SMB
5. Windows 連線：`\\<TrueNAS-IP>\lol-data`（輸入 TrueNAS 帳號密碼）

## 6. Tailscale 遠程訪問

建議使用 TrueNAS SCALE 官方 **Tailscale 應用程式**（Apps → Tailscale）：

1. 至 <https://login.tailscale.com/admin/settings/keys> 建立 Auth Key
2. 於 Tailscale 應用設定貼入 Auth Key
3. 啟用後於 Tailscale 管理頁取得 TrueNAS 的 `100.x.x.x` 位址
4. 瀏覽器開啟 `http://<Tailscale-IP>:8050`

### 不用應用程式時的 CLI 作法

```bash
# Auth Key 只以環境變數帶入，不寫入任何檔案
export TS_AUTHKEY='tskey-auth-XXXXXXXXXXXXXXXXXXXXXXXX'
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --authkey "$TS_AUTHKEY" --hostname truenas-lol
unset TS_AUTHKEY
```

安全原則：

- **僅在 Tailnet 內存取**，不啟用 Funnel、不做連接埠轉發
- Auth Key 用後即於管理後台撤銷；範例值只存在 `deploy/truenas/.env.example`
- 確認：`grep -R "tskey-auth" /mnt/<POOL>/apps/lol-dashboard`
  只應出現 `XXXXXXXX` 佔位符

## 7. 失敗時的服務持續性

- 下載失敗（網路中斷、Google Drive 限流）：保留舊 CSV，管線以舊數據繼續，
  儀表板不受影響；失敗細節寫入 `logs/cron.log`
- 倉儲/聚合採「暫存檔 + 原子取代」，中斷不會毀損既有檔案
- 容器異常自動重啟（`restart: unless-stopped`）
- 查詢頁面對缺資料一律顯示「—」或空狀態，不丟出例外

## 8. 維護指令速查

| 用途 | 指令 |
| --- | --- |
| 查看日誌 | `docker logs -f lol-dashboard` |
| 手動更新 | `docker exec lol-dashboard python -m backend.pipeline all` |
| 只重建 JSON | `docker exec lol-dashboard python -m backend.pipeline aggregate` |
| 重做全量倉儲 | `docker exec lol-dashboard python -m backend.pipeline clean` |
| 手動爬 dpm 積分 | `docker exec lol-dashboard python -m scripts.scrape_dpm` |
| dpm 爬蟲日誌 | `tail -f /data/warehouse/scrape.log` |
| 重啟服務 | `cd /mnt/<POOL>/lol-data && docker compose restart` |
| 更新映像 | 於專案目錄 `docker build -t lol-dashboard:latest . && docker compose up -d` |
