FROM python:3.11-slim

# 시스템 패키지 & 크롬/드라이버 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver \
    fonts-noto fonts-noto-cjk fonts-noto-color-emoji \
    ca-certificates wget curl gnupg tini \
    && rm -rf /var/lib/apt/lists/*

# 경로 환경변수
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER=/usr/lib/chromium/chromedriver
ENV PATH="$PATH:/usr/lib/chromium:/usr/bin"

# ✅ 다양한 이름으로 찾도록 심볼릭 링크
RUN ln -sf /usr/lib/chromium/chromedriver /usr/bin/chromedriver && \
    ln -sf /usr/bin/chromium /usr/bin/google-chrome && \
    ln -sf /usr/bin/chromium /usr/bin/chrome

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 캐시 디렉토리
RUN mkdir -p /data
ENV CACHE_DIR=/data
ENV PYTHONUNBUFFERED=1
# Selenium Manager가 드라이버를 내려받으려다 실패하는 걸 방지(우리는 시스템 드라이버 사용)
ENV SELENIUM_MANAGER=off

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "gunicorn -w 2 -k gthread -t 180 -b 0.0.0.0:${PORT} wsgi:application"]
