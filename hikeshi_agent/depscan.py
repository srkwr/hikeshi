"""防火（proactive）— 依存ライブラリの既知脆弱性スキャン → 安全な更新提案（決定的コア）。

設計（§20 正直さ重視）:
- **脆弱性データは実物**＝OSV.dev（https://api.osv.dev/v1/query・無料・APIキー不要・Google 運営）。
  返るのは実在の GHSA/CVE/PYSEC アドバイザリ・重大度・修正版。捏造しない。
- **スキャン対象の requirements は合成サンプル**（古い pin で実CVEを引くため）。各 doc/UI で
  「合成マニフェスト・OSVは実物」と明示する。
- 本モジュールは**決定的**（パース＋OSV照会＋semver差で破壊リスク＋提案生成）。LLM は使わない
  ＝オフライン/CI ではキャッシュ（fixtures）から照会して無課金・鍵なし・ネット非依存で回せる。
  自然言語の「安全性評価」は別レイヤ（depscan_agent）で実 OSV データに接地して付ける。
- **提案のみ**＝自動で pip install / アップグレード / PR 作成はしない（実行は人間承認後）。

公開 API:
    parse_requirements(text) -> list[(name, version)]
    scan(text, cache_dir=None, allow_network=True) -> ScanReport
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

_OSV_URL = "https://api.osv.dev/v1/query"
_PIN_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([0-9][0-9A-Za-z.+!-]*)\s*(?:#.*)?$")
# OSV の重大度ラベル → 並べ替え用の重み（高いほど危険）。
_SEV_RANK = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}


def parse_requirements(text: str) -> list[tuple[str, str]]:
    """`name==version` の固定 pin だけを抽出する（範囲指定/コメント/-r 等は対象外）。"""
    out: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        m = _PIN_RE.match(line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


# 末尾のプレリリース識別子（a/b/c/rc/alpha/beta/pre/preview/dev・任意の区切り付き）。PEP440 簡易判定。
_PRE_RE = re.compile(r"(?i)[._-]?(?:rc|alpha|beta|pre|preview|dev|a|b|c)\d*$")


def _ver_nums(v: str) -> tuple[int, int, int]:
    """先頭の X.Y.Z を数値化（足りない桁は 0）＝破壊リスク（major/minor/patch 差）の概算用。"""
    s = (v or "").split("!", 1)[-1].split("+", 1)[0]  # epoch と local(+...) を除く
    s = _PRE_RE.sub("", s)  # 末尾のプレリリース（rc1 等）の数字を release 桁に混ぜない
    nums = [int(n) for n in re.findall(r"\d+", s)[:3]]
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def _ver_key(v: str) -> tuple:
    """版の順序キー（PEP440 簡易）。epoch を尊重し、**プレリリースは同番号の安定版より下**に並べる。

    同一 release のプレリリース同士は識別子の番号で順序付ける（b1 < b2 < b10）＝
    `_max_version` の決定性を入力順に依存させない。
    """
    s = (v or "").strip()
    epoch = 0
    if "!" in s:
        e, _, s = s.partition("!")
        epoch = int(re.sub(r"\D", "", e) or 0)
    s = s.split("+", 1)[0]
    major, minor, patch = _ver_nums(s)
    m = _PRE_RE.search(s)
    is_stable = 0 if m else 1  # 安定版=1 が大＝プレリリースより上位
    pre_digits = re.findall(r"\d+", m.group(0)) if m else []
    pre_ord = int(pre_digits[-1]) if pre_digits else 0  # 安定版は 0（is_stable が支配）
    return (epoch, major, minor, patch, is_stable, pre_ord)


def _breaking_risk(current: str, recommended: str) -> str:
    """current→recommended の semver 差で破壊リスクを概算（major=高/minor=中/patch=低）。"""
    if not recommended:
        return "unknown"
    c, r = _ver_nums(current), _ver_nums(recommended)
    if r[0] != c[0]:
        return "high"      # メジャー更新＝破壊的変更の可能性
    if r[1] != c[1]:
        return "medium"    # マイナー更新
    return "low"           # パッチ更新


def _max_version(versions: list[str]) -> str:
    """既知修正版の中で**最も新しい安定寄りの版**（個々の脆弱性の修正版は各 fixed_version を参照）。"""
    best = ""
    for v in versions:
        if not best or _ver_key(v) > _ver_key(best):
            best = v
    return best


@dataclass
class Vuln:
    id: str
    aliases: list[str]
    severity: str           # CRITICAL/HIGH/MODERATE/LOW/UNKNOWN（OSV database_specific 由来）
    summary: str
    fixed_version: str      # この脆弱性の修正版（無ければ ""）
    advisory_url: str       # 実アドバイザリへのリンク（詳細・変更点の確認先）


@dataclass
class Finding:
    package: str
    current_version: str
    vulns: list[Vuln]
    recommended_version: str   # 既知修正版のうち最も新しい版（各CVEの修正版は vuln.fixed_version 参照）
    breaking_risk: str         # low/medium/high/unknown（semver 差）
    max_severity: str          # この package の最悪重大度
    requires_hitl: bool = True  # 更新は必ず人間承認（提案のみ・自動実行しない）
    # 夜回り（securitywatch）の CISA KEV 突合で付与（additive）。実際に悪用中なら最優先で更新。
    actively_exploited: bool = False   # KEV に載る CVE を含むか（実悪用の確証）
    kev_due_date: str | None = None    # KEV の是正期限（dueDate）・無ければ None

    @property
    def fix_command(self) -> str:
        return f"{self.package}=={self.recommended_version}" if self.recommended_version else ""


@dataclass
class ScanReport:
    manifest_label: str
    total_packages: int
    findings: list[Finding] = field(default_factory=list)
    skipped_unpinned: int = 0
    osv_source: str = "OSV.dev (api.osv.dev/v1/query)"


# CVSS v3.x base metric の係数（CVSS v3.1 仕様）。
_CVSS3 = {
    "AV": {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2},
    "AC": {"L": 0.77, "H": 0.44},
    "UI": {"N": 0.85, "R": 0.62},
    "PR_U": {"N": 0.85, "L": 0.62, "H": 0.27},  # Scope=Unchanged
    "PR_C": {"N": 0.85, "L": 0.68, "H": 0.5},   # Scope=Changed
    "CIA": {"H": 0.56, "L": 0.22, "N": 0.0},
}


def _parse_cvss_vector(vec: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (vec or "").split("/"):
        k, sep, val = part.partition(":")
        if sep:
            out[k] = val
    return out


def _cvss3_base(vec: str) -> float | None:
    """CVSS v3.x ベクタから base score を算出（仕様準拠・roundup は小数第1位切上げ）。不正なら None。"""
    m = _parse_cvss_vector(vec)
    try:
        scope_c = m["S"] == "C"
        av, ac, ui = _CVSS3["AV"][m["AV"]], _CVSS3["AC"][m["AC"]], _CVSS3["UI"][m["UI"]]
        pr = (_CVSS3["PR_C"] if scope_c else _CVSS3["PR_U"])[m["PR"]]
        c, i, a = _CVSS3["CIA"][m["C"]], _CVSS3["CIA"][m["I"]], _CVSS3["CIA"][m["A"]]
    except KeyError:
        return None
    iss = 1 - (1 - c) * (1 - i) * (1 - a)
    impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15 if scope_c else 6.42 * iss
    if impact <= 0:
        return 0.0
    expl = 8.22 * av * ac * pr * ui
    raw = min((1.08 if scope_c else 1.0) * (impact + expl), 10.0)
    return math.ceil(raw * 10) / 10.0  # CVSS roundup（小数第1位）


def _band_from_score(score: float) -> str:
    """CVSS base score → 定性バンド（OSV/GHSA の語彙に合わせる）。"""
    if score >= 9.0:
        return "CRITICAL"
    if score >= 7.0:
        return "HIGH"
    if score >= 4.0:
        return "MODERATE"
    if score > 0.0:
        return "LOW"
    return "UNKNOWN"


def _cvss4_impact_band(vec: str) -> str | None:
    """CVSS v4 は厳密スコアを実装しない＝影響メトリクス(VC/VI/VA)から保守的な粗バンドに接地。"""
    m = _parse_cvss_vector(vec)
    imp = [m.get(k) for k in ("VC", "VI", "VA")]
    if not any(imp):
        return None
    if "H" in imp:
        return "HIGH"
    if "L" in imp:
        return "MODERATE"
    return "LOW"


def _severity_of(v: dict) -> str:
    sev = (v.get("database_specific") or {}).get("severity")
    if sev:
        return str(sev).upper()
    # database_specific が無くても、CVSS ベクタがあれば概算バンドへ接地（UNKNOWN を減らす）。
    # v3 は仕様準拠で base score を計算、v4 は影響メトリクスの粗バンド。複数あれば最悪を採る。
    best, best_rank = "", -1
    for s in v.get("severity") or []:
        vec = s.get("score") or ""
        typ = (s.get("type") or "").upper()
        if typ.startswith("CVSS_V3") or vec.startswith("CVSS:3"):
            sc = _cvss3_base(vec)
            band = _band_from_score(sc) if sc is not None else None
        elif typ.startswith("CVSS_V4") or vec.startswith("CVSS:4"):
            band = _cvss4_impact_band(vec)
        else:
            band = None
        if band and _SEV_RANK.get(band, 0) > best_rank:
            best, best_rank = band, _SEV_RANK[band]
    return best or "UNKNOWN"


def _fixed_of(v: dict, pkg: str) -> str:
    fixed: list[str] = []
    for aff in v.get("affected", []):
        if (aff.get("package") or {}).get("name", "").lower() != pkg.lower():
            continue
        for rng in aff.get("ranges", []):
            # PyPI バージョンのみ採用。GIT range の fixed は**コミットハッシュ**なので除外する。
            if rng.get("type") != "ECOSYSTEM":
                continue
            for ev in rng.get("events", []):
                if "fixed" in ev:
                    fixed.append(ev["fixed"])
    return _max_version(fixed)


def _advisory_url(v: dict) -> str:
    refs = v.get("references", [])
    for r in refs:
        if r.get("type") == "ADVISORY" and r.get("url"):
            return r["url"]
    return f"https://osv.dev/vulnerability/{v.get('id', '')}" if v.get("id") else ""


def _cache_path(cache_dir: Path, name: str, version: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", f"{name}-{version}")
    return cache_dir / f"{safe}.json"


def _osv_query(name: str, version: str, cache_dir: Path | None, allow_network: bool) -> dict:
    """OSV へ単一パッケージ照会（cache 優先・無ければネット・両方不可なら空）。"""
    if cache_dir is not None:
        cp = _cache_path(cache_dir, name, version)
        if cp.exists():
            try:
                return json.loads(cp.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass  # 破損キャッシュはミス扱い＝スキャン全体を巻き込まずネット/空へフォールバック
    if not allow_network:
        return {}
    body = json.dumps({"package": {"name": name, "ecosystem": "PyPI"}, "version": version}).encode()
    req = urllib.request.Request(_OSV_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, TimeoutError):
        return {}
    if cache_dir is not None:  # 取得できたらキャッシュ（次回オフライン/決定的に）
        cache_dir.mkdir(parents=True, exist_ok=True)
        cp = _cache_path(cache_dir, name, version)
        # アトミック書込（同一ディレクトリの temp → os.replace）。中断で破損キャッシュを残さない。
        tmp = cp.with_name(f"{cp.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, cp)
    return data


def _build_finding(name: str, version: str, osv: dict) -> Finding | None:
    raw = osv.get("vulns", [])
    if not raw:
        return None
    vulns: list[Vuln] = []
    fixed_all: list[str] = []
    for v in raw:
        sev = _severity_of(v)
        fixed = _fixed_of(v, name)
        if fixed:
            fixed_all.append(fixed)
        vulns.append(
            Vuln(
                id=v.get("id", ""),
                aliases=list(v.get("aliases", [])),
                severity=sev,
                summary=(v.get("summary") or v.get("details") or "")[:200],
                fixed_version=fixed,
                advisory_url=_advisory_url(v),
            )
        )
    vulns.sort(key=lambda x: -_SEV_RANK.get(x.severity, 0))
    recommended = _max_version(fixed_all)
    max_sev = max((x.severity for x in vulns), key=lambda s: _SEV_RANK.get(s, 0), default="UNKNOWN")
    return Finding(
        package=name,
        current_version=version,
        vulns=vulns,
        recommended_version=recommended,
        breaking_risk=_breaking_risk(version, recommended),
        max_severity=max_sev,
    )


def _annotate_kev(findings: list[Finding], kev_idx: dict[str, dict]) -> None:
    """夜回りの CISA KEV インデックスと突合し actively_exploited/kev_due_date を付与（additive）。

    突合キー = finding の各 vuln の id と aliases（CVE/GHSA 等）を大文字化した集合。KEV は CVE
    単位なので実質 CVE で当たる。KEV データが無ければ全 finding が false のまま（捏造しない）。
    """
    if not kev_idx:
        return
    for f in findings:
        ids = set()
        for v in f.vulns:
            if v.id:
                ids.add(v.id.upper())
            ids.update(a.upper() for a in v.aliases if a)
        # sorted で決定的に走査（set の反復順は PYTHONHASHSEED 依存＝非決定的）。
        # 複数 KEV に該当する finding は最も早い是正期限を採る（保守的・決定的）。
        due_dates = []
        for cid in sorted(ids):
            entry = kev_idx.get(cid)
            if entry is not None:
                f.actively_exploited = True
                d = entry.get("dueDate")
                if d:
                    due_dates.append(d)
        if due_dates:
            f.kev_due_date = min(due_dates)


def scan(text: str, cache_dir: str | Path | None = None, allow_network: bool = True,
         manifest_label: str = "合成サンプル manifest（OSV アドバイザリは実物）") -> ScanReport:
    """requirements テキストを OSV で照会し、脆弱な package の更新提案を重大度順で返す。"""
    cdir = Path(cache_dir) if cache_dir else None
    pins = parse_requirements(text)
    # 固定 pin でない行（範囲指定/-r 等）はスキャン対象外＝件数を正直に記録する。
    declared = sum(1 for ln in (text or "").splitlines() if ln.strip() and not ln.strip().startswith("#"))
    findings: list[Finding] = []
    for name, version in pins:
        osv = _osv_query(name, version, cdir, allow_network)
        f = _build_finding(name, version, osv)
        if f:
            findings.append(f)
    # 夜回りの CISA KEV（実データ・既定オフライン＝コミット済みキャッシュ）と突合。
    # enrichment なので失敗しても depscan 本体は落とさない（KEV 無し＝全 false）。
    # import は securitywatch モジュール自体が無い最小環境（bench 等）を別に扱う
    # ＝except 節が import 由来の名前を参照して落ちないようにする。
    try:
        from .securitywatch import SecurityWatchError, kev_index
    except ImportError:
        SecurityWatchError = None  # securitywatch 未導入＝KEV 突合はスキップ（本体は続行）
    if SecurityWatchError is not None:
        try:
            _annotate_kev(findings, kev_index(allow_network=False))
        except (SecurityWatchError, OSError, ValueError) as e:
            # 失敗は無言で握りつぶさず可視化する（突合が静かに死なない）。
            print(f"depscan: KEV 突合をスキップ（{type(e).__name__}: {e}）", file=sys.stderr, flush=True)
    findings.sort(key=lambda f: (-_SEV_RANK.get(f.max_severity, 0), f.package))
    return ScanReport(
        manifest_label=manifest_label,
        total_packages=len(pins),
        findings=findings,
        skipped_unpinned=max(0, declared - len(pins)),
        osv_source=_OSV_URL,
    )


def report_to_dict(r: ScanReport) -> dict:
    """API/UI 用の素の dict へ（dataclass→JSON 可能形）。"""
    return {
        "manifest_label": r.manifest_label,
        "osv_source": r.osv_source,
        "total_packages": r.total_packages,
        "skipped_unpinned": r.skipped_unpinned,
        "vulnerable_packages": len(r.findings),
        "findings": [
            {
                "package": f.package,
                "current_version": f.current_version,
                "recommended_version": f.recommended_version,
                "fix_command": f.fix_command,
                "breaking_risk": f.breaking_risk,
                "max_severity": f.max_severity,
                "requires_hitl": f.requires_hitl,
                "actively_exploited": f.actively_exploited,
                "kev_due_date": f.kev_due_date,
                "vulns": [
                    {
                        "id": v.id, "aliases": v.aliases, "severity": v.severity,
                        "summary": v.summary, "fixed_version": v.fixed_version,
                        "advisory_url": v.advisory_url,
                    }
                    for v in f.vulns
                ],
            }
            for f in r.findings
        ],
    }
