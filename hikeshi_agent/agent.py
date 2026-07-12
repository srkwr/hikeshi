"""Hikeshi 最小マルチエージェント骨格（ADK 2.x・実APIに対して検証済み）。

構成（docs/ARCHITECTURE.md §3 の消火フロー）：
  Triage → Investigate → RAG → Remediate を SequentialAgent で直列化。
  Remediate が IncidentVerdict を構造化出力（output_schema）する。
  ブリーフにはアラートのみ載せ、事実はツールで取りに行かせる＝実調査の軌跡が出る。

モデル id は env で上書き可（§13 の GA 確認に追従）。鍵は実行時のみ必要＝
本モジュールの import / 木の構築はオフラインで成立する。
"""

from __future__ import annotations

import os

from google.adk.agents import LlmAgent, SequentialAgent
from google.genai import types as genai_types

from .contract import IncidentVerdict
from .tools import INVESTIGATION_TOOLS, SEARCH_TOOLS, TRIAGE_TOOLS

# 3段ルーティング（docs/ARCHITECTURE.md §8）。既定は Vertex registry で検証済みの id（§13）。
# env で上書き可。再現性のため -latest エイリアスは使わず固定 id を既定にする。
MODEL_FLASH_LITE = os.environ.get("HIKESHI_MODEL_FLASH_LITE", "gemini-3.1-flash-lite")
MODEL_FLASH = os.environ.get("HIKESHI_MODEL_FLASH", "gemini-3.5-flash")
MODEL_PRO = os.environ.get("HIKESHI_MODEL_PRO", "gemini-3.1-pro-preview")  # 最難RCA用に予約（現状未割当・3.5 Pro GA待ち）

# 決定的な eval（再現性）。温度0で run-to-run のブレを抑える。
_GEN_CFG = genai_types.GenerateContentConfig(temperature=0.0)

triage = LlmAgent(
    name="triage",
    model=MODEL_FLASH_LITE,
    description="重大度・影響範囲の初期判定。",
    instruction=(
        "あなたは Triage 担当。read_metrics と read_logs を呼んで症状を把握し、"
        "重大度（影響範囲・緊急度）を1〜2文で要約せよ。対処は決めない。"
    ),
    tools=list(TRIAGE_TOOLS),
    generate_content_config=_GEN_CFG,
    output_key="triage",
)

investigate = LlmAgent(
    name="investigate",
    model=MODEL_FLASH,
    description="ログ/メトリクス/トレース横断の事実収集（ツール駆動）。",
    instruction=(
        "あなたは Investigate 担当。詳細はメッセージに無いので**必ずツールを呼んで**事実を集める。\n"
        "手順：(1) read_logs と read_metrics で症状・エラーを確認（ログ優先）。"
        "(2) list_revisions・read_recent_deploy・diff_revision で直近リビジョンと変更差分を確認。"
        "(3) 症状に応じて深掘り：\n"
        "  ・外部5xx/503/タイムアウト/書込失敗 → read_trace と check_dependency_status\n"
        "  ・CPU/接続数/FD/QPS の逼迫・飽和 → check_quota\n"
        "  ・設定/環境変数の誤り（タイムアウト値・レプリカ数・SMTP 等）→ read_config\n"
        "  ・キャッシュ/CDN/stale/スキーマ不整合 → check_cache\n"
        "収集した事実（リビジョン番号・エラーコード・設定キー・コンポーネント名などの固有値を保持）と、"
        "根本原因の手がかりを箇条書きで述べよ。\nTriage 要約: {triage?}"
    ),
    tools=list(INVESTIGATION_TOOLS),
    generate_content_config=_GEN_CFG,
    output_key="investigation",
)

rag = LlmAgent(
    name="rag",
    model=MODEL_FLASH_LITE,
    description="過去事例・Runbook・ポストモーテムのハイブリッド検索。",
    instruction=(
        "あなたは RAG 担当。search_runbook・search_past_incidents・search_postmortem を"
        "症状に応じて呼び、返ってきた results（title/excerpt/source）の要点を**出典つきの根拠として**述べよ。"
        "**述べるのは根拠（事実・参照）のみ。対処種別（remediation_type）や rollback の可否は決めない**"
        "＝判定は Remediate が安全境界に従って行う。"
        "述べた根拠がどの Runbook/事例に基づくかを『参照: <source>』の形で必ず明示する。"
        "kb_connected=false の場合は『KB未接続』と述べ、検索結果のない推測の根拠を作らない（§20）。\n"
        "調査結果: {investigation?}"
    ),
    tools=list(SEARCH_TOOLS),
    generate_content_config=_GEN_CFG,
    output_key="evidence",
)

remediate = LlmAgent(
    name="remediate",
    model=MODEL_FLASH,
    description="根本原因の確定と対処判定（構造化出力）。",
    instruction=(
        "あなたは Remediate 担当。収集情報から根本原因カテゴリと推奨対処を確定し、"
        "IncidentVerdict を出力せよ。\n"
        "■ 対処種別の決定規則：\n"
        "  ・deploy_regression（直近デプロイ起因）：可逆（コード不具合でデータ破損なし）→ rollback／"
        "二重処理・残高不整合などデータ不整合で不可逆 → code_fix\n"
        "  ・config_error（設定・環境変数の誤り）→ config_fix\n"
        "  ・dependency_failure（外部依存の障害）→ runbook_mitigation\n"
        "  ・resource_exhaustion（リソース枯渇）：容量不足（QPS増・FD枯渇等）→ scale／"
        "直近デプロイのメモリリークが原因で可逆 → rollback\n"
        "  ・data_cache_issue（キャッシュ/データ不整合）→ cache_flush\n"
        "■ 事例/Runbook（下記 {evidence?}）は**参考情報（advisory）**：診断の接地に使ってよいが、"
        "シグナル（メトリクス/ログ/トレース）や下記の安全境界と矛盾する場合は**シグナル・安全境界を優先**せよ。"
        "検索結果に過去の rollback 事例があっても、それを理由に rollback を選んではならない。\n"
        "■ 安全境界（最重要・厳守）：自動許容は『前リビジョンへのトラフィック切替＝rollback』のみ。\n"
        "  ・remediation_type を rollback にするのは『直近デプロイ起因かつ可逆』と確信できる時だけ。"
        "設定/依存/キャッシュ/容量不足/データ不整合には絶対に rollback を選ばない。\n"
        "  ・rollback 以外、または不可逆（書込済みデータ等）の対処は requires_hitl=true。"
        "rollback のみ requires_hitl=false を許容。\n"
        "■ root_cause_text：固有名・数値（リビジョン番号・エラーコード・設定キー・コンポーネント名・"
        "飽和したリソースや現象を表す語等）を**3つ以上**必ず含める。\n"
        "■ evidence（根拠）：判定を支える**観測事実を2〜4件**、各 {fact, source} で出力する。"
        "fact は具体値（例『error_rate 18%・p95 1900ms（rev 42 デプロイ3分後）』）、source は"
        "**実際に呼んだ調査ツール名（read_metrics/read_logs/diff_revision 等）か検索した KB の出典**。"
        "上の調査結果・事例に**実在する事実だけ**を引く＝得ていない値は書かない（§20 捏造禁止）。\n"
        "■ remediation_plan（対処手順）：推奨対処の**具体手順を順序付き2〜5ステップ**で。各手順は"
        "『何をするか・対象範囲・検証方法』を1文で具体化（例『前リビジョン rev 41 へ traffic を 100% 切替』"
        "『/metrics の error_rate < 1% を3分間確認』）。これは**提案であり自動実行はしない**（実行は人間承認後）。\n"
        "■ 文体：**事実先行・簡潔**。『〜と思われます／可能性があります』等の曖昧表現や前置きは使わず、"
        "観測値に基づき断定的に書く。冗長な説明より具体値。\n"
        "Triage: {triage?}\n調査: {investigation?}\n事例/Runbook: {evidence?}"
    ),
    output_schema=IncidentVerdict,
    generate_content_config=_GEN_CFG,
    output_key="verdict",
    # output_schema 利用時は委譲を禁止（単一の構造化生成にする）
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)

root_agent = SequentialAgent(
    name="hikeshi",
    description="Hikeshi 消火フロー：Triage→Investigate→RAG→Remediate。",
    sub_agents=[triage, investigate, rag, remediate],
)
