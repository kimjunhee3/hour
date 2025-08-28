FROM python:3.11-slim

# 시스템 업데이트 + chromium, chromedriver 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    fonts-noto fonts-noto-cjk fonts-noto-color-emoji \
    ca-certificates wget curl gnupg tini \
    && rm -rf /var/lib/apt/lists/*

# 환경변수: 바이너리/드라이버 경로
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER=/usr/lib/chromium/chromedriver
ENV PATH="$PATH:/usr/lib/chromium:/usr/bin"

# ✅ 심볼릭 링크 강제 생성 (어느 경로로 호출해도 찾게끔)
RUN ln -sf /usr/lib/chromium/chromedriver /usr/bin/chromedriver && \
    ln -sf /usr/bin/chromium /usr/bin/google-chrome && \
    ln -sf /usr/bin/chromium /usr/bin/chrome

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 캐시 저장 폴더
RUN mkdir -p /data
ENV CACHE_DIR=/data
ENV PYTHONUNBUFFERED=1
ENV SELENIUM_MANAGER=off 

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "gunicorn -w 2 -k gthread -t 180 -b 0.0.0.0:${PORT} wsgi:application"]
