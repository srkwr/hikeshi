# Hikeshi デモ KB（Agentic RAG 用・合成サンプル）

`search_runbook` / `search_past_incidents` / `search_postmortem` ツールが実検索する
ナレッジベース。`runbooks/`・`postmortems/`・`incidents/` の Markdown を
`hikeshi_agent/retriever.py`（キーワード/BM25-lite・オフライン）が索引化し、診断を根拠付ける。

**provenance（由来の明示）**：本 KB は**合成のサンプル**であり、実顧客データではありません。
本番では同じ `search()` インターフェースの背後を **Vertex Vector Search 2.0（セマンティック検索）**
に差し替え、組織の実 Runbook/ポストモーテムを索引化します（ここでは「キーワード検索」を正直に名乗る）。

**収録範囲**：5カテゴリの対処 Runbook に加え、**コード起因の調査**（diff の読み方・不可逆変更の
見分け方・`code_fix` の進め方＝`runbooks/code-investigation.md`）と、その実例ポストモーテム
（`postmortems/billing-deploy-irreversible.md`＝「デプロイ起因＝即ロールバックではない」）を含む。

**更新のしかた**：
1. 手動＝Markdown を追加し `retriever.reindex()`（または再起動）で即反映。
2. **ポストモーテム→KB 提案ループ（HITL）**＝console で復旧完了後にドラフトを生成→人間が編集・
   承認したものだけが `postmortems/` に着地→即時再索引（Cloud Run ではインスタンス寿命のみ＝
   永続化は GCS/Vector Search が roadmap）。
3. **規律**：KB はカテゴリ・パターン水準で書く。INCIDENT-BENCH ケース固有の識別子（リビジョン
   番号・固有キーワード）は写さない＝ベンチへの混入（training on the test set）を避ける。
