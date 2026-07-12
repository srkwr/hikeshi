"""demo のうだつ（`POST /admin/contain`＝延焼防止・隔離）のテスト（オフライン・鍵不要）。

固定する境界：
  - fault 無しの contain は 400（偽の封じ込め状態を作らない）
  - contained 中も fault は残る＝healthz・recover 判定のセマンティクスは不変
  - contained でユーザ向け影響の悪化が止まる（error_rate/5xx が degraded の安定値に落ち、
    正常値には戻らない＝完全復旧には正しい対処が必要）
  - inject／正しい対処での recover 成功で contained はリセットされる
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import demo.app as dapp  # noqa: E402

_BASELINE_ERROR_RATE = 0.002
_BASELINE_P95_MS = 180


def _reset() -> None:
    dapp.STATE.update(revision=1, fault=None, since=None, contained=False)


def test_contain_requires_active_fault():
    """fault 無しの contain は 400＝封じ込め対象が無いのに contained を立てない。"""
    client = TestClient(dapp.app)
    _reset()
    assert client.post("/admin/contain").status_code == 400
    assert dapp.STATE["contained"] is False
    assert client.get("/metrics").json()["contained"] is False


def test_contain_stops_spread_but_fault_remains():
    """全シナリオ：延焼停止（悪化が止まる）・fault 残存・安全境界と healthz は不変。"""
    client = TestClient(dapp.app)
    for scenario, spec in dapp.SCENARIOS.items():
        _reset()
        assert client.post(f"/admin/inject/{scenario}").status_code == 200
        before = client.get("/metrics").json()
        assert before["contained"] is False

        r = client.post("/admin/contain")
        assert r.status_code == 200
        body = r.json()
        assert body["contained"] is True and body["fault"] == scenario
        assert body["state"]["contained"] is True

        m = client.get("/metrics").json()
        assert m["contained"] is True
        assert m["fault_active"] == scenario  # fault はアクティブなまま（延焼だけ止まる）
        assert client.get("/admin/state").json()["contained"] is True
        if scenario == "deploy_regression":
            # 新リビジョン遮断＝5xx が止まり error_rate は半減水準で安定（正常値ではない）
            assert _BASELINE_ERROR_RATE < m["error_rate"] < before["error_rate"]
            assert client.get("/work").json() == {"ok": True, "degraded": True}
        elif scenario == "dependency_failure":
            # サーキットブレーカ開＝5xx 連鎖停止・フォールバック応答。上流は壊れたまま
            assert _BASELINE_ERROR_RATE < m["error_rate"] < before["error_rate"]
            assert m["upstream_status"] == "5xx"
            assert client.get("/work").json()["fallback"] is True
        elif scenario == "resource_exhaustion":
            # 流入制限＝飽和の進行が止まる（高止まり＝正常値ではない）
            assert _BASELINE_P95_MS < m["latency_p95_ms"] < before["latency_p95_ms"]
            assert m["memory_pct"] < before["memory_pct"]

        # healthz セマンティクス不変：dependency_failure 以外は fault が残る限り 503
        expected_health = 200 if scenario == "dependency_failure" else 503
        assert client.get("/healthz").status_code == expected_health

        # 安全境界不変：誤った対処は contained 中も復旧しない（contained も解除しない）
        wrong = "rollback" if scenario != "deploy_regression" else "scale"
        out = client.post("/admin/recover", params={"action": wrong}).json()
        assert out["recovered"] is False
        assert client.get("/admin/state").json()["contained"] is True

        # 正しい対処で完全復旧＝fault も contained もリセット
        out = client.post("/admin/recover", params={"action": spec["correct_remediation"]}).json()
        assert out["recovered"] is True
        st = client.get("/admin/state").json()
        assert st["fault"] is None and st["contained"] is False
        assert client.get("/metrics").json()["error_rate"] == _BASELINE_ERROR_RATE
    _reset()


def test_inject_resets_contained():
    """新しい障害の注入は封じ込め状態を引き継がない（contained リセット）。"""
    client = TestClient(dapp.app)
    _reset()
    assert client.post("/admin/inject/deploy_regression").status_code == 200
    assert client.post("/admin/contain").status_code == 200
    assert client.get("/metrics").json()["contained"] is True
    assert client.post("/admin/inject/dependency_failure").status_code == 200
    m = client.get("/metrics").json()
    assert m["contained"] is False and m["fault_active"] == "dependency_failure"
    _reset()


if __name__ == "__main__":
    test_contain_requires_active_fault()
    test_contain_stops_spread_but_fault_remains()
    test_inject_resets_contained()
    print("demo contain (うだつ=延焼防止) tests: OK")
