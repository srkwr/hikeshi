"""夜回り（Yomawari）— 火の用心（YOJIN・予防）配下の定期巡回ジョブ（決定的コア）。

差別化（§20 正直さ重視）:
- 汎用 LLM の知識カットオフを超えて、**実際に悪用中**の最新脆弱性を定期取得し火元帳(KB)に蓄える。
- **実データのみ**（捏造しない）:
    - CISA KEV（Known Exploited Vulnerabilities・無料・鍵不要）
      https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json
    - GitHub Advisory（無料・鍵不要）
      https://api.github.com/advisories?per_page=N&sort=published
- **オフライン決定性**: depscan の osv_cache と同じ流儀で、取得スナップショットを
  `securitywatch_cache/` にコミットし、`allow_network=False` でオフライン再現可能。
  テストはネットに出ない（コミット済みキャッシュを引く）。
- 取得 URL は**固定の一次情報源のみ**（リクエストから URL を受けない＝SSRF 面なし）。
- 全ネット取得は timeout 付き・失敗時は明確な例外（黙って握りつぶさない・偽値フォールバック禁止）。

公開 API:
    fetch_kev(cache_dir=None, allow_network=True) -> dict
    fetch_ghsa(cache_dir=None, allow_network=True, per_page=30) -> list[dict]
    kev_index(cache_dir=None, allow_network=False) -> dict[cveID(str), entry]
    refresh(kb_dir=None, cache_dir=None, allow_network=True, top_kev=30, top_ghsa=20) -> dict
CLI:
    python -m hikeshi_agent.securitywatch --refresh
    python -m hikeshi_agent.securitywatch --status
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

# --- 固定の一次情報源（URL は外部入力を受けない＝ SSRF 面なし） ------------------------------
_KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
_GHSA_URL = "https://api.github.com/advisories"
_GHSA_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
_TIMEOUT = 20.0

_KEV_CACHE = "kev.json"
_GHSA_CACHE = "ghsa.json"
_MANIFEST_CACHE = "manifest.json"
_ADVISORY_KIND_DIR = "advisories"

# 火元帳(KB) へ書き起こす advisory doc 数（リポを肥大させない・3〜6 点）。
_TOP_KEV_DOCS = 4
_KEV_INDEX_CACHE = "kev_index.json"  # 突合用の全件軽量インデックス（KB doc の抜粋とは分離）
_TOP_GHSA_DOCS = 2

_SEV_RANK = {"critical": 4, "high": 3, "moderate": 2, "medium": 2, "low": 1, "unknown": 0}


class SecurityWatchError(RuntimeError):
    """夜回りの取得/キャッシュ失敗（黙って偽値を返さず、原因を明示して投げる）。"""


def _default_cache_dir() -> Path:
    return Path(__file__).parent / "securitywatch_cache"


def _default_kb_dir() -> Path:
    return Path(__file__).parent / "kb"


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _http_get_json(url: str, headers: dict | None = None, params: dict | None = None):
    """固定 URL へ GET（timeout 付き）。失敗は明確な例外へ変換（握りつぶさない）。

    httpx は取得時のみ遅延 import する＝オフライン経路（kev_index のキャッシュ読み）は
    httpx 無しでも動く（bench 等の最小環境で depscan の KEV 突合が import で落ちない）。
    """
    try:
        import httpx  # 遅延 import（ネット取得の時だけ必要）
    except ImportError as e:
        raise SecurityWatchError(
            "夜回りのネット取得には httpx が必要です（`pip install httpx`）。"
            "オフラインのキャッシュ読みには不要です。"
        ) from e
    try:
        resp = httpx.get(url, headers=headers, params=params, timeout=_TIMEOUT,
                         follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as e:  # ネット障害・非 2xx・JSON 破損
        raise SecurityWatchError(f"夜回りの取得に失敗しました（{url}）: {e}") from e


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    """アトミック書込（同一ディレクトリの temp → os.replace）。中断で破損を残さない。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# --- CISA KEV -------------------------------------------------------------------------------


def fetch_kev(cache_dir: str | Path | None = None, allow_network: bool = True) -> dict:
    """CISA KEV を返す（title/count/vulnerabilities）。

    cache_dir 指定時はキャッシュ優先 → 無ければ取得してキャッシュ。
    allow_network=False かつキャッシュ無しは明確な例外（偽値フォールバック禁止）。
    """
    cdir = Path(cache_dir) if cache_dir else None
    if cdir is not None:
        cp = cdir / _KEV_CACHE
        if cp.exists():
            return _read_json(cp)
    if not allow_network:
        raise SecurityWatchError(
            "CISA KEV のキャッシュが無く allow_network=False です（取得できないため空を返しません）。"
        )
    raw = _http_get_json(_KEV_URL)
    if not isinstance(raw, dict):
        raise SecurityWatchError("CISA KEV の応答がオブジェクトではありません（形式異常）。")
    vulns = raw.get("vulnerabilities", []) or []
    snapshot = {
        "title": raw.get("title", ""),
        "catalogVersion": raw.get("catalogVersion", ""),
        "dateReleased": raw.get("dateReleased", ""),
        "source_url": _KEV_URL,
        "fetched_at": _utcnow_iso(),
        "count": len(vulns),
        "vulnerabilities": vulns,
    }
    if cdir is not None:
        _write_json(cdir / _KEV_CACHE, snapshot)
    return snapshot


# --- GitHub Advisory ------------------------------------------------------------------------


def _trim_ghsa(entry: dict) -> dict:
    """GHSA entry を必要フィールドだけに絞る（リポを軽く保つ・provenance は保持）。"""
    return {
        "ghsa_id": entry.get("ghsa_id", ""),
        "cve_id": entry.get("cve_id"),
        "severity": (entry.get("severity") or "unknown"),
        "summary": entry.get("summary", ""),
        "published_at": entry.get("published_at", ""),
        "html_url": entry.get("html_url", ""),
        "vulnerabilities": [
            {"package": {
                "ecosystem": (v.get("package") or {}).get("ecosystem", ""),
                "name": (v.get("package") or {}).get("name", ""),
            }}
            for v in (entry.get("vulnerabilities") or [])
        ],
    }


def fetch_ghsa(cache_dir: str | Path | None = None, allow_network: bool = True,
               per_page: int = 30) -> list[dict]:
    """GitHub Advisory の最新公開分を返す（list[dict]）。

    cache_dir 指定時はキャッシュ優先。allow_network=False かつキャッシュ無しは明確な例外。
    """
    cdir = Path(cache_dir) if cache_dir else None
    if cdir is not None:
        cp = cdir / _GHSA_CACHE
        if cp.exists():
            return _read_json(cp)
    if not allow_network:
        raise SecurityWatchError(
            "GitHub Advisory のキャッシュが無く allow_network=False です（偽値を返しません）。"
        )
    raw = _http_get_json(_GHSA_URL, headers=_GHSA_HEADERS,
                         params={"per_page": min(max(int(per_page), 1), 100), "sort": "published"})
    if not isinstance(raw, list):
        raise SecurityWatchError("GitHub Advisory の応答が配列ではありません（形式異常）。")
    entries = [_trim_ghsa(e) for e in raw]
    if cdir is not None:
        _write_json(cdir / _GHSA_CACHE, entries)
    return entries


# --- depscan 突合用インデックス（既定オフライン＝コミット済みキャッシュ） ----------------------


def _build_kev_index(vulns: list[dict]) -> dict[str, dict]:
    """KEV カタログ全件を突合用の軽量 dict {cveID: {due/ransom/product/vendor}} に落とす。"""
    idx: dict[str, dict] = {}
    for v in vulns or []:
        cid = str(v.get("cveID", "")).strip().upper()
        if not cid:
            continue
        idx.setdefault(cid, {
            "cveID": cid,
            "dueDate": v.get("dueDate", ""),
            "knownRansomwareCampaignUse": v.get("knownRansomwareCampaignUse", ""),
            "product": v.get("product", ""),
            "vendorProject": v.get("vendorProject", ""),
        })
    return idx


def kev_index(cache_dir: str | Path | None = None, allow_network: bool = False) -> dict[str, dict]:
    """KEV を {cveID(大文字): entry} で返す（depscan 突合用・既定オフライン）。

    cache_dir 未指定はコミット済みの既定キャッシュを引く。取得できない場合は空 dict
    （enrichment なので depscan を落とさない＝「KEV データ無し」を正直に空で表す。捏造しない）。
    """
    cdir = Path(cache_dir) if cache_dir else _default_cache_dir()
    # 突合用の全件軽量インデックスがあればそれを使う（カバレッジ＝カタログ全件）。
    ip = cdir / _KEV_INDEX_CACHE
    if ip.exists():
        try:
            return _read_json(ip)
        except (OSError, ValueError):
            pass
    # 無ければ取得を試み（allow_network 時のみ）、失敗時は KB 抜粋 kev.json にフォールバック。
    if allow_network:
        try:
            kev = fetch_kev(cache_dir=None, allow_network=True)
            idx = _build_kev_index(kev.get("vulnerabilities", []))
            _write_json(ip, idx)
            return idx
        except SecurityWatchError:
            pass
    try:
        kev = fetch_kev(cache_dir=cdir, allow_network=False)
    except SecurityWatchError:
        return {}
    return _build_kev_index(kev.get("vulnerabilities", []))


# --- refresh（取得 → KB 生成 → manifest） ----------------------------------------------------


def _sanitize_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", s or "")


def _select_kev_docs(vulns: list[dict], n: int) -> list[dict]:
    """KB 化する KEV を決定的に選ぶ（ランサム使用→新しい追加日→cveID の順）。"""
    def key(v: dict):
        ransom = 1 if str(v.get("knownRansomwareCampaignUse", "")).lower() == "known" else 0
        return (-ransom, _neg_date(v.get("dateAdded", "")), str(v.get("cveID", "")))
    return sorted(vulns, key=key)[:n]


def _select_ghsa_docs(entries: list[dict], n: int) -> list[dict]:
    """KB 化する GHSA を決定的に選ぶ（重大度→新しい公開日→ghsa_id の順）。"""
    def key(e: dict):
        rank = _SEV_RANK.get(str(e.get("severity", "")).lower(), 0)
        return (-rank, _neg_str(e.get("published_at", "")), str(e.get("ghsa_id", "")))
    return sorted(entries, key=key)[:n]


def _neg_date(s: str) -> str:
    # ISO 日付文字列を「新しいほど小さい」キーへ（降順ソートを昇順 sorted で表現）。
    return _neg_str(s)


def _neg_str(s: str) -> str:
    # 文字コードを反転して降順化（決定的・ロケール非依存）。
    return "".join(chr(0x10FFFF - ord(c)) for c in (s or ""))


def _kev_doc_md(v: dict, fetched_at: str) -> tuple[str, str]:
    """KEV 1 件 → (filename, markdown)。title 行＝先頭非空行（retriever 規則）。"""
    cve = str(v.get("cveID", "")).strip()
    name = v.get("vulnerabilityName", "") or cve
    ransom = str(v.get("knownRansomwareCampaignUse", "")).lower() == "known"
    fname = f"kev-{_sanitize_id(cve)}.md"
    lines = [
        f"# 夜回り速報（CISA KEV・実際に悪用中）: {cve} — {name}",
        "",
        "（実データ：CISA KEV／合成ではありません）",
        "",
        "## 分類",
        "- advisory 種別: KEV（Known Exploited Vulnerabilities＝実際に悪用が確認された脆弱性）。",
        f"- CVE: {cve}",
        f"- ベンダ/製品: {v.get('vendorProject', '')} / {v.get('product', '')}",
        f"- 重大度: KEV 掲載＝実悪用中（active_exploitation）。ランサムウェア悪用: "
        f"{'確認あり（Known）' if ransom else '未確認（Unknown）'}。",
        "",
        "## 要点",
        f"- {v.get('shortDescription', '')}",
        "",
        "## 推奨対処",
        f"- {v.get('requiredAction', '')}",
        f"- KEV 期限（dueDate）: {v.get('dueDate', '')} までに是正（連邦機関向け基準・一般にも指標）。",
        "",
        "## 出典（provenance）",
        f"- 出典URL: {_KEV_URL}",
        f"- 参考: {v.get('notes', '')}",
        f"- 取得日時（fetched_at）: {fetched_at}",
        "- 実データ（CISA KEV）を夜回りジョブが定期取得して決定的に生成。汎用 LLM の知識"
        "カットオフを超えた「今まさに悪用中」の情報を火元帳に蓄える。",
        "",
    ]
    return fname, "\n".join(lines)


def _ghsa_doc_md(e: dict, fetched_at: str) -> tuple[str, str]:
    """GHSA 1 件 → (filename, markdown)。"""
    ghsa = str(e.get("ghsa_id", "")).strip()
    cve = e.get("cve_id") or "（CVE 未採番）"
    pkgs = ", ".join(
        f"{(v.get('package') or {}).get('ecosystem', '')}:{(v.get('package') or {}).get('name', '')}"
        for v in (e.get("vulnerabilities") or [])
    ) or "（パッケージ情報なし）"
    fname = f"ghsa-{_sanitize_id(ghsa)}.md"
    lines = [
        f"# 夜回り速報（GitHub Advisory）: {ghsa} — {e.get('summary', '')}",
        "",
        "（実データ：GitHub Advisory（GHSA）／合成ではありません）",
        "",
        "## 分類",
        "- advisory 種別: GHSA（GitHub Security Advisory・公開直後の新規脆弱性）。",
        f"- GHSA: {ghsa}",
        f"- CVE: {cve}",
        f"- 重大度: {str(e.get('severity', 'unknown')).upper()}",
        f"- 影響パッケージ: {pkgs}",
        "",
        "## 要点",
        f"- {e.get('summary', '')}",
        "",
        "## 出典（provenance）",
        f"- 出典URL: {e.get('html_url', '')}",
        f"- 公開日時（published_at）: {e.get('published_at', '')}",
        f"- 取得日時（fetched_at）: {fetched_at}",
        "- 実データ（GHSA）を夜回りジョブが定期取得して決定的に生成。合成ではない。",
        "",
    ]
    return fname, "\n".join(lines)


def refresh(kb_dir: str | Path | None = None, cache_dir: str | Path | None = None,
            allow_network: bool = True, top_kev: int = 30, top_ghsa: int = 20) -> dict:
    """取得 → KB(advisories/*.md) を決定的に生成 → manifest を返す。

    - allow_network=True: 一次情報源から取得し、top_kev/top_ghsa 件に絞ってキャッシュへ保存。
    - allow_network=False: コミット済みキャッシュから決定的に再生成（fetched_at はキャッシュ由来）。
    生成 md はファイル名・内容ともに決定的（同一キャッシュ→同一出力）。
    """
    cdir = Path(cache_dir) if cache_dir else _default_cache_dir()
    kbdir = Path(kb_dir) if kb_dir else _default_kb_dir()

    if allow_network:
        # 一次情報源から取得 → top_N に絞る → キャッシュへ（コミット用スナップショット）。
        kev = fetch_kev(cache_dir=None, allow_network=True)
        ghsa = fetch_ghsa(cache_dir=None, allow_network=True, per_page=min(max(top_ghsa, 30), 100))
        kev_vulns = _select_kev_docs(kev.get("vulnerabilities", []), top_kev)
        kev_snapshot = {
            "title": kev.get("title", ""),
            "catalogVersion": kev.get("catalogVersion", ""),
            "dateReleased": kev.get("dateReleased", ""),
            "source_url": _KEV_URL,
            "fetched_at": kev.get("fetched_at", _utcnow_iso()),
            "catalog_total": kev.get("count", len(kev.get("vulnerabilities", []))),
            "count": len(kev_vulns),
            "vulnerabilities": kev_vulns,
        }
        ghsa_snapshot = _select_ghsa_docs(ghsa, top_ghsa)
        # 突合は KB 抜粋(top_kev)ではなくカタログ全件に効かせる＝軽量インデックスを別途保存。
        kev_full_index = _build_kev_index(kev.get("vulnerabilities", []))
        _write_json(cdir / _KEV_CACHE, kev_snapshot)
        _write_json(cdir / _GHSA_CACHE, ghsa_snapshot)
        _write_json(cdir / _KEV_INDEX_CACHE, kev_full_index)
        fetched_at = kev_snapshot["fetched_at"]
    else:
        kev_snapshot = fetch_kev(cache_dir=cdir, allow_network=False)
        ghsa_snapshot = fetch_ghsa(cache_dir=cdir, allow_network=False)
        # fetched_at はキャッシュ由来（決定的）。無ければ manifest から補完。
        fetched_at = kev_snapshot.get("fetched_at", "")
        mp = cdir / _MANIFEST_CACHE
        if not fetched_at and mp.exists():
            fetched_at = _read_json(mp).get("fetched_at", "")

    # KB(advisories/*.md) を決定的に生成（既存 advisory md は再生成前に掃除＝stale を残さない）。
    adv_dir = kbdir / _ADVISORY_KIND_DIR
    adv_dir.mkdir(parents=True, exist_ok=True)
    for old in adv_dir.glob("*.md"):
        old.unlink()

    written: list[str] = []
    for v in _select_kev_docs(kev_snapshot.get("vulnerabilities", []), _TOP_KEV_DOCS):
        fname, md = _kev_doc_md(v, fetched_at)
        (adv_dir / fname).write_text(md, encoding="utf-8")
        written.append(fname)
    for e in _select_ghsa_docs(ghsa_snapshot, _TOP_GHSA_DOCS):
        fname, md = _ghsa_doc_md(e, fetched_at)
        (adv_dir / fname).write_text(md, encoding="utf-8")
        written.append(fname)

    manifest = {
        "fetched_at": fetched_at,
        "kev_count": len(kev_snapshot.get("vulnerabilities", [])),
        "ghsa_count": len(ghsa_snapshot),
        "advisory_docs": sorted(written),
        "source_urls": {"kev": _KEV_URL, "ghsa": _GHSA_URL},
    }
    _write_json(cdir / _MANIFEST_CACHE, manifest)
    return manifest


# --- コンソール表示用の読み取り専用ヘルパ（状態変更なし・実データのみ） ------------------------


def current_manifest(cache_dir: str | Path | None = None) -> dict:
    """直近の夜回りマニフェスト（fetched_at/件数/出典）を返す。無ければ空 dict。"""
    cdir = Path(cache_dir) if cache_dir else _default_cache_dir()
    mp = cdir / _MANIFEST_CACHE
    if mp.exists():
        try:
            return _read_json(mp)
        except (OSError, ValueError):
            return {}
    return {}


def newest_kev(limit: int = 5, cache_dir: str | Path | None = None) -> list[dict]:
    """表示用に最新の KEV を数点返す（実データ・KB 抜粋キャッシュから・決定的順）。"""
    cdir = Path(cache_dir) if cache_dir else _default_cache_dir()
    try:
        kev = fetch_kev(cache_dir=cdir, allow_network=False)
    except SecurityWatchError:
        return []
    out = []
    for v in _select_kev_docs(kev.get("vulnerabilities", []), max(1, int(limit))):
        out.append({
            "cveID": v.get("cveID", ""),
            "vendorProject": v.get("vendorProject", ""),
            "product": v.get("product", ""),
            "vulnerabilityName": v.get("vulnerabilityName", ""),
            "dueDate": v.get("dueDate", ""),
            "knownRansomwareCampaignUse": v.get("knownRansomwareCampaignUse", ""),
        })
    return out


# --- CLI（決定的な要約を stdout・鍵不要） -----------------------------------------------------


def _cmd_status() -> int:
    cdir = _default_cache_dir()
    try:
        kev = fetch_kev(cache_dir=cdir, allow_network=False)
        ghsa = fetch_ghsa(cache_dir=cdir, allow_network=False)
    except SecurityWatchError as e:
        print(f"夜回り: キャッシュ未整備（{e}）。--refresh で一次情報源から取得してください。")
        return 1
    mp = cdir / _MANIFEST_CACHE
    fetched_at = kev.get("fetched_at", "")
    if mp.exists():
        fetched_at = _read_json(mp).get("fetched_at", fetched_at)
    print("夜回り（Yomawari）ステータス — 実データ（CISA KEV / GitHub Advisory）")
    print(f"  取得日時: {fetched_at}")
    print(f"  KEV スナップショット: {kev.get('count', 0)} 件"
          f"（カタログ全体 {kev.get('catalog_total', '不明')} 件の抜粋）")
    print(f"  GHSA スナップショット: {len(ghsa)} 件")
    print(f"  出典: {_KEV_URL}")
    print(f"        {_GHSA_URL}")
    return 0


def _cmd_refresh() -> int:
    m = refresh(allow_network=True)
    print("夜回り: 一次情報源から取得し火元帳(KB)を更新しました。")
    print(f"  取得日時: {m['fetched_at']}")
    print(f"  KEV: {m['kev_count']} 件 / GHSA: {m['ghsa_count']} 件")
    print(f"  生成 advisory: {', '.join(m['advisory_docs'])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m hikeshi_agent.securitywatch",
        description="夜回り（Yomawari）— CISA KEV / GitHub Advisory を実取得して火元帳(KB)へ。",
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--refresh", action="store_true", help="一次情報源から取得し KB を更新（要ネット）。")
    g.add_argument("--status", action="store_true", help="コミット済みキャッシュの決定的要約を表示。")
    args = ap.parse_args(argv)
    if args.refresh:
        return _cmd_refresh()
    return _cmd_status()


if __name__ == "__main__":
    raise SystemExit(main())
