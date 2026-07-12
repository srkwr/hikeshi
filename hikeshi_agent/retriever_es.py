"""Elasticsearch バックエンド（任意・既定 off）— retriever.py と同一の公開 IF を提供する。

接続先は2通り（どちらも同じコード経路・env だけで切替）:
- **Elastic Cloud（SaaS）** … API key 認証（従来どおり）。
- **自己ホスト Elasticsearch on Cloud Run（IAM 非公開）** … `HIKESHI_ES_AUTH=idtoken` で
  Google ID トークン接続（`scripts/deploy-es.sh` でデプロイ）。ES 側は xpack.security
  無効のため、Authorization ヘッダは Cloud Run の IAM ゲートだけが解釈する。

設計（正直さ規律）:
- **env ゲート・既定 off**：`HIKESHI_ES_URL` が設定された時だけ `retriever.py` の入り口から
  委譲される。未設定なら本モジュールは一切実行されず、既定のローカル BM25-lite 経路
  （決定的・オフライン・CI 安全）は完全不変。
- `elasticsearch` / `google-auth` は**遅延 import**（未インストールでも本モジュールの
  import 自体は成立）。
- 接続失敗・未インストール時は**明確な日本語 RuntimeError** を投げる。黙ってローカル検索へ
  フォールバックしない＝設定ミスを隠さない（表示値はすべて実データ、の規律）。
- **空インデックス自己修復**：Cloud Run の自己ホスト ES はエフェメラル＝再起動で index が
  消える。検索系操作が index_not_found を検知したら、イメージ内の正本 kb/*.md から
  `reindex()` して**1回だけ**再試行する（正本からの再構築＝捏造ではない・ログに明示）。

env:
    HIKESHI_ES_URL      … Elasticsearch endpoint URL（これが本バックエンドの有効化スイッチ）
    HIKESHI_ES_AUTH     … "idtoken" で Google ID トークン接続（Cloud Run 自己ホスト用）。
                          未設定/その他の値は従来どおり API key 必須。
    HIKESHI_ES_API_KEY  … API key（Elastic Cloud: Kibana → Stack Management → API keys で発行）
    HIKESHI_ES_INDEX    … index 名（既定 "hikeshi-kb"）
    別名（Cloud Run 側で先行準備されていた命名との互換・上記が優先）:
    ELASTIC_URL / ELASTIC_API_KEY / ELASTIC_INDEX_PREFIX（index="<prefix>-kb"）

公開 API（retriever.py と同一シグネチャ・同一返り値形状）:
    search(query, kind=None, k=2) -> list[{title, kind, source, text, score}]
    list_docs() -> list[{title, kind, source, chars}]
    get_doc(source) -> {title, kind, source, text} | None
    reindex() -> int  （kb/*.md を bulk index し、doc 数を返す）
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

_KB_DIR = Path(__file__).parent / "kb"
# advisory は夜回り（securitywatch）が実データ（CISA KEV / GHSA）から生成する新種別（末尾追加）。
_KIND_DIRS = {"runbook": "runbooks", "postmortem": "postmortems", "incident": "incidents",
              "advisory": "advisories"}


def _env(*names: str) -> str | None:
    """複数候補の env 名から最初に設定されている値を返す（正本名→互換別名の順で渡す）。"""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def _index_name() -> str:
    explicit = _env("HIKESHI_ES_INDEX")
    if explicit:
        return explicit
    prefix = _env("ELASTIC_INDEX_PREFIX")
    return f"{prefix}-kb" if prefix else "hikeshi-kb"


# idtoken モードのトークンキャッシュ（exp の5分前まで再利用＝検索ごとの取得を避ける。
# console/app.py の _demo_id_token と同じ流儀。audience も持ち、URL 変更時は再取得）。
_IDTOKEN_CACHE: dict[str, object] = {"value": None, "aud": None, "exp": 0.0}


def _jwt_exp(token: str) -> float:
    """ID トークン(JWT)の exp(秒) を取り出す。失敗時は now+3000（控えめにキャッシュ）。"""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # base64url のパディング復元
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return float(payload["exp"])
    except Exception:
        return time.time() + 3000.0


def _id_token(audience: str) -> str:
    """audience（HIKESHI_ES_URL のオリジン）向け Google ID トークンを取得する。

    Cloud Run サービス間認証（IAM 非公開の自己ホスト ES）用。メタデータサーバ
    （Cloud Run のランタイム SA）か GOOGLE_APPLICATION_CREDENTIALS から取得し、
    exp の5分前まではキャッシュする。取得不可は明確な RuntimeError＝黙って無認証や
    ローカル検索へフォールバックしない。
    """
    now = time.time()
    cached = _IDTOKEN_CACHE.get("value")
    if (
        isinstance(cached, str)
        and _IDTOKEN_CACHE.get("aud") == audience
        and float(_IDTOKEN_CACHE.get("exp", 0.0)) - now > 300
    ):
        return cached
    try:
        # 遅延 import（idtoken モードを使う時だけ google-auth が必要）
        import google.auth.transport.requests
        from google.oauth2 import id_token as _google_id_token
    except ImportError as e:
        raise RuntimeError(
            "HIKESHI_ES_AUTH=idtoken ですが google-auth ライブラリが未インストールです。"
            "`pip install 'google-auth[requests]'` を実行してください"
            "（ローカル検索へは黙ってフォールバックしません）。"
        ) from e
    try:
        token = _google_id_token.fetch_id_token(
            google.auth.transport.requests.Request(), audience
        )
    except Exception as e:
        raise RuntimeError(
            f"Google ID トークンの取得に失敗しました（audience={audience}）: {e} — "
            "Cloud Run のランタイム SA か GOOGLE_APPLICATION_CREDENTIALS を確認してください"
            "（ローカル検索へは黙ってフォールバックしません）。"
        ) from e
    _IDTOKEN_CACHE.update(value=token, aud=audience, exp=_jwt_exp(token))
    return token


def _client():
    """Elasticsearch クライアントを生成する。設定・依存・接続の不備は明確な RuntimeError。"""
    url = _env("HIKESHI_ES_URL", "ELASTIC_URL")
    if not url:
        raise RuntimeError(
            "Elastic バックエンドが呼ばれましたが HIKESHI_ES_URL（別名 ELASTIC_URL）が未設定です。"
            "Elasticsearch の endpoint URL を設定してください。"
        )
    api_key: str | None = None
    bearer: str | None = None
    if (os.environ.get("HIKESHI_ES_AUTH") or "").strip().lower() == "idtoken":
        # 自己ホスト ES on Cloud Run（IAM 非公開）: audience は HIKESHI_ES_URL のオリジン。
        # ES 側は xpack.security 無効＝この Bearer は Cloud Run の IAM ゲートだけが解釈する。
        parts = urlsplit(url)
        bearer = _id_token(f"{parts.scheme}://{parts.netloc}")
    else:
        api_key = _env("HIKESHI_ES_API_KEY", "ELASTIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "HIKESHI_ES_API_KEY（別名 ELASTIC_API_KEY）が未設定です。"
                "Elastic Cloud で API key を発行して設定するか、自己ホスト ES なら"
                " HIKESHI_ES_AUTH=idtoken を設定してください"
                "（ローカル検索へは黙ってフォールバックしません）。"
            )
    try:
        # 遅延 import（未インストールでもモジュール import は成立）
        from elasticsearch import Elasticsearch
    except ImportError as e:
        raise RuntimeError(
            "HIKESHI_ES_URL が設定されていますが elasticsearch ライブラリが未インストールです。"
            "`pip install '.[es]'` を実行してください（ローカル検索へは黙ってフォールバックしません）。"
        ) from e
    try:
        if bearer is not None:
            client = Elasticsearch(url, bearer_auth=bearer)
        else:
            client = Elasticsearch(url, api_key=api_key)
        if not client.ping():
            raise ConnectionError("ping failed")
    except Exception as e:
        raise RuntimeError(
            f"Elasticsearch への接続に失敗しました（HIKESHI_ES_URL={url}）: {e} — "
            "URL / 認証（API key、idtoken モードなら ES サービスへの roles/run.invoker）/ "
            "デプロイ稼働状態を確認してください"
            "（ローカル検索へは黙ってフォールバックしません）。"
        ) from e
    return client


def _load_kb_docs() -> list[dict]:
    """kb/*.md を retriever.py と同一規則（先頭非空行=title・ファイル名=source）で読み込む。"""
    docs: list[dict] = []
    for kind, sub in _KIND_DIRS.items():
        d = _KB_DIR / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            first = next((ln for ln in text.splitlines() if ln.strip()), p.stem)
            title = first.lstrip("# ").strip() or p.stem
            docs.append({"title": title, "kind": kind, "source": p.name, "text": text})
    return docs


def _is_index_not_found(exc: Exception) -> bool:
    """index 未作成（index_not_found_exception）由来の例外かを判定する。

    elasticsearch ライブラリは遅延 import（未インストールでも本モジュールをテスト可能）
    のため、例外型でなく本文で判定する（NotFoundError の str/body には必ず
    "index_not_found_exception" が含まれる）。
    """
    text = str(exc)
    body = getattr(exc, "body", None)
    if body is not None:
        text += " " + str(body)
    return "index_not_found" in text


def _with_reindex_retry(op):
    """検索系操作を実行し、index 未作成なら kb/*.md 正本から reindex して**1回だけ**再試行する。

    Cloud Run の自己ホスト ES はエフェメラル＝コンテナ再起動で index が消える。
    kb/*.md はコンソールイメージ内に常にある正本なので、そこからの再構築は値の捏造では
    ない（黙殺せずログに明示する）。再試行は1回のみ＝reindex 後も失敗するならそのまま
    例外を伝播（無限ループ防止・失敗を隠さない）。
    """
    try:
        return op()
    except Exception as e:
        if not _is_index_not_found(e):
            raise
        print(
            f"[retriever_es] index 未作成を検知（{e}）→ kb/*.md 正本から reindex して再試行します",
            flush=True,
        )
        n = reindex()
        print(f"[retriever_es] reindex 完了: {n} docs", flush=True)
        return op()


def reindex() -> int:
    """kb/*.md を index へ bulk index する（既存 index は作り直し）。doc 数を返す。"""
    client = _client()
    index = _index_name()
    docs = _load_kb_docs()
    if client.indices.exists(index=index):
        client.indices.delete(index=index)
    client.indices.create(
        index=index,
        mappings={
            "properties": {
                "title": {"type": "text"},
                "kind": {"type": "keyword"},
                "source": {"type": "keyword"},
                "text": {"type": "text"},
            }
        },
    )
    ops: list[dict] = []
    for doc in docs:
        ops.append({"index": {"_index": index, "_id": doc["source"]}})
        ops.append(doc)
    if ops:
        resp = client.bulk(operations=ops, refresh=True)
        if resp.get("errors"):
            raise RuntimeError(f"Elastic への bulk index でエラーが発生しました（index={index}）: {resp}")
    return len(docs)


def search(query: str, kind: str | None = None, k: int = 2) -> list[dict]:
    """query に最も合致する KB doc を上位 k 件返す（retriever.search と同一返り値形状）。"""
    client = _client()
    body: dict = {
        "query": {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": query or "",
                            "fields": ["title^2", "text"],
                        }
                    }
                ],
                **({"filter": [{"term": {"kind": kind}}]} if kind else {}),
            }
        },
        "size": max(1, k),
    }
    resp = _with_reindex_retry(lambda: client.search(index=_index_name(), **body))
    out = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        out.append(
            {
                "title": src["title"],
                "kind": src["kind"],
                "source": src["source"],
                "text": src["text"].strip(),
                "score": round(float(hit["_score"] or 0.0), 4),
            }
        )
    return out


def list_docs() -> list[dict]:
    """index 内の全 doc メタデータを決定的順（kind→source）で返す（retriever.list_docs と同形状）。"""
    client = _client()
    resp = _with_reindex_retry(
        lambda: client.search(index=_index_name(), query={"match_all": {}}, size=1000)
    )
    rows = [hit["_source"] for hit in resp["hits"]["hits"]]
    order = {k: i for i, k in enumerate(_KIND_DIRS)}
    rows.sort(key=lambda r: (order.get(r["kind"], 99), r["source"]))
    return [
        {"title": r["title"], "kind": r["kind"], "source": r["source"], "chars": len(r["text"])}
        for r in rows
    ]


def get_doc(source: str) -> dict | None:
    """source（ファイル名）への完全一致で1件返す。無ければ None（retriever.get_doc と同形状）。"""
    client = _client()
    resp = _with_reindex_retry(
        lambda: client.search(index=_index_name(), query={"term": {"source": source}}, size=1)
    )
    hits = resp["hits"]["hits"]
    if not hits:
        return None
    src = hits[0]["_source"]
    return {"title": src["title"], "kind": src["kind"], "source": src["source"], "text": src["text"]}
