"""Hikeshi 運用コンソール (operator console) — HITL 承認 UI ＋ agent↔demo 配線。

価値提案「当番1人が、承認1クリックで復旧」を**1システムで**実演する:
  demo に障害注入 → Hikeshi(実 ADK エージェント)が demo を HTTP 観測して診断 →
  人間が提案カードを承認(1クリック) → demo の /admin/recover で実際に復旧。

console は demo を victim service として HTTP で観測/操作し(HIKESHI_DEMO_URL)、
診断は実エージェント(hikeshi_agent)を Vertex/Gemini で走らせる。安全境界:
**誤った対処は demo が復旧を拒否する**＝判断の重要性がライブで出る(§20)。

実行:
  python demo/app.py            # victim service  (:8080)
  python console/app.py         # this console    (:8081) → http://localhost:8081
鍵: 診断は Vertex/ADC か GOOGLE_API_KEY が必要(未設定なら /api/diagnose が 503・偽値は返さない)。
"""
from __future__ import annotations

import base64
import hmac
import itertools
import json
import os
import pathlib
import time

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

DEMO_URL = os.environ.get("HIKESHI_DEMO_URL", "http://localhost:8080").rstrip("/")
STATIC = pathlib.Path(__file__).parent / "static"

app = FastAPI(title="Hikeshi operator console")
_client = httpx.Client(timeout=8.0)

# 直近インシデントの実行記録（ポストモーテム→KB 提案ループの材料・デモ用の単一セッション前提）。
# verdict=実診断の IncidentVerdict＋実測／recovery=適用対処と結果／injected_ts=注入時刻。
_last_incident: dict = {}

# インシデント台帳（履歴ページ用・追記専用・インスタンス寿命のみ＝エフェメラル）。
# _last_incident とは独立：注入／診断要約／承認試行（成功・失敗とも＝選択的表示をしない・§20）／
# 防火スキャンの実イベントだけを実タイムスタンプで残す。値はすべて実行記録由来（捏造しない）。
_ledger: list[dict] = []
_LEDGER_MAX = 200
# 1行内の承認試行の保持上限（超過分は落とした件数を attempts_dropped で正直に開示）
_ATTEMPTS_MAX = 30
_inc_seq = itertools.count(1)  # INC 採番＝単調カウンタ（台帳の切り詰めと独立＝id は重複しない）


def _ledger_incident(injected_ts) -> dict | None:
    """injected_ts が一致する直近のインシデント行（無ければ None＝別行に書かない）。"""
    if injected_ts is None:
        return None
    for e in reversed(_ledger):
        if e.get("type") == "incident" and e.get("injected_ts") == injected_ts:
            return e
    return None


def _ledger_append(entry: dict) -> None:
    _ledger.append(entry)
    del _ledger[:-_LEDGER_MAX]  # 古い行から落とす（追記専用・上限のみ）

# --- 認証（console = 唯一の公開面） --------------------------------------
# 公開デプロイでは HIKESHI_CONSOLE_TOKEN を設定しない＝誰でも閲覧可（公開モード）。
# その代わり LLM コスト経路はアプリ層レート制限（下記）で守る。トークンの仕組み
# 自体は互換のため残す：設定すれば従来どおり /api/* が共有トークン必須になり、
# フロントは 401 を受けてアンロック画面を出す。
CONSOLE_TOKEN = os.environ.get("HIKESHI_CONSOLE_TOKEN", "").strip()


def _bearer(authorization: str | None) -> str | None:
    """`Authorization: Bearer <token>` からトークンを取り出す（無ければ None）。"""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def require_console_auth(authorization: str | None = Header(default=None)) -> None:
    """/api/* のゲート。HIKESHI_CONSOLE_TOKEN 未設定なら素通り（ローカル）。"""
    if not CONSOLE_TOKEN:
        return
    token = _bearer(authorization)
    # bytes 比較＝攻撃者制御のヘッダに非ASCIIが来ても TypeError(→500)にせず一定時間で 401。
    expected = CONSOLE_TOKEN.encode("utf-8")
    if token is None or not hmac.compare_digest(token.encode("utf-8"), expected):
        raise HTTPException(status_code=401, detail="unauthorized")


# --- レート制限（公開モード用・LLM コスト経路のみ） ------------------------
# トークン無し公開の防御：/api/diagnose・/api/diagnose/stream・/api/depscan に
# (a) 最小間隔 HIKESHI_LLM_MIN_INTERVAL_S（既定12秒） (b) 日次上限
# HIKESHI_LLM_DAILY_CAP（既定300回）を課す。超過は 429。env に 0 を設定すると
# 無効化（テスト/ローカル用）。プロセス内カウンタで足りる＝deploy.sh は
# max-instances=1（単一インスタンス）前提。値は捏造しない＝実カウントのみ。
_llm_rate: dict = {"last_ts": 0.0, "day": None, "count": 0}


def _llm_rate_env() -> tuple[float, int]:
    """(最小間隔秒, 日次上限)。毎リクエストで env を読む＝テストから切替可能。"""
    try:
        interval = float(os.environ.get("HIKESHI_LLM_MIN_INTERVAL_S", "12"))
    except ValueError:
        interval = 12.0
    try:
        cap = int(os.environ.get("HIKESHI_LLM_DAILY_CAP", "300"))
    except ValueError:
        cap = 300
    return max(0.0, interval), max(0, cap)


def _enforce_rate(state: dict, interval: float, cap: int, what: str) -> None:
    """間隔＋日次上限の共通ガード。超過は 429（日本語 detail）・実行時のみ消費。"""
    now = time.time()
    if interval > 0:
        wait = interval - (now - float(state["last_ts"]))
        if wait > 0:
            raise HTTPException(429, (
                f"連打防止のため {what}は {interval:.0f} 秒間隔です。"
                f"約 {max(1, int(wait + 0.999))} 秒後に再試行してください。"))
    if cap > 0:
        day = time.strftime("%Y-%m-%d", time.gmtime(now + 9 * 3600))  # JST 基準＝案内文と一致
        if state["day"] != day:
            state["day"], state["count"] = day, 0
        if state["count"] >= cap:
            raise HTTPException(429, (
                f"本日の上限（{cap} 回）に達しました。"
                "日付が変わってから再試行してください。"))
        state["count"] += 1
    state["last_ts"] = now


def _enforce_llm_rate_limit() -> None:
    """LLM コスト経路の入口ガード。"""
    interval, cap = _llm_rate_env()
    _enforce_rate(_llm_rate, interval, cap, "LLM 実行")


# --- KB 書き込みガード（公開モード用・/api/postmortem/approve のみ） ---------
# トークン無し公開では approve が唯一の「外部入力→KB→次診断の LLM コンテキスト」
# 永続化経路になる（postmortem.py の HITL ゲートはトークン時代の前提）。承認 UI の
# 1クリック体験は保ったまま、書き込み頻度を絞って大量注入を防ぐ＝既存の
# create-only・_MAX_DOCS/_MAX_BYTES・slug サニタイズとの多層防御。
# トークン運用時（HIKESHI_CONSOLE_TOKEN 設定）は従来どおり認証が前段ゲート＝制限しない。
_kb_write_rate: dict = {"last_ts": 0.0, "day": None, "count": 0}


def _kb_write_rate_env() -> tuple[float, int]:
    """(最小間隔秒, 日次上限)。毎リクエストで env を読む＝テストから切替可能。env=0 で無効。"""
    try:
        interval = float(os.environ.get("HIKESHI_KB_WRITE_MIN_INTERVAL_S", "10"))
    except ValueError:
        interval = 10.0
    try:
        cap = int(os.environ.get("HIKESHI_KB_WRITE_DAILY_CAP", "30"))
    except ValueError:
        cap = 30
    return max(0.0, interval), max(0, cap)


def _enforce_kb_write_rate_limit() -> None:
    """公開モードの KB 書き込みガード（トークン設定時は認証が前段＝素通り）。"""
    if CONSOLE_TOKEN:
        return
    interval, cap = _kb_write_rate_env()
    _enforce_rate(_kb_write_rate, interval, cap, "ポストモーテム承認（KB 書き込み）")


# --- 半鐘（通知発信）レート制限 --------------------------------------------
# /api/notify は外部 webhook への発信経路。連打されると通知先（Slack 等）を溢れ
# させるため、LLM 経路と同方式（最小間隔＋日次上限・env=0 で無効・超過 429）で
# 常に絞る（トークン運用時も外部発信である事実は変わらないため素通りにしない）。
_notify_rate: dict = {"last_ts": 0.0, "day": None, "count": 0}


def _notify_rate_env() -> tuple[float, int]:
    """(最小間隔秒, 日次上限)。毎リクエストで env を読む＝テストから切替可能。env=0 で無効。"""
    try:
        interval = float(os.environ.get("HIKESHI_NOTIFY_MIN_INTERVAL_S", "30"))
    except ValueError:
        interval = 30.0
    try:
        cap = int(os.environ.get("HIKESHI_NOTIFY_DAILY_CAP", "20"))
    except ValueError:
        cap = 20
    return max(0.0, interval), max(0, cap)


def _enforce_notify_rate_limit() -> None:
    """通知発信の入口ガード。"""
    interval, cap = _notify_rate_env()
    _enforce_rate(_notify_rate, interval, cap, "通知発信")


# --- demo（victim）への認証付き呼び出し ----------------------------------
# クラウドでは demo を Cloud Run の IAM 非公開にし、console は Google ID トークン
# （audience=demo URL）を添付して呼ぶ＝共有シークレット無しのサービス間認証。
# ローカル/非GCP では HIKESHI_DEMO_TOKEN（静的共有トークン）でも可。両方とも
# 未設定なら無認証で呼ぶ（ローカルで demo も無認証のとき）。
DEMO_TOKEN = os.environ.get("HIKESHI_DEMO_TOKEN", "").strip()
# auto: https の demo なら ID トークンを試す／off: 付けない／on: 常に試す
DEMO_IDTOKEN_MODE = os.environ.get("HIKESHI_DEMO_USE_IDTOKEN", "auto").strip().lower()
_idtoken_cache: dict[str, object] = {"value": None, "exp": 0.0}


def _jwt_exp(token: str) -> float:
    """ID トークン(JWT)の exp(秒) を取り出す。失敗時は now+3000（控えめにキャッシュ）。"""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # base64url のパディング復元
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return float(payload["exp"])
    except Exception:
        return time.time() + 3000.0


def _demo_id_token() -> str | None:
    """demo URL を audience とする Google ID トークンを取得（Cloud Run サービス間認証）。

    メタデータサーバ（Cloud Run のランタイム SA）か GOOGLE_APPLICATION_CREDENTIALS
    から取得する。取得不可なら None＝黙って偽トークンを作らない（§20）。期限の
    5分前まではキャッシュして毎回の取得を避ける（status ポーリングが頻繁なため）。
    """
    now = time.time()
    cached = _idtoken_cache.get("value")
    if isinstance(cached, str) and float(_idtoken_cache.get("exp", 0.0)) - now > 300:
        return cached
    try:
        import google.auth.transport.requests
        from google.oauth2 import id_token as _google_id_token

        token = _google_id_token.fetch_id_token(
            google.auth.transport.requests.Request(), DEMO_URL
        )
    except Exception:
        return None
    _idtoken_cache.update(value=token, exp=_jwt_exp(token))
    return token


def _demo_headers() -> dict[str, str]:
    """console→demo 呼び出しに添付する認証ヘッダ（無ければ空 dict）。"""
    if DEMO_TOKEN:
        return {"Authorization": f"Bearer {DEMO_TOKEN}"}
    if DEMO_IDTOKEN_MODE not in ("off", "0", "false", "no"):
        if DEMO_IDTOKEN_MODE == "on" or DEMO_URL.startswith("https://"):
            token = _demo_id_token()
            if token:
                return {"Authorization": f"Bearer {token}"}
    return {}

def _demo_error_detail(r: httpx.Response) -> str:
    """demo の 4xx/5xx 本文を人向け detail へ（JSON の {"detail": ...} は中身を取り出す）。"""
    try:
        j = r.json()
        if isinstance(j, dict) and isinstance(j.get("detail"), str):
            return j["detail"]
    except ValueError:
        pass
    return r.text


# demo の障害シナリオ(UI 表示用・demo/app.py SCENARIOS と対応)。
# symptom=注入前に出す中立な症状ヒント／expect=診断"後"の答え合わせ用(事前には出さない)。
SCENARIOS = [
    {"key": "deploy_regression", "label": "デプロイ不具合",
     "symptom": "新リビジョン後に 5xx 急騰・self degraded", "expect": "rollback"},
    {"key": "dependency_failure", "label": "外部依存障害",
     "symptom": "上流 5xx・self は healthy", "expect": "runbook_mitigation"},
    {"key": "resource_exhaustion", "label": "リソース枯渇",
     "symptom": "メモリ/レイテンシが飽和", "expect": "scale"},
]

# クライアントへ公開する scenarios（"expect"＝正解 は除く）。
# 期待対処は診断"後"の /api/diagnose でのみ返す＝DevTools/Network でも事前に答えが見えない（§20）。
PUBLIC_SCENARIOS = [{k: v for k, v in s.items() if k != "expect"} for s in SCENARIOS]


def _expected_for(fault: str | None) -> str | None:
    return next((s["expect"] for s in SCENARIOS if s["key"] == fault), None)


def _demo_get(path: str) -> httpx.Response:
    return _client.get(f"{DEMO_URL}{path}", headers=_demo_headers())


def _demo_get_json(path: str, default: dict) -> dict:
    """補助的な demo 取得＝失敗（非JSON/4xx/接続）でも default を返す（demo_ok を落とさない）。

    一部環境（Cloud Run の GFE）で `/healthz` が 404 を返すことがあるため、必須シグナル
    （`/metrics`・`/admin/state`）と違い health 取得は best-effort にする。health は
    フロント/`signals_from_demo` ともに未使用＝あくまで補助情報。
    """
    try:
        r = _demo_get(path)
        if r.status_code >= 400:
            return default
        return r.json()
    except (httpx.HTTPError, ValueError):
        return default


def _demo_get_required(path: str) -> dict:
    """必須シグナル（/metrics・/admin/state）の取得。4xx/5xx を明示的に弾く＝

    「到達不能」と「非JSON 200（IAM 403 の HTML 等）」を取り違えない。呼び出し側は
    httpx.HTTPError/ValueError を捕捉して 502/503 に整形する（既存の経路と互換）。
    """
    r = _demo_get(path)
    r.raise_for_status()
    return r.json()


@app.get("/")
def index() -> FileResponse:
    # 単一HTMLの UI 本体。同日中の再デプロイでも古いタブが残らないようキャッシュ無効
    # （ページを開き直すだけで常に最新リビジョンの UI が届く）。
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/status", dependencies=[Depends(require_console_auth)])
def status() -> dict:
    """demo の現在状態(state/metrics/health)を集約して返す。down 時も UI は生きる。"""
    try:
        state = _demo_get_required("/admin/state")
        metrics = _demo_get_required("/metrics")
    except (httpx.HTTPError, ValueError) as e:
        return {"demo_ok": False, "demo_url": DEMO_URL, "error": str(e),
                "scenarios": PUBLIC_SCENARIOS}
    health = _demo_get_json("/healthz", {"status": "unknown"})  # 補助＝失敗しても demo_ok は維持
    # インシデント採番の単一の出所＝サーバ台帳（最新行）。
    # 無ければ None（クライアント側で採番しない）。
    latest = next((e for e in reversed(_ledger) if e.get("type") == "incident"), None)
    incident = ({"id": latest["id"], "fault": latest["fault"],
                 "injected_ts": latest["injected_ts"]} if latest else None)
    return {"demo_ok": True, "demo_url": DEMO_URL, "state": state,
            "metrics": metrics, "health": health, "scenarios": PUBLIC_SCENARIOS,
            "incident": incident}


@app.post("/api/inject/{scenario}", dependencies=[Depends(require_console_auth)])
def inject(scenario: str) -> dict:
    try:
        r = _client.post(f"{DEMO_URL}/admin/inject/{scenario}", headers=_demo_headers())
    except httpx.HTTPError as e:
        raise HTTPException(502, f"demo に接続できません: {e}") from e
    if r.status_code >= 400:
        raise HTTPException(r.status_code, _demo_error_detail(r))
    # 診断前に正解(correct_remediation/category)をクライアントへ渡さない
    # ＝答え合わせの独立性を担保（DevTools/Network でも答えが見えない・§20）。
    data = r.json()
    _last_incident.clear()
    _last_incident["injected_ts"] = time.time()
    # 台帳へ追記（採番＝インシデント行の通し番号。_last_incident のロジックは変えない）。
    _ledger_append({
        "type": "incident",
        "id": next(_inc_seq),
        "fault": data.get("injected"),
        "injected_ts": _last_incident["injected_ts"],
        "attempts": [],
    })
    return {"injected": data.get("injected"), "state": data.get("state")}


# 承認できる対処の許可リスト（UI の REM・bench の RemediationType と同一集合）。
# 自由文字列を demo へ転送しない／監査台帳に載せない（防御的検証・台帳の内容偽装防止）。
_ALLOWED_ACTIONS = frozenset(
    {"rollback", "runbook_mitigation", "scale", "config_fix", "cache_flush", "code_fix"}
)


@app.post("/api/recover", dependencies=[Depends(require_console_auth)])
def recover(action: str) -> dict:
    """HITL 承認後の対処を demo に適用。正しい対処のときだけ demo は復旧する。"""
    if action not in _ALLOWED_ACTIONS:
        raise HTTPException(400, f"unknown action; choose from {sorted(_ALLOWED_ACTIONS)}")
    try:
        r = _client.post(
            f"{DEMO_URL}/admin/recover", params={"action": action}, headers=_demo_headers()
        )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"demo に接続できません: {e}") from e
    # demo 側の 4xx/5xx（IAM 非公開時の 403 等）は HTML 本文＝r.json() が落ちるので先に弾く。
    if r.status_code >= 400:
        raise HTTPException(r.status_code, _demo_error_detail(r))
    out = r.json()
    # "applied" は demo がアクティブな fault に対して対処を実評価したときだけ返る（fault 無しの
    # recovered:true では返らない）＝それを条件にし、適用していない対処を記録に捏造しない（§20）。
    if "applied" in out:
        # 台帳には承認試行を成功・失敗とも記録する（成功だけ並べる台帳は選択的表示＝§20違反。
        # 「誤った対処では復旧しない」安全境界の実記録にもなる）。
        row = _ledger_incident(_last_incident.get("injected_ts"))
        if row is not None:
            attempt = {"action": out["applied"], "recovered": bool(out.get("recovered")),
                       "ts": time.time()}
            row["attempts"].append(attempt)
            if len(row["attempts"]) > _ATTEMPTS_MAX:  # 行内上限＝落とした件数は正直に開示
                row["attempts_dropped"] = (row.get("attempts_dropped", 0)
                                           + len(row["attempts"]) - _ATTEMPTS_MAX)
                del row["attempts"][:-_ATTEMPTS_MAX]
            if attempt["recovered"]:
                row["recovered_ts"] = attempt["ts"]
                if isinstance(row.get("injected_ts"), (int, float)):
                    row["duration_s"] = round(attempt["ts"] - row["injected_ts"], 1)
    if out.get("recovered") and "applied" in out:
        _last_incident["recovery"] = {
            "applied": out["applied"],
            "ts": time.time(),
            "incident_id": _last_incident.get("injected_ts"),
        }
    return out


@app.post("/api/contain", dependencies=[Depends(require_console_auth)])
def contain() -> dict:
    """うだつ（延焼防止・隔離）＝ demo の /admin/contain へのプロキシ。

    fault は残したままユーザ向け影響の悪化を止める（完全復旧は /api/recover）。
    fault 無しは demo が 400 を返す＝素通し。封じ込め時刻は台帳の該当インシデント行
    （contained_ts）と _last_incident（ポストモーテム材料）に実タイムスタンプで記録する。
    """
    try:
        r = _client.post(f"{DEMO_URL}/admin/contain", headers=_demo_headers())
    except httpx.HTTPError as e:
        raise HTTPException(502, f"demo に接続できません: {e}") from e
    # demo 側の 4xx（fault 無し 400 等）/5xx は素通し（IAM 403 の HTML 本文にも安全）。
    if r.status_code >= 400:
        raise HTTPException(r.status_code, _demo_error_detail(r))
    out = r.json()
    ts = time.time()
    row = _ledger_incident(_last_incident.get("injected_ts"))
    if row is not None:
        row["contained_ts"] = ts
    _last_incident["containment"] = {"ts": ts,
                                     "incident_id": _last_incident.get("injected_ts")}
    return {"contained": True, "state": out.get("state")}


# --- 半鐘（通知発信）＝影響を知らせる --------------------------------------
# 通知先は運用者がデプロイ時に env HIKESHI_NOTIFY_WEBHOOK で固定する（Slack 互換
# incoming webhook）。リクエストから URL は一切受けない＝公開ユーザが宛先を指定
# できず SSRF 面を作らない。本文は demo の実観測と台帳の実記録だけからサーバ側で
# 決定的に組み立てる（値の捏造・選択的表示・黙ったフォールバック禁止・§20）。
_NOTIFY_TIMEOUT_S = 5.0


def _notify_text(metrics: dict, state: dict) -> str:
    """半鐘の通知本文。実観測値（metrics/state）と台帳の実記録のみ・決定的。

    値が実観測に無い項目は行ごと省く（無い値を作らない）。継続時間は台帳の
    injected_ts（注入のサーバ実測時刻）起点。診断要約は台帳 verdict がある時だけ。
    """
    fault = metrics.get("fault_active") or state.get("fault")
    lines = ["【半鐘】Hikeshi 状況通知"]
    if fault:
        contained = bool(state.get("contained") or metrics.get("contained"))
        lines.append(f"状態: 障害あり（{fault}）"
                     + ("・封じ込め済み（うだつ）" if contained else ""))
    else:
        lines.append("状態: 障害なし（平常）")
    er, p95 = metrics.get("error_rate"), metrics.get("latency_p95_ms")
    if isinstance(er, (int, float)):
        lines.append(f"error_rate: {er}")
    if isinstance(p95, (int, float)):
        lines.append(f"p95: {p95}ms")
    row = _ledger_incident(_last_incident.get("injected_ts"))
    if fault and row is not None and isinstance(row.get("injected_ts"), (int, float)):
        dur = max(0, int(time.time() - row["injected_ts"]))
        lines.append(f"継続: 約{dur}秒（注入からのサーバ実測）")
    if row is not None and isinstance(row.get("verdict"), dict):
        v = row["verdict"]
        lines.append(f"直近診断: {v.get('remediation_type')}（確信度 {v.get('confidence')}）")
    return "\n".join(lines)


@app.post("/api/notify", dependencies=[Depends(require_console_auth)])
def notify() -> dict:
    """半鐘（通知発信）：demo の現在の実状態を運用者設定の webhook へ知らせる。

    HIKESHI_NOTIFY_WEBHOOK 未設定は 409 で正直に断る（黙って成功を装わない）。
    送信は Slack 互換 {"text": ...} の POST（timeout 5s）。送信失敗は 502。台帳には
    成功・失敗の両方を記録する（成功だけ残す台帳は選択的表示＝§20違反）。宛先 URL
    は台帳・応答に載せない（Slack webhook URL は秘匿情報）。
    """
    webhook = os.environ.get("HIKESHI_NOTIFY_WEBHOOK", "").strip()
    if not webhook:
        raise HTTPException(409, "通知先が未設定です（HIKESHI_NOTIFY_WEBHOOK）＝デモでは省略可")
    try:
        metrics, state, _health = _observe_demo()
    except (httpx.HTTPError, ValueError) as e:
        # 観測できなければ本文を作れない＝送信前に 502（台帳の notify 行も書かない：
        # 送信を試みていない事実のまま）。
        raise HTTPException(502, f"demo に接続できません: {e}") from e
    text = _notify_text(metrics, state)
    _enforce_notify_rate_limit()  # 枠の消費は実送信の直前＝観測失敗では浪費しない
    err: str | None = None
    try:
        r = httpx.post(webhook, json={"text": text}, timeout=_NOTIFY_TIMEOUT_S)
        if r.status_code >= 400:
            err = f"webhook が {r.status_code} を返しました"
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        err = f"webhook に接続できません: {e}"
    _ledger_append({"type": "hansho_notify", "ts": time.time(), "ok": err is None})
    if err is not None:
        raise HTTPException(502, f"通知を送信できませんでした: {err}")
    return {"sent": True}


def _friendly_llm_error(msg: str) -> str:
    """LLM 実行エラーを判定員にも分かる言葉へ。原文はサーバログに残す（§20）。

    429 の原因（自プロジェクトの quota か全体の混雑か）は外形から確定できないため断定しない。
    """
    if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
        print(f"LLM error (shown as rate-limit message): {msg[:500]}", flush=True)
        return (
            "LLM API のレート上限に達しました（429）。"
            "30秒〜数分おいて「診断」を再試行してください。"
        )
    return msg


def _observe_demo() -> tuple[dict, dict, dict]:
    """demo の実観測（/metrics・/admin/state・best-effort /healthz）。

    診断と signals 表示の共通経路（二重実装を作らない）。
    """
    metrics = _demo_get_required("/metrics")
    state = _demo_get_required("/admin/state")
    health = _demo_get_json("/healthz", {})  # 補助＝失敗しても診断は止めない（health は未使用）
    return metrics, state, health


def _gather_diagnose_inputs() -> tuple[dict | None, str | None]:
    """demo を HTTP 観測して signals を作る。fault が無ければ (None, None)。"""
    metrics, state, health = _observe_demo()
    fault = metrics.get("fault_active") or state.get("fault")
    if not fault:
        return None, None
    from hikeshi_agent.live import signals_from_demo  # 純関数・オフライン可

    return signals_from_demo(metrics, health, state), fault


def _verdict_response(out, fault: str | None) -> dict:
    """AgentOutput → API 応答（/api/diagnose と /api/diagnose/stream の共通整形＋記録保持）。"""
    from hikeshi_agent.agent import MODEL_FLASH  # 判定(remediate)モデル＝出所表示用

    rag_on = os.environ.get("HIKESHI_RAG", "on").lower() not in ("off", "0", "false", "no")
    resp = {
        "no_fault": False,
        "root_cause_text": out.root_cause_text,
        "root_cause_category": out.root_cause_category,
        "remediation_type": out.remediation_type,
        "confidence": out.confidence,
        "requires_hitl": out.requires_hitl,
        "tool_trajectory": out.tool_trajectory,
        "tool_trajectory_detail": out.tool_trajectory_detail,
        "reasoning": out.reasoning,
        "evidence": out.evidence,                 # [{fact, source}, ...]＝判定の根拠（出典つき）
        "remediation_plan": out.remediation_plan,  # [str, ...]＝具体的対処手順（提案・未実行）
        "cost_yen": out.cost_yen,
        "latency_ms": out.latency_ms,
        # 期待対処は診断"後"にのみ開示（答え合わせ用・事前には出さない）。
        "expected_remediation": _expected_for(fault),
        "model": MODEL_FLASH,
        "temperature": 0.0,
        # RAG が実 KB に接続しているか（HIKESHI_RAG=off で従来の未接続挙動）。
        "kb_connected": rag_on,
    }
    # ポストモーテム生成の材料として実診断記録を保持（答え合わせ用の expected は含めない）。
    # 前インシデントの recovery が残っていれば破棄＝新診断と旧復旧の混成ドラフトを防ぐ（§20）。
    _last_incident.pop("recovery", None)
    _last_incident["verdict"] = {
        **{k: resp[k]
           for k in ("root_cause_text", "root_cause_category", "remediation_type",
                     "confidence", "tool_trajectory", "cost_yen", "latency_ms")},
        "incident_id": _last_incident.get("injected_ts"),
    }
    # 台帳の該当インシデント行にも診断要約を添付（履歴ページ用・実測値そのまま）。
    # verdict は「直近の診断」、コストは全 run の累積＝再診断しても合計が過少表示にならない。
    row = _ledger_incident(_last_incident.get("injected_ts"))
    if row is not None:
        row["verdict"] = dict(_last_incident["verdict"])
        row["diag_runs"] = row.get("diag_runs", 0) + 1
        if isinstance(resp.get("cost_yen"), (int, float)):
            row["cost_total_yen"] = round(row.get("cost_total_yen", 0.0) + resp["cost_yen"], 2)
    return resp


_NO_FAULT_MSG = "障害が注入されていません。まず障害を注入してください。"


@app.post("/api/diagnose", dependencies=[Depends(require_console_auth)])
def diagnose() -> dict:
    """demo を HTTP 観測 → signals へ写像 → 実エージェントで診断 → IncidentVerdict を返す。"""
    try:
        signals, fault = _gather_diagnose_inputs()
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(502, f"demo に接続できません: {e}") from e
    if not signals:
        return {"no_fault": True, "message": _NO_FAULT_MSG}
    _enforce_llm_rate_limit()  # fault があり実 LLM を呼ぶ時だけ消費（no_fault は無料経路）

    # 遅延 import＝console 起動/UI 配信/状態取得は adk・鍵が無くても動く。
    from hikeshi_agent.live import diagnose_live

    try:
        out = diagnose_live(signals)
    except RuntimeError as e:  # 鍵未設定/実行失敗 → 503(§20: 黙って偽値を返さない)
        raise HTTPException(503, _friendly_llm_error(str(e))) from e
    return _verdict_response(out, fault)


@app.post("/api/webhook/alert", dependencies=[Depends(require_console_auth)])
def webhook_alert() -> dict:
    """アラート連動の受け口＝実運用で PagerDuty/Alertmanager/Cloud Monitoring が叩く線。

    アラート本文（誰がどんな根因を主張するか）は**信用せず**、demo の実状態を自分で再観測して
    診断する＝偽アラートから判定を捏造する経路が無い（§20）。返り値は /api/diagnose と同一。
    本番は webhook の署名検証や重複抑止を足す（roadmap）。
    """
    return diagnose()


@app.post("/api/diagnose/stream", dependencies=[Depends(require_console_auth)])
def diagnose_stream() -> StreamingResponse:
    """診断のライブストリーミング（SSE）。サブエージェント完了ごとに実推論を流す。

    events: phase（実推論＋実ツール）／retry（一時失敗で再試行）／verdict（/api/diagnose と
    同一の最終 JSON）／no_fault／error（鍵なし・全試行失敗＝偽値は流さない・§20）。
    既存の POST /api/diagnose は無変更＝後方互換・bench 経路にも影響しない。
    """
    try:
        signals, fault = _gather_diagnose_inputs()
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(502, f"demo に接続できません: {e}") from e
    if signals:
        _enforce_llm_rate_limit()  # 429 は SSE 開始前に HTTP で返す（no_fault は消費しない）

    def _sse(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    def gen():
        if not signals:
            yield _sse({"type": "no_fault", "message": _NO_FAULT_MSG})
            return
        # 遅延 import＝鍵が無くても endpoint 自体は応答できる（error イベントで返す）。
        from hikeshi_agent.live import live_brief
        from hikeshi_agent.runtime import run_signals_stream

        rag_on = os.environ.get("HIKESHI_RAG", "on").lower() not in ("off", "0", "false", "no")
        try:
            for kind, payload in run_signals_stream(signals, "live", live_brief(signals)):
                if kind == "phase":
                    if payload.get("phase") == "rag":
                        # ストリーム中も KB 接続状態を正直に表示
                        payload = {**payload, "kb_connected": rag_on}
                    yield _sse({"type": "phase", **payload})
                elif kind == "retry":
                    yield _sse({"type": "retry", **payload})
                elif kind == "output":
                    yield _sse({"type": "verdict", **_verdict_response(payload, fault)})
        except Exception as e:  # noqa: BLE001 — ヘッダ送信後は error イベントで明示（無言の切断にしない）
            yield _sse({"type": "error", "message": _friendly_llm_error(str(e))})

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- ポストモーテム→KB 提案ループ（自己改善・HITL） ---------------------
# 復旧完了後：実診断記録から決定的にドラフト生成（LLM 創作なし＝§20）→ 人間が編集・
# 承認したものだけ kb/postmortems/ に着地 → 即時再索引＝次の診断から検索対象。
class _ApproveBody(BaseModel):
    markdown: str
    filename: str | None = None


def _on_cloud_run() -> bool:
    return bool(os.environ.get("K_SERVICE"))


# --- コックピット・ページ（サービス/Runbook/履歴）用の読み取り専用 API -----
# すべて GET・状態変更なし・LLM 不要。表示値は実観測値/実ファイル/実記録のみ（§20）。


@app.get("/api/signals", dependencies=[Depends(require_console_auth)])
def signals_view() -> dict:
    """エージェントが診断時に受け取る signals 契約（ARCHITECTURE §5.1）の現在値プレビュー。

    診断と同じ観測（`_observe_demo`）に同じ純関数 `signals_from_demo` を適用する＝
    「診断を実行した時点でエージェントへ渡る入力」の忠実な写像。fault が無い間は
    診断エージェント自体が起動しない（その事実も fault=None としてそのまま返す）。
    """
    try:
        metrics, state, health = _observe_demo()
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(502, f"demo に接続できません: {e}") from e
    from hikeshi_agent.live import signals_from_demo  # 純関数・オフライン可

    return {
        "fault": metrics.get("fault_active") or state.get("fault"),
        "raw": {"metrics": metrics, "state": state},
        "signals": signals_from_demo(metrics, health, state),
        "demo_url": DEMO_URL,
    }


@app.get("/api/kb/list", dependencies=[Depends(require_console_auth)])
def kb_list() -> dict:
    """KB（エージェントの検索対象そのもの）の doc 一覧。

    承認されたポストモーテムも再索引後ここに現れる。
    """
    from hikeshi_agent.retriever import list_docs

    try:
        docs = list_docs()
    except RuntimeError as e:
        raise HTTPException(
            503, "KB バックエンドに接続できません。少し待って再試行してください。") from e
    return {"docs": docs, "kb_size": len(docs), "persistent": not _on_cloud_run()}


@app.get("/api/kb/doc", dependencies=[Depends(require_console_auth)])
def kb_doc(source: str) -> dict:
    """KB doc 本文。索引済みの source 名への完全一致のみ（パスは受けない＝トラバーサル不可）。"""
    from hikeshi_agent.retriever import get_doc

    try:
        doc = get_doc(source)
    except RuntimeError as e:
        raise HTTPException(
            503, "KB バックエンドに接続できません。少し待って再試行してください。") from e
    if doc is None:
        raise HTTPException(404, "KB にその doc はありません")
    return doc


@app.get("/api/kb/search", dependencies=[Depends(require_console_auth)])
def kb_search(q: str = "", kind: str | None = None, k: int = 5) -> dict:
    """エージェントと同一の retriever.search() を素通しする（スコア＝tf-idf 生値・決定的）。"""
    from hikeshi_agent.retriever import search

    q = (q or "").strip()
    if not q:
        raise HTTPException(400, "q（検索語）を指定してください")
    if kind not in (None, "", "runbook", "postmortem", "incident", "advisory"):
        raise HTTPException(400, "kind は runbook / postmortem / incident / advisory のいずれか")
    try:
        hits = search(q, kind=kind or None, k=max(1, min(int(k), 8)))
    except RuntimeError as e:
        raise HTTPException(
            503, "KB バックエンドに接続できません。少し待って再試行してください。") from e
    return {"query": q, "kind": kind or None, "hits": hits}


@app.get("/api/incidents", dependencies=[Depends(require_console_auth)])
def incidents() -> dict:
    """セッション内の実記録台帳（履歴ページ用）。承認試行は成功・失敗とも返す（§20）。"""
    inc = [e for e in _ledger if e.get("type") == "incident"]
    durations = [e["duration_s"] for e in inc if isinstance(e.get("duration_s"), (int, float))]
    # コストは行ごとの累積（cost_total_yen）を合算＝同一インシデントの再診断分も含む実支出。
    costs = [e.get("cost_total_yen") for e in inc]
    costs = [c for c in costs if isinstance(c, (int, float))]
    return {
        "entries": list(reversed(_ledger)),  # 新しい順
        "summary": {
            "incidents": len(inc),
            "diagnosed": sum(1 for e in inc if e.get("verdict")),
            "recovered": sum(1 for e in inc if e.get("recovered_ts")),
            "avg_recovery_s": round(sum(durations) / len(durations), 1) if durations else None,
            "total_cost_yen": round(sum(costs), 2) if costs else None,
        },
        # Cloud Run はエフェメラル＝台帳はインスタンス寿命のみ（正直に開示）。
        "persistent": not _on_cloud_run(),
    }


@app.post("/api/depscan", dependencies=[Depends(require_console_auth)])
def depscan_endpoint() -> dict:
    """防火：合成サンプル manifest を OSV で実スキャン→LLM 安全性評価→更新提案（HITL・提案のみ）。

    脆弱性データは OSV の実物（GHSA/CVE/PYSEC）。スキャン対象 requirements は合成サンプル。
    自動更新・PR 作成はしない＝提案を人間がレビュー・承認して別途実行する（§20・防火）。
    """
    _enforce_llm_rate_limit()  # LLM 安全性評価＋外部 OSV 呼び出しを持つコスト経路
    from hikeshi_agent import depscan

    base = pathlib.Path(depscan.__file__).parent / "depscan_sample"
    req = (base / "requirements.txt").read_text(encoding="utf-8")
    report = depscan.scan(req, cache_dir=base / "osv_cache", allow_network=True)
    out = depscan.report_to_dict(report)
    out["requirements"] = req
    # LLM 安全性評価（best-effort：鍵が無ければ決定的結果のみ返す＝偽値は作らない）。
    try:
        from hikeshi_agent.depscan_agent import assess

        amap = assess(out["findings"])
        for f in out["findings"]:
            f["assessment"] = amap.get(f["package"])
        out["assessment_available"] = bool(amap)
    except RuntimeError:
        out["assessment_available"] = False
    # 防火イベントも台帳へ（履歴ページで消火と並ぶ・実行時刻と実検出数のみ記録）。
    _ledger_append({"type": "depscan", "ts": time.time(),
                    "findings": len(out["findings"]),
                    "assessment_available": out["assessment_available"]})
    return out


# --- 夜回り（Yomawari）: セキュリティ定期巡回で火元帳(KB)を最新化 -------------------------
# 汎用 LLM の知識カットオフを超えて、CISA KEV（実際に悪用中）と GitHub Advisory を定期取得し
# advisory 種別として KB に蓄える。取得URLは固定の一次情報源のみ（リクエスト由来URLは受けない）。


@app.get("/api/securitywatch", dependencies=[Depends(require_console_auth)])
def securitywatch_status() -> dict:
    """夜回りの現況（最終巡回・蓄積件数・最新 KEV 数点）を返す（読み取り専用・状態変更なし）。"""
    from hikeshi_agent import securitywatch as sw

    manifest = sw.current_manifest()  # コミット済み/直近取得のマニフェスト（無ければ空）
    newest = sw.newest_kev(limit=5)   # 表示用の最新 KEV 数点（実データ・キャッシュから）
    return {"manifest": manifest, "newest_kev": newest,
            "advisory_count": len([e for e in (manifest.get("advisory_docs") or [])]),
            "persistent": not _on_cloud_run()}


@app.post("/api/securitywatch/refresh", dependencies=[Depends(require_console_auth)])
def securitywatch_refresh() -> dict:
    """夜回りを実行：一次情報源から取得→ KB(advisories) を最新化→即時再索引（Cloud Scheduler 用）。

    LLM は使わないが外部取得コスト経路のため LLM 経路と同じレート制限で保護する。
    取得失敗は 502（偽値で埋めない）。成功後は retriever.reindex() で即座に検索対象化。
    """
    _enforce_llm_rate_limit()
    from hikeshi_agent import securitywatch as sw
    from hikeshi_agent.retriever import reindex

    try:
        manifest = sw.refresh(allow_network=True)
    except sw.SecurityWatchError as e:
        raise HTTPException(502, f"夜回りの取得に失敗しました: {e}") from e
    kb_size = reindex()
    _ledger_append({"type": "yomawari", "ts": time.time(),
                    "kev_count": manifest.get("kev_count"),
                    "ghsa_count": manifest.get("ghsa_count")})
    return {"refreshed": True, "manifest": manifest, "kb_size": kb_size,
            "persistent": not _on_cloud_run()}


@app.post("/api/postmortem/draft", dependencies=[Depends(require_console_auth)])
def postmortem_draft() -> dict:
    """直近の「診断→承認→復旧」からポストモーテム案を生成する（保存はしない）。"""
    verdict = _last_incident.get("verdict")
    recovery = _last_incident.get("recovery")
    if not verdict or not recovery:
        msg = "復旧まで完了したインシデントがありません（診断→承認→復旧の後に呼ぶ）"
        raise HTTPException(409, msg)
    if verdict.get("incident_id") != recovery.get("incident_id"):
        # 並行操作等で診断と復旧が別インシデントに属する場合は混成ドラフトを作らない（§20）。
        msg = "診断と復旧が同一インシデントではありません（最初からやり直してください）"
        raise HTTPException(409, msg)
    from hikeshi_agent.postmortem import build_draft  # 純標準ライブラリ・遅延 import

    duration = None
    if _last_incident.get("injected_ts"):
        duration = max(0.0, recovery["ts"] - _last_incident["injected_ts"])
    return {**build_draft(verdict, recovery, duration), "persistent": not _on_cloud_run()}


@app.post("/api/postmortem/approve", dependencies=[Depends(require_console_auth)])
def postmortem_approve(body: _ApproveBody) -> dict:
    """人間が編集・承認したポストモーテムを KB へ保存し、即時再索引する（HITL の出口）。"""
    _enforce_kb_write_rate_limit()  # 公開モードでは KB への永続化経路＝頻度を絞る
    from hikeshi_agent.postmortem import save_to_kb
    from hikeshi_agent.retriever import reindex

    try:
        path = save_to_kb(body.markdown, body.filename)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    try:
        n = reindex()
    except RuntimeError as e:
        # 保存自体は完了している＝失敗を隠さず、次の検索時の自己修復で再構築される旨を伝える。
        raise HTTPException(503, "承認内容は保存済みですが、KB の再索引に一時失敗しました。"
                                 "次の検索時に自動で再構築されます。") from e
    return {
        "saved": True,
        "source": path.name,
        "kb_size": n,
        # Cloud Run のファイルシステムはエフェメラル＝インスタンス寿命のみ（正直に開示）。
        "persistent": not _on_cloud_run(),
    }


if __name__ == "__main__":
    import uvicorn

    if not CONSOLE_TOKEN:
        # 公開デプロイの既定＝トークン無し。LLM コスト経路はアプリ層レート制限で保護。
        print("WARN: HIKESHI_CONSOLE_TOKEN 未設定＝公開モード（レート制限あり）", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8081")))
