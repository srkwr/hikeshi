"""Agentic RAG リトリーバ — Runbook/ポストモーテム/過去事例の curated KB を実検索する。

設計（§20 正直さ重視）:
- KB は `hikeshi_agent/kb/<kind>/*.md`（kind = runbook | postmortem | incident）。**合成のサンプル**
  （実顧客データではない・各 doc 冒頭に provenance）。
- 既定バックエンド = **決定的なキーワード/BM25-lite**（標準ライブラリのみ・オフライン・CI 安全・無課金・
  日本語は CJK 文字バイグラムで分かち書き不要）。エージェントが「ツール名だけ」でなく**実際に取得した
  本文**で診断を根拠付けられる＝モデルの創作（hallucination）を排除する、という RAG の核を満たす。
- **Vertex Vector Search 2.0（セマンティック）は同一の `search()` インターフェース背後の本番バックエンド**
  （スケール時に差し替え）。ここでは過大主張せず「キーワード検索」を正直に名乗る。
- **Elastic Cloud バックエンド（任意・既定 off）**：`HIKESHI_ES_URL` 設定時のみ、公開関数の入り口で
  `retriever_es.py` へ委譲（同一 IF）。未設定なら本経路は完全不変（README「Elastic Cloud バックエンド」参照）。

公開 API:
    search(query, kind=None, k=2) -> list[{title, kind, source, text, score}]
"""

from __future__ import annotations

import math
import os
import re
from functools import lru_cache
from pathlib import Path

_KB_DIR = Path(__file__).parent / "kb"
# advisory は夜回り（securitywatch.refresh）が実データ（CISA KEV / GHSA）から決定的生成する新種別。
# 末尾に追加＝既存 kind の索引順・list_docs 順を不変に保つ。
_KIND_DIRS = {"runbook": "runbooks", "postmortem": "postmortems", "incident": "incidents",
              "advisory": "advisories"}

def _es_backend():
    """HIKESHI_ES_URL（別名 ELASTIC_URL）設定時だけ Elastic バックエンド（retriever_es）を返す。

    既定（env 未設定）は None ＝ 本モジュールの BM25-lite 経路が完全不変（決定的・CI 安全）。
    判定は公開関数の**呼び出し時**に行う＝モジュール読み込みは env に依存しない。
    別名は Cloud Run 側で先行準備されていた命名（ELASTIC_URL/ELASTIC_API_KEY）との互換。
    """
    if os.environ.get("HIKESHI_ES_URL") or os.environ.get("ELASTIC_URL"):
        from . import retriever_es

        return retriever_es
    return None


_ASCII = re.compile(r"[a-z0-9_]+")
_CJK = re.compile(r"[぀-ヿ一-鿿]+")


def _tokenize(s: str) -> list[str]:
    """ASCII 単語 ＋ CJK 文字バイグラム（形態素解析なしで日本語を検索可能に）。"""
    s = s.lower()
    toks = _ASCII.findall(s)
    for run in _CJK.findall(s):
        if len(run) == 1:
            toks.append(run)
        else:
            toks.extend(run[i : i + 2] for i in range(len(run) - 1))
    return toks


class _Doc:
    __slots__ = ("title", "kind", "source", "text", "tf", "norm")

    def __init__(self, title: str, kind: str, source: str, text: str):
        self.title = title
        self.kind = kind
        self.source = source
        self.text = text
        tf: dict[str, int] = {}
        for t in _tokenize(title + "\n" + text):
            tf[t] = tf.get(t, 0) + 1
        self.tf = tf
        self.norm = math.sqrt(sum(v * v for v in tf.values())) or 1.0


# 消火（診断）の RAG が引く中核 kind。advisory（夜回りの脅威インテリ）はこれと
# **別コーパス**で idf を計算する＝advisory を足しても中核 kind の tf-idf スコアが
# advisory 導入前とバイト単位で不変（診断挙動を一切変えない・ベンチ/recorded 不変）。
_CORE_KINDS = ("runbook", "postmortem", "incident")


def _idf_of(docs: list[_Doc]) -> dict[str, float]:
    n = len(docs) or 1
    df: dict[str, int] = {}
    for doc in docs:
        for t in doc.tf:
            df[t] = df.get(t, 0) + 1
    return {t: math.log((n + 1) / (c + 0.5)) + 1.0 for t, c in df.items()}


@lru_cache(maxsize=1)
def _index() -> dict:
    """KB を読み込み、中核コーパスと advisory コーパスを分離して構築（初回のみ・キャッシュ）。

    返り値: {"all": [_Doc,...]（_KIND_DIRS 順・list_docs 用),
             "core": ([中核 _Doc], idf), "advisory": ([advisory _Doc], idf)}。
    中核 idf は advisory を含まない＝advisory 追加前と完全一致。
    """
    all_docs: list[_Doc] = []
    for kind, sub in _KIND_DIRS.items():
        d = _KB_DIR / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            first = next((ln for ln in text.splitlines() if ln.strip()), p.stem)
            title = first.lstrip("# ").strip() or p.stem
            all_docs.append(_Doc(title=title, kind=kind, source=p.name, text=text))
    core = [d for d in all_docs if d.kind in _CORE_KINDS]
    advisory = [d for d in all_docs if d.kind == "advisory"]
    return {"all": all_docs, "core": (core, _idf_of(core)),
            "advisory": (advisory, _idf_of(advisory))}


def kb_size() -> int:
    return len(_index()["all"])


def reindex() -> int:
    """KB を再索引する（`_index` のキャッシュ破棄→次回検索で再構築）。

    ポストモーテム承認（HITL）で `kb/` に doc が追加された直後に呼び、
    プロセス再起動なしで**次の診断から検索対象**にする。新しい KB サイズを返す。
    """
    es = _es_backend()
    if es is not None:
        return es.reindex()
    _index.cache_clear()
    return kb_size()


def list_docs() -> list[dict]:
    """KB 全 doc のメタデータ（title/kind/source/文字数）を索引順＝決定的に返す（KB ブラウザ用）。"""
    es = _es_backend()
    if es is not None:
        return es.list_docs()
    docs = _index()["all"]
    return [{"title": d.title, "kind": d.kind, "source": d.source, "chars": len(d.text)} for d in docs]


def get_doc(source: str) -> dict | None:
    """索引済み doc の source（ファイル名）への完全一致で1件返す。無ければ None。

    クライアントからパスを受けず、インメモリ索引にある名前しか引けない＝
    パストラバーサル経路が存在しない（コンソールの /api/kb/doc 用）。
    """
    es = _es_backend()
    if es is not None:
        return es.get_doc(source)
    for d in _index()["all"]:
        if d.source == source:
            return {"title": d.title, "kind": d.kind, "source": d.source, "text": d.text}
    return None


def search(query: str, kind: str | None = None, k: int = 2) -> list[dict]:
    """query に最も合致する KB チャンクを上位 k 件返す（tf-idf 重みの決定的ランキング）。

    advisory は中核 kind と別コーパス（別 idf）。kind 未指定は中核のみ検索＝診断挙動を
    advisory 導入前と完全一致に保つ。advisory は kind="advisory" で明示検索する（夜回りパネル）。
    """
    es = _es_backend()
    if es is not None:
        return es.search(query, kind=kind, k=k)
    idx = _index()
    corpus = "advisory" if kind == "advisory" else "core"
    docs, idf = idx[corpus]
    qt = set(_tokenize(query or ""))
    scored: list[tuple[float, str, _Doc]] = []
    for doc in docs:
        if kind and doc.kind != kind:  # 中核内の kind 絞り込み（runbook 等）
            continue
        s = sum(idf.get(t, 0.0) * doc.tf.get(t, 0) for t in qt) / doc.norm
        if s > 0:
            scored.append((s, doc.source, doc))  # source でタイブレーク＝決定的
    scored.sort(key=lambda x: (-x[0], x[1]))
    out = []
    for s, _src, doc in scored[: max(1, k)]:
        out.append(
            {
                "title": doc.title,
                "kind": doc.kind,
                "source": doc.source,
                "text": doc.text.strip(),
                "score": round(s, 4),
            }
        )
    return out
