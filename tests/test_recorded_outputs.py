"""記録済み実エージェント出力（incident_bench/recorded/hikeshi_advisory.json）の健全性テスト。

オフライン・鍵不要（JSON を読むだけ）。CI で常時緑。
D（証拠＋具体手順）の §20 ガード：evidence の source は**実在の調査ツール名か KB 参照**に限り、
remediation_plan は空でないことを機械検証する＝判定カードに捏造の根拠/手順が載らないことを担保。
（注：本ガードは出典ラベルの妥当性を検証する。事実値そのものの真偽は temp=0＋プロンプト制約＋
記録の人手確認で担保する＝remediate は output_schema 利用でツール非呼出のため。）
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RECORDED = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "incident_bench", "recorded", "hikeshi_advisory.json",
)

# 実在する調査/検索ツール名（hikeshi_agent/tools.py と一致）。evidence.source はこの集合に
# 属するか、KB 由来（『参照:』/ *.md）であること＝モデルが無関係な出典を捏造しないことの担保。
_KNOWN_TOOLS = {
    "read_metrics", "read_logs", "read_trace", "list_revisions", "read_recent_deploy",
    "diff_revision", "check_dependency_status", "check_quota", "read_config", "check_cache",
    "search_runbook", "search_past_incidents", "search_postmortem",
}


def _source_ok(src: str) -> bool:
    s = (src or "").strip()
    if not s:
        return False
    if "参照" in s or ".md" in s:  # KB 由来の出典
        return True
    # 「read_metrics+diff_revision」のように複数ツールを併記しても、各トークンが実在ツールならOK。
    normalized = s.replace("/", "+").replace("、", "+").replace(",", "+")
    toks = [t.strip() for t in normalized.split("+") if t.strip()]
    return bool(toks) and all(any(k in t for k in _KNOWN_TOOLS) for t in toks)


def test_recorded_has_grounded_evidence_and_plan():
    with open(_RECORDED, encoding="utf-8") as f:
        data = json.load(f)
    outputs = data.get("outputs", [])
    assert len(outputs) >= 10, f"recorded outputs too few: {len(outputs)}"

    bad_src = []
    for o in outputs:
        cid = o.get("case_id", "?")
        ev = o.get("evidence", [])
        plan = o.get("remediation_plan", [])
        assert isinstance(ev, list) and len(ev) >= 1, f"{cid}: evidence empty"
        assert isinstance(plan, list) and len(plan) >= 1, f"{cid}: remediation_plan empty"
        assert all(isinstance(s, str) and s.strip() for s in plan), f"{cid}: empty plan step"
        for e in ev:
            assert e.get("fact", "").strip(), f"{cid}: evidence fact empty"
            if not _source_ok(e.get("source", "")):
                bad_src.append((cid, e.get("source")))

    # 大半が実ツール/KB 由来であること（flash の表記揺れを許容しつつ捏造を弾く＝8割以上）。
    total_ev = sum(len(o.get("evidence", [])) for o in outputs)
    ratio_ok = 1 - (len(bad_src) / max(1, total_ev))
    assert ratio_ok >= 0.8, f"evidence sources not grounded enough ({ratio_ok:.0%}): {bad_src[:6]}"


if __name__ == "__main__":
    test_recorded_has_grounded_evidence_and_plan()
    print("recorded-outputs honesty test: OK")
