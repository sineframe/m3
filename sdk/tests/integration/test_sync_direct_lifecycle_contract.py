"""Black-box Phase 6 contracts for the synchronous portal/proxy."""

from __future__ import annotations

import asyncio
import inspect
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import types
from mcp.server.lowlevel import Server

from mcp_pal import MCPTestKit
from mcp_pal.errors import OperationCancelled, OperationTimeout, UnsupportedFeature
from mcp_pal.types import InProcessServer, ProtocolConstraint, StdioServer

pytestmark = pytest.mark.process_lifecycle


_ROOT = Path(__file__).parents[2]
_STDIO_FIXTURE = Path(__file__).parents[1] / "fixtures" / "matrix_stdio_server.py"
_SLOW_STARTED = threading.Event()
_SLOW_RELEASE = threading.Event()


def _in_process_binding() -> InProcessServer:
    async def list_tools(_context: Any, params: Any) -> types.ListToolsResult:
        cursor = getattr(params, "cursor", None)
        tool = types.Tool(
            name="failure" if cursor else "echo",
            description="matrix fixture",
            input_schema={"type": "object"},
        )
        return types.ListToolsResult(
            tools=[tool], next_cursor=None if cursor else "page-2"
        )

    async def call_tool(
        _context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        if params.name == "failure":
            return types.CallToolResult(
                content=[types.TextContent(text="expected failure")], is_error=True
            )
        if params.name == "slow":
            _SLOW_STARTED.set()
            while not _SLOW_RELEASE.is_set():
                await asyncio.sleep(0.01)
        return types.CallToolResult(
            content=[
                types.TextContent(text=str((params.arguments or {}).get("text", "ok")))
            ],
        )

    def factory() -> Server:
        return Server("sync-matrix", on_list_tools=list_tools, on_call_tool=call_tool)

    return InProcessServer(name="sync-matrix", factory=factory)


def _stdio_binding() -> StdioServer:
    return StdioServer(
        name="sync-matrix",
        command=sys.executable,
        args=(str(_STDIO_FIXTURE),),
        cwd=str(_ROOT),
    )


@pytest.mark.parametrize("binding_factory", [_in_process_binding, _stdio_binding])
def test_sync_direct_local_contract_returns_only_sync_values(
    binding_factory: Any,
) -> None:
    """Ordinary pytest usage must not expose a coroutine or async session."""

    with MCPTestKit(env={}, cwd=str(_ROOT)) as kit:
        with cast(Any, kit.direct(binding_factory())) as client:
            assert not inspect.iscoroutine(client)
            initialized = client.initialization
            assert "matrix" in initialized.server_info["name"]
            first_page = client.list_tools()
            assert first_page.next_cursor == "page-2"
            tools = client.list_all_tools()
            assert {tool.name for tool in tools} == {"echo", "failure"}
            result = client.call_tool("echo", {"text": "sync"})
            assert not inspect.iscoroutine(result)
            assert result.is_error is False
            assert result.content[0]["text"] == "sync"


def test_sync_nested_and_repeated_kits_are_idempotent_and_reap_portals() -> None:
    baseline = {
        thread.ident for thread in threading.enumerate() if thread.ident is not None
    }
    for _ in range(3):
        with MCPTestKit(env={}, cwd=str(_ROOT)) as outer:
            with MCPTestKit(env={}, cwd=str(_ROOT)) as inner:
                with cast(Any, inner.direct(_in_process_binding())) as client:
                    assert (
                        client.call_tool("echo", {"text": "nested"}).content[0]["text"]
                        == "nested"
                    )
            inner.close()
            outer.close()
        outer.close()
    leaked = {
        thread.ident
        for thread in threading.enumerate()
        if thread.ident is not None and thread.ident not in baseline
    }
    assert leaked == set()


def test_sync_close_from_another_thread_is_safe_and_not_false_closed() -> None:
    kit = MCPTestKit(env={}, cwd=str(_ROOT))
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    def owner() -> None:
        try:
            with kit.direct(_in_process_binding()) as client:
                entered.set()
                release.wait(timeout=5)
                # A close from another thread must either complete cleanup or
                # produce the typed closed-state error, never an async object.
                try:
                    client.call_tool("echo", {"text": "after-close"})
                except Exception as error:
                    assert not inspect.iscoroutine(error)
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    thread = threading.Thread(target=owner, name="mcp-pal-sync-owner")
    thread.start()
    assert entered.wait(timeout=5)
    kit.close()
    release.set()
    assert finished.wait(timeout=5)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors == []
    kit.close()


def test_sync_timeout_and_exception_cleanup_do_not_escape_async_state() -> None:
    _SLOW_STARTED.clear()
    _SLOW_RELEASE.clear()
    with MCPTestKit(env={}, cwd=str(_ROOT)) as kit:
        with cast(Any, kit.direct(_in_process_binding(), timeout=0.05)) as client:
            with pytest.raises(OperationTimeout) as timeout_error:
                client.call_tool("slow", {}, timeout=0.01)
            assert not inspect.iscoroutine(timeout_error.value)
            # A per-operation timeout does not close the direct session.
            failure = client.call_tool("failure", {})
            assert failure.is_error is True
            assert client.ping() is not None
    _SLOW_RELEASE.set()


def test_twenty_concurrent_direct_portals_are_reaped() -> None:
    baseline = {
        thread.ident for thread in threading.enumerate() if thread.ident is not None
    }
    barrier = threading.Barrier(20)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            with MCPTestKit(env={}, cwd=str(_ROOT)) as kit:
                with cast(Any, kit.direct(_in_process_binding())) as client:
                    assert (
                        client.call_tool("echo", {"text": "parallel"}).content[0][
                            "text"
                        ]
                        == "parallel"
                    )
        except BaseException as error:
            errors.append(error)

    threads = [
        threading.Thread(target=worker, name=f"mcp-pal-sync-{index}")
        for index in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        active = {
            thread.ident for thread in threading.enumerate() if thread.ident is not None
        }
        if active <= baseline:
            break
        threading.Event().wait(0.01)
    assert {
        thread.ident for thread in threading.enumerate() if thread.ident is not None
    } <= baseline


def test_invalid_protocol_fails_before_startup_without_portal_leak() -> None:
    baseline = {
        thread.ident for thread in threading.enumerate() if thread.ident is not None
    }
    kit = MCPTestKit(env={}, cwd=str(_ROOT))
    try:
        with pytest.raises(UnsupportedFeature):
            with cast(
                Any,
                kit.direct(
                    _in_process_binding(),
                    protocol=ProtocolConstraint(revision="2024-11-05"),
                ),
            ):
                pass
    finally:
        kit.close()
    threading.Event().wait(0.05)
    assert {
        thread.ident for thread in threading.enumerate() if thread.ident is not None
    } <= baseline


def test_close_during_slow_call_cancels_and_cleans_up() -> None:
    _SLOW_STARTED.clear()
    _SLOW_RELEASE.clear()
    kit = MCPTestKit(env={}, cwd=str(_ROOT))
    operation_done = threading.Event()
    close_done = threading.Event()
    operation_errors: list[BaseException] = []
    close_errors: list[BaseException] = []

    def owner() -> None:
        try:
            with cast(Any, kit.direct(_in_process_binding())) as client:
                try:
                    client.call_tool("slow", {})
                except BaseException as error:
                    operation_errors.append(error)
        finally:
            operation_done.set()

    def close_kit() -> None:
        try:
            kit.close()
        except BaseException as error:
            close_errors.append(error)
        finally:
            close_done.set()

    owner_thread = threading.Thread(target=owner, name="mcp-pal-sync-slow-owner")
    owner_thread.start()
    assert _SLOW_STARTED.wait(timeout=5)
    closer_thread = threading.Thread(target=close_kit, name="mcp-pal-sync-closer")
    closer_thread.start()
    if not close_done.wait(timeout=2):
        # Avoid masking a portal deadlock with a hanging test; the assertions
        # below still fail and preserve the required cancellation signal.
        _SLOW_RELEASE.set()
    assert close_done.wait(timeout=5)
    _SLOW_RELEASE.set()
    assert operation_done.wait(timeout=5)
    owner_thread.join(timeout=5)
    closer_thread.join(timeout=5)
    assert not owner_thread.is_alive()
    assert not closer_thread.is_alive()
    assert close_errors == []
    assert len(operation_errors) == 1
    assert isinstance(operation_errors[0], OperationCancelled)


def test_concurrent_close_calls_are_idempotent_and_deadlock_free() -> None:
    kit = MCPTestKit(env={}, cwd=str(_ROOT))
    client = cast(Any, kit.direct(_in_process_binding()))
    client.__enter__()
    errors: list[BaseException] = []

    def close() -> None:
        try:
            kit.close()
        except BaseException as error:
            errors.append(error)

    threads = [
        threading.Thread(target=close, name=f"mcp-pal-close-{index}")
        for index in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    kit.close()
