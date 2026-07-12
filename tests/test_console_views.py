"""console コックピット・ページ API のテスト（オフライン・鍵不要・demo は MockTransport で偽装）。

固定する境界：
  - /api/signals は診断と同一の純関数 signals_from_demo の出力をそのまま返す（写像の忠実性＝§20）
  - /api/kb/doc は索引済み source 名への完全一致のみ（パストラバーサル不可＝404）
  - /api/kb/search は空クエリ/不正 kind を 400・エージェントと同一の retriever.search 結果を返す
  - /api/incidents（台帳）は注入/診断要約/承認試行を追記専用で記録し、
    demo が拒否した承認（recovered:false）も残す＝成功のみの選択的表示をしない（§20）
  - /api/contain（うだつ＝延焼防止）は fault 無しの demo 400 を素通しし、
    成功時のみ台帳行に contained_ts を実タイムスタンプで記録する
  - LLM コスト経路（diagnose/stream/depscan）はレート制限＝間隔内の連打と
    日次上限超過を 429 で弾く。env=0 で無効化（公開モードの防御）
  - 公開モード（トークン未設定）では KB 書き込み経路（postmortem/approve）も
    間隔・日次上限で 429＝不特定多数からの KB 大量注入を防ぐ（トークン時は認証が前段）
  - HIKESHI_CONSOLE_TOKEN 設定時、新規エンドポイントも全て 401（公開面を広げない）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# レート制限は既定で無効化してテストする（対象テストだけが明示的に有効化・復元する）。
os.environ["HIKESHI_LLM_MIN_INTERVAL_S"] = "0"
os.environ["HIKESHI_LLM_DAILY_CAP"] = "0"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import console.app as capp  # noqa: E402
from hikeshi_agent.live import signals_from_demo  # noqa: E402

_FAULT_ACTIVE: str | None = None
_RECOVER_RESPONSES: list[dict] = []

_METRICS_FAULT = {"fault_active": "deploy_regression", "error_rate": 0.18,
                  "latency_p95_ms": 1900, "memory_pct": 45, "upstream_status": "ok",
                  "self_health": "degraded", "revision": 2,
                  "recent_deploy": {"revision": 2, "deployed_min_ago": 3}}
_STATE_FAULT = {"fault": "deploy_regression", "revision": 2}
_HEALTH = {"status": "ok"}


def _demo_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.startswith("/admin/inject/"):
        return httpx.Response(200, json={"injected": path.rsplit("/", 1)[-1], "state": {}})
    if path == "/admin/recover":
        return httpx.Response(200, json=_RECOVER_RESPONSES.pop(0))
    if path == "/admin/contain":
        if not _FAULT_ACTIVE:
            return httpx.Response(400, json={"detail": "アクティブな障害がありません"})
        return httpx.Response(200, json={"contained": True, "fault": _FAULT_ACTIVE,
                                         "state": {"fault": _FAULT_ACTIVE, "contained": True}})
    if path == "/metrics":
        return httpx.Response(200, json=_METRICS_FAULT if _FAULT_ACTIVE else
                              {"fault_active": None, "error_rate": 0.002,
                               "latency_p95_ms": 180, "memory_pct": 45, "revision": 1})
    if path == "/admin/state":
        return httpx.Response(200, json=_STATE_FAULT if _FAULT_ACTIVE else
                              {"fault": None, "revision": 1})
    if path == "/healthz":
        return httpx.Response(200, json=_HEALTH)
    return httpx.Response(404, json={})


def _client() -> TestClient:
    capp._client = httpx.Client(transport=httpx.MockTransport(_demo_handler), timeout=5)
    return TestClient(capp.app)


class _FakeOut:
    """AgentOutput の最小スタブ（_verdict_response の台帳フック検証用・LLM 不要）。"""

    root_cause_text = "rev 2 のデプロイ起因"
    root_cause_category = "deploy_regression"
    remediation_type = "rollback"
    confidence = 0.9
    requires_hitl = True
    tool_trajectory = ["read_metrics", "diff_revision"]
    tool_trajectory_detail = [{"agent": "investigate", "tool": "read_metrics"}]
    reasoning = {}
    evidence = [{"fact": "error_rate 0.18", "source": "read_metrics"}]
    remediation_plan = ["前リビジョンへ切替"]
    cost_yen = 6.5
    latency_ms = 21000.0


def test_signals_parity():
    """/api/signals ＝ 診断と同一の純関数の出力そのまま（並行実装を作らない・§20）。"""
    global _FAULT_ACTIVE
    client = _client()

    _FAULT_ACTIVE = "deploy_regression"
    r = client.get("/api/signals")
    assert r.status_code == 200
    body = r.json()
    assert body["fault"] == "deploy_regression"
    assert body["signals"] == signals_from_demo(_METRICS_FAULT, _HEALTH, _STATE_FAULT)
    assert body["raw"]["metrics"] == _METRICS_FAULT  # 実観測値の素通し（コンソール表示用）
    # 正解ラベル（fault_active＝カテゴリ enum）はエージェント入力に含めない（bench と同型）。
    assert "fault_active" not in body["signals"]["metrics"]

    # fault 無しでも 200 で正直に fault=None（エージェントは起動しない事実をそのまま返す）
    _FAULT_ACTIVE = None
    body = client.get("/api/signals").json()
    assert body["fault"] is None
    assert "signals" in body


def test_kb_endpoints():
    """KB ブラウザ＝実ファイルのみ・トラバーサル不可・エージェントと同一検索。"""
    client = _client()

    docs = client.get("/api/kb/list").json()
    assert docs["kb_size"] >= 10 and len(docs["docs"]) == docs["kb_size"]
    kinds = {d["kind"] for d in docs["docs"]}
    assert kinds <= {"runbook", "postmortem", "incident", "advisory"}

    src = docs["docs"][0]["source"]
    doc = client.get("/api/kb/doc", params={"source": src}).json()
    assert doc["source"] == src and doc["text"]

    # パストラバーサル/索引外は 404（クライアントからパスを受けない）
    for bad in ("../../etc/passwd", "/etc/passwd", "deploy-regression.md/../x", "nope.md"):
        assert client.get("/api/kb/doc", params={"source": bad}).status_code == 404

    hits = client.get("/api/kb/search", params={"q": "外部依存 5xx"}).json()["hits"]
    assert hits and hits[0]["score"] >= hits[-1]["score"]  # 決定的ランキング（降順）
    from hikeshi_agent.retriever import search
    assert hits == search("外部依存 5xx", kind=None, k=5)  # エージェントと同一関数・同一結果

    assert client.get("/api/kb/search", params={"q": "  "}).status_code == 400
    assert client.get("/api/kb/search", params={"q": "x", "kind": "bogus"}).status_code == 400


def test_incident_ledger():
    """台帳＝追記専用・注入/診断/承認試行（拒否も）/復旧 duration を実記録から残す。"""
    global _FAULT_ACTIVE
    import itertools

    client = _client()
    capp._ledger.clear()
    capp._last_incident.clear()
    # 採番カウンタをリセット＝他テストファイル（pytest 一括実行）の注入数に依存しない。
    capp._inc_seq = itertools.count(1)
    _FAULT_ACTIVE = "deploy_regression"

    # 0) 台帳が空のうちは /api/status は incident を返さない（クライアントで採番しない前提）
    assert client.get("/api/status").json()["incident"] is None

    # 1) 注入 → 台帳に採番つきで現れ、/api/status の incident と一致する
    assert client.post("/api/inject/deploy_regression").status_code == 200
    st = client.get("/api/status").json()
    assert st["incident"]["id"] == 1 and st["incident"]["fault"] == "deploy_regression"

    # 1b) 許可リスト外の action は demo へ転送せず 400（自由文字列を監査台帳に載せない）
    assert client.post("/api/recover", params={"action": "rm -rf /"}).status_code == 400

    # 2) 診断要約の添付（_verdict_response の台帳フック）
    capp._verdict_response(_FakeOut(), "deploy_regression")
    entry = client.get("/api/incidents").json()["entries"][0]
    assert entry["verdict"]["remediation_type"] == "rollback"
    assert entry["verdict"]["cost_yen"] == 6.5

    # 3) 誤った対処の承認 → demo が拒否（recovered:false）。これも台帳に残る（§20）
    _RECOVER_RESPONSES.append({"recovered": False, "applied": "scale",
                               "correct": "rollback", "message": "no"})
    assert client.post("/api/recover", params={"action": "scale"}).json()["recovered"] is False
    entry = client.get("/api/incidents").json()["entries"][0]
    assert entry["attempts"] == [{"action": "scale", "recovered": False,
                                  "ts": entry["attempts"][0]["ts"]}]
    assert "recovered_ts" not in entry  # 拒否では復旧扱いにしない

    # 4) 正しい対処 → 復旧。duration はサーバ実測（注入→復旧適用）
    _RECOVER_RESPONSES.append({"recovered": True, "applied": "rollback", "revision": 1,
                               "message": "復旧しました"})
    assert client.post("/api/recover", params={"action": "rollback"}).json()["recovered"] is True
    body = client.get("/api/incidents").json()
    entry = body["entries"][0]
    assert len(entry["attempts"]) == 2 and entry["attempts"][1]["recovered"] is True
    assert entry["duration_s"] >= 0 and entry["recovered_ts"] >= entry["injected_ts"]
    assert body["summary"] == {"incidents": 1, "diagnosed": 1, "recovered": 1,
                               "avg_recovery_s": entry["duration_s"],
                               "total_cost_yen": 6.5}

    # 5) fault 無しの recover（applied が返らない）は試行として記録しない（捏造防止）
    _FAULT_ACTIVE = None
    _RECOVER_RESPONSES.append({"recovered": True, "message": "no active fault"})
    client.post("/api/recover", params={"action": "rollback"})
    assert len(client.get("/api/incidents").json()["entries"][0]["attempts"]) == 2

    capp._ledger.clear()
    capp._last_incident.clear()


def test_ledger_ids_stay_unique_after_trim():
    """台帳キャップで古い行が落ちても INC 採番は単調＝既存行と衝突しない。"""
    global _FAULT_ACTIVE
    client = _client()
    capp._ledger.clear()
    capp._last_incident.clear()
    _FAULT_ACTIVE = "deploy_regression"
    saved = capp._LEDGER_MAX
    capp._LEDGER_MAX = 3
    try:
        for _ in range(5):
            assert client.post("/api/inject/deploy_regression").status_code == 200
        entries = client.get("/api/incidents").json()["entries"]
        ids = [e["id"] for e in entries]
        assert len(entries) == 3  # キャップで古い行は落ちる
        assert len(set(ids)) == len(ids)  # id は重複しない
        assert ids == sorted(ids, reverse=True)  # 新しい順＝単調採番
    finally:
        capp._LEDGER_MAX = saved
        capp._ledger.clear()
        capp._last_incident.clear()


def test_depscan_ledger_hook():
    """防火スキャンも台帳に実行時刻と実検出数だけを記録する（消火と同じ履歴に並ぶ）。"""
    from hikeshi_agent import depscan as dep

    client = _client()
    capp._ledger.clear()
    saved_scan, saved_dict = dep.scan, dep.report_to_dict
    import hikeshi_agent.depscan_agent as dep_agent
    saved_assess = dep_agent.assess

    def _fake_assess(_findings):
        raise RuntimeError("no key")

    dep.scan = lambda *a, **k: "fake-report"
    dep.report_to_dict = lambda _r: {"findings": [{"package": "x"}], "total_packages": 1,
                                     "osv_source": "cache", "skipped_unpinned": 0}
    dep_agent.assess = _fake_assess
    try:
        r = client.post("/api/depscan")
        assert r.status_code == 200 and r.json()["assessment_available"] is False
        row = client.get("/api/incidents").json()["entries"][0]
        assert row["type"] == "depscan" and row["findings"] == 1
        assert row["assessment_available"] is False
    finally:
        dep.scan, dep.report_to_dict, dep_agent.assess = saved_scan, saved_dict, saved_assess
        capp._ledger.clear()


def test_contain_endpoint():
    """うだつ（延焼防止）＝ fault 無しは demo の 400 素通し・成功時のみ contained_ts を実記録。"""
    global _FAULT_ACTIVE
    client = _client()
    capp._ledger.clear()
    capp._last_incident.clear()

    # fault 無し → demo が 400 ＝素通し（偽の contained を作らない）
    _FAULT_ACTIVE = None
    assert client.post("/api/contain").status_code == 400
    assert not capp._ledger and "containment" not in capp._last_incident

    # 注入 → contain 成功。台帳行に contained_ts、_last_incident に containment（実時刻）
    _FAULT_ACTIVE = "deploy_regression"
    assert client.post("/api/inject/deploy_regression").status_code == 200
    r = client.post("/api/contain")
    assert r.status_code == 200
    body = r.json()
    assert body["contained"] is True and body["state"]["contained"] is True
    entry = client.get("/api/incidents").json()["entries"][0]
    assert entry["contained_ts"] >= entry["injected_ts"]
    cont = capp._last_incident["containment"]
    assert cont["incident_id"] == capp._last_incident["injected_ts"]
    assert cont["ts"] == entry["contained_ts"]

    _FAULT_ACTIVE = None
    capp._ledger.clear()
    capp._last_incident.clear()


def _reset_llm_rate() -> None:
    capp._llm_rate.update(last_ts=0.0, day=None, count=0)


def test_llm_rate_limit():
    """公開モードの防御＝LLM コスト経路のみ 429（間隔・日次上限）。env=0 で無効。"""
    global _FAULT_ACTIVE
    import hikeshi_agent.live as live

    client = _client()
    capp._ledger.clear()
    capp._last_incident.clear()
    _FAULT_ACTIVE = "deploy_regression"
    keys = ("HIKESHI_LLM_MIN_INTERVAL_S", "HIKESHI_LLM_DAILY_CAP")
    saved_env = {k: os.environ.get(k) for k in keys}
    saved_diag = live.diagnose_live
    live.diagnose_live = lambda signals: _FakeOut()  # LLM 不要（レート制限だけを見る）
    _reset_llm_rate()
    try:
        # 間隔内2連打 → 2回目が 429（連打防止・stream も同じガード＝SSE 開始前に HTTP で返す）
        os.environ["HIKESHI_LLM_MIN_INTERVAL_S"] = "12"
        os.environ["HIKESHI_LLM_DAILY_CAP"] = "300"
        assert client.post("/api/diagnose").status_code == 200
        r = client.post("/api/diagnose")
        assert r.status_code == 429 and "連打防止" in r.json()["detail"]
        assert client.post("/api/diagnose/stream").status_code == 429
        # 非 LLM 経路（status/incidents 等）は制限なし
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/incidents").status_code == 200

        # env=0 → 無効（連打しても 200）
        os.environ["HIKESHI_LLM_MIN_INTERVAL_S"] = "0"
        os.environ["HIKESHI_LLM_DAILY_CAP"] = "0"
        assert client.post("/api/diagnose").status_code == 200
        assert client.post("/api/diagnose").status_code == 200

        # 日次上限（cap=1・間隔なし）→ 2回目が 429（本日の上限）
        os.environ["HIKESHI_LLM_DAILY_CAP"] = "1"
        _reset_llm_rate()
        assert client.post("/api/diagnose").status_code == 200
        r = client.post("/api/diagnose")
        assert r.status_code == 429 and "本日の上限" in r.json()["detail"]

        # fault 無し（no_fault＝LLM を呼ばない）はカウントを消費しない
        os.environ["HIKESHI_LLM_MIN_INTERVAL_S"] = "12"
        os.environ["HIKESHI_LLM_DAILY_CAP"] = "300"
        _FAULT_ACTIVE = None
        _reset_llm_rate()
        assert client.post("/api/diagnose").json()["no_fault"] is True
        assert capp._llm_rate["count"] == 0 and capp._llm_rate["last_ts"] == 0.0
    finally:
        live.diagnose_live = saved_diag
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_llm_rate()
        _FAULT_ACTIVE = None
        capp._ledger.clear()
        capp._last_incident.clear()


def test_kb_write_rate_limit():
    """公開モードでは approve（KB 書き込み）も間隔・日次上限で 429。トークン時は素通り。"""
    import tempfile
    from pathlib import Path

    import hikeshi_agent.postmortem as pm
    from hikeshi_agent.retriever import reindex

    client = _client()
    keys = ("HIKESHI_KB_WRITE_MIN_INTERVAL_S", "HIKESHI_KB_WRITE_DAILY_CAP")
    saved_env = {k: os.environ.get(k) for k in keys}
    saved_dir = pm._KB_POSTMORTEMS
    saved_token = capp.CONSOLE_TOKEN
    capp._kb_write_rate.update(last_ts=0.0, day=None, count=0)
    try:
        with tempfile.TemporaryDirectory() as d:
            pm._KB_POSTMORTEMS = Path(d)
            body = {"markdown": "# 承認レート制限テスト\n\nx"}

            # 間隔内2連打 → 2回目が 429（公開モード＝トークン未設定）
            os.environ["HIKESHI_KB_WRITE_MIN_INTERVAL_S"] = "10"
            os.environ["HIKESHI_KB_WRITE_DAILY_CAP"] = "30"
            assert client.post("/api/postmortem/approve", json=body).status_code == 200
            r = client.post("/api/postmortem/approve", json=body)
            assert r.status_code == 429 and "連打防止" in r.json()["detail"]

            # トークン設定時は認証が前段ゲート＝この制限は掛からない
            capp.CONSOLE_TOKEN = "t0ken"
            r = client.post("/api/postmortem/approve", json=body,
                            headers={"Authorization": "Bearer t0ken"})
            assert r.status_code == 200
            capp.CONSOLE_TOKEN = saved_token

            # env=0 → 無効（公開モードでも連続 200）
            os.environ["HIKESHI_KB_WRITE_MIN_INTERVAL_S"] = "0"
            os.environ["HIKESHI_KB_WRITE_DAILY_CAP"] = "0"
            assert client.post("/api/postmortem/approve", json=body).status_code == 200
    finally:
        capp.CONSOLE_TOKEN = saved_token
        pm._KB_POSTMORTEMS = saved_dir
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        capp._kb_write_rate.update(last_ts=0.0, day=None, count=0)
        reindex()  # 一時 dir を索引した状態を実 KB へ戻す


def test_new_endpoints_require_auth():
    """トークン設定時、新規エンドポイントも全て 401＝公開面を広げない。"""
    client = _client()
    saved = capp.CONSOLE_TOKEN
    capp.CONSOLE_TOKEN = "t0ken"
    try:
        for path in ("/api/signals", "/api/kb/list", "/api/kb/doc?source=x.md",
                     "/api/kb/search?q=x", "/api/incidents"):
            assert client.get(path).status_code == 401, path
        assert client.post("/api/contain").status_code == 401  # 状態変更系も同じゲート
        ok = client.get("/api/kb/list", headers={"Authorization": "Bearer t0ken"})
        assert ok.status_code == 200
    finally:
        capp.CONSOLE_TOKEN = saved


if __name__ == "__main__":
    test_signals_parity()
    test_kb_endpoints()
    test_incident_ledger()
    test_ledger_ids_stay_unique_after_trim()
    test_depscan_ledger_hook()
    test_contain_endpoint()
    test_llm_rate_limit()
    test_kb_write_rate_limit()
    test_new_endpoints_require_auth()
    print("console views (signals/kb/ledger/contain/ratelimit/auth) tests: OK")
