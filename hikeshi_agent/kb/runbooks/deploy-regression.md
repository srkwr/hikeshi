# Runbook: デプロイ起因のエラー急騰（デプロイ・リグレッション）

（合成サンプル KB・実データではない）

## 症状
- 新リビジョンのデプロイ直後に error_rate（HTTP 500 internal error）が急騰。
- p95 レイテンシが悪化。self_health が degraded、upstream_status は ok（外部依存は健全）。
- recent_deploy が数分前に存在し、それ以外の最近の変更（設定・依存）はない。

## 根本原因
- 直近デプロイのコード不具合（リグレッション）。category = deploy_regression。

## 推奨対処
- **直近デプロイ起因かつ可逆（コード不具合でデータ破損なし）→ ロールバック（rollback）**。
  前リビジョンへトラフィックを切り替え、MTTR 短縮を最優先。rollback は自動許容の対象。
- 二重決済・残高不整合などデータ書込で**不可逆**な場合は rollback せず **code_fix ＋ 補償**。要 HITL。

## 注意
- 設定誤り・外部依存障害・容量不足にはロールバックは効かない（別 Runbook を参照）。
