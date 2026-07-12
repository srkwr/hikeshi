#!/usr/bin/env bash
# =============================================================================
# Hikeshi — Cloud Run デプロイ（稼働URL・公開UI）
# -----------------------------------------------------------------------------
# 構成（最小・安全）:
#   hikeshi-demo    … victim service。--no-allow-unauthenticated（IAM 非公開）。
#                     インターネットからは到達不可。console だけが ID トークンで呼ぶ。
#   hikeshi-console … 運用コンソール＋実 ADK エージェント。--allow-unauthenticated
#                     の公開UI（トークン不要＝誰でも閲覧可）。LLM コスト経路
#                     （/api/diagnose・/api/diagnose/stream・/api/depscan）は
#                     アプリ層レート制限（最小間隔12秒＋日次上限300回・console/app.py）
#                     で保護する。専用ランタイム SA が Vertex(aiplatform.user) と
#                     demo への run.invoker を持ち、console→demo は Google ID
#                     トークンで接続（demo の IAM 非公開は従来どおり）。
#
# 前提:
#   - gcloud 認証済み・課金リンク済みプロジェクト。
#   - 有効 API（未有効なら下記を一度実行）:
#       gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
#         cloudbuild.googleapis.com aiplatform.googleapis.com
#   - リポジトリ root から実行（root Dockerfile=console / demo/Dockerfile=demo）。
#
# 使い方:
#   bash scripts/deploy.sh --dry-run        # 実行計画の表示のみ
#   bash scripts/deploy.sh                   # 実デプロイ
#   REGION=asia-northeast1 bash scripts/deploy.sh       # リージョン上書き
#
# ※ トークン運用に戻す場合（アプリは互換のまま）: HIKESHI_CONSOLE_TOKEN を env に足すと
#    /api/* が共有トークン必須に戻る。平文 env を避けるなら Secret Manager:
#      printf '%s' "$TOKEN" | gcloud secrets create hikeshi-console-token --data-file=-
#      gcloud secrets add-iam-policy-binding hikeshi-console-token \
#        --member="serviceAccount:${RUNTIME_SA}" --role=roles/secretmanager.secretAccessor
#      gcloud run deploy ... --set-secrets="HIKESHI_CONSOLE_TOKEN=hikeshi-console-token:latest"
# =============================================================================
set -euo pipefail

REGION="${REGION:-us-central1}"
PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
DEMO_SVC="hikeshi-demo"
CONSOLE_SVC="hikeshi-console"
RUNTIME_SA_NAME="hikeshi-run"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
RAG="${HIKESHI_RAG:-on}"
VERTEX_LOCATION="${GOOGLE_CLOUD_LOCATION:-global}"

DRY="${1:-}"
run() { echo "+ $*"; [[ "${DRY}" == "--dry-run" ]] || "$@"; }

command -v gcloud >/dev/null 2>&1 || { echo "ERROR: gcloud が見つかりません。"; exit 1; }
[[ -n "${PROJECT}" ]] || { echo "ERROR: project 未設定。'gcloud config set project <ID>' か PROJECT=<ID> を指定。"; exit 1; }
[[ -f Dockerfile && -f demo/Dockerfile ]] || { echo "ERROR: リポジトリ root から実行してください（Dockerfile が見つかりません）。"; exit 1; }

echo "================ デプロイ計画 ================"
echo "  project : ${PROJECT}   region=${REGION}"
echo "  demo    : ${DEMO_SVC}（IAM 非公開・--source ./demo）"
echo "  console : ${CONSOLE_SVC}（公開UI・トークン不要＋アプリ層レート制限・--source .）  SA=${RUNTIME_SA}"
echo "  vertex  : location=${VERTEX_LOCATION}   RAG=${RAG}"
[[ "${DRY}" == "--dry-run" ]] && echo "  （DRY-RUN: 実際には実行しません）"
echo "============================================="

# 1) console 専用ランタイム SA（冪等）＋ Vertex 呼び出し権限。
if ! gcloud iam service-accounts describe "${RUNTIME_SA}" >/dev/null 2>&1; then
  run gcloud iam service-accounts create "${RUNTIME_SA_NAME}" \
    --project="${PROJECT}" --display-name="Hikeshi Cloud Run runtime"
  # SA 作成は結果整合＝伝播待ち（直後の IAM バインドが "does not exist" になる競合を防ぐ）。
  if [[ "${DRY}" != "--dry-run" ]]; then
    echo "  ランタイム SA の伝播を待機中..."
    for _i in $(seq 1 30); do
      gcloud iam service-accounts describe "${RUNTIME_SA}" >/dev/null 2>&1 && break
      sleep 2
    done
  fi
fi
run gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/aiplatform.user" --condition=None

# 2) demo を IAM 非公開でデプロイ（Cloud Build がイメージをビルド／push）。
run gcloud run deploy "${DEMO_SVC}" --source ./demo --project="${PROJECT}" --region="${REGION}" \
  --no-allow-unauthenticated --min-instances=0 --max-instances=2

DEMO_URL="$(gcloud run services describe "${DEMO_SVC}" --project="${PROJECT}" --region="${REGION}" \
  --format='value(status.url)' 2>/dev/null || echo 'https://DEMO_URL')"
echo "demo URL（非公開）: ${DEMO_URL}"

# 3) console SA に demo への run.invoker（ID トークンで呼べるように）。
run gcloud run services add-iam-policy-binding "${DEMO_SVC}" --project="${PROJECT}" --region="${REGION}" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/run.invoker"

# 4) console を公開でデプロイ（トークン不要の公開UI・専用 SA・Vertex 設定）。
#    LLM コスト経路はアプリ層レート制限（console/app.py・既定 12s 間隔/日次300回）が守る。
#    max-instances=1：単一オペレータのデモ前提＋プロセス内レート制限カウンタの前提。
#    直近インシデント記録と承認 KB（エフェメラル）がインスタンス局所のため、
#    複数インスタンスに散らさない。
#    --clear-secrets：デプロイを決定的にする（サービスに残った Secret Manager 参照を毎回リセット）。
#    過去に ELASTIC_URL/ELASTIC_API_KEY のシークレット参照が SA 権限なしで残置され、以後の全
#    リビジョン作成が SecretsAccessCheckFailed で失敗する事故が実際に起きた（2026-06-25→07-10）。
#    Elastic を有効化する場合はデプロイ後に --update-secrets で明示的に載せる（hikeshi_agent/README 参照）。
# 自己ホスト Elasticsearch（hikeshi-es）が存在すれば配線を自動で含める
# （--set-env-vars は全置換のため、ここで含めないと deploy.sh のたびに ES 配線が消える）。
ES_ENV=""
ES_URL_LIVE="$(gcloud run services describe hikeshi-es --project="${PROJECT}" --region="${REGION}" \
  --format='value(status.url)' 2>/dev/null || true)"
if [[ -n "${ES_URL_LIVE}" ]]; then
  ES_ENV=",HIKESHI_ES_URL=${ES_URL_LIVE},HIKESHI_ES_AUTH=idtoken"
  echo "  検索バックエンド: Elasticsearch（自己ホスト・${ES_URL_LIVE}）を配線します"
else
  echo "  検索バックエンド: ローカル BM25-lite（hikeshi-es 未デプロイ）"
fi
# 半鐘（通知発信）：ローカル env に HIKESHI_NOTIFY_WEBHOOK があれば console へ配線する。
# 未設定なら含めない＝/api/notify は 409 を正直に返す（デモでは省略可）。
# 注意：--set-env-vars は全置換のため、以前のデプロイで設定していても今回ローカル env が
# 無ければ配線は外れる（下の echo で正直に開示）。
NOTIFY_ENV=""
if [[ -n "${HIKESHI_NOTIFY_WEBHOOK:-}" ]]; then
  NOTIFY_ENV=",HIKESHI_NOTIFY_WEBHOOK=${HIKESHI_NOTIFY_WEBHOOK}"
  echo "  半鐘通知: HIKESHI_NOTIFY_WEBHOOK を配線します（/api/notify 有効）"
else
  echo "  半鐘通知: 未設定（/api/notify は 409。以前の設定も --set-env-vars 全置換で外れます）"
fi
run gcloud run deploy "${CONSOLE_SVC}" --source . --project="${PROJECT}" --region="${REGION}" \
  --allow-unauthenticated --service-account="${RUNTIME_SA}" \
  --min-instances=0 --max-instances=1 \
  --set-env-vars="HIKESHI_DEMO_URL=${DEMO_URL},GOOGLE_GENAI_USE_VERTEXAI=1,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${VERTEX_LOCATION},HIKESHI_RAG=${RAG}${ES_ENV}${NOTIFY_ENV}" \
  --clear-secrets

CONSOLE_URL="$(gcloud run services describe "${CONSOLE_SVC}" --project="${PROJECT}" --region="${REGION}" \
  --format='value(status.url)' 2>/dev/null || echo 'https://CONSOLE_URL')"

echo
echo "================ 完了 ================"
echo "  稼働URL（公開・トークン不要） : ${CONSOLE_URL}"
echo "  demo（非公開）               : ${DEMO_URL}"
echo
echo "  動作確認:"
echo "    open ${CONSOLE_URL}    # そのまま誰でも閲覧できる（公開モード）"
echo "    # 200 を確認: curl -s -o /dev/null -w '%{http_code}\\n' ${CONSOLE_URL}/api/status"
echo "  トークン運用に戻す場合（アプリは互換のまま /api/* が 401 ゲートに戻る）:"
echo "    gcloud run services update ${CONSOLE_SVC} --region ${REGION} --update-env-vars HIKESHI_CONSOLE_TOKEN=<token>"
echo "  ※ LLM コスト経路はアプリ層レート制限（既定: 12秒間隔・日次300回）で保護。"
echo "  ※ コスト地雷回避: --min-instances=0（ゼロスケール）/ Vector Search 常時稼働・GKE・GPU は使わない。"
