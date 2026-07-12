"""エージェントの構造化出力契約（INCIDENT-BENCH AgentOutput の「推論部」）。

`tool_trajectory` / `cost_yen` / `latency_ms` / `case_id` はランタイムが付与する
（モデルが生み出す値ではない）。ここはモデルが推論で埋める部分だけを定義し、
閉じた語彙は incident_bench/schema.py と一致させる（テストで突き合わせる）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RootCauseCategory = Literal[
    "deploy_regression",
    "config_error",
    "resource_exhaustion",
    "dependency_failure",
    "data_cache_issue",
]

RemediationType = Literal[
    "rollback",
    "config_fix",
    "scale",
    "runbook_mitigation",
    "cache_flush",
    "code_fix",
]


class EvidenceItem(BaseModel):
    """根拠1件＝『事実』とその『出典』。出典は実際に呼んだ調査ツール名か検索した KB の `参照:`。"""

    fact: str = Field(description="観測された具体的事実（数値・リビジョン・エラーコード・ログ行など）")
    source: str = Field(
        description="その事実の出典＝呼んだツール名（read_metrics/read_logs/diff_revision 等）"
        "または検索した KB（『参照: <source>』）。捏造禁止＝実際に得た情報のみ。"
    )


class IncidentVerdict(BaseModel):
    """Remediate エージェントが必ず生成する構造化判定。"""

    root_cause_text: str = Field(description="根本原因の簡潔な説明")
    root_cause_category: RootCauseCategory = Field(description="根本原因カテゴリ")
    remediation_type: RemediationType = Field(description="推奨する対処種別")
    confidence: float = Field(ge=0.0, le=1.0, description="確信度 0..1")
    requires_hitl: bool = Field(
        description=(
            "人間承認が必要か。安全境界：自動許容は rollback のみ。"
            "rollback 以外、または不可逆な対処は必ず true。"
        )
    )
    evidence: list[EvidenceItem] = Field(
        default_factory=list,
        description="判定を支える根拠（事実＋出典）を2〜4件。調査/検索で実際に得た事実のみ。",
    )
    remediation_plan: list[str] = Field(
        default_factory=list,
        description=(
            "推奨対処を実行する具体手順（順序付き2〜5ステップ）。各手順は『何をするか・対象範囲・"
            "検証方法』を1文で具体的に。例:『前リビジョン(rev N-1)へ traffic を 100% 切替』『/metrics の "
            "error_rate < 1% を3分確認』。提案であり自動実行はしない（実行は人間承認後）。"
        ),
    )
