"""ADK 骨格の構造テスト（Gemini 鍵不要・CI で常時緑）。

木が組める／出力契約が schema.py と一致する／鍵が無ければ run_case が
明示的に失敗する、をオフラインで検証する（モデルは呼ばない）。
"""

import os
import sys
import typing

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_tree_builds():
    from hikeshi_agent.agent import root_agent

    assert root_agent.name == "hikeshi"
    assert [a.name for a in root_agent.sub_agents] == [
        "triage",
        "investigate",
        "rag",
        "remediate",
    ]


def test_remediate_emits_structured_verdict():
    from hikeshi_agent.agent import remediate
    from hikeshi_agent.contract import IncidentVerdict

    assert remediate.output_schema is IncidentVerdict
    assert remediate.output_key == "verdict"
    # output_schema 利用時は委譲を禁止していること
    assert remediate.disallow_transfer_to_parent is True
    assert remediate.disallow_transfer_to_peers is True


def test_verdict_vocab_matches_bench_contract():
    from hikeshi_agent.contract import IncidentVerdict
    from incident_bench.schema import REMEDIATION_TYPES, ROOT_CAUSE_CATEGORIES

    f = IncidentVerdict.model_fields
    required = (
        "root_cause_text", "root_cause_category", "remediation_type",
        "confidence", "requires_hitl",
    )
    for k in required:
        assert k in f, f"missing field {k}"
    cats = set(typing.get_args(f["root_cause_category"].annotation))
    rems = set(typing.get_args(f["remediation_type"].annotation))
    assert cats == set(ROOT_CAUSE_CATEGORIES), cats
    assert rems == set(REMEDIATION_TYPES), rems


def test_run_case_requires_credentials():
    # 鍵が無ければ run_case は RuntimeError（黙って偽値を返さない＝§20）
    from hikeshi_agent import runtime
    from incident_bench.schema import GroundTruth, IncidentCase

    saved = {k: os.environ.pop(k, None) for k in ("GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI")}
    try:
        case = IncidentCase(
            id="t",
            title="t",
            difficulty="easy",
            signals={},
            ground_truth=GroundTruth("deploy_regression", [], [], "rollback", True, True),
        )
        raised = False
        try:
            runtime.run_case(case)
        except RuntimeError as e:
            raised = True
            assert "Gemini" in str(e)
        assert raised, "run_case should fail fast without credentials"
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_tools_cover_expected_trajectories():
    """ツール集合が全ケースの expected_tool_trajectory を網羅する（網羅しないと
    trajectory スコアが構造的に頭打ち＝missing-tools バグの再発防止・実際に起きた）。"""
    from hikeshi_agent.tools import INVESTIGATION_TOOLS, SEARCH_TOOLS, TRIAGE_TOOLS
    from incident_bench.run import _DEFAULT_CASES
    from incident_bench.scorer import load_cases

    have = {f.__name__ for f in [*INVESTIGATION_TOOLS, *SEARCH_TOOLS, *TRIAGE_TOOLS]}
    needed: set = set()
    for c in load_cases(_DEFAULT_CASES):
        needed |= set(c.ground_truth.expected_tool_trajectory)
    missing = needed - have
    assert not missing, f"tools missing for expected trajectories: {missing}"


def test_cost_attribution():
    """cost 換算: author のティア価格でトークンを USD 換算し、未知 author は flash 上限寄り。"""
    from hikeshi_agent.runtime import _event_cost_usd

    toks = {"prompt_token_count": 1000, "candidates_token_count": 500, "thoughts_token_count": 0}
    um = type("UM", (), toks)()
    mk = lambda author: type("E", (), {"usage_metadata": um, "author": author})()  # noqa: E731
    assert abs(_event_cost_usd(mk("remediate")) - 0.006) < 1e-9  # flash 1.5/9.0
    assert abs(_event_cost_usd(mk("triage")) - 0.001) < 1e-9  # flash-lite 0.25/1.5
    assert _event_cost_usd(type("E", (), {"usage_metadata": None, "author": "x"})()) == 0.0


if __name__ == "__main__":
    test_tree_builds()
    test_remediate_emits_structured_verdict()
    test_verdict_vocab_matches_bench_contract()
    test_run_case_requires_credentials()
    test_tools_cover_expected_trajectories()
    test_cost_attribution()
    print("agent skeleton tests: OK")
