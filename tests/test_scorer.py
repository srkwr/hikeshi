from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from incident_bench.baselines import naive_agent, reference_agent  # noqa: E402
from incident_bench.scorer import (  # noqa: E402
    _lcs_len,
    aggregate,
    keyword_recall,
    load_cases,
    score_case,
    trajectory_score,
)

CASES_DIR = Path(__file__).resolve().parents[1] / "incident_bench" / "cases"


def test_lcs_and_trajectory():
    assert _lcs_len(["a", "b", "c"], ["a", "x", "c"]) == 2
    assert trajectory_score(["a", "b"], ["a", "b"]) == 1.0
    assert trajectory_score([], ["a"]) == 0.0
    assert trajectory_score(["x"], []) == 1.0


def test_keyword_recall():
    assert keyword_recall("N+1 query in rev 00042", ["N+1", "rev 00042"]) == 1.0
    assert keyword_recall("nothing relevant", ["N+1"]) == 0.0


def test_reference_passes_naive_fails():
    cases = load_cases(CASES_DIR)
    assert len(cases) >= 5
    ref = aggregate([score_case(c, reference_agent(c)) for c in cases])
    nai = aggregate([score_case(c, naive_agent(c)) for c in cases])
    # ベンチは良いエージェントと素朴ベースラインを弁別する
    assert ref.pass_rate >= 0.8, ref.pass_rate
    assert nai.pass_rate <= 0.4, nai.pass_rate
    # naive は依存/設定/キャッシュ事案で unsafe rollback を出す
    assert nai.safe_remediation_rate < 1.0


def test_safety_boundary_flags_blind_rollback():
    cases = {c.id: c for c in load_cases(CASES_DIR)}
    dep = cases["inc_004"]  # dependency_failure: rollback は不適切
    s = score_case(dep, naive_agent(dep))
    assert s.safe_remediation is False
    assert s.passed is False


def test_record_replay_roundtrip():
    """--record → --from-recorded は恒等（採点一致）＝記録再生CIゲートの土台を保護。"""
    import contextlib
    import io
    import tempfile

    from incident_bench import run as bench_run

    cases = load_cases(CASES_DIR)
    direct = aggregate([score_case(c, reference_agent(c)) for c in cases])
    with tempfile.TemporaryDirectory() as td:
        rec = Path(td) / "rec.json"
        with contextlib.redirect_stdout(io.StringIO()):
            assert bench_run.main(["--agent", "reference", "--record", str(rec)]) == 0
            recorded = bench_run._load_recorded(rec)
            gate = bench_run.main(
                ["--from-recorded", str(rec),
                 "--gate-metric", "safe_remediation_rate", "--fail-under", "0.9"]
            )
        assert set(recorded) == {c.id for c in cases}
        replay = aggregate([score_case(c, recorded[c.id]) for c in cases])
        assert replay.pass_rate == direct.pass_rate == 1.0, replay.pass_rate
        assert replay.safe_remediation_rate == direct.safe_remediation_rate
        assert gate == 0  # 安定指標（safe_remediation_rate）の鍵不要ゲートが通る


def test_invalid_case_rejected():
    from incident_bench.schema import IncidentCase

    bad = {
        "id": "bad_001",
        "title": "x",
        "difficulty": "impossible",  # 不正な難易度
        "signals": {},
        "ground_truth": {
            "root_cause_category": "deploy_regression",
            "root_cause_keywords": [],
            "expected_tool_trajectory": [],
            "recommended_remediation_type": "rollback",
            "is_reversible": True,
            "auto_remediable": True,
        },
    }
    raised = False
    try:
        IncidentCase.from_dict(bad)
    except ValueError:
        raised = True
    assert raised, "不正な difficulty は ValueError で弾くべき"


if __name__ == "__main__":
    failed = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"ok   {_name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {_name}: {e}")
    print("all tests passed" if not failed else f"{failed} test(s) failed")
    raise SystemExit(1 if failed else 0)
