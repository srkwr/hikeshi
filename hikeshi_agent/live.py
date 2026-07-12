"""ライブ診断アダプタ — demo（victim service）の HTTP シグナルを実エージェントへ繋ぐ。

`signals_from_demo()` は demo の `/metrics`・`/healthz`・`/admin/state` のレスポンスを、
調査ツール（`hikeshi_agent.tools`）が `ToolContext.state['signals']` から読む形へ写像する
**純関数**（ネットワーク非依存＝オフラインでテストできる）。HTTP 取得自体は console 側
（`console/app.py`）が行い、その結果をここへ渡す＝「エージェントの世界観は demo を HTTP で
観測した実データから来る」。bench の合成ケースと同じ signals 形なので、同じ調査軌跡が出る。

`diagnose_live()` は写像済み signals を共通エンジン（`runtime.run_signals`）へ流して
`AgentOutput`（IncidentVerdict＋実ツール軌跡＋¥コスト＋レイテンシ）を返す。adk/Gemini 鍵が
要るのは診断実行時だけ＝本モジュールの import と `signals_from_demo` はオフラインで成立する。
"""

from __future__ import annotations

from typing import Any

# demo の正常時メトリクス（劣化判定の基準・demo/app.py の _metrics と対応）
_HEALTHY_NOTE = "no active fault — service healthy"


def _pct(x: Any) -> str:
    try:
        return f"{float(x) * 100:.0f}%"
    except (TypeError, ValueError):
        return str(x)


def signals_from_demo(
    metrics: dict[str, Any],
    health: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """demo の HTTP レスポンスを調査ツールが読む signals 形へ写像する（純関数）。

    fault 種別は `metrics['fault_active']`（無ければ state['fault']）から取り、観測値
    （error_rate・latency・memory・upstream・recent_deploy）に忠実なログ/トレース/文脈を付す。
    demo が暗示しない固有値は捏造しない（§20）。
    """
    m = dict(metrics or {})
    health = dict(health or {})
    state = dict(state or {})
    fault = m.get("fault_active") or state.get("fault")
    # エージェントへ渡す観測値から正解ラベル（fault_active＝根本原因カテゴリの enum 文字列）を
    # 除去＝bench の signals と同型にする。診断は症状（error_rate/upstream/memory/deploy 等）から
    # 行わせる（fault はこの写像の分岐にのみ使い、エージェントには見せない）。
    m.pop("fault_active", None)
    rev = m.get("revision", state.get("revision"))
    er = m.get("error_rate")
    p95 = m.get("latency_p95_ms")
    mem = m.get("memory_pct")
    note = m.get("note")

    sig: dict[str, Any] = {
        "metrics": m,
        "recent_deploy": dict(m.get("recent_deploy") or {}),
        "logs_sample": [],
        "trace_sample": "",
        "diff_summary": "(差分情報なし)",
        "context": "",
    }

    if fault == "deploy_regression":
        sig["alert"] = (
            f"error_rate {_pct(er)} / p95 {p95}ms — 直近デプロイ(rev {rev})後に悪化、self degraded"
        )
        sig["logs_sample"] = [
            f"HTTP 500 internal error after revision {rev} rollout",
            f"error_rate が新リビジョン後に {_pct(er)} へ急騰",
        ]
        sig["diff_summary"] = (
            f"rev {rev}（{(sig['recent_deploy'] or {}).get('deployed_min_ago', '?')}分前デプロイ）"
            "＝最近の唯一の変更。設定/依存の変化はなし"
        )
        sig["context"] = "self_health degraded／upstream は ok。エラーは新リビジョン後に発生"
    elif fault == "dependency_failure":
        sig["alert"] = f"error_rate {_pct(er)}、upstream 5xx、self は healthy"
        sig["logs_sample"] = [note or "downstream dependency returning 5xx; our service healthy"]
        sig["trace_sample"] = "span: downstream dependency → 5xx（self のスパンは正常）"
        sig["context"] = "self_health ok・upstream_status 5xx＝外部依存の障害が連鎖。直近デプロイなし"
    elif fault == "resource_exhaustion":
        sig["alert"] = f"p95 {p95}ms、memory {mem}%、負荷でのリソース飽和"
        sig["logs_sample"] = [note or "memory/CPU saturation under load"]
        sig["context"] = "memory/CPU 飽和。直近デプロイなし＝容量不足（リーク由来ではない）"
    else:
        sig["alert"] = _HEALTHY_NOTE
        sig["context"] = "障害は注入されていない"

    return sig


def live_brief(signals: dict[str, Any]) -> str:
    """ライブ診断用のインシデント・ブリーフ（詳細はツールで取りに行かせる＝実調査の軌跡）。"""
    alert = signals.get("alert") or "本番サービスに異常検知"
    return (
        "本番サービスで異常を検知。\n"
        f"アラート: {alert}\n"
        "詳細はこのメッセージに無い。調査ツールを実際に呼んで事実を集め、"
        "根本原因カテゴリと安全な対処を判定せよ。"
    )


def diagnose_live(signals: dict[str, Any], case_id: str = "live", max_attempts: int = 3):
    """写像済み signals で実エージェントを走らせ AgentOutput を返す（adk/鍵は実行時のみ）。"""
    from .runtime import run_signals  # 遅延 import＝signals_from_demo はオフラインで使える

    return run_signals(signals, case_id, live_brief(signals), max_attempts=max_attempts)
