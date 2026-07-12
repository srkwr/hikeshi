# syntax=docker/dockerfile:1
# Hikeshi demo（fault-injection victim service）— Cloud Run。
# ビルドコンテキスト＝この demo/ ディレクトリ（`gcloud run deploy --source ./demo`）。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . ./

# Cloud Run は $PORT を注入（既定 8080）。/admin/* は IAM 非公開（--no-allow-unauthenticated）
# ＋呼び出し元の Google ID トークンで保護する（多層防御に HIKESHI_DEMO_TOKEN も可）。
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
