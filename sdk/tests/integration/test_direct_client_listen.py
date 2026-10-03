"""Direct-client ``subscriptions/listen`` over the real MCP SDK 2.x transport."""

from __future__ import annotations

import threading
import time
from typing import Any

import anyio
import pytest
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.mcpserver import Context, MCPServer

from m3 import (
    InProcessServer,
    MCPTestKit,
    OperationCancelled,
    OperationTimeout,
    ProtocolError,
    TransportError,
    UnsupportedFeature,
)
from m3.async_api import AsyncMCPTestKit
from m3.types import TraceResult

TOOLS_CHANGED = "notifications/tools/list_changed"
MODERN = "2026-07-28"


def _server() -> InProcessServer:
    def build() -> object:
        app = MCPServer("listen-fixture")

        @app.resource("res://doc")
        def doc() -> str:
            return "doc"

        @app.tool()
        async def unlock(ctx: Context) -> str:
            # Both channels: the handshake era delivers the connection
            # notification, 2026-07-28 delivers only the listen-bus event.
            await ctx.session.send_tool_list_changed()
            await ctx.notify_tools_changed()
            return "ok"

        @app.tool()
        async def touch(ctx: Context) -> str:
            await ctx.notify_resource_updated("res://doc")
            return "ok"

        return app._lowlevel_server

    return InProcessServer(name="listen-fixture", factory=build)


def _notification_methods(trace: TraceResult | None) -> list[str]:
    assert trace is not None
    return [
        event.payload["method"]
        for event in trace.events
        if event.kind.value == "mcp.notification"
    ]


def test_modern_list_changed_reaches_the_listener_and_the_trace() -> None:
    with MCPTestKit(env={}) as kit:
        with kit.direct(_server(), protocol=MODERN) as client:
            with client.listen(tools_list_changed=True) as subscription:
                client.call_tool("unlock")
                event = subscription.next(timeout=5)
            assert subscription.honored is not None
            assert subscription.honored.tools_list_changed is True

    assert event is not None and event.method == TOOLS_CHANGED
    assert TOOLS_CHANGED in _notification_methods(client.final_trace)


def test_modern_resource_updated_carries_the_subscribed_uri() -> None:
    with MCPTestKit(env={}) as kit:
        with kit.direct(_server(), protocol=MODERN) as client:
            with client.listen(resource_subscriptions=["res://doc"]) as subscription:
                client.call_tool("touch")
                event = subscription.next(timeout=5)

    assert event is not None
    assert (event.method, event.uri) == ("notifications/resources/updated", "res://doc")


async def test_async_listen_iterates_events() -> None:
    async with AsyncMCPTestKit(env={}) as kit:
        async with kit.direct(_server(), protocol=MODERN) as client:
            async with client.listen(tools_list_changed=True) as subscription:
                await client.call_tool("unlock")
                event = await anext(aiter(subscription))

    assert event.method == TOOLS_CHANGED


def test_handshake_era_rejects_listen_and_keeps_connection_notifications() -> None:
    with MCPTestKit(env={}) as kit:
        with kit.direct(_server()) as client:
            with pytest.raises(UnsupportedFeature):
                client.listen(tools_list_changed=True).__enter__()
            client.call_tool("unlock")

    assert TOOLS_CHANGED in _notification_methods(client.final_trace)


def test_closing_the_client_ends_an_open_subscription() -> None:
    with MCPTestKit(env={}) as kit:
        with kit.direct(_server(), protocol=MODERN) as client:
            subscription = client.listen(tools_list_changed=True).__enter__()
            client.call_tool("unlock")
            assert subscription.next(timeout=5) is not None
        subscription.close()

    assert TOOLS_CHANGED in _notification_methods(client.final_trace)


def _listen_server(handler: Any) -> InProcessServer:
    return InProcessServer(
        name="listen-handler",
        factory=lambda: Server("listen-handler", on_subscriptions_listen=handler),
    )


async def _reject(_context: Any, _params: Any) -> None:
    raise RuntimeError("LISTEN_REJECTED_CANARY")


def _outcome(trace: TraceResult | None) -> str:
    assert trace is not None
    return str(trace.events[-1].payload["outcome"])


async def test_rejected_listen_surfaces_the_original_failure_once() -> None:
    async with AsyncMCPTestKit(env={}) as kit:
        client = kit.direct(
            _listen_server(_reject), protocol=MODERN, raise_server_exceptions=True
        )
        async with client:
            with pytest.raises(RuntimeError, match="LISTEN_REJECTED_CANARY"):
                async with client.listen(tools_list_changed=True):
                    pass

    assert _outcome(client.final_trace) == "failed"


def test_caught_listen_rejection_still_fails_the_trace() -> None:
    # No listen handler: the server answers with a JSON-RPC error.
    server = InProcessServer(name="no-listen", factory=lambda: Server("no-listen"))
    with MCPTestKit(env={}) as kit:
        with kit.direct(server, protocol=MODERN) as client:
            with pytest.raises(ProtocolError):
                client.listen(tools_list_changed=True).__enter__()

    assert _outcome(client.final_trace) == "failed"


def test_stream_lost_before_acknowledgement_is_a_transport_error() -> None:
    async def cancel_before_ack(context: Any, _params: Any) -> None:
        await context.session.send_notification(
            types.CancelledNotification(
                params=types.CancelledNotificationParams(request_id=context.request_id)
            )
        )
        await anyio.sleep_forever()

    with MCPTestKit(env={}) as kit:
        with kit.direct(_listen_server(cancel_before_ack), protocol=MODERN) as client:
            with pytest.raises(TransportError):
                client.listen(tools_list_changed=True).__enter__()


def test_closing_the_client_cancels_a_blocked_next() -> None:
    outcome: dict[str, object] = {}
    with MCPTestKit(env={}) as kit:
        with kit.direct(_server(), protocol=MODERN) as client:
            subscription = client.listen(tools_list_changed=True).__enter__()

            def read() -> None:
                try:
                    outcome["value"] = subscription.next(timeout=30)
                except BaseException as exc:
                    outcome["error"] = exc

            reader = threading.Thread(target=read)
            reader.start()
            time.sleep(0.5)  # let the reader block on the stream
        reader.join(timeout=10)

    assert not reader.is_alive()
    assert isinstance(outcome.get("error"), OperationCancelled)


def test_an_empty_wait_times_out_without_failing_the_trace() -> None:
    with MCPTestKit(env={}) as kit:
        with kit.direct(_server(), protocol=MODERN) as client:
            with client.listen(tools_list_changed=True) as subscription:
                with pytest.raises(OperationTimeout):
                    subscription.next(timeout=0.2)

    assert _outcome(client.final_trace) == "completed"
