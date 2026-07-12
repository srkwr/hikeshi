"""console のポストモーテム配線テスト（オフライン・鍵不要・demo は MockTransport で偽装）。

審査で指摘されたライフサイクル境界を固定する：
  - 復旧前の draft は 409
  - recovery は demo が実際に対処を適用した（"applied" を返す）ときだけ記録（捏造防止＝§20）
  - fault 無しの recovered:true（2回目の recover）で本物の記録を上書きしない
  - 診断と復旧の incident_id が一致しない場合は 409（混成ドラフト防止）
  - approve は curated doc を上書きしない（create-only）
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 公開モードのレート制限はこのテストの対象外＝無効化（制限自体の検証は test_console_views.py）。
os.environ["HIKESHI_LLM_MIN_INTERVAL_S"] = "0"
os.environ["HIKESHI_LLM_DAILY_CAP"] = "0"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import console.app as capp  # noqa: E402
import hikeshi_agent.postmortem as pm  # noqa: E402

_RECOVER_RESPONSES: list[dict] = []
_FAULT_ACTIVE: str | None = None  # /metrics・/admin/state が返す fault（stream テスト用）


def _demo_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.startswith("/admin/inject/"):
        return httpx.Response(200, json={"injected": path.rsplit("/", 1)[-1], "state": {}})
    if path == "/admin/recover":
        return httpx.Response(200, json=_RECOVER_RESPONSES.pop(0))
    if path == "/metrics":
        return httpx.Response(200, json={"fault_active": _FAULT_ACTIVE, "error_rate": 0.18,
                                         "latency_p95_ms": 1900, "revision": 2})
    if path == "/admin/state":
        return httpx.Response(200, json={"fault": _FAULT_ACTIVE, "revision": 2})
    if path == "/healthz":
        return httpx.Response(200, json={"status": "ok"})
    return httpx.Response(404, json={})


def test_diagnose_stream_offline():
    """SSE 配線の決定的テスト（鍵不要）：no_fault と error（認証未設定）の両イベント経路。"""
    capp._client = httpx.Client(transport=httpx.MockTransport(_demo_handler), timeout=5)
    client = TestClient(capp.app)

    # 鍵を確実に外す（run_signals_stream は最初のイテレーションで RuntimeError → error イベント）
    saved = {k: os.environ.pop(k, None) for k in ("GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI")}
    try:
        # fault なし → no_fault イベント1つ
        global _FAULT_ACTIVE
        _FAULT_ACTIVE = None
        r = client.post("/api/diagnose/stream")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert '"type": "no_fault"' in r.text or '"type":"no_fault"' in r.text.replace(" ", "")

        # fault あり＋鍵なし → error イベント（偽の verdict を流さない＝§20）
        _FAULT_ACTIVE = "deploy_regression"
        r = client.post("/api/diagnose/stream")
        assert r.status_code == 200
        assert '"type": "error"' in r.text
        assert "Gemini 認証が未設定" in r.text
        assert '"type": "verdict"' not in r.text
    finally:
        _FAULT_ACTIVE = None
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_diagnose_stream_success_contract():
    """成功経路の SSE 契約（鍵不要＝runtime スタブ）：phase/retry/verdict の順序と中身。"""
    import json as _json

    import hikeshi_agent.runtime as rt
    from incident_bench.schema import AgentOutput

    out = AgentOutput(
        case_id="live", root_cause_text="rev 2 起因", root_cause_category="deploy_regression",
        tool_trajectory=["read_metrics"], remediation_type="rollback", confidence=0.9,
        requires_hitl=False, cost_yen=5.0, latency_ms=20000,
        tool_trajectory_detail=[{"agent": "triage", "tool": "read_metrics"}],
        reasoning={"triage": "t"},
    )

    def _stub(signals, case_id, brief, max_attempts=3):
        yield ("phase", {"phase": "triage", "reasoning": "重大度: 高", "tools": ["read_metrics"]})
        yield ("retry", {"attempt": 2, "max_attempts": 3})
        yield ("phase", {"phase": "rag", "reasoning": "参照: deploy-regression.md",
                         "tools": ["search_runbook"]})
        yield ("output", out)

    orig = rt.run_signals_stream
    rt.run_signals_stream = _stub
    try:
        capp._client = httpx.Client(transport=httpx.MockTransport(_demo_handler), timeout=5)
        client = TestClient(capp.app)
        global _FAULT_ACTIVE
        _FAULT_ACTIVE = "deploy_regression"
        r = client.post("/api/diagnose/stream")
        assert r.status_code == 200
        evs = [_json.loads(ln[6:]) for ln in r.text.split("\n") if ln.startswith("data: ")]
        assert [e["type"] for e in evs] == ["phase", "retry", "phase", "verdict"]
        # rag phase は KB 接続状態を同送
        assert evs[2]["phase"] == "rag" and isinstance(evs[2]["kb_connected"], bool)
        v = evs[3]
        assert v["no_fault"] is False and v["remediation_type"] == "rollback"
        assert v["expected_remediation"] == "rollback"  # 答え合わせは診断後にのみ開示
        for k in ("cost_yen", "latency_ms", "kb_connected", "model", "reasoning"):
            assert k in v, f"verdict event missing {k}"
        assert capp._last_incident.get("verdict", {}).get("remediation_type") == "rollback"
    finally:
        rt.run_signals_stream = orig
        _FAULT_ACTIVE = None
        capp._last_incident.clear()


def test_postmortem_wiring():
    capp._client = httpx.Client(transport=httpx.MockTransport(_demo_handler), timeout=5)
    client = TestClient(capp.app)
    capp._last_incident.clear()

    # 1) 復旧まで完了していなければ 409
    assert client.post("/api/postmortem/draft").status_code == 409

    # 2) 注入 → injected_ts が立つ
    assert client.post("/api/inject/deploy_regression").status_code == 200
    iid = capp._last_incident["injected_ts"]

    # 3) 診断は実 LLM のため、diagnose() が書くのと同形の verdict を直接置く
    capp._last_incident["verdict"] = {
        "root_cause_text": "rev 2 のデプロイ起因", "root_cause_category": "deploy_regression",
        "remediation_type": "rollback", "confidence": 0.9, "tool_trajectory": ["read_metrics"],
        "cost_yen": 5.0, "latency_ms": 20000, "incident_id": iid,
    }

    # 4) demo が "applied" を返す復旧 → 記録される
    _RECOVER_RESPONSES.append({"recovered": True, "applied": "rollback", "message": "復旧しました"})
    assert client.post("/api/recover", params={"action": "rollback"}).status_code == 200
    rec = capp._last_incident["recovery"]
    assert rec["applied"] == "rollback" and rec["incident_id"] == iid

    # 5) fault 無しの recovered:true（applied なし）では上書きしない（捏造防止）
    _RECOVER_RESPONSES.append({"recovered": True, "message": "no active fault"})
    assert client.post("/api/recover", params={"action": "scale"}).status_code == 200
    assert capp._last_incident["recovery"] is rec  # 同一オブジェクト＝未上書き

    # 6) draft 成功＝実記録の値が入る
    r = client.post("/api/postmortem/draft")
    assert r.status_code == 200
    assert "rev 2 のデプロイ起因" in r.json()["markdown"]

    # 7) incident_id 不一致なら 409（混成ドラフト防止）
    capp._last_incident["verdict"]["incident_id"] = -1
    assert client.post("/api/postmortem/draft").status_code == 409
    capp._last_incident["verdict"]["incident_id"] = iid

    # 8) approve は create-only（curated を上書きしない）— KB は一時 dir に差し替え
    orig = pm._KB_POSTMORTEMS
    try:
        with tempfile.TemporaryDirectory() as d:
            pm._KB_POSTMORTEMS = Path(d)
            (Path(d) / "curated.md").write_text("# 原本\n", encoding="utf-8")
            r = client.post("/api/postmortem/approve",
                            json={"markdown": "# 承認テスト\n\nx", "filename": "curated.md"})
            assert r.status_code == 200 and r.json()["source"] == "curated-2.md"
            assert (Path(d) / "curated.md").read_text(encoding="utf-8") == "# 原本\n"
    finally:
        pm._KB_POSTMORTEMS = orig
    capp._last_incident.clear()


if __name__ == "__main__":
    test_diagnose_stream_offline()
    test_diagnose_stream_success_contract()
    test_postmortem_wiring()
    print("console postmortem wiring tests: OK")
