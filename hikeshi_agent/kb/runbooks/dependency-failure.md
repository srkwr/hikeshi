# Runbook: 外部依存の障害連鎖（dependency failure）

（合成サンプル KB・実データではない）

## 症状
- error_rate 上昇、upstream_status が 5xx、self_health は ok（自分は健全・下流が死んでいる）。
- 直近デプロイなし。トレースに downstream dependency → 5xx のスパン。

## 根本原因
- 外部依存（下流 API / DB / IdP / 決済プロバイダ）の障害が連鎖。category = dependency_failure。

## 推奨対処
- **ロールバックは無効**（自分のコードは正常なので前リビジョンに戻しても直らない）。
- **Runbook 緩和策（runbook_mitigation）**：フェイルオーバー、サーキットブレーカ作動、リトライ抑制、
  縮退運転（機能フラグで該当機能を一時停止）。**要 HITL**。

## 注意
- 盲目的ロールバックは復旧しないどころか MTTR を浪費する（典型的なアンチパターン）。
