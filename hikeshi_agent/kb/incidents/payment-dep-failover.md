# 過去事例: 外部決済 API の 5xx — ロールバック無効、フェイルオーバーで回復

（合成サンプル KB・実データではない）

## 症状
- upstream_status 5xx、self_health は ok。直近デプロイなし。決済 API が広域障害。

## 試行と解決
- 最初に rollback を試したが**無効**（自分のコードは正常）。
- 決済プロバイダの**フェイルオーバー（runbook_mitigation）**に切替え、サーキットブレーカ作動で回復。

## 教訓
- dependency_failure に rollback は効かない。緩和策（フェイルオーバー/縮退）が正解。
