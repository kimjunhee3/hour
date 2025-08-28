FROM python:3.11-slim

# 시스템 패키지 & 크롬 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver fonts-noto fonts-noto-cjk fonts-noto-color-emoji \
    ca-certificates wget curl gnupg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 캐시용 디렉토리 (Railway 볼륨을 /data로 마운트할 것)
RUN mkdir -p /data
ENV CACHE_DIR=/data
ENV PYTHONUNBUFFERED=1

# gunicorn으로 앱 실행, Railway의 $PORT 사용
CMD ["sh", "-c", "gunicorn -w 2 -k gthread -t 180 -b 0.0.0.0:${PORT} wsgi:application"]
