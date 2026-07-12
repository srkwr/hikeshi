"""半鐘（通知発信）/api/notify のテスト（オフライン・webhook は httpx.post を差し替え）。

固定する境界：
  - 通知先は env HIKESHI_NOTIFY_WEBHOOK のみ（リクエスト body の URL は無視＝SSRF 面なし）
  - 未設定は 409（黙って成功を装わない）・台帳にも書かない（送信を試みていない事実のまま）
  - 送信成功は {"sent": true}＋台帳 hansho_notify ok:true。本文は実観測値・台帳実記録のみ
    から決定的に組み立てる（fault/error_rate/p95/継続秒/直近診断・§20）
  - 送信失敗（webhook 4xx/5xx・接続不可）は 502＋台帳 ok:false（成功だけ残す選択的表示をしない）
  - レート制限（最小間隔・日次上限）は 429 日本語 detail・env=0 で無効
  - HIKESHI_CONSOLE_TOKEN 設定時は 401（公開面を広げない）
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# レート制限は既定で無効化してテストする（対象テストだけが明示的に有効化・復元する）。
os.environ["HIKESHI_NOTIFY_MIN_INTERVAL_S"] = "0"
os.environ["HIKESHI_NOTIFY_DAILY_CAP"] = "0"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import console.app as capp  # noqa: E402

_WEBHOOK = "https://hooks.example.invalid/services/T000/B000/XXXX"

_FAULT_ACTIVE: str | None = None
_METRICS_FAULT = {"fault_active": "deploy_regression", "error_rate": 0.18,
                  "latency_p95_ms": 1900, "memory_pct": 45, "upstream_status": "ok",
                  "self_health": "degraded", "revision": 2, "contained": False,
                  "recent_deploy": {"revision": 2, "deployed_min_ago": 3}}
_METRICS_OK = {"fault_active": None, "error_rate": 0.002, "latency_p95_ms": 180,
               "memory_pct": 45, "revision": 1, "contained": False}


def _demo_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.startswith("/admin/inject/"):
        return httpx.Response(200, json={"injected": path.rsplit("/", 1)[-1], "state": {}})
    if path == "/metrics":
        return httpx.Response(200, json=_METRICS_FAULT if _FAULT_ACTIVE else _METRICS_OK)
    if path == "/admin/state":
        return httpx.Response(200, json={"fault": _FAULT_ACTIVE,
                                         "revision": 2 if _FAULT_ACTIVE else 1,
                                         "contained": False})
    if path == "/healthz":
        return httpx.Response(200, json={"status": "ok"})
    return httpx.Response(404, json={})


def _client() -> TestClient:
    capp._client = httpx.Client(transport=httpx.MockTransport(_demo_handler), timeout=5)
    return TestClient(capp.app)


class _FakeOut:
    """AgentOutput の最小スタブ（_verdict_response の台帳フック用・LLM 不要）。"""

    root_cause_text = "rev 2 のデプロイ起因"
    root_cause_category = "deploy_regression"
    remediation_type = "rollback"
    confidence = 0.9
    requires_hitl = True
    tool_trajectory = ["read_metrics"]
    tool_trajectory_detail = [{"agent": "investigate", "tool": "read_metrics"}]
    reasoning = {}
    evidence = [{"fact": "error_rate 0.18", "source": "read_metrics"}]
    remediation_plan = ["前リビジョンへ切替"]
    cost_yen = 6.5
    latency_ms = 21000.0


class _WebhookRecorder:
    """httpx.post の差し替え＝実ネットワークへ出さず呼び出しを記録する。"""

    def __init__(self, status_code: int = 200, exc: Exception | None = None):
        self.calls: list[dict] = []
        self.status_code = status_code
        self.exc = exc

    def __call__(self, url, **kwargs):
        self.calls.append({"url": str(url), **kwargs})
        if self.exc is not None:
            raise self.exc
        return httpx.Response(self.status_code, request=httpx.Request("POST", str(url)))


def _reset_notify_rate() -> None:
    capp._notify_rate.update(last_ts=0.0, day=None, count=0)


def _notify_entries() -> list[dict]:
    return [e for e in capp._ledger if e.get("type") == "hansho_notify"]


def test_notify_unconfigured_409():
    """未設定は 409（日本語で正直に）。送信を試みず台帳にも書かない。"""
    client = _client()
    capp._ledger.clear()
    saved_env = os.environ.pop("HIKESHI_NOTIFY_WEBHOOK", None)
    saved_post = httpx.post

    def _must_not_send(*_a, **_k):
        raise AssertionError("未設定なのに送信を試みた")

    httpx.post = _must_not_send
    try:
        r = client.post("/api/notify")
        assert r.status_code == 409
        assert "通知先が未設定です" in r.json()["detail"]
        assert "HIKESHI_NOTIFY_WEBHOOK" in r.json()["detail"]
        assert not _notify_entries()  # 送信を試みていない事実のまま（偽の記録を作らない）
    finally:
        httpx.post = saved_post
        if saved_env is not None:
            os.environ["HIKESHI_NOTIFY_WEBHOOK"] = saved_env
        capp._ledger.clear()


def test_notify_sends_real_state():
    """設定時：env の webhook へ {"text": 実データ本文} を POST し、台帳に ok:true を記録。"""
    global _FAULT_ACTIVE
    client = _client()
    capp._ledger.clear()
    capp._last_incident.clear()
    _FAULT_ACTIVE = "deploy_regression"
    recorder = _WebhookRecorder(200)
    saved_post = httpx.post
    httpx.post = recorder
    os.environ["HIKESHI_NOTIFY_WEBHOOK"] = _WEBHOOK
    _reset_notify_rate()
    try:
        # 注入→診断要約（台帳の実記録）を作ってから通知する
        assert client.post("/api/inject/deploy_regression").status_code == 200
        capp._verdict_response(_FakeOut(), "deploy_regression")

        # body で URL を渡しても無視される（通知先は env のみ＝SSRF 面なし）
        r = client.post("/api/notify", json={"url": "http://attacker.invalid/"})
        assert r.status_code == 200 and r.json() == {"sent": True}

        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["url"] == _WEBHOOK  # 宛先は env の固定 URL のみ
        assert call["timeout"] == 5.0
        payload = call["json"]
        assert set(payload) == {"text"}  # Slack 互換 {"text": ...}
        text = payload["text"]
        # 本文＝実観測値と台帳実記録のみ（fault・error_rate・p95・継続・直近診断）
        assert "deploy_regression" in text
        assert "0.18" in text and "1900" in text
        assert "継続" in text
        assert "rollback" in text and "0.9" in text

        entry = _notify_entries()[-1]
        assert entry["ok"] is True
        # 宛先 URL は台帳に載せない（webhook URL は秘匿情報）
        assert _WEBHOOK not in json.dumps(client.get("/api/incidents").json())
    finally:
        httpx.post = saved_post
        os.environ.pop("HIKESHI_NOTIFY_WEBHOOK", None)
        _FAULT_ACTIVE = None
        capp._ledger.clear()
        capp._last_incident.clear()


def test_notify_send_failure_502():
    """送信失敗（webhook 5xx／接続不可）は 502 で正直に＋台帳に ok:false を記録。"""
    client = _client()
    capp._ledger.clear()
    os.environ["HIKESHI_NOTIFY_WEBHOOK"] = _WEBHOOK
    _reset_notify_rate()
    saved_post = httpx.post
    try:
        # webhook が 500 を返す
        httpx.post = _WebhookRecorder(500)
        r = client.post("/api/notify")
        assert r.status_code == 502 and "500" in r.json()["detail"]
        assert _notify_entries()[-1]["ok"] is False

        # webhook へ接続できない
        httpx.post = _WebhookRecorder(exc=httpx.ConnectError("boom"))
        r = client.post("/api/notify")
        assert r.status_code == 502
        entries = _notify_entries()
        assert len(entries) == 2 and entries[-1]["ok"] is False  # 失敗も両方記録（§20）
    finally:
        httpx.post = saved_post
        os.environ.pop("HIKESHI_NOTIFY_WEBHOOK", None)
        capp._ledger.clear()


def test_notify_rate_limit():
    """最小間隔・日次上限は 429（日本語 detail）。env=0 で無効。"""
    client = _client()
    capp._ledger.clear()
    keys = ("HIKESHI_NOTIFY_MIN_INTERVAL_S", "HIKESHI_NOTIFY_DAILY_CAP")
    saved_env = {k: os.environ.get(k) for k in keys}
    os.environ["HIKESHI_NOTIFY_WEBHOOK"] = _WEBHOOK
    saved_post = httpx.post
    httpx.post = _WebhookRecorder(200)
    try:
        # 間隔内2連打 → 2回目が 429
        os.environ["HIKESHI_NOTIFY_MIN_INTERVAL_S"] = "30"
        os.environ["HIKESHI_NOTIFY_DAILY_CAP"] = "20"
        _reset_notify_rate()
        assert client.post("/api/notify").status_code == 200
        r = client.post("/api/notify")
        assert r.status_code == 429 and "連打防止" in r.json()["detail"]

        # 日次上限（cap=1・間隔なし）→ 2回目が 429
        os.environ["HIKESHI_NOTIFY_MIN_INTERVAL_S"] = "0"
        os.environ["HIKESHI_NOTIFY_DAILY_CAP"] = "1"
        _reset_notify_rate()
        assert client.post("/api/notify").status_code == 200
        r = client.post("/api/notify")
        assert r.status_code == 429 and "本日の上限" in r.json()["detail"]

        # env=0 → 無効（連打しても 200）
        os.environ["HIKESHI_NOTIFY_MIN_INTERVAL_S"] = "0"
        os.environ["HIKESHI_NOTIFY_DAILY_CAP"] = "0"
        _reset_notify_rate()
        assert client.post("/api/notify").status_code == 200
        assert client.post("/api/notify").status_code == 200
    finally:
        httpx.post = saved_post
        os.environ.pop("HIKESHI_NOTIFY_WEBHOOK", None)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_notify_rate()
        capp._ledger.clear()


def test_notify_requires_auth():
    """HIKESHI_CONSOLE_TOKEN 設定時は 401（公開面を広げない）。Bearer 一致なら通る。"""
    client = _client()
    capp._ledger.clear()
    saved_token = capp.CONSOLE_TOKEN
    capp.CONSOLE_TOKEN = "t0ken"
    os.environ["HIKESHI_NOTIFY_WEBHOOK"] = _WEBHOOK
    saved_post = httpx.post
    httpx.post = _WebhookRecorder(200)
    _reset_notify_rate()
    try:
        assert client.post("/api/notify").status_code == 401
        ok = client.post("/api/notify", headers={"Authorization": "Bearer t0ken"})
        assert ok.status_code == 200 and ok.json() == {"sent": True}
    finally:
        httpx.post = saved_post
        capp.CONSOLE_TOKEN = saved_token
        os.environ.pop("HIKESHI_NOTIFY_WEBHOOK", None)
        capp._ledger.clear()


if __name__ == "__main__":
    test_notify_unconfigured_409()
    test_notify_sends_real_state()
    test_notify_send_failure_502()
    test_notify_rate_limit()
    test_notify_requires_auth()
    print("hansho notify (/api/notify) tests: OK")
