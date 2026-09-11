"""Lifecycle contracts for the async direct-client facade."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import pytest
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.server import (
    InitializationOptions,
    NotificationOptions,
    ReadStream,
    SessionMessage,
    WriteStream,
)
from mcp.types import ListToolsResult

from mcp_pal.async_api import AsyncMCPTestKit, InputRequiredResult
from mcp_pal.errors import KitClosed, ProtocolError, UnsupportedFeature
from mcp_pal.transport.local import TransportProcessError, TransportStartupError
from mcp_pal.types import (
    HTTPServer,
    InProcessServer,
    ProtocolConstraint,
    RevisionSelection,
    ServerBinding,
    ServerProfileId,
    ServerProfileRef,
    StdioServer,
    TransportKind,
    TrustLevel,
)


def _server() -> Server:
    async def list_tools(_context: object, _params: object) -> ListToolsResult:
        return ListToolsResult(tools=[])

    return Server("async-direct-fixture", on_list_tools=list_tools)


def _callback_server() -> Server:
    async def list_tools(_context: object, _params: object) -> ListToolsResult:
        return ListToolsResult(
            tools=[
                types.Tool(
                    name="callback_tool",
                    description="exercise server-to-client callbacks",
                    input_schema={"type": "object", "properties": {}},
                )
            ]
        )

    async def call_tool(context: object, _params: object) -> types.CallToolResult:
        session: Any = context.session
        await session.create_message(
            [
                types.SamplingMessage(
                    role="user", content=types.TextContent(text="callback prompt")
                )
            ],
            max_tokens=5,
        )
        await session.elicit_form(
            "confirm callback",
            {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        )
        await session.list_roots()
        await session.send_log_message("info", "server callback log")
        await session.report_progress(0.5, 1.0, "halfway")
        return types.CallToolResult(
            content=[types.TextContent(text="callback complete")]
        )

    return Server(
        "callback-direct-fixture", on_list_tools=list_tools, on_call_tool=call_tool
    )


def test_input_required_result_is_public_async_surface() -> None:
    result = InputRequiredResult(request_state="state-1")
    assert result.result_type == "input_required"
    assert result.request_state == "state-1"


def _failing_server() -> Server:
    async def list_tools(_context: object, _params: object) -> ListToolsResult:
        raise RuntimeError("PUBLIC_IN_PROCESS_CANARY")

    return Server("async-direct-failure-fixture", on_list_tools=list_tools)


class _InitializationFailureServer(Server):
    def create_initialization_options(
        self,
        notification_options: NotificationOptions | None = None,
        experimental_capabilities: dict[str, dict[str, Any]] | None = None,
        extensions: dict[str, dict[str, Any]] | None = None,
    ) -> InitializationOptions:
        raise RuntimeError("INIT_IN_PROCESS_CANARY")


def _initialization_failure_server() -> Server:
    return _InitializationFailureServer("async-direct-init-failure-fixture")


def _call_failure_server() -> Server:
    async def call_tool(_context: object, _params: object) -> types.CallToolResult:
        raise RuntimeError("CALL_IN_PROCESS_CANARY")

    return Server("async-direct-call-failure-fixture", on_call_tool=call_tool)


class _ShutdownFailureServer(Server):
    async def run(
        self,
        read_stream: ReadStream[SessionMessage | Exception],
        write_stream: WriteStream[SessionMessage],
        initialization_options: InitializationOptions,
        raise_exceptions: bool = False,
    ) -> None:
        await super().run(
            read_stream, write_stream, initialization_options, raise_exceptions
        )
        raise RuntimeError("SHUTDOWN_IN_PROCESS_CANARY")


def _shutdown_failure_server() -> Server:
    return _ShutdownFailureServer("async-direct-shutdown-failure-fixture")


async def _http_fixture(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        headers = await reader.readuntil(b"\r\n\r\n")
        method, _path, _version = headers.split(b"\r\n", 1)[0].split(b" ", 2)
        content_length = 0
        for line in headers.split(b"\r\n")[1:]:
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":", 1)[1].strip())
        body = await reader.readexactly(content_length) if content_length else b"{}"
        request = json.loads(body)
        if method == b"DELETE":
            payload = b"{}"
        elif request.get("method") == "initialize":
            payload = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {
                            "extensions": {
                                "fixture.server.extension": {"version": "1"}
                            },
                        },
                        "serverInfo": {"name": "http-fixture", "version": "1"},
                    },
                }
            ).encode()
        else:
            payload = b"{}"
        status = (
            b"200 OK"
            if method == b"DELETE" or request.get("method") == "initialize"
            else b"202 Accepted"
        )
        writer.write(
            b"HTTP/1.1 "
            + status
            + b"\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(payload)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + payload
        )
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_kit_direct_owns_in_process_client_and_closes_it() -> None:
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(InProcessServer(name="fixture", factory=_server))
    async with client as entered:
        assert entered is client
        assert client.initialization is not None
        assert client.initialization.server_info["name"] == "async-direct-fixture"
        assert (
            client.transport_evidence.protocol_version
            == client.initialization.protocol_version
        )
        assert client.transport_evidence.server_name == "async-direct-fixture"
        assert (await client.list_tools()).tools == ()
        assert client.trace is not None
        assert client.trace.execution_id == client.trace.events[0].execution_id
        assert any(event.kind.value == "mcp.request" for event in client.trace.events)
        assert any(event.kind.value == "mcp.response" for event in client.trace.events)
    assert client.final_trace is not None
    assert client.final_trace.completeness == "partial"
    assert "capture_incomplete" in client.final_trace.limitations
    with pytest.raises(RuntimeError, match="direct client must be entered"):
        await client.list_tools()
    await kit.aclose()


@pytest.mark.asyncio
async def test_kit_direct_exercises_official_server_callbacks_and_initialization_extensions() -> (
    None
):
    callback_events: dict[str, list[object]] = {
        "sampling": [],
        "elicitation": [],
        "roots": [],
        "logging": [],
        "messages": [],
        "progress": [],
    }

    async def sampling(*args: object, **kwargs: object) -> types.CreateMessageResult:
        callback_events["sampling"].append(args)
        return types.CreateMessageResult(
            role="assistant",
            content=types.TextContent(text="sampled response"),
            model="fixture-model",
            stop_reason="endTurn",
        )

    async def elicitation(*args: object, **kwargs: object) -> types.ElicitResult:
        callback_events["elicitation"].append(args)
        return types.ElicitResult(action="accept", content={"ok": True})

    async def roots(*args: object, **kwargs: object) -> types.ListRootsResult:
        callback_events["roots"].append(args)
        return types.ListRootsResult(
            roots=[
                types.Root.model_validate(
                    {"uri": "file:///tmp/mcp-pal", "name": "fixture"}
                )
            ]
        )

    async def logging(params: object) -> None:
        callback_events["logging"].append(params)

    async def message_handler(message: object) -> None:
        callback_events["messages"].append(message)

    async def progress(
        progress: float, total: float | None, message: str | None
    ) -> None:
        callback_events["progress"].append((progress, total, message))

    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    try:
        async with kit.direct(
            InProcessServer(name="callback-fixture", factory=_callback_server),
            sampling_callback=sampling,
            elicitation_callback=elicitation,
            list_roots_callback=roots,
            logging_callback=logging,
            message_handler=message_handler,
            extensions={"fixture.client.extension": {"version": "1"}},
        ) as client:
            initialization = client.initialization
            assert initialization is not None
            assert initialization.server_info["name"] == "callback-direct-fixture"
            assert initialization.raw is not None
            assert (
                client.transport_evidence.protocol_version
                == initialization.protocol_version
            )

            tools = await client.list_tools()
            assert [tool.name for tool in tools.tools] == ["callback_tool"]
            result = await client.call_tool(
                "callback_tool", {}, progress_callback=progress
            )
            assert not isinstance(result, InputRequiredResult)
            assert result.content[0]["text"] == "callback complete"

        assert len(callback_events["sampling"]) == 1
        assert len(callback_events["elicitation"]) == 1
        assert len(callback_events["roots"]) == 1
        assert callback_events["logging"]
        assert callback_events["progress"] == [(0.5, 1.0, "halfway")]
        assert callback_events["messages"]
    finally:
        await kit.aclose()


@pytest.mark.asyncio
async def test_kit_direct_uses_remote_streamable_http_and_closes_the_server_connection() -> (
    None
):
    server_socket = await asyncio.start_server(_http_fixture, "127.0.0.1", 0)
    port = server_socket.sockets[0].getsockname()[1]
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    try:

        async def sampling(*args: object, **kwargs: object) -> object:
            return None

        server = HTTPServer(
            name="remote-fixture",
            url=f"http://127.0.0.1:{port}/mcp",
            trust=TrustLevel.TRUSTED_PRIVATE,
        )
        async with kit.direct(
            server,
            sampling_callback=sampling,
            extensions={"fixture.extension": {}},
        ) as client:
            assert client.initialization is not None
            assert client.initialization.server_info["name"] == "http-fixture"
            assert client.initialization.capabilities["extensions"] == {
                "fixture.server.extension": {"version": "1"}
            }
            assert client.transport_evidence.state == "initialized"
            assert client.transport_evidence.extensions == ("fixture.server.extension",)
            session = client._session._session
            assert callable(session._sampling_callback)
            assert session._extensions == {"fixture.extension": {}}
        assert client.transport_evidence.state == "closed"
    finally:
        await kit.aclose()
        server_socket.close()
        await server_socket.wait_closed()


@pytest.mark.asyncio
async def test_kit_close_closes_active_direct_clients() -> None:
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(InProcessServer(name="fixture", factory=_server))
    await client.__aenter__()
    await kit.aclose()
    with pytest.raises(RuntimeError, match="direct client must be entered"):
        await client.list_tools()


@pytest.mark.asyncio
async def test_kit_close_from_another_task_reaps_in_process_owner() -> None:
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(InProcessServer(name="fixture", factory=_server))
    await client.__aenter__()
    await asyncio.create_task(kit.aclose())
    assert client.transport_evidence is not None
    assert client.transport_evidence.state == "closed"
    with pytest.raises(RuntimeError, match="direct client must be entered"):
        await client.list_tools()


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_kit_close_from_another_task_reaps_stdio_owner() -> None:
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(
        StdioServer(
            name="echo",
            command=sys.executable,
            args=("-m", "mcp_pal.fixtures.echo_server"),
        )
    )
    await client.__aenter__()
    await asyncio.create_task(kit.aclose())
    assert client.transport_evidence is not None
    assert client.transport_evidence.state == "closed"


@pytest.mark.asyncio
async def test_cancelled_public_enter_cancels_owner_startup_without_leaking(
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_factory() -> Server:
        started.set()
        await release.wait()
        return _server()

    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(InProcessServer(name="fixture", factory=delayed_factory))
    entering = asyncio.create_task(client.__aenter__())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    entering.cancel()
    with pytest.raises(asyncio.CancelledError):
        await entering
    await kit.aclose()
    lifecycle = client._lifecycle
    assert lifecycle is not None
    assert lifecycle._task.done()
    # Drain the future callback locally so this regression does not rely on a
    # later test's caplog assertion to reveal an abandoned exception.
    await asyncio.sleep(0)
    assert "Future exception was never retrieved" not in caplog.text


@pytest.mark.asyncio
async def test_public_direct_forwards_timeout_server_mode_and_session_options() -> None:
    async def sampling(*args: object, **kwargs: object) -> object:
        return None

    async def elicitation(*args: object, **kwargs: object) -> object:
        return None

    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(
        InProcessServer(name="fixture", factory=_server),
        timeout=0.25,
        raise_server_exceptions=False,
        sampling_callback=sampling,
        elicitation_callback=elicitation,
        extensions={"fixture.extension": {}},
    )
    async with client:
        assert client.timeout == 0.25
        session = client._session._session
        assert session._session_read_timeout_seconds == 0.25
        assert callable(session._sampling_callback)
        assert callable(session._elicitation_callback)
        assert session._extensions == {"fixture.extension": {}}
        assert client._connection._raise_server_exceptions is False
    await kit.aclose()


@pytest.mark.asyncio
async def test_public_direct_surfaces_original_in_process_failure_when_enabled() -> (
    None
):
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(
        InProcessServer(name="failing", factory=_failing_server),
        raise_server_exceptions=True,
    )
    async with client:
        with pytest.raises(RuntimeError, match="PUBLIC_IN_PROCESS_CANARY"):
            await client.list_tools()
    await kit.aclose()


@pytest.mark.asyncio
async def test_public_direct_original_failure_survives_scheduler_load(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The transport settlement signal must not depend on checkpoint luck."""

    async def scheduler_noise(stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.sleep(0)

    stop = asyncio.Event()
    noise = asyncio.create_task(scheduler_noise(stop))
    try:
        for _ in range(50):
            kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
            client = kit.direct(
                InProcessServer(name="failing", factory=_failing_server),
                raise_server_exceptions=True,
            )
            async with client:
                with pytest.raises(RuntimeError, match="PUBLIC_IN_PROCESS_CANARY"):
                    await client.list_tools()
            await kit.aclose()
    finally:
        stop.set()
        await noise
    assert "Future exception was never retrieved" not in caplog.text


@pytest.mark.asyncio
async def test_public_direct_sanitizes_in_process_failure_when_disabled() -> None:
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(
        InProcessServer(name="failing", factory=_failing_server),
        raise_server_exceptions=False,
    )
    async with client:
        with pytest.raises(ProtocolError) as error:
            await client.list_tools()
        assert "PUBLIC_IN_PROCESS_CANARY" not in str(error.value)
    await kit.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("factory", "operation"),
    [
        pytest.param(_initialization_failure_server, "initialize", id="initialization"),
        pytest.param(_failing_server, "list_tools", id="list-tools"),
        pytest.param(_call_failure_server, "call-tool", id="call-tool"),
    ],
)
async def test_public_direct_in_process_failures_are_original_and_terminal(
    factory: Any,
    operation: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(
        InProcessServer(name="failure", factory=factory), raise_server_exceptions=True
    )
    with pytest.raises(RuntimeError) as failure:
        async with client:
            if operation == "list_tools":
                await client.list_tools()
            elif operation == "call-tool":
                await client.call_tool("explode", {})
    assert failure.value.args[0].endswith("IN_PROCESS_CANARY")
    assert client.final_trace is not None
    assert client.final_trace.events[-1].kind.value == "execution.finished"
    assert client.final_trace.completeness == "partial"
    assert "IN_PROCESS_CANARY" not in caplog.text
    await kit.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("factory", "operation", "error_type"),
    [
        pytest.param(
            _initialization_failure_server,
            "initialize",
            TransportStartupError,
            id="initialization",
        ),
        pytest.param(_failing_server, "list_tools", ProtocolError, id="list-tools"),
        pytest.param(_call_failure_server, "call-tool", ProtocolError, id="call-tool"),
    ],
)
async def test_public_direct_in_process_failures_are_sanitized_when_disabled(
    factory: Any,
    operation: str,
    error_type: type[Exception],
    caplog: pytest.LogCaptureFixture,
) -> None:
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(
        InProcessServer(name="failure", factory=factory), raise_server_exceptions=False
    )
    with pytest.raises(error_type) as failure:
        async with client:
            if operation == "list_tools":
                await client.list_tools()
            elif operation == "call-tool":
                await client.call_tool("explode", {})
    assert "IN_PROCESS_CANARY" not in str(failure.value)
    assert client.final_trace is not None
    assert client.final_trace.events[-1].kind.value == "execution.finished"
    assert client.final_trace.completeness == "partial"
    assert "IN_PROCESS_CANARY" not in caplog.text
    await kit.aclose()


@pytest.mark.asyncio
async def test_public_direct_shutdown_failure_is_original_and_terminal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(
        InProcessServer(name="shutdown-failure", factory=_shutdown_failure_server),
        raise_server_exceptions=True,
    )
    with pytest.raises(RuntimeError, match="SHUTDOWN_IN_PROCESS_CANARY"):
        async with client:
            pass
    assert client.final_trace is not None
    assert client.final_trace.events[-1].kind.value == "execution.finished"
    assert client.final_trace.completeness == "partial"
    assert "SHUTDOWN_IN_PROCESS_CANARY" not in caplog.text
    await kit.aclose()


@pytest.mark.asyncio
async def test_public_direct_shutdown_failure_is_sanitized_when_disabled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(
        InProcessServer(name="shutdown-failure", factory=_shutdown_failure_server),
        raise_server_exceptions=False,
    )
    with pytest.raises(TransportProcessError) as failure:
        async with client:
            pass
    assert "SHUTDOWN_IN_PROCESS_CANARY" not in str(failure.value)
    assert client.final_trace is not None
    assert client.final_trace.events[-1].kind.value == "execution.finished"
    assert client.final_trace.completeness == "partial"
    assert "SHUTDOWN_IN_PROCESS_CANARY" not in caplog.text
    await kit.aclose()


@pytest.mark.asyncio
async def test_primary_body_exception_survives_cleanup_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    primary_error = RuntimeError("PRIMARY_SERVER_CANARY")

    def failure_server() -> Server:
        async def list_tools(_context: object, _params: object) -> ListToolsResult:
            raise primary_error

        return Server("async-direct-primary-failure-fixture", on_list_tools=list_tools)

    client = kit.direct(
        InProcessServer(name="fixture", factory=failure_server),
        raise_server_exceptions=True,
    )
    with pytest.raises(RuntimeError) as failure:
        async with client:

            async def cleanup_failure() -> None:
                raise RuntimeError("CLEANUP_CANARY")

            assert client._connection is not None
            client._connection.close = cleanup_failure
            await client.list_tools()
    assert failure.value is primary_error
    assert type(failure.value) is type(primary_error)
    assert str(failure.value) == str(primary_error)
    assert client.final_trace is not None
    assert "cleanup_failed" in client.final_trace.limitations
    assert client.final_trace.events[-1].kind.value == "execution.finished"
    assert "CLEANUP_CANARY" not in caplog.text
    await kit.aclose()


@pytest.mark.asyncio
async def test_standalone_close_failure_is_original_and_terminal() -> None:
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(InProcessServer(name="fixture", factory=_server))
    await client.__aenter__()

    async def cleanup_failure() -> None:
        raise RuntimeError("STANDALONE_CLEANUP_CANARY")

    assert client._connection is not None
    client._connection.close = cleanup_failure
    with pytest.raises(RuntimeError, match="STANDALONE_CLEANUP_CANARY"):
        await client.aclose()
    assert client.final_trace is not None
    assert "cleanup_failed" in client.final_trace.limitations
    assert client.final_trace.events[-1].kind.value == "execution.finished"
    await kit.aclose()


@pytest.mark.asyncio
async def test_direct_rejects_closed_kit_and_unresolved_profile_bindings() -> None:
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    binding = ServerBinding(
        profile=ServerProfileRef(
            profile_id=ServerProfileId("server-profile"),
            revision=RevisionSelection(mode="latest"),
        )
    )
    with pytest.raises(UnsupportedFeature, match="runtime resolution"):
        kit.direct(binding)
    await kit.aclose()
    with pytest.raises(KitClosed):
        kit.direct(InProcessServer(name="fixture", factory=_server))


def test_explicit_unsupported_protocol_fails_before_server_startup() -> None:
    started = False

    def factory() -> Server:
        nonlocal started
        started = True
        return _server()

    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    with pytest.raises(UnsupportedFeature, match="protocol revision"):
        kit.direct(
            InProcessServer(name="fixture", factory=factory), protocol="2024-11-05"
        )
    assert started is False


def test_protocol_transport_constraint_is_checked_before_startup() -> None:
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    with pytest.raises(UnsupportedFeature, match="transport"):
        kit.direct(
            InProcessServer(name="fixture", factory=_server),
            protocol=ProtocolConstraint(transport=TransportKind.SSE),
        )
