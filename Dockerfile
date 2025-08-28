FROM python:3.11-slim

# 시스템 패키지 & 크롬/드라이버 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver \
    fonts-noto fonts-noto-cjk fonts-noto-color-emoji \
    ca-certificates wget curl gnupg tini \
    && rm -rf /var/lib/apt/lists/*

# 크롬/드라이버 경로 (Selenium이 찾기 쉽게)
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER=/usr/lib/chromium/chromedriver
ENV PATH="$PATH:/usr/lib/chromium:/usr/bin"

# 드라이버 링크 (환경에 따라 필요)
RUN ln -sf /usr/lib/chromium/chromedriver /usr/bin/chromedriver

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 캐시 디렉토리
RUN mkdir -p /data
ENV CACHE_DIR=/data
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/bin/tini", "--"]

# Railway의 동적 $PORT 사용
CMD ["sh", "-c", "gunicorn -w 2 -k gthread -t 180 -b 0.0.0.0:${PORT} wsgi:application"]
