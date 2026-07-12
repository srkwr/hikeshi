"""INCIDENT-BENCH ↔ ADK エージェントのアダプタ。

`run_case(case)` が 1 ケースを実エージェントで処理し、AgentOutput 契約へ整形する。
この関数を incident_bench/baselines.py が遅延 import で呼ぶ（reference/naive に
adk 依存を持ち込まない）。

Gemini 接続が必要：GOOGLE_API_KEY（AI Studio 無料キー）または Vertex/ADC。
未設定なら明示的に失敗する（§20：黙って偽の値を返さない）。
木の構築・本モジュールの import はオフラインで成立する＝鍵は実行時のみ。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from incident_bench.schema import AgentOutput, IncidentCase

from .agent import root_agent
from .contract import IncidentVerdict

APP = "hikeshi"

# モデル価格（USD / 1M tok・in,out・docs/ARCHITECTURE.md §8）。
_PRICE_USD = {"flash_lite": (0.25, 1.50), "flash": (1.50, 9.00), "pro": (2.0, 12.0)}
# サブエージェント→モデルティア（agent.py の割当と一致）。
_AGENT_TIER = {"triage": "flash_lite", "investigate": "flash", "rag": "flash_lite", "remediate": "flash"}
# 為替は変動＝env 上書き可（既定は概算の仮定値・§20）。
USD_JPY = float(os.environ.get("HIKESHI_USD_JPY", "150"))


def _event_cost_usd(event) -> float:
    """1 イベントのトークン使用量を author のモデル価格で USD 換算（不明 author は flash 上限寄り）。"""
    um = getattr(event, "usage_metadata", None)
    if not um:
        return 0.0
    pin, pout = _PRICE_USD[_AGENT_TIER.get(getattr(event, "author", ""), "flash")]
    pt = getattr(um, "prompt_token_count", 0) or 0
    ot = (getattr(um, "candidates_token_count", 0) or 0) + (getattr(um, "thoughts_token_count", 0) or 0)
    return pt / 1e6 * pin + ot / 1e6 * pout


def _have_credentials() -> bool:
    if os.environ.get("GOOGLE_API_KEY"):
        return True  # AI Studio 無料キー（dev）
    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true", "yes"):
        return True  # Vertex/ADC（prod・gcloud auth application-default login 済み前提）
    return False


def _incident_brief(case: IncidentCase) -> str:
    # 詳細はブリーフに載せない＝エージェントがツールで取りに行く（実調査の軌跡が出る）。
    alert = case.signals.get("alert") or case.title
    return (
        f"インシデント『{case.title}』(難易度: {case.difficulty})。\n"
        f"アラート: {alert}\n"
        "詳細はこのメッセージに無い。調査ツールを実際に呼んで事実を集め、"
        "根本原因カテゴリと安全な対処を判定せよ。"
    )


def _dedup_keep_order(xs: list[str]) -> list[str]:
    seen: set = set()
    out: list[str] = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _dedup_pairs(xs: list[dict]) -> list[dict]:
    """(agent, tool) ペアを順序保持で一意化（表示用トレース）。"""
    seen: set = set()
    out: list[dict] = []
    for x in xs:
        k = (x.get("agent"), x.get("tool"))
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _coerce_verdict(raw: Any) -> IncidentVerdict:
    if isinstance(raw, IncidentVerdict):
        return raw
    if isinstance(raw, dict):
        return IncidentVerdict(**raw)
    if isinstance(raw, str):
        return IncidentVerdict(**json.loads(raw))
    raise RuntimeError(f"remediate が IncidentVerdict を生成しませんでした: {type(raw)!r}")


# サブエージェント→output_key（agent.py の output_key と一致。remediate は verdict）。
_PHASE_KEY = {"triage": "triage", "investigate": "investigation", "rag": "evidence"}


def _run_once_stream(signals: dict, case_id: str, brief: str, attempt: int):
    """1 試行の実行を**逐次イベント**で yield する単一実装（bench/ライブ/ストリーミング共通）。

    yield: ("phase", {"phase","reasoning","tools"}) … サブエージェント完了ごと
           ("output", AgentOutput)                  … 最後に1回（従来 _run_once の戻り値と同一）
    bench は `_run_once()` がこのジェネレータを消費して output だけ返す＝経路は1本のまま。
    """
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    runner = InMemoryRunner(agent=root_agent, app_name=APP)
    sid = f"case-{case_id}-a{attempt}"
    runner.session_service.create_session_sync(
        app_name=APP,
        user_id="bench",
        session_id=sid,
        state={"signals": signals, "case_id": case_id},
    )
    msg = types.Content(role="user", parts=[types.Part.from_text(text=brief)])

    def _phase_event(author: str) -> tuple | None:
        """完了したサブエージェントの実推論をセッション state から読んで phase イベント化。"""
        if author not in _PHASE_KEY and author != "remediate":
            return None
        s = runner.session_service.get_session_sync(app_name=APP, user_id="bench", session_id=sid)
        st = (s.state or {}) if s else {}
        text = str(st.get(_PHASE_KEY.get(author, ""), "") or "")
        tools = [d["tool"] for d in _dedup_pairs(detail) if d.get("agent") == author]
        return ("phase", {"phase": author, "reasoning": text, "tools": tools})

    trajectory: list[str] = []
    detail: list[dict] = []
    cost_usd = 0.0
    current_author = ""
    t0 = time.perf_counter()
    for event in runner.run(user_id="bench", session_id=sid, new_message=msg):
        content = getattr(event, "content", None)
        author = getattr(event, "author", "") or ""
        for part in getattr(content, "parts", None) or []:
            fc = getattr(part, "function_call", None)
            if fc and getattr(fc, "name", None):
                trajectory.append(fc.name)
                detail.append({"agent": author, "tool": fc.name})
        cost_usd += _event_cost_usd(event)
        # author の遷移＝直前サブエージェントの完了（SequentialAgent は直列・§3）。
        if author and author != current_author:
            if current_author:
                ev = _phase_event(current_author)
                if ev:
                    yield ev
            current_author = author
    latency_ms = int((time.perf_counter() - t0) * 1000)

    sess = runner.session_service.get_session_sync(app_name=APP, user_id="bench", session_id=sid)
    st = (sess.state or {}) if sess else {}
    raw = st.get("verdict")
    verdict = _coerce_verdict(raw)  # None/未パースなら raise
    # 最終サブエージェント（remediate）の完了 phase（verdict 確定後にのみ通知）。
    if current_author:
        ev = _phase_event(current_author)
        if ev:
            yield ev
    # 各サブエージェントが output_key で残した実際の推論テキスト（表示用＝採点には不使用）。
    reasoning = {k: str(st[k]) for k in ("triage", "investigation", "evidence") if st.get(k)}

    yield (
        "output",
        AgentOutput(
            case_id=case_id,
            root_cause_text=verdict.root_cause_text,
            root_cause_category=verdict.root_cause_category,
            tool_trajectory=_dedup_keep_order(trajectory),
            remediation_type=verdict.remediation_type,
            confidence=float(verdict.confidence),
            # 安全境界をコードで強制する（プロンプト/契約の記述だけに依存しない＝多層防御）。
            # 自動許容は rollback のみ＝rollback 以外は、モデルが何を返しても必ず HITL にする。
            # scorer の unsafe_auto_exec（非rollbackの自動実行を不合格）と同じ規律をライブ経路でも保証。
            requires_hitl=bool(verdict.requires_hitl) or verdict.remediation_type != "rollback",
            cost_yen=round(cost_usd * USD_JPY, 3),
            latency_ms=latency_ms,
            tool_trajectory_detail=_dedup_pairs(detail),
            reasoning=reasoning,
            evidence=[{"fact": e.fact, "source": e.source} for e in verdict.evidence],
            remediation_plan=list(verdict.remediation_plan),
        ),
    )


def _run_once(signals: dict, case_id: str, brief: str, attempt: int) -> AgentOutput:
    """1 試行ぶんの実行（セッションは試行ごとに分離）。verdict が無ければ raise。

    bench（case.signals）でもライブ（demo の HTTP から写像した signals）でも同じ経路
    （_run_once_stream を消費して最終 AgentOutput のみ返す）。
    """
    for kind, payload in _run_once_stream(signals, case_id, brief, attempt):
        if kind == "output":
            return payload
    raise RuntimeError("stream ended without output")  # _coerce_verdict が先に raise するため通常到達しない


def run_signals(
    signals: dict, case_id: str, brief: str, max_attempts: int = 3
) -> AgentOutput:
    """与えられた signals で実 ADK エージェントを1回処理し AgentOutput に整形して返す。

    bench（`run_case`）とライブ診断（`hikeshi_agent.live`）の共通エンジン。
    構造化出力(verdict)が空／一時的な API エラー時は指数バックオフで再試行する
    （Flash の構造化出力はまれに空を返す＝本番運用耐性）。鍵が無ければ即失敗（§20）。
    """
    if not _have_credentials():
        raise RuntimeError(
            "Gemini 認証が未設定です。AI Studio 無料キーなら GOOGLE_API_KEY を、"
            "Vertex/ADC なら GOOGLE_GENAI_USE_VERTEXAI=1 ＋ "
            "`gcloud auth application-default login` を設定してください"
            "（モデル id は HIKESHI_MODEL_FLASH/_FLASH_LITE/_PRO で上書き可・§13）。"
        )
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _run_once(signals, case_id, brief, attempt)
        except Exception as e:  # noqa: BLE001 — 一時失敗は再試行し、最終的に明示 raise
            last_err = e
            if attempt < max_attempts:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"run が {max_attempts} 回失敗 (case={case_id}): {last_err}") from last_err


def run_signals_stream(signals: dict, case_id: str, brief: str, max_attempts: int = 3):
    """`run_signals` のストリーミング版（ライブ診断の進捗表示用）。

    yield: ("phase", {...})    … サブエージェント完了ごと（実推論テキスト＋実ツール）
           ("retry", {...})    … 一時失敗で再試行するとき（フロントはレーンを巻き戻す）
           ("output", AgentOutput) … 成功時の最終結果（run_signals と同一の整形）
    鍵が無い／全試行失敗は run_signals と同じ RuntimeError（§20: 黙って偽値を返さない）。
    採点（bench）はこの関数を使わない＝従来の run_signals/_run_once 経路のまま。
    既知の制約：クライアント切断後も ADK Runner の背景スレッドは当該 run を完走する
    （課金は最大1診断ぶん≒¥6。従来の一括 POST と同じプロファイル＝デモ規模では許容）。
    """
    if not _have_credentials():
        raise RuntimeError(
            "Gemini 認証が未設定です。AI Studio 無料キーなら GOOGLE_API_KEY を、"
            "Vertex/ADC なら GOOGLE_GENAI_USE_VERTEXAI=1 ＋ "
            "`gcloud auth application-default login` を設定してください"
            "（モデル id は HIKESHI_MODEL_FLASH/_FLASH_LITE/_PRO で上書き可・§13）。"
        )
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            yield from _run_once_stream(signals, case_id, brief, attempt)
            return
        except Exception as e:  # noqa: BLE001 — 一時失敗は再試行し、最終的に明示 raise
            last_err = e
            if attempt < max_attempts:
                yield ("retry", {"attempt": attempt + 1, "max_attempts": max_attempts})
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"run が {max_attempts} 回失敗 (case={case_id}): {last_err}") from last_err


def run_case(case: IncidentCase, max_attempts: int = 3) -> AgentOutput:
    """実 ADK エージェントで 1 ベンチケースを処理し AgentOutput に整形して返す。"""
    return run_signals(case.signals, case.id, _incident_brief(case), max_attempts)
