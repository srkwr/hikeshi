# Runbook: リソース枯渇・飽和（resource exhaustion）

（合成サンプル KB・実データではない）

## 症状
- p95 レイテンシ悪化、memory_pct 高騰（OOM 兆候）、CPU/接続数/FD/QPS の飽和。
- 負荷の増加。直近デプロイがなければメモリリーク由来ではない。

## 根本原因
- 容量不足（負荷増・QPS 増・FD 枯渇など）。category = resource_exhaustion。

## 推奨対処
- **スケールアウト（scale）**：レプリカ/インスタンスを増やして容量を追加。要 HITL。
- 直近デプロイのメモリリークが原因で**可逆**なら rollback。

## 注意
- 設定のレプリカ数/上限値の誤りが原因なら config_fix（設定修正）。
