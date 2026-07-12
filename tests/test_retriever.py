"""Agentic RAG リトリーバの決定的テスト（オフライン・API/鍵不要・CI で常時緑）。

KB（hikeshi_agent/kb）への実検索が、各インシデントの症状クエリで正しい Runbook を
上位に返すことを検証する（取得→グラウンドの土台＝モデルの創作ではない実コンテンツ）。
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hikeshi_agent.retriever import kb_size, reindex, search  # noqa: E402


def test_kb_loads():
    assert kb_size() >= 6, "KB doc が読み込めていない"


def _top(query, kind=None):
    r = search(query, kind=kind, k=2)
    assert r, f"no results for {query!r}"
    return r[0]


def test_retrieves_right_runbook_per_symptom():
    # 症状クエリ → 正しい Runbook が1位（remediation の語が本文に含まれる）。
    cases = [
        ("デプロイ直後 5xx 急騰 self degraded", "deploy-regression.md", "ロールバック"),
        ("upstream 5xx self healthy 外部依存 連鎖", "dependency-failure.md", "runbook_mitigation"),
        ("メモリ 96% 飽和 直近デプロイなし", "resource-exhaustion.md", "scale"),
        ("設定 環境変数 タイムアウト 誤り", "config-error.md", "config_fix"),
        ("キャッシュ stale データ不整合", "data-cache-issue.md", "cache_flush"),
    ]
    for query, expect_source, expect_term in cases:
        top = _top(query, kind="runbook")
        assert top["source"] == expect_source, f"{query!r} -> {top['source']}"
        assert expect_term in top["text"], f"{expect_source} should mention {expect_term}"


def test_kind_filter():
    assert _top("デプロイ ロールバック", kind="postmortem")["kind"] == "postmortem"
    assert _top("外部依存 フェイルオーバー", kind="incident")["kind"] == "incident"


def test_deterministic():
    a = search("デプロイ 5xx ロールバック", kind="runbook", k=3)
    b = search("デプロイ 5xx ロールバック", kind="runbook", k=3)
    assert [x["source"] for x in a] == [x["source"] for x in b]


def test_code_knowledge():
    # コード起因の調査（diff/code_fix）の知識が引けること（「コーディング情報はあるか」への回答）。
    top = _top("diff の読み方 コード 原因 特定", kind="runbook")
    assert top["source"] == "code-investigation.md", f"-> {top['source']}"
    assert "code_fix" in top["text"]
    # 不可逆（書込済みデータ）のデプロイ不具合 → rollback でなく code_fix のポストモーテム。
    top = _top("不可逆 書込 データ 過剰請求 デプロイ", kind="postmortem")
    assert top["source"] == "billing-deploy-irreversible.md", f"-> {top['source']}"
    assert "code_fix" in top["text"]


def test_reindex():
    # 再索引（lru_cache 破棄）後もサイズ・結果が一貫（HITL 承認後の即時反映に使う）。
    before = kb_size()
    assert reindex() == before
    top = _top("デプロイ直後 5xx 急騰 self degraded", kind="runbook")
    assert top["source"] == "deploy-regression.md"


# --- Elastic Cloud バックエンド（任意・既定 off）のオフライン検証 -------------------------


_ES_ENV_KEYS = ("HIKESHI_ES_URL", "HIKESHI_ES_API_KEY", "HIKESHI_ES_INDEX", "HIKESHI_ES_AUTH",
                "ELASTIC_URL", "ELASTIC_API_KEY", "ELASTIC_INDEX_PREFIX")


def _clear_es_env():
    for k in _ES_ENV_KEYS:
        os.environ.pop(k, None)


def test_es_env_unset_uses_local_path_unchanged():
    # env 未設定なら委譲先は None ＝ 既存のローカル BM25-lite 経路が使われ、結果も従来と同一。
    _clear_es_env()
    from hikeshi_agent import retriever

    assert retriever._es_backend() is None
    a = search("デプロイ直後 5xx 急騰 self degraded", kind="runbook", k=2)
    assert a and a[0]["source"] == "deploy-regression.md"
    assert set(a[0]) == {"title", "kind", "source", "text", "score"}


def test_es_alias_env_names_also_gate_and_map():
    # Cloud Run 側で先行準備されていた別名（ELASTIC_URL/ELASTIC_API_KEY/ELASTIC_INDEX_PREFIX）
    # でもゲートが開き、index 名は "<prefix>-kb" に写像される（正本名があれば正本名が優先）。
    _clear_es_env()
    from hikeshi_agent import retriever, retriever_es

    try:
        os.environ["ELASTIC_URL"] = "https://example.invalid:9200"
        assert retriever._es_backend() is retriever_es
        os.environ["ELASTIC_INDEX_PREFIX"] = "hikeshi"
        assert retriever_es._index_name() == "hikeshi-kb"
        os.environ["HIKESHI_ES_INDEX"] = "explicit-index"  # 正本名が優先
        assert retriever_es._index_name() == "explicit-index"
    finally:
        _clear_es_env()


def test_retriever_es_importable_without_elasticsearch():
    # elasticsearch ライブラリ未インストールでも import しただけでは落ちない（遅延 import）。
    import hikeshi_agent.retriever_es  # noqa: F401


def _fake_es_module():
    """elasticsearch ライブラリの決定的フェイク（オフライン・実接続なし）。

    検索の応答/例外はテスト側が `mod._search_impl(calls, kwargs)` を差し込んで決める。
    calls: init に渡った kwargs・search/bulk 呼び出し回数を記録（自己修復の検証用）。
    """
    mod = types.ModuleType("elasticsearch")
    calls = {"init": [], "search": 0, "bulk": 0}

    class _Indices:
        def exists(self, index):
            return False

        def create(self, index, **kwargs):
            return None

        def delete(self, index):
            return None

    class Elasticsearch:
        def __init__(self, url, **kwargs):
            calls["init"].append({"url": url, **kwargs})
            self.indices = _Indices()

        def ping(self):
            return True

        def bulk(self, operations, refresh=True):
            calls["bulk"] += 1
            return {"errors": False}

        def search(self, **kwargs):
            calls["search"] += 1
            return mod._search_impl(calls, kwargs)

    mod.Elasticsearch = Elasticsearch
    return mod, calls


def _fake_google_modules(token, audiences):
    """google-auth の ID トークン取得部の決定的フェイク（fetch_id_token の audience を記録）。"""
    g = types.ModuleType("google")
    g_auth = types.ModuleType("google.auth")
    g_tr = types.ModuleType("google.auth.transport")
    g_req = types.ModuleType("google.auth.transport.requests")
    g_req.Request = lambda: "fake-request"
    g_oauth2 = types.ModuleType("google.oauth2")
    g_idt = types.ModuleType("google.oauth2.id_token")

    def fetch_id_token(request, audience):
        audiences.append(audience)
        return token

    g_idt.fetch_id_token = fetch_id_token
    # sys.modules 直挿しでは親の属性が張られないため、属性チェーンを手で張る。
    g.auth, g.oauth2 = g_auth, g_oauth2
    g_auth.transport = g_tr
    g_tr.requests = g_req
    g_oauth2.id_token = g_idt
    return {"google": g, "google.auth": g_auth, "google.auth.transport": g_tr,
            "google.auth.transport.requests": g_req,
            "google.oauth2": g_oauth2, "google.oauth2.id_token": g_idt}


_HIT = {"_score": 1.0, "_source": {"title": "デプロイ起因の 5xx", "kind": "runbook",
                                   "source": "deploy-regression.md", "text": "ロールバック"}}


def _swap_modules(fakes: dict):
    """sys.modules へフェイクを差し込み、復元用の元の値を返す。"""
    saved = {k: sys.modules.get(k) for k in fakes}
    sys.modules.update(fakes)
    return saved


def _restore_modules(saved: dict):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def test_es_idtoken_mode_passes_bearer_with_origin_audience():
    # HIKESHI_ES_AUTH=idtoken: API key 不要で Google ID トークンが bearer_auth に渡り、
    # audience は URL のオリジン。有効期限内はキャッシュ（fetch は増えない）。
    _clear_es_env()
    from hikeshi_agent import retriever_es

    audiences: list = []
    es_mod, calls = _fake_es_module()
    es_mod._search_impl = lambda c, kw: {"hits": {"hits": [_HIT]}}
    saved = _swap_modules({**_fake_google_modules("fake-idtoken", audiences),
                           "elasticsearch": es_mod})
    try:
        os.environ["HIKESHI_ES_URL"] = "https://es.example.invalid:9200/"
        os.environ["HIKESHI_ES_AUTH"] = "idtoken"
        retriever_es._IDTOKEN_CACHE.update(value=None, aud=None, exp=0.0)
        r = retriever_es.search("デプロイ 5xx", kind="runbook", k=1)
        assert r and r[0]["source"] == "deploy-regression.md"
        assert calls["init"][0].get("bearer_auth") == "fake-idtoken"
        assert "api_key" not in calls["init"][0]
        assert audiences == ["https://es.example.invalid:9200"]  # オリジン＝末尾パス/スラッシュ除去
        retriever_es.search("デプロイ 5xx", kind="runbook", k=1)
        assert len(audiences) == 1, "exp 前のトークンはキャッシュされるべき（fetch 再実行なし）"
    finally:
        _restore_modules(saved)
        retriever_es._IDTOKEN_CACHE.update(value=None, aud=None, exp=0.0)
        _clear_es_env()


def test_es_index_not_found_reindexes_from_kb_and_retries_once():
    # エフェメラル ES の自己修復: index_not_found → kb/*.md 正本から reindex → 1回だけ再試行。
    _clear_es_env()
    from hikeshi_agent import retriever_es

    es_mod, calls = _fake_es_module()

    def impl(c, kw):
        if c["bulk"] == 0:  # reindex（bulk）前は index 無し
            raise RuntimeError("index_not_found_exception: no such index [hikeshi-kb]")
        return {"hits": {"hits": [_HIT]}}

    es_mod._search_impl = impl
    saved = _swap_modules({"elasticsearch": es_mod})
    try:
        os.environ["HIKESHI_ES_URL"] = "https://example.invalid:9200"
        os.environ["HIKESHI_ES_API_KEY"] = "dummy"
        r = retriever_es.search("デプロイ 5xx", kind="runbook", k=1)
        assert r and r[0]["source"] == "deploy-regression.md"
        assert calls["bulk"] == 1, "reindex は1回だけ走るべき"
        assert calls["search"] == 2, "失敗1回＋再試行1回のみのはず"
    finally:
        _restore_modules(saved)
        _clear_es_env()


def test_es_index_not_found_after_reindex_propagates_no_loop():
    # reindex 後も index_not_found が続く場合は例外を伝播（無限ループしない・失敗を隠さない）。
    _clear_es_env()
    from hikeshi_agent import retriever_es

    es_mod, calls = _fake_es_module()

    def impl(c, kw):
        raise RuntimeError("index_not_found_exception: no such index [hikeshi-kb]")

    es_mod._search_impl = impl
    saved = _swap_modules({"elasticsearch": es_mod})
    try:
        os.environ["HIKESHI_ES_URL"] = "https://example.invalid:9200"
        os.environ["HIKESHI_ES_API_KEY"] = "dummy"
        try:
            retriever_es.list_docs()
            raise AssertionError("index_not_found は再試行1回で伝播するべき")
        except RuntimeError as e:
            assert "index_not_found" in str(e)
        assert calls["search"] == 2 and calls["bulk"] == 1
    finally:
        _restore_modules(saved)
        _clear_es_env()


def test_es_env_set_without_lib_raises_clear_runtime_error():
    # env 設定済みだが lib 無し → 黙ってローカルへフォールバックせず、明確な日本語 RuntimeError。
    saved = {k: os.environ.get(k) for k in ("HIKESHI_ES_URL", "HIKESHI_ES_API_KEY")}
    blocked = "elasticsearch" not in sys.modules
    try:
        os.environ["HIKESHI_ES_URL"] = "https://example.invalid:9200"
        os.environ["HIKESHI_ES_API_KEY"] = "dummy"
        if blocked:
            # import を確実に失敗させる（lib 有無に依らず決定的）
            sys.modules["elasticsearch"] = None
        try:
            search("デプロイ 5xx", kind="runbook", k=1)
            raise AssertionError("RuntimeError が投げられるべき")
        except RuntimeError as e:
            assert "elasticsearch" in str(e) and "フォールバックしません" in str(e)
    finally:
        if blocked:
            sys.modules.pop("elasticsearch", None)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    test_kb_loads()
    test_retrieves_right_runbook_per_symptom()
    test_kind_filter()
    test_deterministic()
    test_code_knowledge()
    test_reindex()
    test_es_env_unset_uses_local_path_unchanged()
    test_retriever_es_importable_without_elasticsearch()
    test_es_env_set_without_lib_raises_clear_runtime_error()
    test_es_idtoken_mode_passes_bearer_with_origin_audience()
    test_es_index_not_found_reindexes_from_kb_and_retries_once()
    test_es_index_not_found_after_reindex_propagates_no_loop()
    print("retriever tests: OK")
