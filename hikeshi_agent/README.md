# hikeshi_agent — ADK 最小マルチエージェント骨格

`docs/ARCHITECTURE.md §3` の消火フローを ADK 2.x（`google-adk==2.2.0` で検証）で実装した骨格。

```
Triage → Investigate → RAG → Remediate   (SequentialAgent)
                                  └─ IncidentVerdict を構造化出力（output_schema）
```

- `contract.py` … `IncidentVerdict`（出力契約の推論部・閉じた語彙は `incident_bench/schema.py` と一致）
- `tools.py` … 調査/検索ツール（`ToolContext.state['signals']` を読む／検索は実 KB を引く）
- `agent.py` … `triage / investigate / rag / remediate` と `root_agent`（モデルティア・指示・安全境界）
- `retriever.py` … キーワード/BM25-lite リトリーバ（`kb/*.md` を決定的に検索・標準ライブラリのみ）
- `kb/` … 合成サンプル KB（`runbooks/ postmortems/ incidents/`・実顧客データではない＝由来を正直に明示）
- `live.py` … demo の HTTP 観測値 → `signals` 写像（純関数）＋ライブ診断
- `runtime.py` … bench/ライブ共通エンジン `run_signals()`／`run_case(case) -> AgentOutput`（軌跡・¥・秒を計測）。`run_signals_stream()`＝ライブ診断の逐次表示用（同一の `_run_once_stream` を消費＝経路は1本・bench は不使用）

## 実行（Gemini 鍵が必要）

接続は2択（`docs/ARCHITECTURE.md §8`）：

```bash
# (a) dev: AI Studio 無料キー（$0）
export GOOGLE_API_KEY=...          # aistudio.google.com で発行
# (b) prod: Vertex/ADC（$300 クーポン）
export GOOGLE_GENAI_USE_VERTEXAI=1 && gcloud auth application-default login

# モデル id は GA を一次確認して上書き（§13）
export HIKESHI_MODEL_FLASH=...  HIKESHI_MODEL_FLASH_LITE=...  HIKESHI_MODEL_PRO=...

# INCIDENT-BENCH で採点（reference/naive と同じ AgentOutput 契約）
python -m incident_bench.run --agent hikeshi

# 対話で確認
adk web hikeshi_agent      # or: adk run hikeshi_agent
```

鍵が未設定なら `run_case` は**明示的に失敗**する（誠実性規律：黙って偽値を返さない）。
木の構築・`import hikeshi_agent` はオフラインで成立する（テスト `tests/test_agent_skeleton.py` は鍵不要）。

## Agentic RAG（実 KB 検索）

`search_runbook`／`search_past_incidents`／`search_postmortem` は **実 KB を検索**し、取得した
本文（title/excerpt/**source**）で診断を根拠付ける（モデルの創作ではなく**出典つきの根拠**）。
- KB＝`hikeshi_agent/kb/<runbooks|postmortems|incidents>/*.md`（**合成サンプル**・実顧客データではない＝由来を正直に明示）。
- リトリーバ＝`retriever.py`（**キーワード/BM25-lite**・標準ライブラリのみ・オフライン・決定的・日本語は
  CJK バイグラム）。テスト `tests/test_retriever.py`（鍵不要・CI 緑）。
- 本番は同じ `search()` IF の背後を **Vertex Vector Search 2.0（セマンティック）** に差し替え（過大主張せず
  「キーワード検索」を正直に名乗る）。
- **RAG 寄与の計測**：`HIKESHI_RAG=off` で従来の未接続挙動。`--agent hikeshi` を on/off で回して差分を見る。
  例: `HIKESHI_RAG=off python -m incident_bench.run --agent hikeshi --json`。
- RAG 担当は根拠を `参照: <source>` で明示し、`kb_connected=false` のときは「KB未接続」と述べる。

### 計測（RAG を advisory 化して再計測：安全性 1.0 に回復・スコアは off と同等）

**advisory 化前（素朴 RAG）**は on が off をわずかに下回っていた（pass 0.611／safe 0.944 vs off pass 0.722／
safe 1.0）＝近接 Runbook を引いてエッジで判断を乱す例。そこで **RAG を advisory 化**した：rag は出典つき根拠
のみを述べ対処を決めない／remediate に「事例/Runbook は参考＝シグナル・安全境界と矛盾時は後者を優先し、
検索結果を理由に rollback しない」を明記（`agent.py`）。**事前に回数を固定**して再計測（advisory-on ×2＋off ×1）:

実測（18件・temp=0・`gemini-3.5-flash`・advisory 化の導出時＝KB 8 docs 時点）:

| 指標 | advisory-on #1 | advisory-on #2 | off |
|---|---|---|---|
| pass_rate | 0.778 | 0.833 | 0.833 |
| remediation_accuracy | 1.000 | 1.000 | 1.000 |
| **safe_remediation_rate** | **1.000** | **1.000** | 1.000 |
| rca_kw | 0.611 | 0.583 | 0.583 |
| traj | 0.880 | 0.880 | 0.880 |

- advisory 化で safe_remediation_rate は 0.944 から 1.0 に戻り、2 run 連続で安定した。検索結果よりも
  構造化された安全境界が優先されるためである。pass も 0.611 から 0.778–0.833（off と同等以上）に回復し、
  RAG が自身のスコアを下げる問題は解消した。
- **value は接地（出典つき引用）**：advisory RAG は off とスコア同等のまま `参照: <source>` の根拠を残す
  ＝Agentic RAG の本旨（取得本文で根拠付け）と、eval 駆動＋ガードレールを同時に満たす。
- **KB 10 docs（コード調査 runbook＋不可逆ポストモーテム追加後）で再記録（2026-06-10・1 run）**：
  **safe_remediation_rate 1.0・remediation_accuracy 1.0**（code_fix ケース含む全対処が正解）・pass 0.778
  （従来レンジ内）・約¥6.2/件。現在の `incident_bench/recorded/hikeshi_advisory.json` はこの記録＝
  **CI ゲートは現行 KB の実出力を再採点している**。
- **pass_rate は依然 `kw`（substring 代理）依存で run 間に揺れる**ため、**CI ゲートは揺れる pass_rate ではなく
  安定な safe_remediation_rate** を、**記録した実エージェント出力**（`incident_bench/recorded/hikeshi_advisory.json`・
  `--from-recorded`）に対して**決定的に再採点**する（鍵もコストも不要で、CI だけで再現できる）。`kw`→実 LLM-judge は W1。

## 夜回り（Yomawari）— 火の用心（YOJIN・予防）配下の定期巡回

`securitywatch.py` は**汎用 LLM の知識カットオフを超えて**、いま実際に悪用中の最新脆弱性を
**実データで**定期取得し、火元帳(KB)へ advisory 種別として蓄える定期巡回ジョブ。

- **実データのみ・捏造禁止（§20）**。一次情報源は固定 URL のみ（リクエストから URL を受けない＝SSRF 面なし）:
  - **CISA KEV**（Known Exploited Vulnerabilities・無料・鍵不要）＝「実際に悪用中」の確証。
  - **GitHub Advisory**（GHSA・無料・鍵不要）＝公開直後の新規脆弱性。
- **オフライン決定性**：depscan の `osv_cache` と同じ流儀。取得スナップショットを
  `securitywatch_cache/`（`kev.json`／`ghsa.json`／`manifest.json`・top_kev/top_ghsa 件に抑制）へ
  コミットし、`allow_network=False` で決定的に再現。テストはネットに出ない（`tests/test_securitywatch.py`）。
- **KB 生成**：`refresh()` が実データから `kb/advisories/*.md` を決定的に生成（ファイル名・内容が
  同一キャッシュ→同一出力）。各 doc に CVE/GHSA・重大度・要点・出典URL・"実データ（CISA KEV / GHSA）"・
  取得日時（fetched_at）・**合成ではない旨**を明記。retriever は新種別 `advisory` として索引化
  （既存 runbook/postmortem/incident の検索結果は不変＝回帰ガード済み）。
- **防火との接続**：depscan の各 finding に **`actively_exploited`（KEV 突合）** と **`kev_due_date`** を
  additive 付与。KEV に載る CVE を含む依存は「今まさに悪用中」＝最優先で更新（実突合・捏造なし）。

- **定期自動巡回（稼働中）**：`scripts/deploy-cron.sh` が Cloud Scheduler ジョブ `hikeshi-YOMAWARI` を作成し、
  console の `POST /api/securitywatch/refresh` を定期的に叩いて火元帳を最新化する。**ライブデモでは毎朝6時JSTで自動実行**しており、
  無人で最新の脅威インテリを蓄え続ける（手動: `gcloud scheduler jobs run hikeshi-YOMAWARI --location us-central1`）。

```bash
# 一次情報源から取得し KB を更新（要ネット・鍵不要）
python -m hikeshi_agent.securitywatch --refresh
# コミット済みキャッシュの決定的要約（オフライン・鍵不要）
python -m hikeshi_agent.securitywatch --status
```

## Elasticsearch バックエンド（任意・既定 off）

`retriever.py` と同一の公開 IF（`search / list_docs / get_doc / reindex`）を持つ
**Elasticsearch バックエンド**（`retriever_es.py`）を選択できる。目的は**セマンティック検索への
拡張余地とスケール**（多数 doc・複数チーム KB）。**env ゲート・既定 off** なので、
未設定なら既定のローカル BM25-lite 経路（決定的・オフライン・CI/ベンチ安全）は完全不変。

- `HIKESHI_ES_URL`（別名 `ELASTIC_URL`）を設定した時だけ、公開関数の入り口で `retriever_es` へ委譲する。
- 接続失敗・ライブラリ未インストール時は**明確な日本語 RuntimeError**（黙ってローカルへ
  フォールバックしない＝設定ミスを隠さない）。
- index 名は `hikeshi-kb`（`HIKESHI_ES_INDEX` で上書き可。別名 `ELASTIC_INDEX_PREFIX` は
  `<prefix>-kb` として解釈）。
- 認証は2通り: **API key**（既定・`HIKESHI_ES_API_KEY`）と **Google ID トークン**
  （`HIKESHI_ES_AUTH=idtoken`・下記 A の自己ホスト用）。
- 別名（`ELASTIC_URL` / `ELASTIC_API_KEY` / `ELASTIC_INDEX_PREFIX`）は Cloud Run 側で
  先行準備されていた Secret Manager 命名との互換。正本名が設定されていれば正本名が優先。

接続先は2通りあり、**正直に区別する**: **(A) は Elastic Cloud（SaaS）ではない**。公式
Elasticsearch イメージ（`docker.elastic.co`・basic ライセンス）を Cloud Run で自己ホスト
するもので、アカウント作成不要で即日動かせる代わりに、マネージドの永続化・スナップ
ショット・監視は無い。**(B) が Elastic のマネージド SaaS**（アカウント作成が必要）。

### A) 自己ホスト Elasticsearch on Cloud Run（アカウント不要・即日）

```bash
# 1) ES 本体をデプロイ（IAM 非公開・console のランタイム SA にだけ run.invoker を付与）
bash scripts/deploy-es.sh --dry-run   # まず実行計画を確認
bash scripts/deploy-es.sh
# 2) スクリプト完了出力の配線コマンドを実行（console へ env を載せる）
gcloud run services update hikeshi-console --region us-central1 \
  --update-env-vars "HIKESHI_ES_URL=<ES の URL>,HIKESHI_ES_AUTH=idtoken"
# 3) 即時ロールバック（既定のローカル BM25-lite 経路へ戻す）
gcloud run services update hikeshi-console --region us-central1 \
  --remove-env-vars HIKESHI_ES_URL,HIKESHI_ES_AUTH
```

- **認証**: ES サービスは `--no-allow-unauthenticated`（IAM 非公開）。console は
  `HIKESHI_ES_AUTH=idtoken` で Google ID トークン（audience=ES URL のオリジン）を
  `bearer_auth` に載せて呼ぶ。ES 側は `xpack.security.enabled=false` のため、この
  Authorization ヘッダは **Cloud Run の IAM ゲートだけが解釈する**（ES 自身は認証しない）。
- **クライアントは `elasticsearch>=8,<9` に固定**: サーバ 8.x と互換ヘッダを揃える（9.x クライアントは 8.x サーバに 400 で弾かれる）。
- **エフェメラル＋自己修復**: Cloud Run のファイルシステムはインメモリ＝インスタンス
  再起動で index は消える。`retriever_es.py` は `index_not_found` を検知すると、
  コンソールイメージ内の正本 `kb/*.md` から自動 `reindex()` して**1回だけ**再試行する
  （ログに明示・reindex 後も失敗するなら例外を隠さず伝播）。事前の手動索引化は不要。
- **注意**: `scripts/deploy.sh` は `--set-env-vars`（全置換）で console を再デプロイする
  ため、deploy.sh を実行し直したら上記 2) の配線を毎回再適用すること。
- **コスト**: `--min-instances=1`（常時1インスタンス・CPU 常時割当）のため、稼働中は
  課金が続く。使い終わったら `gcloud run services delete hikeshi-es --region us-central1`
  で削除する（index は正本 kb/*.md からいつでも再構築できる＝使い捨てで良い）。

### B) Elastic Cloud（SaaS・アカウント作成が必要）

```bash
# 1) Elastic Cloud (cloud.elastic.co) で最小構成のデプロイを作成
# 2) Kibana → Stack Management → API keys で API key を発行
# 3) env を設定
export HIKESHI_ES_URL="https://<deployment>.es.<region>.gcp.cloud.es.io"
export HIKESHI_ES_API_KEY="<api-key>"
# 4) クライアントを入れて索引化
pip install '.[es]'
python -c "from hikeshi_agent import retriever; print(retriever.reindex(), 'docs indexed')"
```

Cloud Run（コンソール）へ載せる場合は `scripts/deploy.sh` 実行後に載せる。
**注意: `deploy.sh` は `--clear-secrets` でシークレット参照を毎回リセットする**（SA 権限の
無いシークレット参照が残ると以後の全リビジョン作成が `SecretsAccessCheckFailed` で失敗する
事故が実際に起きたため）。有効化はデプロイ後に毎回、明示的に行う:

```bash
# 1) シークレット作成（初回のみ。API key 側も同様に）
printf '%s' "$ES_URL" | gcloud secrets create elastic-url --data-file=-
printf '%s' "$ES_API_KEY" | gcloud secrets create elastic-api-key --data-file=-
# 2) ランタイム SA に読み取り権限（初回のみ・シークレット単位で付与）
for s in elastic-url elastic-api-key; do
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:hikeshi-run@hikeshi-demo.iam.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor
done
# 3) サービスへ載せる（deploy.sh の後に毎回）
gcloud run services update hikeshi-console --region us-central1 \
  --update-secrets "HIKESHI_ES_URL=elastic-url:latest,HIKESHI_ES_API_KEY=elastic-api-key:latest"
```

（console イメージには `elasticsearch` クライアントが入っている＝`console/requirements.txt`。
ローカルから使う場合は `pip install '.[es]'` を実行する。）

#### 料金目安と運用注意（Elastic Cloud）

- Elastic Cloud Hosted（Standard）は**最小構成で $99/月〜**（構成・リージョンで変動）。
  出典: [Elastic Cloud Hosted pricing](https://www.elastic.co/pricing/cloud-hosted)（確認日 2026-07-10）。
- 最小構成（1 zone・最小 RAM）なら低コストに収まるが、ノード増強・ストレージ追加で
  変動するため、料金ページの見積もりを事前確認すると良い。
- Hosted は稼働時間で課金されるため、使わない期間はデプロイを削除して構わない。削除しても
  `reindex()` で kb/*.md からいつでも再構築できる＝index は使い捨てで良い。

## 未了（次段）

- 本番 KB＝**Vertex Vector Search 2.0** へ（組織の実 Runbook/ポストモーテムを索引化）
- `adk eval` 用 evalset（INCIDENT-BENCH cases → evalset JSON 変換）
- Release（配信ガード）／Postmortem（事後）エージェント追加
