# ポストモーテム: checkout-svc デプロイ起因 5xx（rev 2）

（合成サンプル KB・実データではない）

## 概要
- rev 2 のデプロイ 3 分後に error_rate が 18%、p95 が 1900ms に悪化。リクエストハンドラ変更の
  リグレッション（deploy_regression）。データ破損はなく可逆。

## 対応
- 直近デプロイ起因かつ可逆と判断し、**即時ロールバック（rollback）**。MTTR 約 4 分で回復。

## 学び
- デプロイ直後の異常はカナリア＋自動ロールバック（repairRolloutRule）で検知・復旧を短縮できる。
- self degraded ＆ upstream ok ＆ recent_deploy あり、は deploy_regression の強いシグナル。
