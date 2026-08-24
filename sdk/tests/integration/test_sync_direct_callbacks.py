"""Callback parity contracts for the synchronous direct facade."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import pytest
from mcp import types
from mcp.server.lowlevel import Server

from mcp_pal import MCPTestKit
from mcp_pal.async_api import (
    AsyncMCPTestKit,
    InputRequiredResult as AsyncInputRequiredResult,
    _adapt_callback as _async_adapt_callback,
)
from mcp_pal.errors import ProtocolError
from mcp_pal.sync_api import InputRequiredResult, _adapt_callback as _sync_adapt_callback
from mcp_pal.types import InProcessServer


pytestmark = pytest.mark.filterwarnings("ignore::mcp.shared.exceptions.MCPDeprecationWarning")


def _callback_server(
    *, include_all_callbacks: bool = True, include_sampling: bool = True, include_logging: bool = False
) -> Server:
    async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[types.Tool(name="callback_tool", input_schema={"type": "object"})]
        )

    async def call_tool(context: Any, _params: object) -> types.CallToolResult:
        session = context.session
        if include_sampling:
            await session.create_message(
                [types.SamplingMessage(role="user", content=types.TextContent(text="callback prompt"))],
                max_tokens=5,
            )
        if include_all_callbacks:
            await session.elicit_form(
                "confirm callback",
                {"type": "object", "properties": {"ok": {"type": "boolean"}}},
            )
            await session.list_roots()
            await session.send_log_message("info", "callback log")
            await session.report_progress(0.5, 1.0, "callback progress")
            await asyncio.sleep(0.05)
        elif include_logging:
            await session.send_log_message("info", "callback log")
            await asyncio.sleep(0.05)
        return types.CallToolResult(content=[types.TextContent(text="callback complete")])

    return Server("sync-callback-fixture", on_list_tools=list_tools, on_call_tool=call_tool)


def test_plain_sync_callbacks_run_on_portal_thread_and_return_typed_values() -> None:
    caller_thread = threading.get_ident()
    callback_threads: list[int] = []
    seen: dict[str, list[object]] = {
        "sampling": [],
        "elicitation": [],
        "roots": [],
        "logging": [],
        "messages": [],
    }

    def sampling(context: object, params: object) -> types.CreateMessageResult:
        callback_threads.append(threading.get_ident())
        seen["sampling"].append(params)
        return types.CreateMessageResult(
            role="assistant",
            content=types.TextContent(text="sampled"),
            model="sync-fixture",
            stop_reason="endTurn",
        )

    def elicitation(context: object, params: object) -> types.ElicitResult:
        callback_threads.append(threading.get_ident())
        seen["elicitation"].append(params)
        return types.ElicitResult(action="accept", content={"ok": True})

    def roots(context: object) -> types.ListRootsResult:
        callback_threads.append(threading.get_ident())
        seen["roots"].append(context)
        return types.ListRootsResult(
            roots=[types.Root.model_validate({"uri": "file:///tmp/sync-callback", "name": "fixture"})]
        )

    def logging(params: object) -> None:
        callback_threads.append(threading.get_ident())
        seen["logging"].append(params)

    def message_handler(message: object) -> None:
        callback_threads.append(threading.get_ident())
        seen["messages"].append(message)

    with MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        with kit.direct(
            InProcessServer(name="sync-callback", factory=_callback_server),
            sampling_callback=sampling,
            elicitation_callback=elicitation,
            list_roots_callback=roots,
            logging_callback=logging,
            message_handler=message_handler,
        ) as client:
            result = client.call_tool("callback_tool", {})
            assert not isinstance(result, InputRequiredResult)
            assert result.content[0]["text"] == "callback complete"

    assert len(seen["sampling"]) == 1
    assert len(seen["elicitation"]) == 1
    assert len(seen["roots"]) == 1
    assert seen["logging"]
    assert seen["messages"]
    assert callback_threads
    assert all(thread_id != caller_thread for thread_id in callback_threads)
    assert len(set(callback_threads)) == 1


def test_sync_callback_exception_is_typed_and_value_free(caplog: pytest.LogCaptureFixture) -> None:
    secret = "SYNC_CALLBACK_SECRET"

    def sampling(context: object, params: object) -> types.CreateMessageResult:
        raise RuntimeError(secret)

    with MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        with kit.direct(
            InProcessServer(name="sync-callback", factory=_callback_server),
            sampling_callback=sampling,
        ) as client:
            with pytest.raises(ProtocolError) as caught:
                client.call_tool("callback_tool", {})

    assert secret not in str(caught.value)
    assert secret not in repr(caught.value.details)
    assert secret not in caplog.text


def test_sync_notification_callback_failure_does_not_log_secret(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "SYNC_NOTIFICATION_CALLBACK_SECRET"
    called = threading.Event()

    def logging_callback(params: object) -> None:
        called.set()
        raise RuntimeError(secret)

    with caplog.at_level(logging.ERROR):
        with MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
            with kit.direct(
                InProcessServer(
                    name="sync-callback",
                    factory=lambda: _callback_server(
                        include_all_callbacks=False, include_sampling=False, include_logging=True
                    ),
                ),
                logging_callback=logging_callback,
            ) as client:
                result = client.call_tool("callback_tool", {})
                assert not isinstance(result, InputRequiredResult)
                assert result.content[0]["text"] == "callback complete"
                assert called.wait(timeout=1)

    captured = capsys.readouterr()
    assert secret not in caplog.text
    assert secret not in captured.out + captured.err


@pytest.mark.parametrize("adapt", [_sync_adapt_callback, _async_adapt_callback])
def test_callback_adapters_preserve_cancellation_and_system_exit(adapt: Any) -> None:
    secret = "CALLBACK_ADAPTER_SECRET"

    def failed(*args: object, **kwargs: object) -> None:
        raise RuntimeError(secret)

    async def cancelled(*args: object, **kwargs: object) -> None:
        raise asyncio.CancelledError

    async def exited(*args: object, **kwargs: object) -> None:
        raise SystemExit("callback process control")

    async def run() -> None:
        with pytest.raises(RuntimeError) as caught:
            await adapt(failed)()
        assert str(caught.value) == "MCP callback failed"
        assert caught.value.__context__ is None
        with pytest.raises(asyncio.CancelledError):
            await adapt(cancelled)()
        with pytest.raises(SystemExit):
            await adapt(exited)()

    asyncio.run(run())


@pytest.mark.asyncio
async def test_async_notification_callback_failure_does_not_log_secret(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "ASYNC_NOTIFICATION_CALLBACK_SECRET"
    called = asyncio.Event()

    async def logging_callback(params: object) -> None:
        called.set()
        raise RuntimeError(secret)

    with caplog.at_level(logging.ERROR):
        kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
        async with kit:
            async with kit.direct(
                InProcessServer(
                    name="async-callback",
                    factory=lambda: _callback_server(
                        include_all_callbacks=False, include_sampling=False, include_logging=True
                    ),
                ),
                logging_callback=logging_callback,
            ) as client:
                result = await client.call_tool("callback_tool", {})
                assert not isinstance(result, AsyncInputRequiredResult)
                assert result.content[0]["text"] == "callback complete"
                await asyncio.wait_for(called.wait(), timeout=1)

    captured = capsys.readouterr()
    assert secret not in caplog.text
    assert secret not in captured.out + captured.err


@pytest.mark.asyncio
async def test_async_callbacks_remain_explicitly_supported_on_async_twin() -> None:
    seen: list[int] = []

    async def sampling(context: object, params: object) -> types.CreateMessageResult:
        seen.append(threading.get_ident())
        return types.CreateMessageResult(
            role="assistant",
            content=types.TextContent(text="async sampled"),
            model="async-fixture",
            stop_reason="endTurn",
        )

    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    async with kit:
        async with kit.direct(
            InProcessServer(
                name="async-callback",
                factory=lambda: _callback_server(include_all_callbacks=False),
            ),
            sampling_callback=sampling,
        ) as client:
            result = await client.call_tool("callback_tool", {})
            assert not isinstance(result, AsyncInputRequiredResult)
            assert result.content[0]["text"] == "callback complete"
    assert seen
