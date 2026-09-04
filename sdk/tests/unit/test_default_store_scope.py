from __future__ import annotations

import asyncio
from pathlib import Path

from mcp_pal import MCPTestKit
from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal._default_store import (
    install_default_store_factory,
    make_default_store,
    restore_default_store_factory,
)
from mcp_pal.storage import SQLiteExecutionStore


def test_nested_default_store_scopes_restore_and_stale_tokens_are_safe() -> None:
    outer = object()
    inner = object()
    first = install_default_store_factory(lambda: outer)
    second = install_default_store_factory(lambda: inner)
    try:
        assert make_default_store() is inner
        restore_default_store_factory(first)
        assert make_default_store() is inner
        restore_default_store_factory(second)
        assert make_default_store() is outer
    finally:
        restore_default_store_factory(first)
    assert make_default_store() is None


def test_sync_scoped_store_explicit_precedence_and_owned_close(tmp_path: Path) -> None:
    path = (tmp_path / "scoped.sqlite").resolve()
    created: list[SQLiteExecutionStore] = []

    def factory() -> SQLiteExecutionStore:
        store = SQLiteExecutionStore(path)
        created.append(store)
        return store

    token = install_default_store_factory(factory)
    try:
        kit = MCPTestKit()
        assert kit.store is created[0]
        calls = 0
        original = created[0].close

        def close() -> None:
            nonlocal calls
            calls += 1
            original()

        created[0].close = close  # type: ignore[method-assign]
        kit.close()
        kit.close()
        assert calls == 1
        explicit = SQLiteExecutionStore((tmp_path / "explicit.sqlite").resolve())
        selected = MCPTestKit(store=explicit)
        assert selected.store is explicit
        selected.close()
    finally:
        restore_default_store_factory(token)
    assert MCPTestKit().store is None


def test_async_scoped_store_explicit_precedence_and_owned_close(tmp_path: Path) -> None:
    path = (tmp_path / "async-scoped.sqlite").resolve()
    created: list[SQLiteExecutionStore] = []

    def factory() -> SQLiteExecutionStore:
        store = SQLiteExecutionStore(path)
        created.append(store)
        return store

    token = install_default_store_factory(factory)
    try:
        async def exercise() -> None:
            kit = AsyncMCPTestKit()
            assert kit.store is created[0]
            calls = 0
            original = created[0].close

            def close() -> None:
                nonlocal calls
                calls += 1
                original()

            created[0].close = close  # type: ignore[method-assign]
            await kit.aclose()
            await kit.aclose()
            assert calls == 1
            explicit = SQLiteExecutionStore((tmp_path / "async-explicit.sqlite").resolve())
            selected = AsyncMCPTestKit(store=explicit)
            assert selected.store is explicit
            await selected.aclose()

        asyncio.run(exercise())
    finally:
        restore_default_store_factory(token)
