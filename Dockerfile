# syntax=docker/dockerfile:1
# Hikeshi 運用コンソール（HITL 承認 UI ＋実 ADK エージェント）— Cloud Run。
# ビルドコンテキスト＝リポジトリ root（console は hikeshi_agent と
# incident_bench.schema を import するため）。demo 用は demo/Dockerfile。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# 依存だけ先に入れてレイヤキャッシュを効かせる
COPY console/requirements.txt ./console/requirements.txt
RUN pip install -r ./console/requirements.txt

# console（UI＋API）＋診断に必要なパッケージのみ
COPY console/ ./console/
COPY hikeshi_agent/ ./hikeshi_agent/
COPY incident_bench/ ./incident_bench/

# Cloud Run は $PORT を注入（既定 8080）。console は /api/* を共有トークンで保護し、
# demo（IAM 非公開）へは Google ID トークンで接続する。
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn console.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
