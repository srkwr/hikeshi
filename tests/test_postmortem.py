"""ポストモーテム→KB 提案ループの決定的テスト（オフライン・鍵不要・CI 常時緑）。

build_draft = 実行記録からの決定的生成（捏造なし）／save_to_kb = HITL 承認後の
保存（パストラバーサル防止・provenance 強制・サイズ上限）を検証する。
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hikeshi_agent.postmortem import _APPROVED, _PROVENANCE, build_draft, save_to_kb  # noqa: E402

VERDICT = {
    "root_cause_text": "rev 5 のデプロイ後にエラー率が急騰",
    "root_cause_category": "deploy_regression",
    "remediation_type": "rollback",
    "confidence": 0.95,
    "tool_trajectory": ["read_metrics", "read_logs", "diff_revision"],
    "cost_yen": 6.73,
    "latency_ms": 22654,
}
RECOVERY = {"applied": "rollback", "ts": 1000.0}


def test_draft_is_deterministic_and_grounded():
    a = build_draft(VERDICT, RECOVERY, duration_s=41.2)
    b = build_draft(VERDICT, RECOVERY, duration_s=41.2)
    assert a["markdown"] == b["markdown"]  # 決定的（filename の時刻のみ可変）
    md = a["markdown"]
    # 実行記録の値だけが入る（捏造しない）
    assert "rev 5 のデプロイ後にエラー率が急騰" in md
    assert "¥6.73" in md and "22.7s" in md and "3 回" in md
    assert "41.2s" in md and "デモ実測" in md
    assert _PROVENANCE in md
    assert "（承認者が追記" in md  # HITL の編集余地を明示


def test_draft_missing_values_are_omitted_not_fabricated():
    md = build_draft({"root_cause_category": "config_error"}, {"applied": "config_fix"})["markdown"]
    assert "¥" not in md  # cost が無ければコスト行を出さない
    assert "設定変更履歴" in md  # カテゴリ別の一般則（学び）は出る


def test_save_to_kb_sanitizes_and_enforces_provenance():
    with tempfile.TemporaryDirectory() as d:
        kb = Path(d)
        # パストラバーサル不可（ディレクトリ要素は剥がされ slug 化される）
        p = save_to_kb("# t\n\nbody", filename="../../etc/EVIL.md", kb_dir=kb)
        assert p.parent == kb and p.name == "evil.md"
        # 承認スタンプが保存時に付く（ドラフト由来行とは別＝§20 で主張を分離）
        assert _APPROVED in p.read_text(encoding="utf-8")
        # 空・過大は拒否
        for bad in ("", "x" * 30_000):
            try:
                save_to_kb(bad, kb_dir=kb)
                raise AssertionError("ValueError expected")
            except ValueError:
                pass


def test_save_to_kb_is_create_only():
    # 既存名（curated doc を想定）と衝突しても上書きせず連番でユニーク化する。
    with tempfile.TemporaryDirectory() as d:
        kb = Path(d)
        curated = kb / "curated.md"
        curated.write_text("# curated 原本\n", encoding="utf-8")
        p = save_to_kb("# 新規\n\nx", filename="curated.md", kb_dir=kb)
        assert p.name == "curated-2.md"  # 別名で保存
        assert curated.read_text(encoding="utf-8") == "# curated 原本\n"  # 原本は無傷
        p2 = save_to_kb("# 新規2\n\ny", filename="curated.md", kb_dir=kb)
        assert p2.name == "curated-3.md"


def test_saved_doc_is_searchable_after_reindex():
    # 実 KB ディレクトリに一時 doc を置き、reindex で検索対象になることを確認（終了時に削除）。
    from hikeshi_agent.retriever import reindex, search

    md = "# 一時テスト: 緑色の障害テスト文書\n\n緑色の特殊シグナルの収束手順。"
    p = save_to_kb(md, filename="tmp-test-green.md")
    try:
        reindex()
        top = search("緑色の特殊シグナル", kind="postmortem", k=1)
        assert top and top[0]["source"] == "tmp-test-green.md"
    finally:
        p.unlink()
        reindex()


if __name__ == "__main__":
    test_draft_is_deterministic_and_grounded()
    test_draft_missing_values_are_omitted_not_fabricated()
    test_save_to_kb_sanitizes_and_enforces_provenance()
    test_save_to_kb_is_create_only()
    test_saved_doc_is_searchable_after_reindex()
    print("postmortem tests: OK")
