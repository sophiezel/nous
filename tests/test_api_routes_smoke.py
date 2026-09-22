"""API 路由 smoke 测试 — 钉住 safe_query 的调用约定。

背景（真实事故）：11 个路由模块原本从 `nous.core.db` 导入 safe_query，
而它的签名是 `(query, params=())`；路由却按 `(db_path, sql)` 调用：
    safe_query(SCREENER_DB, "SELECT ...")
→ 实测 `OperationalError: near "reports": syntax error`，**整片接口取不到数据**。
正确实现一直存在：`nous.api.db.safe_query(db_path, sql, params=())`。
这组测试就是防止再次退化。
"""

from __future__ import annotations

import inspect
import importlib
import sqlite3

import pytest

ROUTE_MODULES = (
    "flow",
    "futures",
    "index_routes",
    "macro",
    "messages",
    "portfolio",
    "quant_routes",
    "reports",
    "risk",
    "sentiment",
    "theme",
)


def test_core_safe_query_is_query_first():
    """core.db 的签名是 (query, params)，因此不能拿它当 (db, sql) 用。"""
    from nous.core.db import safe_query as core_safe_query

    params = list(inspect.signature(core_safe_query).parameters)
    assert params[:2] == ["query", "params"]


def test_api_safe_query_is_db_first():
    from nous.api.db import safe_query as api_safe_query

    params = list(inspect.signature(api_safe_query).parameters)
    assert params[:3] == ["db_path", "sql", "params"]


@pytest.mark.parametrize("name", ROUTE_MODULES)
def test_routes_bind_safe_query_from_api_db(name):
    module = importlib.import_module(f"nous.api.routes.{name}")
    assert getattr(module, "safe_query").__module__ == "nous.api.db", (
        f"{name}.py 的 safe_query 又指回 core.db 了 —— 会整片报 OperationalError"
    )


@pytest.mark.parametrize("name", ROUTE_MODULES)
def test_routes_expose_absolute_db_paths(name):
    """api.db 的 DB 常量是绝对路径；core.db 的是相对名（传进去会开不出库）。"""
    module = importlib.import_module(f"nous.api.routes.{name}")
    for attr in ("SCREENER_DB", "REPORTS_DB"):
        value = getattr(module, attr, None)
        if value is not None:
            assert str(value).startswith("/"), f"{name}.{attr} 不是绝对路径: {value}"


# ── 端到端：真实调用一个路由函数 ───────────────────────────────────────
@pytest.fixture()
def fake_readonly_db(monkeypatch):
    """把 api.db 的连接工厂换成内存库，验证路由→safe_query→SQL 这条链路。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE theme_pool_stocks (theme TEXT, segment TEXT, symbol TEXT, name TEXT)")
    conn.execute(
        "INSERT INTO theme_pool_stocks VALUES ('光伏玻璃','玻璃','00968','信义光能')"
    )
    conn.commit()

    import nous.api.db as api_db

    monkeypatch.setattr(api_db, "get_readonly_db", lambda path: conn)
    yield conn
    conn.close()


def test_theme_stocks_route_returns_rows(fake_readonly_db):
    """修复后：路由能真的取到数据（修复前这里会抛 OperationalError/HTTPException）。"""
    from nous.api.routes import theme

    rows = theme.theme_stocks()
    assert isinstance(rows, list) and rows
    assert rows[0]["theme"] == "光伏玻璃"
    assert [s["symbol"] for s in rows[0]["stocks"]] == ["00968"]


def test_route_query_failure_becomes_http_503(monkeypatch):
    """api.db 的 safe_query 会把 DB 错误转成 HTTPException(503)，而不是裸异常。"""
    import nous.api.db as api_db
    from fastapi import HTTPException

    def broken(path):
        raise sqlite3.OperationalError("no such table")

    monkeypatch.setattr(api_db, "get_readonly_db", broken)
    with pytest.raises(HTTPException) as exc:
        api_db.safe_query("screener.db", "SELECT 1")
    assert exc.value.status_code == 503
