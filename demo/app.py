"""障害注入デモアプリ（victim service）。

Hikeshi（消火エージェント）が監視・対処する「やられ役」の最小サービス。
- 通常は正常応答。`/admin/inject` で障害を注入すると `/metrics`・`/healthz` が劣化する。
- `/metrics` はエージェントが読む「シグナル」（INCIDENT-BENCH の signals に対応）。
- `/admin/contain` は「うだつ」（江戸の破壊消防に由来する延焼防止・隔離）。fault は残したまま
  ユーザ向け影響（5xx・error_rate）の悪化だけを止める＝degraded の安定値に落ち着く。
- `/admin/recover` は HITL 承認後の「対処」。**正しい対処のときだけ復旧**する
  （例: 依存障害に盲目的 rollback しても直らない＝判断の重要性をライブで示す）。

ローカル実行:  python demo/app.py   または  uvicorn demo.app:app --port 8080
"""
from __future__ import annotations

import hmac
import os
import time
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response

app = FastAPI(title="Hikeshi demo: fault-injection victim service")

# --- 認証（状態を変える /admin/* を保護） -----------------------------------
# 非ローカルへ出す前提の防御。クラウドでは Cloud Run を IAM 非公開
# （--no-allow-unauthenticated）にし、呼び出し元（console）が Google ID トークンを
# 添付する＝プラットフォーム層で全経路を保護。この HIKESHI_DEMO_TOKEN は
# (a) ローカル/非GCPでの app 層保護、(b) 多層防御 のための任意の共有シークレット。
# 未設定なら従来どおり無認証（ローカルデモ前提）＝挙動後方互換。
DEMO_TOKEN = os.environ.get("HIKESHI_DEMO_TOKEN", "").strip()


def _bearer(authorization: str | None) -> str | None:
    """`Authorization: Bearer <token>` からトークンを取り出す（無ければ None）。"""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def require_admin_auth(authorization: str | None = Header(default=None)) -> None:
    """/admin/* のゲート。HIKESHI_DEMO_TOKEN 未設定なら素通り（ローカル）。"""
    if not DEMO_TOKEN:
        return
    token = _bearer(authorization)
    # bytes 比較＝攻撃者制御のヘッダに非ASCIIが来ても TypeError(→500)にせず一定時間で 401。
    if token is None or not hmac.compare_digest(token.encode("utf-8"), DEMO_TOKEN.encode("utf-8")):
        raise HTTPException(status_code=401, detail="unauthorized")

# 障害シナリオ（INCIDENT-BENCH のカテゴリ／推奨対処に対応）
SCENARIOS: dict[str, dict[str, Any]] = {
    "deploy_regression": {
        "category": "deploy_regression",
        "correct_remediation": "rollback",            # 直近デプロイ起因 → 前リビジョンへ切替で回復
        "summary": "新リビジョン後にエラー率・レイテンシ急騰（self unhealthy）",
    },
    "dependency_failure": {
        "category": "dependency_failure",
        "correct_remediation": "runbook_mitigation",  # 外部依存障害 → rollback では直らない
        "summary": "外部依存が5xx。self は healthy のままエラー連鎖",
    },
    "resource_exhaustion": {
        "category": "resource_exhaustion",
        "correct_remediation": "scale",               # 負荷増 → スケールで回復（rollback不適）
        "summary": "メモリ/負荷の上昇でレイテンシ悪化・OOM 兆候",
    },
}

# インメモリ状態（デモ用）。contained=うだつ（延焼防止）適用済みか。
STATE: dict[str, Any] = {"revision": 1, "fault": None, "since": None, "contained": False}


def _healthy() -> bool:
    # 依存障害は「自分は健全（下流が死んでいる）」を再現 → self は healthy
    if STATE["fault"] == "dependency_failure":
        return True
    return STATE["fault"] is None


def _metrics() -> dict[str, Any]:
    """現在の障害状態を反映したシグナル（エージェントの入力）。

    contained（うだつ＝延焼防止）は値の捏造ではなく状態機械の分岐：fault はアクティブな
    まま、封じ込め手段（トラフィック遮断・サーキットブレーカ・負荷制限）を適用した
    状態のユーザ向け影響を返す。悪化は止まるが正常値には戻らない（復旧は /admin/recover）。
    """
    f = STATE["fault"]
    contained = bool(STATE["contained"])
    m: dict[str, Any] = {
        "revision": STATE["revision"],
        "error_rate": 0.002,
        "latency_p95_ms": 180,
        "memory_pct": 45,
        "upstream_status": "ok",
        "self_health": "ok",
        "fault_active": f,
        "contained": contained,
    }
    if f == "deploy_regression":
        m.update(error_rate=0.18, latency_p95_ms=1900, self_health="degraded",
                 recent_deploy={"revision": STATE["revision"], "deployed_min_ago": 3})
        if contained:
            # 新リビジョンへのトラフィックを止めた状態＝5xx が半減して安定（fault は残存）
            m.update(error_rate=0.09, latency_p95_ms=950,
                     note="containment: traffic to new revision stopped; fault still present")
    elif f == "dependency_failure":
        m.update(error_rate=0.16, upstream_status="5xx", self_health="ok",
                 note="downstream dependency returning 5xx; our service healthy")
        if contained:
            # サーキットブレーカで上流呼び出しを遮断＝5xx 連鎖が止まりフォールバック応答で安定
            # （上流依存そのものは壊れたまま＝upstream_status は 5xx のまま）。
            # 0.03＝UI 表示閾値 0.02 より上の安定値：悪化は止まるが「正常（緑）」には見せない。
            m.update(error_rate=0.03,
                     note="containment: circuit breaker open, serving fallback; upstream still 5xx")
    elif f == "resource_exhaustion":
        m.update(latency_p95_ms=2400, memory_pct=96, self_health="degraded",
                 note="memory/CPU saturation under load")
        if contained:
            # 流入制限（load shedding）で飽和の進行が止まる＝高止まりの安定値（容量不足は残存）
            m.update(latency_p95_ms=900, memory_pct=88,
                     note="containment: load shedding active; capacity shortfall still present")
    return m


@app.get("/")
def root() -> dict[str, Any]:
    return {"service": "hikeshi-demo", "revision": STATE["revision"], "healthy": _healthy()}


@app.get("/work")
def work() -> dict[str, Any]:
    """通常業務エンドポイント。障害時は失敗/遅延を再現。contained 中は縮退応答で延焼停止。"""
    f = STATE["fault"]
    if f == "deploy_regression":
        if STATE["contained"]:
            return {"ok": True, "degraded": True}  # 新リビジョン遮断中＝5xx は止まるが縮退
        raise HTTPException(status_code=500, detail="internal error after bad revision")
    if f == "dependency_failure":
        if STATE["contained"]:
            return {"ok": True, "degraded": True, "fallback": True}  # ブレーカ開＝フォールバック
        raise HTTPException(status_code=502, detail="upstream dependency 5xx")
    if f == "resource_exhaustion":
        return {"ok": True, "slow": True}  # 実際は遅延。デモではメトリクスで表現
    return {"ok": True}


@app.get("/healthz")
def healthz(response: Response) -> dict[str, Any]:
    if not _healthy():
        response.status_code = 503
        return {"status": "unhealthy", "fault": STATE["fault"]}
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> dict[str, Any]:
    return _metrics()


@app.get("/admin/state", dependencies=[Depends(require_admin_auth)])
def admin_state() -> dict[str, Any]:
    return STATE


@app.post("/admin/inject/{scenario}", dependencies=[Depends(require_admin_auth)])
def inject(scenario: str) -> dict[str, Any]:
    if scenario not in SCENARIOS:
        raise HTTPException(
            status_code=400, detail=f"unknown scenario; choose from {list(SCENARIOS)}"
        )
    STATE["fault"] = scenario
    STATE["since"] = time.time()
    STATE["contained"] = False  # 新しい障害＝封じ込め状態をリセット
    if scenario == "deploy_regression":
        STATE["revision"] += 1  # 壊れた新リビジョンを模擬
    s = SCENARIOS[scenario]
    return {"injected": scenario, "category": s["category"],
            "correct_remediation": s["correct_remediation"], "state": STATE}


@app.post("/admin/contain", dependencies=[Depends(require_admin_auth)])
def contain() -> dict[str, Any]:
    """うだつ（延焼防止・隔離）。fault はアクティブなまま、ユーザ向け影響の悪化だけを止める。

    エラー率/5xx は degraded の安定値に落ち着くが正常には戻らない＝完全復旧には
    正しい対処（/admin/recover）が必要。recover 判定・healthz のセマンティクスは不変。
    """
    if STATE["fault"] is None:
        raise HTTPException(status_code=400,
                            detail="アクティブな障害がありません（先に注入してください）")
    STATE["contained"] = True
    return {"contained": True, "fault": STATE["fault"], "state": STATE,
            "message": "延焼を止めました（fault は残存＝完全復旧には正しい対処が必要）"}


@app.post("/admin/recover", dependencies=[Depends(require_admin_auth)])
def recover(action: str) -> dict[str, Any]:
    """HITL 承認後の対処。正しい対処のときだけ復旧する（判断の重要性を実演）。"""
    f = STATE["fault"]
    if f is None:
        STATE["contained"] = False  # fault 無しに封じ込めは残さない（成功応答＝リセット）
        return {"recovered": True, "message": "no active fault"}
    correct = SCENARIOS[f]["correct_remediation"]
    if action != correct:
        return {"recovered": False, "applied": action, "correct": correct,
                "message": f"'{action}' では復旧しません（正しい対処: '{correct}'）"}
    if action == "rollback":
        STATE["revision"] = max(1, STATE["revision"] - 1)  # 前リビジョンへ
    STATE["fault"] = None
    STATE["since"] = None
    STATE["contained"] = False  # 完全復旧＝封じ込め解除
    return {"recovered": True, "applied": action, "revision": STATE["revision"],
            "message": "復旧しました"}


if __name__ == "__main__":
    import uvicorn

    if not DEMO_TOKEN:
        # 非ローカル公開時は Cloud Run を IAM 非公開にするか HIKESHI_DEMO_TOKEN を設定する。
        print("WARN: HIKESHI_DEMO_TOKEN 未設定 → /admin/* は無認証です", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
