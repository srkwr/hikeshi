"""夜回り（securitywatch）の決定的テスト（全てオフライン・コミット済みキャッシュ使用・鍵不要）。

実データ（CISA KEV / GitHub Advisory）を使うが**ネットには出ない**（allow_network=False で
コミット済みスナップショットを引く）＝再現可能・CI 常時緑。消火（診断）の挙動は一切変えない
ことも回帰ガードで担保する。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hikeshi_agent import depscan, securitywatch  # noqa: E402
from hikeshi_agent.retriever import list_docs, reindex, search  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE = os.path.join(_ROOT, "hikeshi_agent", "securitywatch_cache")


# --- fetch はオフラインで決定的にキャッシュから返る --------------------------------------------


def test_fetch_kev_offline_from_cache():
    kev = securitywatch.fetch_kev(cache_dir=_CACHE, allow_network=False)
    assert kev["title"] and kev["count"] == len(kev["vulnerabilities"])
    assert kev["count"] > 0
    assert kev["source_url"].startswith("https://www.cisa.gov/")
    # 各エントリは実 KEV の形（cveID を持つ）。
    for v in kev["vulnerabilities"]:
        assert v.get("cveID", "").upper().startswith("CVE-")


def test_fetch_ghsa_offline_from_cache():
    ghsa = securitywatch.fetch_ghsa(cache_dir=_CACHE, allow_network=False)
    assert isinstance(ghsa, list) and ghsa
    for e in ghsa:
        assert e["ghsa_id"].startswith("GHSA-")
        assert e["html_url"].startswith("https://github.com/")


def test_fetch_deterministic():
    a = securitywatch.fetch_kev(cache_dir=_CACHE, allow_network=False)
    b = securitywatch.fetch_kev(cache_dir=_CACHE, allow_network=False)
    assert [v["cveID"] for v in a["vulnerabilities"]] == [v["cveID"] for v in b["vulnerabilities"]]


def test_fetch_offline_no_cache_raises_not_fabricates():
    # キャッシュもネットも無い＝偽値フォールバックせず明確な例外（§20 捏造禁止）。
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        try:
            securitywatch.fetch_kev(cache_dir=d, allow_network=False)
            raise AssertionError("SecurityWatchError が投げられるべき")
        except securitywatch.SecurityWatchError:
            pass


# --- kev_index（depscan 突合用・既定オフライン） ----------------------------------------------


def test_kev_index_offline_default_cache():
    idx = securitywatch.kev_index()  # 既定オフライン＝コミット済みキャッシュ
    assert idx, "既定キャッシュから KEV index が引けること"
    # 既知 CVE（キャッシュ内の任意の 1 件）を大文字キーで引ける。
    kev = securitywatch.fetch_kev(cache_dir=_CACHE, allow_network=False)
    sample_cve = kev["vulnerabilities"][0]["cveID"].upper()
    assert sample_cve in idx
    assert idx[sample_cve]["cveID"].upper() == sample_cve


# --- refresh はオフラインで KB を決定的生成し manifest を返す ----------------------------------


def test_refresh_offline_generates_kb_and_manifest(tmp_path):
    m = securitywatch.refresh(kb_dir=tmp_path, cache_dir=_CACHE, allow_network=False)
    assert m["fetched_at"], "manifest に fetched_at がある"
    assert m["kev_count"] > 0 and m["ghsa_count"] > 0
    assert m["source_urls"]["kev"].startswith("https://www.cisa.gov/")
    adv = tmp_path / "advisories"
    docs = sorted(p.name for p in adv.glob("*.md"))
    assert 3 <= len(docs) <= 6, f"advisory doc 数は 3〜6 点: {docs}"
    # 実データ・provenance が各 doc に明記され、合成でない旨がある。
    for p in adv.glob("*.md"):
        text = p.read_text(encoding="utf-8")
        first = next(ln for ln in text.splitlines() if ln.strip())
        assert first.startswith("#"), "title 行＝先頭非空行（retriever 規則）"
        assert "出典URL" in text and m["fetched_at"] in text
        assert "実データ" in text and "合成では" in text


def test_refresh_deterministic(tmp_path):
    a = securitywatch.refresh(kb_dir=tmp_path / "a", cache_dir=_CACHE, allow_network=False)
    b = securitywatch.refresh(kb_dir=tmp_path / "b", cache_dir=_CACHE, allow_network=False)
    assert a["advisory_docs"] == b["advisory_docs"]
    for name in a["advisory_docs"]:
        ta = (tmp_path / "a" / "advisories" / name).read_text(encoding="utf-8")
        tb = (tmp_path / "b" / "advisories" / name).read_text(encoding="utf-8")
        assert ta == tb, f"{name} が決定的でない"


# --- retriever に advisory 種別が現れる & 既存 kind の検索結果が不変（回帰ガード） --------------


def test_advisory_kind_present_in_kb():
    reindex()
    kinds = {d["kind"] for d in list_docs()}
    assert "advisory" in kinds, "夜回りの advisory 種別が KB に現れる"
    top = search("KEV 実際に悪用中 脆弱性", kind="advisory", k=2)
    assert top and top[0]["kind"] == "advisory"


def test_existing_kind_search_unchanged_regression_guard():
    # 消火(診断)の RAG が一切変わらないこと＝既存 5 症状の 1 位 Runbook が改名前と同一。
    cases = [
        ("デプロイ直後 5xx 急騰 self degraded", "deploy-regression.md"),
        ("upstream 5xx self healthy 外部依存 連鎖", "dependency-failure.md"),
        ("メモリ 96% 飽和 直近デプロイなし", "resource-exhaustion.md"),
        ("設定 環境変数 タイムアウト 誤り", "config-error.md"),
        ("キャッシュ stale データ不整合", "data-cache-issue.md"),
    ]
    for query, expect in cases:
        top = search(query, kind="runbook", k=2)
        got = top[0]["source"] if top else None
        assert got == expect, f"{query!r} -> {got}"
    # postmortem/incident の代表クエリも不変。
    assert search("デプロイ ロールバック", kind="postmortem", k=1)[0]["kind"] == "postmortem"
    assert search("外部依存 フェイルオーバー", kind="incident", k=1)[0]["kind"] == "incident"
    assert search("不可逆 書込 データ 過剰請求 デプロイ", kind="postmortem", k=1)[0]["source"] \
        == "billing-deploy-irreversible.md"


# --- depscan の actively_exploited フィールド（additive・型が正しい） -------------------------


def test_depscan_actively_exploited_field_present_and_typed():
    sample = os.path.join(_ROOT, "hikeshi_agent", "depscan_sample", "requirements.txt")
    cache = os.path.join(_ROOT, "hikeshi_agent", "depscan_sample", "osv_cache")
    with open(sample, encoding="utf-8") as f:
        d = depscan.report_to_dict(depscan.scan(f.read(), cache_dir=cache, allow_network=False))
    assert d["findings"], "サンプルは脆弱 finding を持つ"
    for f in d["findings"]:
        assert isinstance(f["actively_exploited"], bool)
        assert f["kev_due_date"] is None or isinstance(f["kev_due_date"], str)


def test_depscan_kev_annotation_matches_index():
    # KEV に載る CVE を含む finding のみ actively_exploited=True（実突合・捏造なし）。
    sample = os.path.join(_ROOT, "hikeshi_agent", "depscan_sample", "requirements.txt")
    cache = os.path.join(_ROOT, "hikeshi_agent", "depscan_sample", "osv_cache")
    with open(sample, encoding="utf-8") as f:
        report = depscan.scan(f.read(), cache_dir=cache, allow_network=False)
    idx = securitywatch.kev_index()
    for finding in report.findings:
        ids = {v.id.upper() for v in finding.vulns if v.id}
        for v in finding.vulns:
            ids.update(a.upper() for a in v.aliases)
        expected = any(cid in idx for cid in ids)
        assert finding.actively_exploited == expected


if __name__ == "__main__":
    test_fetch_kev_offline_from_cache()
    test_fetch_ghsa_offline_from_cache()
    test_fetch_deterministic()
    test_fetch_offline_no_cache_raises_not_fabricates()
    test_kev_index_offline_default_cache()
    test_advisory_kind_present_in_kb()
    test_existing_kind_search_unchanged_regression_guard()
    test_depscan_actively_exploited_field_present_and_typed()
    test_depscan_kev_annotation_matches_index()
    print("securitywatch tests: OK")


# --- console エンドポイント（夜回り現況）: オフライン・TestClient -----------------------------

def test_console_securitywatch_status_offline():
    """GET /api/securitywatch はコミット済みキャッシュから現況を返す（ネットに出ない）。"""
    import os
    os.environ["HIKESHI_LLM_MIN_INTERVAL_S"] = "0"
    os.environ["HIKESHI_LLM_DAILY_CAP"] = "0"
    from fastapi.testclient import TestClient

    import console.app as capp
    c = TestClient(capp.app)
    r = c.get("/api/securitywatch")
    assert r.status_code == 200
    body = r.json()
    assert "manifest" in body and "newest_kev" in body
    # newest_kev は実データ由来の CVE 形（あれば CVE- で始まる）
    for v in body["newest_kev"]:
        assert v["cveID"].startswith("CVE-")


def test_depscan_findings_carry_kev_fields():
    """depscan の各 finding が actively_exploited/kev_due_date を持つ（KEV 突合の可視化用）。"""
    import pathlib as _p

    from hikeshi_agent import depscan
    base = _p.Path(depscan.__file__).parent / "depscan_sample"
    rep = depscan.scan((base / "requirements.txt").read_text(encoding="utf-8"),
                       cache_dir=base / "osv_cache", allow_network=False)
    d = depscan.report_to_dict(rep)
    for f in d["findings"]:
        assert "actively_exploited" in f and isinstance(f["actively_exploited"], bool)
        assert "kev_due_date" in f


def test_depscan_kev_offline_without_httpx(monkeypatch):
    """httpx 未導入の最小環境（bench 等）でも depscan の KEV 突合が import で落ちない。

    securitywatch の httpx は遅延 import＝オフラインのキャッシュ読みは httpx 不要。
    """
    import sys
    monkeypatch.setitem(sys.modules, "httpx", None)  # import httpx を ImportError に
    import importlib

    import hikeshi_agent.securitywatch as sw
    importlib.reload(sw)  # 遅延 import の状態で読み直す
    import pathlib as _p

    from hikeshi_agent import depscan
    base = _p.Path(depscan.__file__).parent / "depscan_sample"
    rep = depscan.scan((base / "requirements.txt").read_text(encoding="utf-8"),
                       cache_dir=base / "osv_cache", allow_network=False)
    assert len(rep.findings) >= 1  # 本体は完走
    # オフラインの突合インデックスも httpx 無しで引ける
    assert len(sw.kev_index(allow_network=False)) > 0
