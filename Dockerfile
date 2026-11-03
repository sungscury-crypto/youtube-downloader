FROM python:3.12-slim

# 영상+소리 합치는 ffmpeg 설치
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp가 유튜브를 풀 때 필요한 Deno(자바스크립트 실행기) 설치
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app
COPY requirements.txt .
# 배포할 때마다 yt-dlp 최신 버전 받기
RUN pip install --no-cache-dir -U -r requirements.txt

COPY . .

ENV PORT=5000
ENV PYTHONUNBUFFERED=1
EXPOSE 5000
CMD gunicorn -b 0.0.0.0:$PORT --timeout 600 --workers 1 --threads 8 app:app
