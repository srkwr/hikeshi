"""Hikeshi ADK エージェント・パッケージ。

`adk run/web/eval` は __init__.py 経由で `agent` モジュール＋`root_agent` を解決する。
ここでは **PEP 562 の遅延 `__getattr__`** で必要になった時だけ解決する。こうすると
`retriever`/`live` のような純・標準ライブラリのサブモジュールを import しても
google.adk を引き込まない＝各モジュールが宣言する「オフラインで成立する」契約を保てる。
"""

from importlib import import_module


def __getattr__(name):  # adk が要求する時だけ agent/root_agent を遅延ロード（PEP 562）
    if name in ("agent", "root_agent"):
        agent = import_module(".agent", __name__)  # import 機構経由＝__getattr__ を再帰させない
        return agent if name == "agent" else agent.root_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["agent", "root_agent"]
