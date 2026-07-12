#!/usr/bin/env bash
# =============================================================================
# Hikeshi — 自己ホスト Elasticsearch を Cloud Run へデプロイ（IAM 非公開）
# -----------------------------------------------------------------------------
# 構成:
#   hikeshi-es … 公式 docker.elastic.co の Elasticsearch 8.x（単一ノード・security off）。
#                --no-allow-unauthenticated（IAM 非公開）＝Authorization ヘッダは
#                Cloud Run の IAM ゲートだけが解釈する（ES 側は xpack.security 無効）。
#                console のランタイム SA にだけ roles/run.invoker を付与し、console は
#                Google ID トークンで接続する（HIKESHI_ES_AUTH=idtoken・
#                hikeshi_agent/retriever_es.py）。
#
# 注意（Elastic Cloud ではない）:
#   これは Elastic Cloud（SaaS）ではなく、公式 Elasticsearch イメージ（basic ライセンス）
#   の自己ホスト。アカウント作成不要で今すぐ動かせる代わりに、マネージドの永続化・
#   スナップショット・監視は無い。
#
# 注意（エフェメラル）:
#   Cloud Run のファイルシステムはインメモリ＝インスタンス再起動で index は消える。
#   retriever_es.py は index_not_found を検知すると kb/*.md（コンソールイメージ内の正本）
#   から自動 reindex するため、運用上は自己修復される（ログに明示される）。
#
# 注意（コスト・一般論）:
#   --min-instances=1 は常時1インスタンス分の課金が発生する（ゼロスケールしない）。
#   使う期間だけ有効化し、終わったら本スクリプト末尾の削除コマンドで片付けるのが安全。
#
# 使い方:
#   bash scripts/deploy-es.sh --dry-run   # 実行計画の表示のみ
#   bash scripts/deploy-es.sh             # 実デプロイ
# =============================================================================
set -euo pipefail

REGION="${REGION:-us-central1}"
PROJECT="${PROJECT:-hikeshi-demo}"
ES_SVC="hikeshi-es"
CONSOLE_SVC="hikeshi-console"
RUNTIME_SA="hikeshi-run@${PROJECT}.iam.gserviceaccount.com"

# Elasticsearch 8.x 系の現行安定タグ（確認日 2026-07-11: 8.19.18 が 8.x 最新パッチ。
# 出典: https://www.elastic.co/guide/en/elasticsearch/reference/8.19/release-notes-8.19.18.html）
ES_VERSION="${ES_VERSION:-8.19.18}"

# Cloud Run は docker.elastic.co から直接 pull できないため、Artifact Registry の
# remote repository（docker.elastic.co をプロキシ）経由で公式イメージを参照する。
AR_REPO="elastic-remote"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/elasticsearch/elasticsearch:${ES_VERSION}"

DRY="${1:-}"
run() { echo "+ $*"; [[ "${DRY}" == "--dry-run" ]] || "$@"; }

command -v gcloud >/dev/null 2>&1 || { echo "ERROR: gcloud が見つかりません。"; exit 1; }

echo "========= デプロイ計画（自己ホスト Elasticsearch on Cloud Run） ========="
echo "  project : ${PROJECT}   region=${REGION}"
echo "  service : ${ES_SVC}（IAM 非公開・port 9200・単一ノード・2Gi/2cpu・常時1インスタンス）"
echo "  image   : ${IMAGE}"
echo "  invoker : ${RUNTIME_SA}（console のランタイム SA だけが呼べる）"
[[ "${DRY}" == "--dry-run" ]] && echo "  （DRY-RUN: 実際には実行しません）"
echo "=========================================================================="

# 1) Artifact Registry remote repository（冪等）— docker.elastic.co のプロキシ。
if ! gcloud artifacts repositories describe "${AR_REPO}" \
    --project="${PROJECT}" --location="${REGION}" >/dev/null 2>&1; then
  run gcloud artifacts repositories create "${AR_REPO}" \
    --project="${PROJECT}" --location="${REGION}" \
    --repository-format=docker --mode=remote-repository \
    --remote-docker-repo="https://docker.elastic.co" \
    --description="docker.elastic.co proxy (Elasticsearch official images)"
fi

# 2) Elasticsearch を IAM 非公開でデプロイ。
#    - env のデリミタは gcloud の代替記法 ^@^（ES_JAVA_OPTS の値にスペースを含むため）。
#      キー名のドット（discovery.type 等）は ES 公式イメージの設定注入方式そのまま。
#    - xpack.security.enabled=false: 認証・認可は Cloud Run の IAM ゲートに一元化する
#      （ES 自体は Authorization ヘッダを解釈しない）。
#    - --no-cpu-throttling: ES はリクエスト外でもバックグラウンド処理（マージ等）が走る
#      ため CPU 常時割当（min-instances=1 前提の安定化）。
run gcloud run deploy "${ES_SVC}" --project="${PROJECT}" --region="${REGION}" \
  --image="${IMAGE}" \
  --no-allow-unauthenticated --port=9200 \
  --memory=2Gi --cpu=2 --min-instances=1 --max-instances=1 \
  --no-cpu-throttling \
  --execution-environment=gen2 \
  --set-env-vars="^@^discovery.type=single-node@xpack.security.enabled=false@ES_JAVA_OPTS=-Xms1g -Xmx1g@node.store.allow_mmap=false@xpack.ml.enabled=false@ingest.geoip.downloader.enabled=false"
# ↑ Cloud Run で ES を動かす要点: gen2（第1世代は ES が要求する syscall で起動死する）／
#   allow_mmap=false（vm.max_map_count を設定できない環境での必須設定）／
#   ML・geoip downloader 無効（ネイティブプロセス・外部DLは本用途に不要）。

# 3) console のランタイム SA にだけ本サービスへの run.invoker（IAM 非公開のまま呼べる）。
run gcloud run services add-iam-policy-binding "${ES_SVC}" \
  --project="${PROJECT}" --region="${REGION}" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/run.invoker"

ES_URL="$(gcloud run services describe "${ES_SVC}" --project="${PROJECT}" --region="${REGION}" \
  --format='value(status.url)' 2>/dev/null || echo 'https://ES_URL')"

echo
echo "================ 完了 ================"
echo "  ES URL（IAM 非公開・console の SA だけが呼べる）: ${ES_URL}"
echo
echo "  console への配線（有効化）:"
echo "    gcloud run services update ${CONSOLE_SVC} --region ${REGION} --project ${PROJECT} \\"
echo "      --update-env-vars \"HIKESHI_ES_URL=${ES_URL},HIKESHI_ES_AUTH=idtoken\""
echo
echo "  即時ロールバック（既定のローカル BM25-lite 経路へ戻す）:"
echo "    gcloud run services update ${CONSOLE_SVC} --region ${REGION} --project ${PROJECT} \\"
echo "      --remove-env-vars HIKESHI_ES_URL,HIKESHI_ES_AUTH"
echo
echo "  片付け（常時1インスタンスの課金を止める）:"
echo "    gcloud run services delete ${ES_SVC} --region ${REGION} --project ${PROJECT}"
echo
echo "  ※ scripts/deploy.sh は --set-env-vars（全置換）で console を再デプロイするため、"
echo "     deploy.sh 実行後は上の配線コマンドを毎回再適用すること。"
