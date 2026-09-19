"""Deterministic lifecycle tests for official MCP local transports."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import MCPError
from mcp.types import ListToolsResult

from m3.transport.local import (
    InProcessMCPTransport,
    StdioMCPTransport,
    TransportProcessError,
    TransportStartupError,
)
from m3.types import InProcessServer, SecretReference, StdioServer


def _server() -> Server:
    async def list_tools(_context: object, _params: object) -> ListToolsResult:
        return ListToolsResult(tools=[])

    return Server("m3-test", on_list_tools=list_tools)


@pytest.mark.asyncio
async def test_in_process_connection_uses_official_client_and_closes_cleanly() -> None:
    transport = InProcessMCPTransport(InProcessServer(name="test", factory=_server))
    async with transport as connection:
        async with ClientSession(
            connection.read_stream, connection.write_stream
        ) as client:
            initialized = await client.initialize()
            assert initialized.server_info.name == "m3-test"
    assert connection.evidence.closed is True


@pytest.mark.asyncio
async def test_in_process_factory_failure_is_sanitized_and_evidenced() -> None:
    def broken() -> Server:
        raise RuntimeError("secret-token-must-not-escape")

    with pytest.raises(TransportStartupError) as error:
        await InProcessMCPTransport(broken).open()
    assert str(error.value) == "in-process MCP server startup failed"
    assert "secret-token" not in str(error.value)
    assert error.value.evidence is not None
    assert error.value.evidence.partial is True


@pytest.mark.asyncio
async def test_in_process_server_exception_can_be_observed_or_sanitized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)

    async def broken_tools(_context: object, _params: object) -> ListToolsResult:
        raise RuntimeError("IN_PROCESS_CANARY_SECRET")

    async with InProcessMCPTransport(
        lambda: Server("broken", on_list_tools=broken_tools),
        raise_server_exceptions=True,
    ) as connection:
        async with ClientSession(
            connection.read_stream, connection.write_stream
        ) as client:
            await client.initialize()
            with pytest.raises(MCPError):
                await client.list_tools()
        await asyncio.sleep(0)
        with pytest.raises(TransportProcessError):
            connection.raise_if_failed()
        assert connection.evidence.partial is True
        assert "IN_PROCESS_CANARY_SECRET" not in caplog.text

    caplog.clear()
    async with InProcessMCPTransport(
        lambda: Server("sanitized", on_list_tools=broken_tools),
        raise_server_exceptions=False,
    ) as connection:
        async with ClientSession(
            connection.read_stream, connection.write_stream
        ) as client:
            await client.initialize()
            with pytest.raises(MCPError):
                await client.list_tools()
        await asyncio.sleep(0)
        connection.raise_if_failed()
        assert "IN_PROCESS_CANARY_SECRET" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_stdio_connection_uses_argv_only_and_owned_cleanup() -> None:
    server = StdioServer(
        name="echo",
        command=sys.executable,
        args=("-m", "m3.fixtures.echo_server"),
    )
    transport = StdioMCPTransport(server)
    async with transport as connection:
        async with ClientSession(
            connection.read_stream, connection.write_stream
        ) as client:
            initialized = await client.initialize()
            assert initialized.server_info.name == "echo-server"
    assert connection.evidence.closed is True
    await connection.close()


@pytest.mark.process_lifecycle
def test_stdio_environment_secret_observer_classifies_api_keys_and_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = "resolved-stdio-reference"
    monkeypatch.setenv("MCP_STDIO_REFERENCE", resolved)
    observed: list[str] = []
    server = StdioServer(
        name="echo",
        command=sys.executable,
        environment={
            "X-API-Key": "literal-x-api-key",
            "ANTHROPIC_API_KEY": "literal-anthropic-api-key",
            "PATH": "ordinary-path",
            "REFERENCE": SecretReference(
                source="environment", name="MCP_STDIO_REFERENCE"
            ),
        },
    )
    transport = StdioMCPTransport(server, secret_observer=observed.append)
    assert transport._environment() == {
        "X-API-Key": "literal-x-api-key",
        "ANTHROPIC_API_KEY": "literal-anthropic-api-key",
        "PATH": "ordinary-path",
        "REFERENCE": resolved,
    }
    assert observed == [
        "literal-x-api-key",
        "literal-anthropic-api-key",
        resolved,
    ]


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_stdio_connection_accepts_an_existing_absolute_cwd(
    tmp_path: Path,
) -> None:
    server = StdioServer(
        name="echo",
        command=sys.executable,
        args=("-m", "m3.fixtures.echo_server"),
        cwd=str(tmp_path),
    )
    async with StdioMCPTransport(server) as connection:
        async with ClientSession(
            connection.read_stream, connection.write_stream
        ) as client:
            initialized = await client.initialize()
            assert initialized.server_info.name == "echo-server"


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_stdio_cwd_requires_an_absolute_existing_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError):
        StdioServer(name="nul", command=sys.executable, cwd="/tmp/\x00cwd")

    relative = StdioServer(name="relative", command=sys.executable, cwd=".")
    with pytest.raises(TransportStartupError) as relative_error:
        await StdioMCPTransport(relative).open()
    assert str(relative_error.value) == "stdio cwd is not an existing directory"

    missing = StdioServer(
        name="missing", command=sys.executable, cwd=str(tmp_path / "gone")
    )
    with pytest.raises(TransportStartupError) as missing_error:
        await StdioMCPTransport(missing).open()
    assert str(missing_error.value) == "stdio cwd is not an existing directory"


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_stdio_cleanup_rejects_cross_task_scope_exit() -> None:
    server = StdioServer(
        name="echo",
        command=sys.executable,
        args=("-m", "m3.fixtures.echo_server"),
    )
    transport = StdioMCPTransport(server)
    async with transport as connection:
        other_close = asyncio.create_task(connection.close())
        with pytest.raises(TransportProcessError):
            await other_close
        assert connection.evidence.error_kind == "cleanup_owner"
    assert connection.evidence.closed is True


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_stdio_startup_errors_are_sanitized() -> None:
    server = StdioServer(name="missing", command="m3-no-such-executable")
    with pytest.raises(TransportStartupError) as error:
        await StdioMCPTransport(server).open()
    assert str(error.value) == "stdio MCP server startup failed"
    assert "no-such" not in str(error.value)
    assert error.value.evidence is not None
    assert error.value.evidence.partial is True


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_stdio_nul_arguments_are_rejected_before_spawn() -> None:
    server = StdioServer(name="invalid", command="python", args=("\x00",))
    with pytest.raises(TransportStartupError):
        await StdioMCPTransport(server).open()


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_stdio_nul_environment_is_rejected_before_spawn() -> None:
    server = StdioServer(
        name="invalid",
        command=sys.executable,
        environment={"TOKEN": "value\x00"},
    )
    with pytest.raises(TransportStartupError) as error:
        await StdioMCPTransport(server).open()
    assert str(error.value) == "stdio environment contains an invalid value"


@pytest.mark.asyncio
async def test_close_is_idempotent_and_survives_caller_cancellation() -> None:
    transport = InProcessMCPTransport(_server)
    connection = await transport.open()
    close_task = asyncio.create_task(connection.close())
    await asyncio.sleep(0)
    close_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close_task
    # The shielded owned cleanup continues and a subsequent close observes the
    # same completion.  A second close is intentionally a no-op.
    await connection.close()
    await connection.close()
    assert connection.evidence.closed is True


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_stdio_secret_resolution_failure_is_partial_and_sanitized() -> None:
    server = StdioServer(
        name="secret",
        command=sys.executable,
        environment={
            "TOKEN": SecretReference(source="provider", name="internal-token")
        },
    )
    with pytest.raises(TransportStartupError) as error:
        await StdioMCPTransport(server).open()
    assert str(error.value) == "stdio secret resolution is unavailable"
    assert "internal-token" not in str(error.value)
    assert error.value.evidence is not None
    assert error.value.evidence.partial is True


@pytest.mark.asyncio
async def test_in_process_startup_cancellation_closes_memory_context() -> None:
    gate = asyncio.Event()

    async def delayed_server() -> Server:
        await gate.wait()
        return _server()

    task = asyncio.create_task(InProcessMCPTransport(delayed_server).open())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
