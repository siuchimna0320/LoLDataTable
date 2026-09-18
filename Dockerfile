# 英雄聯盟電競數據平台 — 單一映像（前後端合一，waitress 提供服務）
FROM python:3.11-slim

# 避免 .pyc 與即時輸出緩衝，容器日誌即時可見
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_ROOT=/data

WORKDIR /app

# 先單獨複製依賴清單，善用映像層快取
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製前後端程式碼與爬蟲腳本（cron 以 python -m scripts.scrape_dpm 呼叫）
COPY backend ./backend
COPY frontend ./frontend
COPY scripts ./scripts

# 數據卷（DuckDB 倉儲、JSON、英雄頭像、原始 CSV、日誌）
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8050

# 健康檢查：以映像內 Python 打首頁，無需額外安裝 curl
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8050/', timeout=5).status == 200 else 1)"

# waitress 正式伺服器（開發可用：python frontend/app.py）
CMD ["waitress-serve", "--host=0.0.0.0", "--port=8050", \
     "--threads=8", "frontend.app:server"]
