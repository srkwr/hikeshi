"""INCIDENT-BENCH の型定義。

外部から読み込む incident ケースは `from_dict` で fail-fast 検証し、
壊れたデータ（未知のカテゴリ・難易度・対処種別）を早期に弾く。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "incident-bench/v0"

DIFFICULTIES = ["easy", "medium", "hard"]

# 根本原因のカテゴリ（v0・拡張時は SCHEMA_VERSION を上げる）
ROOT_CAUSE_CATEGORIES = [
    "deploy_regression",    # 直近デプロイの不具合
    "config_error",         # 設定・環境変数の誤り
    "resource_exhaustion",  # メモリ/CPU 枯渇・OOM
    "dependency_failure",   # 外部依存（下流API等）の障害
    "data_cache_issue",     # データ/キャッシュ不整合
]

# 対処の種類。auto_remediable=True で自動許容されるのは
# 「前リビジョンへのトラフィック切替=rollback」のみ（安全境界）。
# それ以外は人間承認(HITL)前提。
REMEDIATION_TYPES = [
    "rollback",
    "config_fix",
    "scale",
    "runbook_mitigation",
    "cache_flush",
    "code_fix",
]


def _require(value: Any, allowed: list[str], field_name: str, ctx: str) -> str:
    """value が allowed に含まれることを保証し、外れていれば文脈付きで即エラー。"""
    if value not in allowed:
        raise ValueError(f"{ctx}: invalid {field_name}={value!r} (expected one of {allowed})")
    return value


def _key(d: dict[str, Any], k: str, ctx: str) -> Any:
    """必須キーを取り出す。欠落は KeyError でなく文脈付き ValueError に統一（fail-fast を一貫）。"""
    if k not in d:
        raise ValueError(f"{ctx}: missing required field {k!r}")
    return d[k]


@dataclass
class GroundTruth:
    root_cause_category: str
    root_cause_keywords: list[str]
    expected_tool_trajectory: list[str]
    recommended_remediation_type: str
    is_reversible: bool
    auto_remediable: bool

    @staticmethod
    def from_dict(d: dict[str, Any], ctx: str = "<case>") -> GroundTruth:
        return GroundTruth(
            root_cause_category=_require(
                _key(d, "root_cause_category", ctx),
                ROOT_CAUSE_CATEGORIES,
                "root_cause_category",
                ctx,
            ),
            root_cause_keywords=list(_key(d, "root_cause_keywords", ctx)),
            expected_tool_trajectory=list(_key(d, "expected_tool_trajectory", ctx)),
            recommended_remediation_type=_require(
                _key(d, "recommended_remediation_type", ctx),
                REMEDIATION_TYPES,
                "recommended_remediation_type",
                ctx,
            ),
            is_reversible=bool(_key(d, "is_reversible", ctx)),
            auto_remediable=bool(_key(d, "auto_remediable", ctx)),
        )


@dataclass
class IncidentCase:
    id: str
    title: str
    difficulty: str  # easy | medium | hard
    signals: dict[str, Any]
    ground_truth: GroundTruth
    tags: list[str] = field(default_factory=list)
    provenance: str = "synthetic"  # cases/*.json は全て合成データ（実顧客データではない）

    @staticmethod
    def from_dict(d: dict[str, Any]) -> IncidentCase:
        cid = str(_key(d, "id", "<case>"))
        difficulty = _require(_key(d, "difficulty", cid), DIFFICULTIES, "difficulty", cid)
        return IncidentCase(
            id=cid,
            title=_key(d, "title", cid),
            difficulty=difficulty,
            signals=d.get("signals", {}),
            ground_truth=GroundTruth.from_dict(_key(d, "ground_truth", cid), ctx=cid),
            tags=list(d.get("tags", [])),
            provenance=str(d.get("provenance", "synthetic")),
        )


@dataclass
class AgentOutput:
    """エージェント（または stub）の出力契約。実 ADK エージェントもこの形に揃える。"""

    case_id: str
    root_cause_text: str
    root_cause_category: str
    tool_trajectory: list[str]
    remediation_type: str
    confidence: float          # 0..1
    requires_hitl: bool = True
    cost_yen: float = 0.0
    latency_ms: int = 0
    # 表示用: 調査ツール呼び出しをサブエージェント別に並べた詳細 [{"agent","tool"}, ...]。
    # 採点には使わない（採点は flat な tool_trajectory）。bench stub は空のまま。
    tool_trajectory_detail: list[dict[str, str]] = field(default_factory=list)
    # 表示用: 各サブエージェントの実推論テキスト {triage, investigation, evidence}。採点不使用。
    reasoning: dict[str, str] = field(default_factory=dict)
    # 表示用: 判定の根拠 [{"fact","source"}] と具体的対処手順 [str]。採点不使用（加算フィールド）。
    evidence: list[dict[str, str]] = field(default_factory=list)
    remediation_plan: list[str] = field(default_factory=list)


@dataclass
class CaseScore:
    case_id: str
    difficulty: str
    rca_category_match: bool
    rca_keyword_recall: float
    trajectory_score: float
    remediation_match: bool
    safe_remediation: bool
    passed: bool
    confidence: float
    cost_yen: float
    latency_ms: int


@dataclass
class BenchReport:
    n: int
    pass_count: int
    pass_rate: float
    avg_trajectory: float
    avg_rca_keyword_recall: float
    remediation_accuracy: float
    safe_remediation_rate: float
    total_cost_yen: float
    avg_latency_ms: float
    per_case: list[CaseScore]
