"""Phase 6 synchronous portal/proxy contracts."""

from __future__ import annotations

import inspect
import threading

import pytest
from mcp.server.lowlevel import Server
from mcp.types import CallToolResult as MCPCallToolResult, ListToolsResult

from mcp_pal.errors import OperationCancelled, UnsupportedFeature
from mcp_pal.sync_api import DirectClient, MCPTestKit
from mcp_pal.sync_api import _adapt_callback
from mcp_pal.types import InProcessServer


def _server() -> Server:
    async def list_tools(_context: object, _params: object) -> ListToolsResult:
        return ListToolsResult(tools=[])

    return Server("sync-direct-fixture", on_list_tools=list_tools)


def test_sync_direct_uses_typed_results_and_final_trace() -> None:
    kit = MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(InProcessServer(name="fixture", factory=_server))
    with client as entered:
        assert entered is client
        result = client.list_tools()
        assert result.tools == ()
        assert not inspect.isawaitable(result)
        assert client.initialization is not None
        assert client.trace is not None
        assert client.trace.completeness == "partial"
    assert client.final_trace is not None
    assert client.final_trace.events[-1].kind.value == "execution.finished"
    kit.close()
    kit.close()


def test_sync_direct_does_not_expose_async_client_and_matches_supported_surface() -> None:
    from mcp_pal.async_api import AsyncDirectClient

    expected = {
        name
        for name, member in inspect.getmembers(AsyncDirectClient)
        if callable(member) and not name.startswith("_") and name != "aclose"
    }
    actual = {
        name
        for name, member in inspect.getmembers(DirectClient)
        if callable(member) and not name.startswith("_")
    }
    assert expected <= actual

    kit = MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(InProcessServer(name="fixture", factory=_server))
    assert not isinstance(client, AsyncDirectClient)
    assert not hasattr(client, "_session")
    kit.close()


def test_nested_and_repeated_sync_kit_lifecycles_leave_no_portal_threads() -> None:
    baseline = {thread.ident for thread in threading.enumerate()}
    for _ in range(3):
        with MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
            with kit as nested:
                assert nested is kit
                with kit.direct(InProcessServer(name="fixture", factory=_server)) as client:
                    assert client.ping().raw is not None
    lingering = {
        thread.ident
        for thread in threading.enumerate()
        if thread.ident not in baseline and thread.is_alive()
    }
    assert not lingering


def test_sync_callback_mutation_matches_async_unsupported_contract() -> None:
    kit = MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(InProcessServer(name="fixture", factory=_server))
    with client:
        with pytest.raises(UnsupportedFeature, match="callbacks must be supplied"):
            client.register_callbacks(sampling_callback=lambda: None)
    kit.close()


def test_sync_callbacks_adapt_sync_and_async_callbacks() -> None:
    calls: list[tuple[object, ...]] = []

    def ordinary(*args: object) -> None:
        calls.append(args)

    adapted = _adapt_callback(ordinary)
    assert inspect.iscoroutinefunction(adapted)

    async def run() -> None:
        assert await adapted("event") is None

    import asyncio

    asyncio.run(run())
    assert calls == [("event",)]

    async def already_async(*args: object) -> object:
        return args

    adapted_async = _adapt_callback(already_async)
    assert inspect.iscoroutinefunction(adapted_async)
    assert asyncio.run(adapted_async("async-event")) == ("async-event",)


def test_concurrent_direct_creation_shares_one_portal_and_close_reaps_all() -> None:
    kit = MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    barrier = threading.Barrier(8)
    clients: list[DirectClient] = []
    failures: list[BaseException] = []
    lock = threading.Lock()

    def create() -> None:
        try:
            barrier.wait(timeout=2.0)
            client = kit.direct(InProcessServer(name="fixture", factory=_server))
            with lock:
                clients.append(client)
        except BaseException as exc:
            with lock:
                failures.append(exc)

    threads = [threading.Thread(target=create) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3.0)
    assert not failures
    assert len(clients) == 8
    assert len({id(client._portal) for client in clients}) == 1
    kit.close()
    assert all(client.final_trace is not None for client in clients)


def test_failed_new_portal_creation_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_pal.sync_api import _PortalRuntime

    def fail(*args: object, **kwargs: object) -> int:
        raise RuntimeError("portal startup fixture failure")

    monkeypatch.setattr(_PortalRuntime, "create_direct", fail)
    kit = MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    with pytest.raises(RuntimeError, match="portal startup fixture failure"):
        kit.direct(InProcessServer(name="fixture", factory=_server))
    assert kit._portal is None
    kit.close()


def test_close_during_inflight_sync_operation_maps_teardown_to_cancelled() -> None:
    started = threading.Event()

    def hanging_server() -> Server:
        async def call_tool(_context: object, _params: object) -> MCPCallToolResult:
            started.set()
            import asyncio

            await asyncio.sleep(60)
            return MCPCallToolResult(content=[])

        return Server("sync-hanging", on_call_tool=call_tool)

    kit = MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(InProcessServer(name="fixture", factory=hanging_server))
    client.__enter__()
    outcome: list[BaseException] = []

    def invoke() -> None:
        try:
            client.call_tool("hang", {})
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=invoke)
    worker.start()
    assert started.wait(2.0)
    client.close()
    worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], OperationCancelled)
    kit.close()
