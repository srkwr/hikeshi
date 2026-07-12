"""防火：依存脆弱性スキャン結果の「安全性評価」エージェント（ADK・構造化出力・temp0）。

決定的コア（depscan.py）が OSV から得た**実データ**を入力に、各 package の
(1) なぜ危険か (2) 更新の破壊リスクと注意 (3) 更新後の検証方法 を簡潔に評価する。
事実は与えられた OSV データのみ＝捏造しない（§20）。**提案のみ**＝自動更新はしない（HITL）。

鍵が無ければ呼出時に RuntimeError（黙って偽値を返さない）。本モジュールの import は
オフラインで成立する（adk 取り込みは assess() 実行時のみ＝runtime.py と同じ契約）。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

MODEL_FLASH = os.environ.get("HIKESHI_MODEL_FLASH", "gemini-3.5-flash")
_APP = "hikeshi-depguard"

_INSTRUCTION = (
    "あなたは少人数SREチームのセキュリティ担当エージェント。入力は OSV.dev から取得した"
    "**実際の**依存脆弱性スキャン結果（package・現行版・推奨更新先・破壊リスク・CVE/GHSA とその要約）。\n"
    "各 package について以下を簡潔・事実先行で評価し、DepScanAssessment を出力せよ：\n"
    "  ・risk_summary：なぜ危険か。**与えられた CVE 要約に基づき**、悪用されると何が起きるかを1〜2文。\n"
    "  ・upgrade_note：現行版→推奨版の破壊リスク（major/minor/patch の差）に基づく更新時の注意を1文。\n"
    "  ・verification：更新後に確認すべきこと（影響機能の回帰テスト等）を1文。\n"
    "**与えられたデータに無い事実・CVE・版番号は作らない（§20）**。これは提案であり、"
    "実際の更新は人間がレビュー・承認してから行う（自動実行しない）。"
)


@lru_cache(maxsize=1)
def _build_agent():
    from google.adk.agents import LlmAgent
    from google.genai import types as genai_types
    from pydantic import BaseModel, Field

    class PackageAssessment(BaseModel):
        package: str = Field(description="対象 package 名")
        risk_summary: str = Field(description="なぜ危険か（実CVE要約に基づく1〜2文）")
        upgrade_note: str = Field(description="現行版→推奨版の破壊リスクと更新時の注意（1文）")
        verification: str = Field(description="更新後に確認すべきこと（1文）")

    class DepScanAssessment(BaseModel):
        assessments: list[PackageAssessment]

    agent = LlmAgent(
        name="depguard",
        model=MODEL_FLASH,
        description="依存脆弱性スキャン結果の安全性評価（提案のみ・HITL）。",
        instruction=_INSTRUCTION,
        output_schema=DepScanAssessment,
        generate_content_config=genai_types.GenerateContentConfig(temperature=0.0),
        output_key="assessment",
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
    return agent


def _brief(findings: list[dict[str, Any]]) -> str:
    lines = ["以下は OSV から取得した実際の脆弱性スキャン結果（合成サンプル manifest）。"]
    for f in findings:
        top = f.get("vulns", [])[:3]
        vt = "; ".join(
            f"{v['id']}({v['severity']}): {v['summary']}" for v in top if v.get("summary")
        )
        lines.append(
            f"- {f['package']} {f['current_version']} → 推奨 {f['recommended_version']}"
            f"（破壊リスク {f['breaking_risk']}・最悪 {f['max_severity']}）｜ {vt}"
        )
    return "\n".join(lines)


def _have_credentials() -> bool:
    if os.environ.get("GOOGLE_API_KEY"):
        return True
    return os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true", "yes")


def assess(findings: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """findings（depscan の report_to_dict()['findings']）に LLM 安全性評価を付す。

    返り値: {package: {risk_summary, upgrade_note, verification}}。鍵が無ければ RuntimeError。
    """
    if not findings:
        return {}
    if not _have_credentials():
        raise RuntimeError(
            "Gemini 認証が未設定です（GOOGLE_API_KEY か GOOGLE_GENAI_USE_VERTEXAI=1＋ADC）。"
        )
    # LLM/構造化出力の一時失敗（429・空応答・JSON 破損等）は RuntimeError に包む
    # ＝呼び側（console）は決定的な OSV 結果のみへ degrade できる（500 にしない・§20）。
    try:
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        runner = InMemoryRunner(agent=_build_agent(), app_name=_APP)
        runner.session_service.create_session_sync(
            app_name=_APP, user_id="depguard", session_id="scan", state={}
        )
        msg = types.Content(role="user", parts=[types.Part.from_text(text=_brief(findings))])
        for _ in runner.run(user_id="depguard", session_id="scan", new_message=msg):
            pass
        sess = runner.session_service.get_session_sync(
            app_name=_APP, user_id="depguard", session_id="scan"
        )
        raw = (sess.state or {}).get("assessment") if sess else None
        if isinstance(raw, str):
            raw = json.loads(raw)
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump()
    except Exception as e:  # noqa: BLE001 — 一時失敗は明示的に RuntimeError へ
        raise RuntimeError(f"depscan assessment failed: {e}") from e
    items = (raw or {}).get("assessments", []) if isinstance(raw, dict) else []
    out: dict[str, dict[str, str]] = {}
    for a in items:
        pkg = a.get("package", "")
        if pkg:
            out[pkg] = {
                "risk_summary": a.get("risk_summary", ""),
                "upgrade_note": a.get("upgrade_note", ""),
                "verification": a.get("verification", ""),
            }
    return out
