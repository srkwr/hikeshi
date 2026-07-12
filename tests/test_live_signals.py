"""ライブ signals アダプタの構造テスト（ネットワーク/LLM/adk 非依存・CI で常時緑）。

`signals_from_demo` が demo の `/metrics` 形を、調査ツールが読む signals 形へ
正しく写像することをオフラインで検証する。入力は demo/app.py `_metrics()` の出力を
忠実に再現したもの（demo を変えたらここも合わせる＝契約のドリフト検知は console スモークで）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hikeshi_agent.live import _HEALTHY_NOTE, signals_from_demo  # noqa: E402

# demo/app.py の _metrics() を各 fault で再現（基準値は正常時の値）。
_DEPLOY = {
    "revision": 2, "error_rate": 0.18, "latency_p95_ms": 1900, "memory_pct": 45,
    "upstream_status": "ok", "self_health": "degraded", "fault_active": "deploy_regression",
    "recent_deploy": {"revision": 2, "deployed_min_ago": 3},
}
_DEPENDENCY = {
    "revision": 1, "error_rate": 0.16, "latency_p95_ms": 180, "memory_pct": 45,
    "upstream_status": "5xx", "self_health": "ok", "fault_active": "dependency_failure",
    "note": "downstream dependency returning 5xx; our service healthy",
}
_RESOURCE = {
    "revision": 1, "error_rate": 0.002, "latency_p95_ms": 2400, "memory_pct": 96,
    "upstream_status": "ok", "self_health": "degraded", "fault_active": "resource_exhaustion",
    "note": "memory/CPU saturation under load",
}
_HEALTHY = {
    "revision": 1, "error_rate": 0.002, "latency_p95_ms": 180, "memory_pct": 45,
    "upstream_status": "ok", "self_health": "ok", "fault_active": None,
}

# 調査ツールが state['signals'] から読むキー（tools.py と一致）。
_REQUIRED_KEYS = {"alert", "metrics", "recent_deploy", "logs_sample", "trace_sample",
                  "diff_summary", "context"}


def _has_required_keys(sig):
    missing = _REQUIRED_KEYS - set(sig)
    assert not missing, f"signals missing keys: {missing}"


def test_all_scenarios_have_tool_readable_shape():
    for m in (_DEPLOY, _DEPENDENCY, _RESOURCE, _HEALTHY):
        sig = signals_from_demo(m)
        _has_required_keys(sig)
        # 観測値は素通し（read_metrics/check_quota が読む）。ただし正解ラベル fault_active
        # （根本原因カテゴリの enum 文字列）だけは除去＝bench の signals と同型・診断は症状から。
        expected = {k: v for k, v in m.items() if k != "fault_active"}
        assert sig["metrics"] == expected
        assert "fault_active" not in sig["metrics"]
        assert isinstance(sig["logs_sample"], list)


def test_deploy_regression_points_at_recent_deploy():
    sig = signals_from_demo(_DEPLOY)
    # 直近デプロイが read_recent_deploy/list_revisions に出る＝rollback 判定の根拠
    assert sig["recent_deploy"].get("revision") == 2
    assert sig["recent_deploy"].get("deployed_min_ago") == 3
    assert any("revision" in s or "rev" in s for s in sig["logs_sample"])
    assert "2" in sig["diff_summary"]  # rev 番号が差分要約に出る
    assert sig["alert"]


def test_dependency_failure_surfaces_upstream_5xx_not_deploy():
    sig = signals_from_demo(_DEPENDENCY)
    # 外部依存障害：トレースに 5xx が出て、直近デプロイは空（＝盲目 rollback を誘発しない）
    assert "5xx" in sig["trace_sample"]
    assert sig["recent_deploy"] == {}
    assert any("dependency" in s or "5xx" in s for s in sig["logs_sample"])


def test_resource_exhaustion_surfaces_saturation_no_deploy():
    sig = signals_from_demo(_RESOURCE)
    assert sig["metrics"]["memory_pct"] == 96  # check_quota が読む飽和の証拠
    assert sig["recent_deploy"] == {}  # 直近デプロイなし＝rollback でなく scale へ
    saturated = any("saturation" in s or "飽和" in s for s in sig["logs_sample"])
    assert saturated or "飽和" in sig["context"]


def test_healthy_state_is_benign():
    sig = signals_from_demo(_HEALTHY)
    assert sig["alert"] == _HEALTHY_NOTE
    assert sig["logs_sample"] == []
    assert sig["recent_deploy"] == {}


if __name__ == "__main__":
    test_all_scenarios_have_tool_readable_shape()
    test_deploy_regression_points_at_recent_deploy()
    test_dependency_failure_surfaces_upstream_5xx_not_deploy()
    test_resource_exhaustion_surfaces_saturation_no_deploy()
    test_healthy_state_is_benign()
    print("live signals tests: OK")
