#!/bin/bash
# 每 12 小時由 TrueNAS Cron Job 呼叫：於容器內執行完整數據管線。
# 日誌由 crontab 重新導向至 /mnt/<POOL>/lol-data/logs/cron.log
set -u

CONTAINER="lol-dashboard"

# 容器未運行則直接失敗（cron 會把錯誤寫入 cron.log）
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "[$(date '+%F %T')] 錯誤：容器 ${CONTAINER} 未運行"
    exit 1
fi

echo "[$(date '+%F %T')] 開始執行數據管線"
docker exec "${CONTAINER}" python -m backend.pipeline all
code=$?
# 離開碼：0 成功；1 管線失敗；2 下載部分失敗但已用舊數據續航完成
echo "[$(date '+%F %T')] 管線結束，離開碼 ${code}"
exit "${code}"
