"""Hikeshi 調査・検索ツール群（ADK は素の callable を FunctionTool に自動ラップ）。

各ツールは `ToolContext.state['signals']` から該当スライスを返す。インシデントの
詳細はブリーフに載せず、エージェントがツールで取りに行く設計＝**実際の調査軌跡**が出る。
docstring がそのままツール説明としてモデルに渡る（簡潔・命令形）。
signals は flat（alert/metrics/recent_deploy|recent_change/logs_sample/diff_summary/
trace_sample/context）なので、ツールは該当キーに写像する。
"""

from __future__ import annotations

import os
from typing import Any

from google.adk.tools import ToolContext

from .retriever import search as _kb_search

# RAG on/off（bench で寄与を計測するため）。off で従来の「KB未接続」挙動に戻す。
_RAG_ON = os.environ.get("HIKESHI_RAG", "on").lower() not in ("off", "0", "false", "no")
_KB_OFF_NOTE = "(KB未接続 — Vector Search 2.0 接続は次段)"


def _retrieve(query: str, kind: str) -> dict:
    """実 KB を検索し、取得した本文（出典つき）を返す。off なら未接続を明示。"""
    if not _RAG_ON:
        return {"query": query, "kb_connected": False, "note": _KB_OFF_NOTE}
    hits = _kb_search(query, kind=kind, k=2)
    return {
        "query": query,
        "kb_connected": True,
        "results": [{"title": h["title"], "source": h["source"], "excerpt": h["text"][:700]} for h in hits],
    }


def _sig(tc: ToolContext) -> dict[str, Any]:
    return dict(tc.state.get("signals", {}) or {})


def _deploy(s: dict[str, Any]) -> dict[str, Any]:
    return s.get("recent_deploy") or s.get("recent_change") or {}


# ---- 調査系 ----
def read_metrics(tool_context: ToolContext) -> dict:
    """エラー率・レイテンシ・飽和・キャッシュヒット率など直近メトリクスを返す。"""
    return {"metrics": _sig(tool_context).get("metrics", {})}


def read_logs(tool_context: ToolContext) -> dict:
    """直近のエラーログ抜粋を返す。"""
    return {"logs": _sig(tool_context).get("logs_sample", [])}


def read_trace(tool_context: ToolContext) -> dict:
    """分散トレースのスパン要約を返す（外部依存の遅延/失敗の特定に有効）。"""
    return {"trace": _sig(tool_context).get("trace_sample", "(トレース情報なし)")}


def list_revisions(tool_context: ToolContext) -> dict:
    """直近のリビジョン/デプロイ一覧を返す。"""
    d = _deploy(_sig(tool_context))
    return {"revisions": [d] if d else []}


def read_recent_deploy(tool_context: ToolContext) -> dict:
    """直近のデプロイ/変更（サービス・リビジョン・経過分・設定変更）を返す。"""
    return {"recent_deploy": _deploy(_sig(tool_context))}


def diff_revision(tool_context: ToolContext) -> dict:
    """直近リビジョン間のコード/設定差分の要約を返す。"""
    return {"diff": _sig(tool_context).get("diff_summary", "(差分情報なし)")}


def read_config(tool_context: ToolContext) -> dict:
    """設定・環境変数の直近変更（config_change 等）を返す。"""
    d = _deploy(_sig(tool_context))
    return {"config_change": d.get("config_change", d.get("note", "(設定変更の記録なし)")), "deploy": d}


def check_dependency_status(tool_context: ToolContext) -> dict:
    """外部依存（下流API/DB/IdP 等）の健全性をトレース/ログから返す。"""
    s = _sig(tool_context)
    return {"trace": s.get("trace_sample", ""), "logs": s.get("logs_sample", [])}


def check_quota(tool_context: ToolContext) -> dict:
    """リソース/クォータ（CPU・接続数・FD・QPS 等）の逼迫状況を返す。"""
    s = _sig(tool_context)
    return {"metrics": s.get("metrics", {}), "context": s.get("context", "")}


def check_cache(tool_context: ToolContext) -> dict:
    """キャッシュ/CDN の状態（ヒット率・stale・キー整合）を返す。"""
    s = _sig(tool_context)
    return {"metrics": s.get("metrics", {}), "logs": s.get("logs_sample", [])}


# ---- 検索系（Agentic RAG・実 KB を検索して根拠を取得） ----
def search_runbook(query: str, tool_context: ToolContext) -> dict:
    """Runbook を検索し、関連手順の本文（title/excerpt/source）を返す。"""
    return _retrieve(query, "runbook")


def search_past_incidents(query: str, tool_context: ToolContext) -> dict:
    """過去の類似インシデントを検索し、本文（title/excerpt/source）を返す。"""
    return _retrieve(query, "incident")


def search_postmortem(query: str, tool_context: ToolContext) -> dict:
    """過去のポストモーテム（事後分析）を検索し、本文（title/excerpt/source）を返す。"""
    return _retrieve(query, "postmortem")


TRIAGE_TOOLS = [read_metrics, read_logs]
INVESTIGATION_TOOLS = [
    read_metrics,
    read_logs,
    read_trace,
    list_revisions,
    read_recent_deploy,
    diff_revision,
    read_config,
    check_dependency_status,
    check_quota,
    check_cache,
]
SEARCH_TOOLS = [search_runbook, search_past_incidents, search_postmortem]
