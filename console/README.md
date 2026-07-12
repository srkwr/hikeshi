# Hikeshi 運用コンソール（HITL 承認 UI ＋ agent↔demo 配線）

当番1人が承認1クリックで復旧する流れを、1つのシステムで一通り動かせる最小コンソール。
`demo/`（victim service）と `hikeshi_agent/`（実 ADK マルチエージェント）を繋ぎ、
インシデントの一気通貫ループを成立させる：

```
障害注入 → Hikeshi が demo を HTTP 観測して診断 → 人間が提案カードを承認(1クリック) → 実際に復旧
 (/admin/inject)   (/metrics・/healthz → 実エージェント)        (HITL)            (/admin/recover)
```

実エージェント（Triage→Investigate→RAG→Remediate）が demo の `/metrics`・`/healthz` を
HTTP で観測し、`IncidentVerdict`（原因／確信度／対処種別／可逆性）を構造化出力。それを
**提案カード**として描画し、**Approve** で `demo/admin/recover` を叩いて実復旧する。

## UI（一画面 reasoning-feed・単一HTML / 依存ゼロの自作 SVG チャート）

一画面（100vh・ページスクロールなし）。エージェントの**実推論を縦の背骨**に、左右に状態・タイムライン・提案カードを配する。
- **ヘルスバナー**：状態・経過秒・リビジョン・直近 MTTR。
- **メトリクス時系列カード**：エラー率・p95 レイテンシ・メモリ使用率を閾値線つきスパークラインで描画し、
  **閾値超過は朱でハイライト＋「異常」**（障害注入でスパイクが出る）。
- **推論フィード（reasoning-feed）**：実エージェントの推論（`reasoning`＝triage/investigation/evidence）と
  ツール呼び出し（`tool_trajectory_detail`・author 別）を Triage→Investigate→RAG→Remediate の**4フェーズ・レーン**に
  整形して縦に流す。RAG レーンは既定で **デモKB（合成・キーワード検索）の参照（出典つき）** を表示（`HIKESHI_RAG=off` で「KB未接続」を明示）。
- **インシデント・タイムライン**：検知→診断→提案→承認→復旧を時刻・経過つきで記録（MTTR が出る）。
- **提案カード**：推奨対処・確信度・実コスト/レイテンシ/ツール数・**答え合わせ（期待対処）**・Approve。
- 配色はブランド（生成り/墨/朱）。アクセシビリティ（コントラスト・focus-visible・`prefers-reduced-motion`）配慮。

### コックピット・ページ（左ナビ・江戸の火消しの役割名＋元の語を併記・表示値はすべて実データ）
ビュー切替は可視性トグルのみ（既存 DOM を unmount しない＝SSE 診断中の切替も安全）。各ページの値に出所がある（表示値はすべて実データ）：
- **半鐘（サービス＝被害状況・影響範囲）**：demo の生 `/metrics`（実観測値の素通し）＋ **シグナル契約 §5.1 の透視**＝
  同じ観測に純関数 `signals_from_demo()` を適用した「診断時にエージェントへ渡る入力」のプレビュー
  （`GET /api/signals`・診断と同一関数）。安全境界（実装済み）と §5.2 ロードマップを物理的に分けて表示。
- **火の見櫓（メトリクス＝発見・監視）**：コンソールを開いてから収集した**実ポーリング値のみ**の時系列
  （実タイムスタンプ軸・補間/ノイズなし・バックフィルなし）。閾値線は「コンソールの検知表示用」と明示
  （エージェントの判定基準ではない）。
- **火元帳（Runbook＝KB・ポストモーテム）**：実 KB ブラウザ（`GET /api/kb/list|doc`・索引済み source 名への
  完全一致のみ＝トラバーサル不可）＋**エージェントと同一の `retriever.search()`**（`GET /api/kb/search`・
  スコア＝tf-idf 生値）。承認ポストモーテムが即時にここへ現れる（自己改善ループの可視化）。
- **火事場帳（履歴）**：サーバ側の**追記専用台帳**（`GET /api/incidents`）。注入／診断要約（¥・秒・確信度）／**承認試行
  （成功も拒否も両方記録**＝「誤った対処では復旧しない」安全境界の実記録）／うだつ（隔離）の `contained_ts`／
  注入→復旧のサーバ実測 duration／防火スキャン実行。承認者は捏造しない（「コンソール経由・個人識別なし」を明示）。
  インシデント採番（INC-xxxx）の単一の出所もこの台帳（`/api/status` の `incident`）。

## 安全境界（ライブで実演）
demo は**正しい対処のときだけ復旧**する。誤った対処（例：外部依存障害に盲目的ロールバック）は
復旧を拒否し、正しい対処を返す＝**判断の重要性が画面に出る**。鍵が無い／診断が失敗したときは
偽の値を返さず 503（憶測・偽値フォールバック禁止）。ストリーム版（`/api/diagnose/stream`）も偽値は流さず
`error` イベントで明示する（HTTP は 200・SSE のため）。

## 実行（ローカル・2 プロセス）
```bash
# 3.12 venv 推奨。依存:
pip install -r console/requirements.txt        # fastapi/uvicorn/httpx/google-adk

# 1) victim service（別ターミナル）
python demo/app.py                              # → http://localhost:8080

# 2) 運用コンソール（Vertex/ADC で診断する場合）
GOOGLE_GENAI_USE_VERTEXAI=1 GOOGLE_CLOUD_PROJECT=hikeshi-demo GOOGLE_CLOUD_LOCATION=global \
  python console/app.py                         # → http://localhost:8081
#   AI Studio 無料キーなら: GOOGLE_API_KEY=... python console/app.py
#   トークン運用を試すなら: HIKESHI_CONSOLE_TOKEN=<token> を付けると未認証アクセスがアンロック画面になる
```
ブラウザで http://localhost:8081 を開き、「障害を注入」→「Hikeshi に診断させる」→「承認して復旧」。
demo の URL は `HIKESHI_DEMO_URL`（既定 `http://localhost:8080`）、ポートは `PORT`（既定 8081）で上書き可。
**公開モード（既定）**：`HIKESHI_CONSOLE_TOKEN` 未設定＝トークン不要で誰でも閲覧可。代わりに LLM コスト経路
（`/api/diagnose`・`/api/diagnose/stream`・`/api/depscan`）に**アプリ層レート制限**がかかる：
最小間隔 `HIKESHI_LLM_MIN_INTERVAL_S`（既定 12 秒）＋日次上限 `HIKESHI_LLM_DAILY_CAP`（既定 300 回）。
超過は 429（日本語 detail）。env に `0` を設定すると無効化（テスト/ローカル用）。
KB 書き込み経路（`/api/postmortem/approve`）にも同方式の制限がかかる：
`HIKESHI_KB_WRITE_MIN_INTERVAL_S`（既定 10 秒）＋`HIKESHI_KB_WRITE_DAILY_CAP`（既定 30 回）＝
公開モードでの KB 大量注入を防ぐ（create-only・件数/サイズ上限と多層防御）。
通知発信（`POST /api/notify`・半鐘）は運用者が `HIKESHI_NOTIFY_WEBHOOK` を設定した時だけ有効
（未設定は 409 で正直に断る＝デモでは省略可）。発信にも同方式の制限：
`HIKESHI_NOTIFY_MIN_INTERVAL_S`（既定 30 秒）＋`HIKESHI_NOTIFY_DAILY_CAP`（既定 20 回）＝通知先を溢れさせない。
`HIKESHI_CONSOLE_TOKEN` を設定すれば従来どおり `/api/*` が共有トークン必須に戻る（互換維持・このとき KB 書き込み制限は認証が前段のため掛からない）。

## API
| メソッド/パス | 役割 |
|---|---|
| `GET /` | 単一ページ UI（`static/index.html`） |
| `GET /api/status` | demo の `state`/`metrics`/`health` を集約（demo 停止時も UI は生きる） |
| `POST /api/inject/{scenario}` | demo へ障害注入（`deploy_regression`/`dependency_failure`/`resource_exhaustion`） |
| `POST /api/diagnose` | demo を観測 → `signals` 写像 → 実エージェントで診断 → `IncidentVerdict`＋実ツール軌跡＋¥＋秒 |
| `POST /api/diagnose/stream` | 診断の SSE 版（`text/event-stream`）。サブエージェント完了ごとに `phase`（実推論＋実ツール）/`retry` を流し、最後に `verdict`（`/api/diagnose` と同一 JSON）/`no_fault`/`error` |
| `POST /api/webhook/alert` | アラート連動の受け口（PagerDuty/Alertmanager 等が叩く線）。本文は信用せず demo の実状態を再観測して診断＝`/api/diagnose` と同一結果 |
| `POST /api/contain` | **うだつ（延焼防止・隔離）**：demo の `/admin/contain` へプロキシ。fault は残したままユーザ向け影響の悪化だけ止める（degraded 安定値・完全復旧は `/api/recover`）。台帳の該当インシデント行に `contained_ts` を実記録。fault 無しは 400 |
| `POST /api/notify` | **半鐘（通知発信）**：demo を実観測した現在状態（fault／封じ込め／error_rate／p95／継続秒＝台帳 `injected_ts` 起点）＋台帳の直近診断要約（verdict がある時のみ）を日本語テキストにサーバ側で決定的に組み立て、Slack 互換 `{"text": …}` を webhook へ POST。**通知先は運用者が env `HIKESHI_NOTIFY_WEBHOOK` で固定設定**（リクエストから URL は受けない＝SSRF 面なし）。未設定は **409** で正直に断る（デモでは省略可）・送信失敗は 502。成功・失敗とも台帳に `hansho_notify` を記録。レート制限：`HIKESHI_NOTIFY_MIN_INTERVAL_S`（既定 30 秒）＋`HIKESHI_NOTIFY_DAILY_CAP`（既定 20 回）・超過 429・env=0 で無効 |
| `POST /api/recover?action=…` | HITL 承認後の対処を demo に適用（正しい対処のみ復旧） |
| `POST /api/postmortem/draft` | 直近の「診断→承認→復旧」の**実記録から**ポストモーテム案を決定的に生成（復旧前は 409） |
| `POST /api/postmortem/approve` | 人間が編集・承認した案を `kb/postmortems/` に保存→**即時再索引**（次の診断から検索対象） |
| `POST /api/depscan` | **防火**：合成サンプル manifest を OSV.dev で実スキャン→LLM 安全性評価→更新提案（**実CVE・HITL・提案のみ**・自動更新しない） |
| `GET /api/signals` | **シグナル契約の透視**：診断と同一の観測＋同一の純関数 `signals_from_demo()` の出力（生 `/metrics` も併載・LLM 不要） |
| `GET /api/kb/list`・`GET /api/kb/doc?source=…` | KB ブラウザ（実ファイルのみ・索引済み source 名への完全一致＝パストラバーサル不可） |
| `GET /api/kb/search?q=…` | **エージェントと同一の** `retriever.search()`（スコア＝tf-idf 生値・決定的） |
| `GET /api/incidents` | セッション内の**追記専用台帳**（注入／診断要約／承認試行＝成功・拒否とも／復旧 duration／防火イベント＋集計） |

既定は**公開モード**（`HIKESHI_CONSOLE_TOKEN` 未設定＝トークン不要・誰でも閲覧可）。LLM コスト経路（`/api/diagnose`・`/api/diagnose/stream`・`/api/depscan`）はアプリ層レート制限（既定：12 秒間隔＋日次 300 回・超過 429）で保護する。`HIKESHI_CONSOLE_TOKEN` を設定すると従来どおり `GET /`（UI）以外の `/api/*` が `Authorization: Bearer <token>` 必須に戻り、フロントは 401 を受けてアンロック画面を出す（互換維持）。

## 夜回り（YOMAWARI）— 脅威インテリの定期巡回
CISA KEV（いま実際に悪用中の脆弱性）と GitHub Advisory を定期取得し、火元帳(KB)へ advisory 種別として蓄える。
`GET /api/securitywatch`（現況）／`POST /api/securitywatch/refresh`（取得→再索引）。取得は固定の一次情報源のみ・実データのみ。
**Cloud Scheduler で定期巡回**（`scripts/deploy-cron.sh`・ジョブ `hikeshi-YOMAWARI`）。ライブデモでは毎朝6時JSTで自動実行し、無人で鮮度を保つ。

## 自己改善ループ（ポストモーテム→KB・HITL）
復旧完了後に「ポストモーテム案を生成」→ **実際の診断記録**（原因・対処・実測コスト/レイテンシ/ツール数・注入→復旧秒）から決定的に起草（LLM の創作なし＝実記録のみ）→ 人間が**編集・承認したものだけ** KB に着地 → 即時再索引＝**次の診断から検索対象**。使うたびに KB が増えるので、Runbook が陳腐化するという運用課題にそのまま効く。
- ファイル名はサーバ側で slug 化（パストラバーサル不可）・provenance 行を強制・サイズ上限あり。
- **正直な注記**：Cloud Run のファイルシステムはエフェメラル＝承認 KB はインスタンス寿命のみ（UI にも明示）。永続化（GCS／Vector Search 2.0 upsert）は roadmap。

## 配線の要点
- demo の HTTP 観測値は [`hikeshi_agent/live.py`](../hikeshi_agent/live.py) の `signals_from_demo()`（**純関数**・
  オフラインテスト [`tests/test_live_signals.py`](../tests/test_live_signals.py)）で、調査ツールが読む `signals`
  形へ写像する。診断は bench と同じ共通エンジン `runtime.run_signals()` を通る＝**同じ調査軌跡**が出る。
- 診断は実 LLM（Vertex/Gemini）を呼ぶ。実測例（deploy_regression・1 件・RAG 導入前の初期計測）：`rollback`
  提案・確信度 0.95・実ツール 12 回・**¥5.3／35.8s**（temp=0）。bench 全 18 件・**RAG on/off 比較**は
  [`hikeshi_agent/README.md`](../hikeshi_agent/README.md)。

## 注意（誠実性）
- **RAG は実 KB を検索（既定 on）**：`search_*` は `hikeshi_agent/kb/`（**合成サンプル**・キーワード/BM25-lite）
  を引き、UI に **デモKB** バッジと **`参照:` 出典**を表示する（モデルの創作ではなく取得本文で根拠付け）。
  `HIKESHI_RAG=off` で「KB未接続」に戻る。本番は同一 `search()` IF の背後を **Vector Search 2.0** に差し替え
  （過大主張せず「キーワード検索」を正直に名乗る）。なお小ベンチでは naive RAG が採点をわずかに下げる実測あり
  （価値は接地／[`hikeshi_agent/README.md`](../hikeshi_agent/README.md)）。
- `signals_from_demo()` が作る `logs_sample`/`trace_sample`/`diff_summary` は、demo の `/metrics` が実際に出す値
  （5xx・新リビジョン・飽和）から**決定的に導出した記述**で、demo が暗示しない固有値は作らない（値の捏造禁止）。実トレース/
  差分などの独立観測ソース接続はクラウド移行時の課題。
- 注入ボタンは**症状のみ**を表示し、「期待対処」は診断**後**に答え合わせとして出す（デモが答えを先に渡さない）。
- **公開とコスト防御**：console は**公開UI（トークン不要）**が既定。LLM コスト経路はアプリ層レート制限
  （最小間隔＋日次上限・超過 429）で守り、`HIKESHI_CONSOLE_TOKEN` を設定すれば共有トークン運用へ戻せる
  （互換維持）。console→demo は Cloud Run の IAM 非公開＋**Google ID トークン**（audience=demo URL）で
  接続（ローカルは `HIKESHI_DEMO_TOKEN` の静的共有トークンでも可・未設定なら無認証＝ローカルデモ前提）。
  Cloud Run へは [`scripts/deploy.sh`](../scripts/deploy.sh)（demo=IAM 非公開・console=公開＋レート制限）。
