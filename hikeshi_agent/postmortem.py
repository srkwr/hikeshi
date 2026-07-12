"""ポストモーテム→KB 更新提案（自己改善ループ v1・HITL）。

復旧完了後に**実際の診断記録**（IncidentVerdict＋実測＋適用対処）から
ポストモーテム案を**決定的に**生成する（標準ライブラリのみ・オフラインで成立）。
§20: 事実は実行記録から取り、ここで LLM による要約の創作はしない＝追加コスト 0・
テスト可能。LLM による「学び」の起草は Postmortem エージェント（ARCHITECTURE §3・
ロードマップ）として別途。

フロー: 復旧完了 → `build_draft()` でドラフト → **人間が編集・承認（HITL）** →
`save_to_kb()` が `kb/postmortems/` に保存 → `retriever.reindex()` →
**次の診断から検索対象**（Runbook 陳腐化への自己回答＝使うたびに知識が増える）。
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

_KB_POSTMORTEMS = Path(__file__).parent / "kb" / "postmortems"
# provenance は2層に分ける（§20: 由来を正確に主張する）:
#   _PROVENANCE … ドラフトの由来（自動生成）。レビュー済みとはここでは主張しない。
#   _APPROVED  … save_to_kb が保存時に付す（HITL 承認を経た事実のみを主張）。
_PROVENANCE = "（Hikeshi 自動ドラフト＝実際の診断記録から生成・合成デモ環境）"
_APPROVED = "（人間レビュー・承認済み＝ポストモーテム→KB ループ経由）"
_MAX_BYTES = 20_000  # 承認本文の上限（KB は要点の知識ベース＝長文ログ置き場にしない）
_MAX_DOCS = 100  # 承認 doc の件数上限（再索引コスト・インスタンスメモリの暴走防止）

_CAT_JA = {
    "deploy_regression": "デプロイ起因",
    "config_error": "設定誤り",
    "resource_exhaustion": "リソース枯渇",
    "dependency_failure": "外部依存障害",
    "data_cache_issue": "データ/キャッシュ不整合",
}
_REM_JA = {
    "rollback": "ロールバック",
    "runbook_mitigation": "Runbook 緩和策",
    "scale": "スケール増強",
    "config_fix": "設定修正",
    "cache_flush": "キャッシュフラッシュ",
    "code_fix": "コード修正",
}
# カテゴリ別の決定的な「学び」種（事実に基づく一般則のみ。創作はしない）。
_LESSON = {
    "deploy_regression": "直近デプロイ起因でも、書込済みデータへの影響（不可逆）があれば rollback では直らない＝diff と書込経路を先に確認する。",
    "config_error": "コードのロールバックでは直らない。直近の設定変更履歴の確認を最初に行う。",
    "resource_exhaustion": "容量不足はスケールで回復する。直近デプロイの有無でリーク由来かを切り分ける。",
    "dependency_failure": "自サービスは健全（self ok・upstream 5xx）。盲目的ロールバックは無効＝緩和策（フェイルオーバー/リトライ調整）を適用する。",
    "data_cache_issue": "stale 配信はキャッシュフラッシュで収束。スキーマ不整合を伴う場合は要 HITL。",
}


def _slug(s: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", (s or "").lower()).strip("-")
    if cleaned:
        return cleaned
    # 全 CJK 等で英数字が残らない場合は元文字列のハッシュで一意化する
    # （全タイトルが "incident" に潰れてファイル名が衝突・無情報化するのを防ぐ）。
    return f"incident-{hashlib.sha1((s or '').encode('utf-8')).hexdigest()[:8]}"


def build_draft(verdict: dict[str, Any], recovery: dict[str, Any],
                duration_s: float | None = None) -> dict[str, str]:
    """診断記録（verdict）＋復旧結果（recovery）からポストモーテム案を決定的に生成する。

    値はすべて実行記録由来（無い値は出さない・捏造しない）。返り値は
    {"markdown": 本文, "filename": 提案ファイル名}。
    """
    cat = str(verdict.get("root_cause_category") or "incident")
    cat_ja = _CAT_JA.get(cat, cat)
    rem = str(verdict.get("remediation_type") or "")
    applied = str(recovery.get("applied") or rem)
    conf = verdict.get("confidence")
    cost = verdict.get("cost_yen")
    lat = verdict.get("latency_ms")
    tools = verdict.get("tool_trajectory") or []

    cat_line = f"- カテゴリ: {cat}／提案対処: {_REM_JA.get(rem, rem)}"
    if isinstance(conf, (int, float)):
        cat_line += f"（確信度 {conf:.2f}）"
    applied_line = f"- 人間承認のうえ **{_REM_JA.get(applied, applied)}** を適用 → 復旧"
    applied_line += f"（注入→復旧 {duration_s:.1f}s・デモ実測）" if duration_s else "。"

    lines = [
        f"# ポストモーテム: {cat_ja}（{_REM_JA.get(applied, applied)} で復旧）",
        "",
        _PROVENANCE,
        "",
        "## 概要",
        f"- 根本原因: {verdict.get('root_cause_text', '(診断記録なし)')}",
        cat_line,
        "",
        "## 対応（実測）",
        applied_line,
    ]
    if isinstance(cost, (int, float)) and isinstance(lat, (int, float)):
        lines.append(
            f"- 診断コスト ¥{cost:.2f}／診断レイテンシ {lat / 1000:.1f}s／調査ツール {len(tools)} 回（実測）"
        )
    lines += [
        "",
        "## 学び",
        f"- {_LESSON.get(cat, '判定はシグナルと安全境界を優先し、検索結果は参考情報に留める。')}",
        "- （承認者が追記: 再発防止・監視の改善点）",
    ]
    md = "\n".join(lines) + "\n"
    filename = f"pm-{_slug(cat)}-{int(time.time())}.md"
    return {"markdown": md, "filename": filename}


def save_to_kb(markdown: str, filename: str | None = None,
               kb_dir: Path | None = None) -> Path:
    """承認済みポストモーテムを KB へ保存する（HITL 後にのみ呼ぶこと）。

    承認テキストはそのまま次回以降の診断プロンプトへ検索（RAG）経由で入る＝
    **持続型プロンプト・インジェクション面**になり得る。だからこそ着地は
    「人間がレビュー・承認したものだけ」（この関数の前段）に限定し、RAG 自体も
    advisory（安全境界優先）に降格してある（ARCHITECTURE §9/§9.1）。

    - ファイル名はサーバ側で検証/再生成（パストラバーサル不可・`[a-z0-9-]+.md` のみ）。
    - **create-only**：既存名と衝突したら連番でユニーク化＝curated doc を上書きできない。
    - 承認スタンプ（_APPROVED）を保存時に付す（§20: 承認の事実を保存時にだけ主張）。
    - サイズ上限・件数上限あり。保存後は `retriever.reindex()` を呼んで反映する（呼び出し側）。
    """
    text = (markdown or "").strip()
    if not text:
        raise ValueError("empty markdown")
    # 承認スタンプを先に付与してから上限判定する＝**最終ファイル**が上限を超えないことを保証
    # （スタンプ前テキストで判定すると、付与分でファイルが上限を僅かに超えうる）。
    if _APPROVED not in text:
        head, _, rest = text.partition("\n")
        text = f"{head}\n\n{_APPROVED}\n{rest}"
    if len(text.encode("utf-8")) > _MAX_BYTES:
        raise ValueError(f"markdown too large (> {_MAX_BYTES} bytes)")
    d = kb_dir or _KB_POSTMORTEMS
    d.mkdir(parents=True, exist_ok=True)
    if len(list(d.glob("*.md"))) >= _MAX_DOCS:
        raise ValueError(f"KB postmortems が上限（{_MAX_DOCS}件）に達しています")
    name = filename or f"pm-approved-{int(time.time())}.md"
    base = re.sub(r"\.md$", "", Path(name).name)
    stem = _slug(base)
    path = d / f"{stem}.md"
    i = 2
    while path.exists():  # 既存（curated 含む）を上書きしない＝create-only
        path = d / f"{stem}-{i}.md"
        i += 1
    path.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
    return path
