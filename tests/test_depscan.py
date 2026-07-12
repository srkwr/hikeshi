"""防火：依存脆弱性スキャン（hikeshi_agent/depscan.py）の決定的テスト。

オフライン・鍵不要・ネット非依存（コミット済み OSV キャッシュ fixture から照会）。CI 常時緑。
実 CVE データを使うが、ネットには出ない＝再現可能（"検証可能な数字" を担保）。
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hikeshi_agent import depscan  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAMPLE = os.path.join(_ROOT, "hikeshi_agent", "depscan_sample", "requirements.txt")
_CACHE = os.path.join(_ROOT, "hikeshi_agent", "depscan_sample", "osv_cache")


def test_parse_requirements():
    pins = depscan.parse_requirements(
        "jinja2==2.4.1\n# comment\nrequests==2.19.1  # inline\nflask>=1.0\n-r other.txt\n\n"
    )
    assert pins == [("jinja2", "2.4.1"), ("requests", "2.19.1")]  # 固定 pin のみ


def test_breaking_risk():
    assert depscan._breaking_risk("2.4.1", "3.1.6") == "high"     # メジャー
    assert depscan._breaking_risk("5.1", "5.4") == "medium"       # マイナー
    assert depscan._breaking_risk("1.2.3", "1.2.9") == "low"      # パッチ
    assert depscan._breaking_risk("1.0.0", "") == "unknown"


def test_version_ordering_demotes_prerelease_and_handles_epoch():
    # プレリリースは同番号の安定版より下＝"安全な更新先"に beta/rc を勧めない。
    assert depscan._max_version(["2.0", "2.0rc1"]) == "2.0"
    assert depscan._max_version(["5.2", "5.2b1"]) == "5.2"
    assert depscan._max_version(["2.0.dev3", "2.0"]) == "2.0"
    assert depscan._max_version(["2.9", "2.10"]) == "2.10"   # 数値比較（辞書順でない）
    assert depscan._max_version(["1!2.0", "3.0"]) == "1!2.0"  # epoch を尊重


def test_scan_offline_returns_real_advisories():
    with open(_SAMPLE, encoding="utf-8") as f:
        report = depscan.scan(f.read(), cache_dir=_CACHE, allow_network=False)
    d = depscan.report_to_dict(report)
    assert d["total_packages"] == 5
    assert d["vulnerable_packages"] == 5  # 全て古い pin ＝既知脆弱性あり

    by_pkg = {f["package"]: f for f in d["findings"]}
    # 推奨更新先は必ず**版番号**（GIT コミットハッシュが混入しない）。
    for f in d["findings"]:
        rv = f["recommended_version"]
        assert rv and re.match(r"^\d", rv), f"{f['package']} recommended not a version: {rv}"
        assert f["requires_hitl"] is True  # 提案のみ・必ず人間承認
        assert f["vulns"], f"{f['package']} has no vulns"

    # 実在の CVE/GHSA が引けている（捏造でない＝OSV 由来）。
    jinja = by_pkg["jinja2"]
    ids = {a for v in jinja["vulns"] for a in (v["aliases"] + [v["id"]])}
    assert "CVE-2019-10906" in ids and any(i.startswith("GHSA-") for i in ids)
    assert jinja["recommended_version"].startswith("3.")  # 2.4.1 → 3.x = メジャー
    assert jinja["breaking_risk"] == "high"

    # 重大度順（CRITICAL/HIGH が先頭側）。
    sev_rank = depscan._SEV_RANK
    ranks = [sev_rank.get(f["max_severity"], 0) for f in d["findings"]]
    assert ranks == sorted(ranks, reverse=True)
    assert d["findings"][0]["max_severity"] in ("CRITICAL", "HIGH")


def test_scan_no_network_no_cache_is_empty():
    # キャッシュもネットも無い＝空（黙って偽の脆弱性を作らない・§20）。
    report = depscan.scan("jinja2==2.4.1\n", cache_dir=None, allow_network=False)
    assert depscan.report_to_dict(report)["vulnerable_packages"] == 0


def test_skipped_unpinned_is_surfaced():
    # 範囲指定/未固定の依存はスキャン対象外＝件数を正直に出す（カバレッジを偽らない）。
    txt = "jinja2==2.4.1\nflask>=1.0\nrequests  # no pin\n"
    d = depscan.report_to_dict(depscan.scan(txt, cache_dir=_CACHE, allow_network=False))
    assert d["total_packages"] == 1          # 固定 pin は jinja2 のみ
    assert d["skipped_unpinned"] == 2        # flask(範囲) と requests(未固定)


def test_cvss_severity_grounding():
    # database_specific.severity があればそれを優先（GHSA はこれを持つ）。
    assert depscan._severity_of({"database_specific": {"severity": "high"}}) == "HIGH"
    # CVSS v3 ベクタ → 仕様準拠の base score（roundup 小数第1位）。
    assert depscan._cvss3_base("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == 9.8
    assert depscan._cvss3_base("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N") == 5.4
    # database_specific 欠落でも CVSS ベクタからバンドへ接地する（UNKNOWN に潰さない）。
    crit = {"severity": [{"type": "CVSS_V3",
                          "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}]}
    assert depscan._severity_of(crit) == "CRITICAL"
    mod = {"severity": [{"type": "CVSS_V3",
                         "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N"}]}
    assert depscan._severity_of(mod) == "MODERATE"
    # v4 のみ（厳密スコア未実装）＝影響メトリクスの粗バンド。
    v4_vec = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N"
    v4 = {"severity": [{"type": "CVSS_V4", "score": v4_vec}]}
    assert depscan._severity_of(v4) == "HIGH"
    # severity 情報が皆無なら UNKNOWN（捏造しない）。
    assert depscan._severity_of({"id": "PYSEC-x"}) == "UNKNOWN"


def test_cache_corruption_is_miss_not_crash():
    # 破損キャッシュはミス扱い＝例外でスキャン全体を落とさず、ネット無し→空で返す。
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        cp = depscan._cache_path(Path(d), "jinja2", "2.4.1")
        cp.write_text("{ this is not json", encoding="utf-8")
        rep = depscan.scan("jinja2==2.4.1\n", cache_dir=d, allow_network=False)
        assert depscan.report_to_dict(rep)["vulnerable_packages"] == 0


if __name__ == "__main__":
    test_parse_requirements()
    test_breaking_risk()
    test_version_ordering_demotes_prerelease_and_handles_epoch()
    test_scan_offline_returns_real_advisories()
    test_scan_no_network_no_cache_is_empty()
    test_skipped_unpinned_is_surfaced()
    test_cvss_severity_grounding()
    test_cache_corruption_is_miss_not_crash()
    print("depscan tests: OK")
