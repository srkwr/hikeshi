#!/usr/bin/env bash
# =============================================================================
# 夜回り（Yomawari）— Cloud Scheduler で securitywatch を定期実行
# -----------------------------------------------------------------------------
# console の POST /api/securitywatch/refresh を定期的に叩き、CISA KEV / GitHub
# Advisory の最新を取得して火元帳(KB)へ蓄える。console は公開UI（トークン不要）の
# ため、Scheduler は追加の認証なしで叩ける（LLM/取得コスト経路はアプリ層レート制限で保護）。
#
# 使い方:
#   bash scripts/deploy-cron.sh --dry-run     # 計画のみ
#   bash scripts/deploy-cron.sh                # 実作成（毎日 06:00 JST）
#   SCHEDULE="0 * * * *" bash scripts/deploy-cron.sh   # 毎時などに上書き
#
# ローカル/手元での定期実行なら cron に一行:
#   0 6 * * *  cd /path/to/hikeshi && .venv/bin/python -m hikeshi_agent.securitywatch --refresh
# =============================================================================
set -euo pipefail
REGION="${REGION:-us-central1}"
PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
JOB="${JOB:-hikeshi-YOMAWARI}"
SCHEDULE="${SCHEDULE:-0 21 * * *}"   # UTC 21:00 = JST 06:00（毎朝の巡回）
TZ_="${TZ_:-Etc/UTC}"
CONSOLE_SVC="${CONSOLE_SVC:-hikeshi-console}"
DRY="${1:-}"
run(){ echo "+ $*"; [[ "${DRY}" == "--dry-run" ]] || "$@"; }

[[ -n "${PROJECT}" ]] || { echo "ERROR: project 未設定。"; exit 1; }
URL="$(gcloud run services describe "${CONSOLE_SVC}" --project="${PROJECT}" --region="${REGION}" \
  --format='value(status.url)' 2>/dev/null || echo 'https://CONSOLE_URL')"
ENDPOINT="${URL}/api/securitywatch/refresh"
echo "================ 夜回り定期ジョブ計画 ================"
echo "  job=${JOB}  schedule='${SCHEDULE}' (${TZ_})"
echo "  target=POST ${ENDPOINT}"
[[ "${DRY}" == "--dry-run" ]] && echo "  （DRY-RUN）"
echo "====================================================="

run gcloud services enable cloudscheduler.googleapis.com --project="${PROJECT}"
if gcloud scheduler jobs describe "${JOB}" --location="${REGION}" --project="${PROJECT}" >/dev/null 2>&1; then
  run gcloud scheduler jobs update http "${JOB}" --location="${REGION}" --project="${PROJECT}" \
    --schedule="${SCHEDULE}" --time-zone="${TZ_}" --uri="${ENDPOINT}" --http-method=POST
else
  run gcloud scheduler jobs create http "${JOB}" --location="${REGION}" --project="${PROJECT}" \
    --schedule="${SCHEDULE}" --time-zone="${TZ_}" --uri="${ENDPOINT}" --http-method=POST
fi
echo "完了。手動実行: gcloud scheduler jobs run ${JOB} --location ${REGION}"
echo "停止/削除: gcloud scheduler jobs delete ${JOB} --location ${REGION}"
