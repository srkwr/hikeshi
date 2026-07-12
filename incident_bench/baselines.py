from __future__ import annotations

from collections.abc import Callable

from .schema import AgentOutput, IncidentCase

# v0 は LLM を使わず、エージェントの「振る舞いの代理」を stub で与える。
# 後で reference を実 ADK エージェント呼び出しに差し替える（同じ AgentOutput 契約）。

_COST_BY_DIFFICULTY = {"easy": 18.0, "medium": 45.0, "hard": 90.0}
_LATENCY_BY_DIFFICULTY = {"easy": 4200, "medium": 6800, "hard": 9500}
_CONF_BY_DIFFICULTY = {"easy": 0.90, "medium": 0.80, "hard": 0.68}


def reference_agent(case: IncidentCase) -> AgentOutput:
    """オラクル上限ベースライン（ground_truth に近い値を返す＝"良いエージェントの上限"の基準点）。

    hard では調査を1手取りこぼすが、ほぼ正解なので CI の厳密ゲートではない（落ちない）。
    実エージェントの実力検証は `--agent hikeshi`（実 LLM・Vertex）で行う。
    """
    gt = case.ground_truth
    traj = list(gt.expected_tool_trajectory)
    if case.difficulty == "hard" and len(traj) > 1:
        traj = traj[:-1]
    return AgentOutput(
        case_id=case.id,
        root_cause_text="推定根本原因: " + " / ".join(gt.root_cause_keywords),
        root_cause_category=gt.root_cause_category,
        tool_trajectory=traj,
        remediation_type=gt.recommended_remediation_type,
        confidence=_CONF_BY_DIFFICULTY[case.difficulty],
        requires_hitl=not gt.auto_remediable,
        cost_yen=_COST_BY_DIFFICULTY[case.difficulty],
        latency_ms=_LATENCY_BY_DIFFICULTY[case.difficulty],
    )


def naive_agent(case: IncidentCase) -> AgentOutput:
    """素朴ベースライン: とりあえずデプロイのせいにして浅い調査でロールバック。

    依存障害/設定/キャッシュ事案では unsafe_remediation や浅い trajectory で落ちる
    ＝ベンチが「良いエージェント」と弁別できることを示す対照群。
    """
    return AgentOutput(
        case_id=case.id,
        root_cause_text="直近のデプロイが原因と推定",
        root_cause_category="deploy_regression",
        tool_trajectory=["read_logs", "list_revisions"],
        remediation_type="rollback",
        confidence=0.5,
        requires_hitl=False,
        cost_yen=12.0,
        latency_ms=3000,
    )


def hikeshi_agent(case: IncidentCase) -> AgentOutput:
    """実 ADK マルチエージェント（Triage→Investigate→RAG→Remediate）。

    遅延 import で adk 依存を reference/naive に持ち込まない。Gemini 鍵が必要
    （未設定なら明示的に失敗＝§20）。詳細は hikeshi_agent/README.md。
    """
    from hikeshi_agent.runtime import run_case

    return run_case(case)


AGENTS: dict[str, Callable[[IncidentCase], AgentOutput]] = {
    "reference": reference_agent,
    "naive": naive_agent,
    "hikeshi": hikeshi_agent,
}
