# Hikeshi デモ: 障害注入 victim service

Hikeshi（消火エージェント）が監視・対処する「やられ役」の最小サービス。
**壊す → `/metrics`・`/healthz` が劣化 →（エージェントが診断 → 運用コンソール `console/` で人間が承認＝HITL）→ 正しい対処で復旧** をライブで見せるためのもの。

## 実行（ローカル）
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r demo/requirements.txt
python demo/app.py                       # → http://localhost:8080  (PORT で変更可)
# または: uvicorn demo.app:app --port 8080 --reload
```

## 障害シナリオ3本（INCIDENT-BENCH のカテゴリ／対処に対応）
| inject キー | category | 正しい対処 | 挙動 |
|---|---|---|---|
| `deploy_regression` | deploy_regression | **rollback** | 新リビジョン後に5xx・レイテンシ急騰（self unhealthy） |
| `dependency_failure` | dependency_failure | **runbook_mitigation** | 外部依存が5xx・self は healthy（**rollback では直らない**） |
| `resource_exhaustion` | resource_exhaustion | **scale** | メモリ/負荷上昇でレイテンシ悪化 |

## デモの流れ（curl 例）
```bash
curl localhost:8080/healthz                                           # ok
curl -XPOST localhost:8080/admin/inject/dependency_failure            # 障害注入
curl localhost:8080/metrics                                           # error_rate↑ / upstream=5xx / self=ok
curl -XPOST "localhost:8080/admin/recover?action=rollback"            # ← 盲目的 rollback は復旧しない
curl -XPOST "localhost:8080/admin/recover?action=runbook_mitigation"  # ← 正しい対処で復旧
curl localhost:8080/healthz                                           # ok
```
`/admin/recover` は **正しい対処のときだけ復旧**する設計＝「診断が合っても対処を誤ると直らない」を実演（INCIDENT-BENCH の安全境界と同じ思想）。

### うだつ（`/admin/contain`＝延焼防止・隔離）
町家の防火袖壁「うだつ」に由来する**封じ込め**。fault はアクティブなまま、
ユーザ向け影響（5xx・error_rate）の悪化だけを止め、`/metrics` は degraded の**安定値**に落ち着く
（値の捏造ではなく状態機械の分岐＝トラフィック遮断／サーキットブレーカ／流入制限を適用した状態を返す）：
- `deploy_regression`：新リビジョンへのトラフィックを停止した体＝5xx が半減して安定
- `dependency_failure`：サーキットブレーカ開＝上流呼び出しを遮断しフォールバック応答（上流は 5xx のまま）
- `resource_exhaustion`：流入制限（load shedding）＝飽和の進行が止まる（高止まり）

正常値には戻らない＝**完全復旧には正しい対処（`/admin/recover`）が必要**。fault 無しの contain は 400。
`/metrics`・`/admin/state` に `"contained": bool` が載り、`/admin/inject`・`/admin/recover` 成功でリセットされる。
`healthz`・recover 判定のセマンティクスは不変。
```bash
curl -XPOST localhost:8080/admin/inject/dependency_failure
curl -XPOST localhost:8080/admin/contain            # 延焼停止（error_rate が安定値へ・fault は残る）
curl localhost:8080/metrics                          # "contained": true / upstream は 5xx のまま
curl -XPOST "localhost:8080/admin/recover?action=runbook_mitigation"  # 完全復旧＋contained 解除
```

> **認証**：`/admin/*`（状態を変える経路）は `HIKESHI_DEMO_TOKEN` 設定時に `Authorization: Bearer <token>` 必須。
> 未設定なら無認証（上記 curl はローカル＝無認証前提）。クラウドでは Cloud Run を **IAM 非公開**にし、呼び出し元
> （console）が **Google ID トークン**を添付＝プラットフォーム層で全経路を保護する。`/metrics`・`/healthz` は監視
> シグナルなので app 層では公開（IAM 非公開時はプラットフォームが保護）。

## エンドポイント
| method | path | 用途 |
|---|---|---|
| GET | `/` `/work` | 通常業務（障害時は失敗/遅延） |
| GET | `/healthz` | ヘルスチェック（200/503） |
| GET | `/metrics` | エージェントが読むシグナル（JSON） |
| GET | `/admin/state` | 現在の障害/リビジョン状態 |
| POST | `/admin/inject/{scenario}` | 障害注入（`contained` はリセット） |
| POST | `/admin/contain` | **うだつ（延焼防止・隔離）**：fault は残したまま影響悪化を停止（fault 無しは 400） |
| POST | `/admin/recover?action=...` | HITL 承認後の対処（正しい対処で復旧・成功で `contained` 解除） |

## クラウド（Cloud Run）
demo は **IAM 非公開**でデプロイし、console だけが ID トークンで呼ぶ。
両サービスを一括で出すには [`scripts/deploy.sh`](../scripts/deploy.sh)（demo=非公開・console=公開UI（トークン不要）＋アプリ層レート制限）。
demo 単体なら:
```bash
gcloud run deploy hikeshi-demo --source ./demo --region us-central1 \
  --min-instances=0 --max-instances=2 --no-allow-unauthenticated
```
ゼロスケール（`--min-instances=0`）でアイドル時はほぼ $0。`./demo` は `demo/Dockerfile` でビルドされる。
