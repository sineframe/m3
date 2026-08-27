"""User-shaped, black-box E2E tests for the public Python SDK.

These deliberately use real stdio subprocesses and, for persistence, a second
OS process. They are examples MCP authors can adapt without depending on MCP
Pal internals or serialized scenario files.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from typing import IO, Iterator, cast

import pytest
from pydantic import TypeAdapter

from mcp_pal import MCPTestKit
from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.sync_api import InitializationResult, PromptResult, ResourceReadResult, ToolCallResult
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal.testing import FaultInjector
from mcp_pal.types import (
    ACPAgent,
    AgentExecutionSpec,
    ArtifactPolicy,
    CallToolOperation,
    CallToolOperationResult,
    DirectExecutionSpec,
    DirectOperationResult,
    DirectOperation,
    DirectOperationResultBase,
    ExecutionOutcome,
    ExecutionResult,
    ErrorCode,
    GetPromptOperation,
    GetPromptOperationResult,
    ListPromptsOperation,
    ListPromptsOperationResult,
    ListResourcesOperation,
    ListResourcesOperationResult,
    ListResourceTemplatesOperation,
    ListResourceTemplatesOperationResult,
    ListToolsOperation,
    ListToolsOperationResult,
    PingOperation,
    PingOperationResult,
    ReadResourceOperation,
    ReadResourceOperationResult,
    ServerBinding,
    RestrictiveToolPolicy,
    StdioServer,
    TextContent,
    TurnOutcome,
    WorkspaceKind,
    WorkspacePolicy,
)


pytestmark = pytest.mark.e2e

_SDK_ROOT = Path(__file__).parents[2]
_REPOSITORY_ROOT = _SDK_ROOT.parent
_FIXTURES = _SDK_ROOT / "tests" / "fixtures"
_MATRIX_SERVER = _FIXTURES / "matrix_stdio_server.py"
_PERSISTENT_WORKER = _FIXTURES / "persistent_sdk_worker.py"
_HANGING_SERVER = _FIXTURES / "hanging_stdio_server.py"


def _stdio_server(path: Path = _MATRIX_SERVER, *, environment: dict[str, str] | None = None) -> StdioServer:
    return StdioServer(
        name="e2e-mcp",
        command=sys.executable,
        args=(str(path),),
        cwd=str(_REPOSITORY_ROOT),
        environment=environment or {},
    )


def _acp_spec() -> AgentExecutionSpec:
    manifest = {
        "schema_version": "mcp-pal.harness.v1",
        "protocol": "acp",
        "protocol_version": 1,
        "command": sys.executable,
        "args": [
            "-m",
            "mcp_pal.bridge.reference",
            "--target",
            sys.executable,
            "--target-args-json",
            '["-m","mcp_pal.fixtures.structured_cli"]',
        ],
        "env": {},
    }
    return AgentExecutionSpec(
        harness=ACPAgent(model="agent-default", manifest=manifest),
        servers=(ServerBinding(server=_stdio_server(), alias="e2e-mcp"),),
        tool_policy=RestrictiveToolPolicy(allowed_tools=("e2e-mcp:echo",)),
    )


def _wait_for_line(stream: IO[str], timeout: float) -> str:
    selector = selectors.DefaultSelector()
    selector.register(stream, selectors.EVENT_READ)
    try:
        if not selector.select(timeout):
            raise AssertionError("persistent SDK worker did not become ready")
        return stream.readline().strip()
    finally:
        selector.close()


@contextmanager
def _persistent_worker(database: Path) -> Iterator[subprocess.Popen[str]]:
    process = subprocess.Popen(
        [sys.executable, str(_PERSISTENT_WORKER), str(database.resolve())],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name != "nt",
    )
    assert process.stdout is not None
    try:
        assert _wait_for_line(process.stdout, 10) == "READY"
        yield process
    finally:
        if process.stdin is not None and process.poll() is None:
            try:
                process.stdin.write("\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=5)


def _wait_for_file(path: Path, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path.name}")


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_sync_author_exercises_stdio_tools_resources_prompts_and_trace() -> None:
    """A normal pytest test can cover the complete direct MCP surface."""

    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        client = kit.direct(_stdio_server())
        with client:
            initialization = client.initialization
            assert isinstance(initialization, InitializationResult)
            assert initialization.server_info["name"] == "matrix-stdio"
            assert {tool.name for tool in client.list_all_tools()} == {"echo", "failure"}
            echo_result = client.call_tool("echo", {"text": "e2e-value"})
            assert isinstance(echo_result, ToolCallResult)
            assert echo_result.content[0]["text"] == "e2e-value"
            failure_result = client.call_tool("failure", {})
            assert isinstance(failure_result, ToolCallResult)
            assert failure_result.is_error is True
            resource = client.read_resource("memory://document")
            assert isinstance(resource, ResourceReadResult)
            assert resource.text == "resource value"
            prompt = client.get_prompt("greeting")
            assert isinstance(prompt, PromptResult)
            assert prompt.messages[0]["content"]["text"] == "hello greeting"

        trace = client.final_trace
        assert trace is not None
        assert any(event.kind.value == "mcp.request" for event in trace.events)
        assert any(event.kind.value == "mcp.response" for event in trace.events)
        assert trace.events[-1].kind.value == "execution.finished"
        assert trace.events[-1].payload["outcome"] == ExecutionOutcome.COMPLETED.value


def test_sync_run_dispatches_call_tool_to_real_stdio_and_returns_typed_result() -> None:
    spec = DirectExecutionSpec(
        servers=(ServerBinding(server=_stdio_server(), alias="e2e-mcp"),),
        operation=CallToolOperation(
            server="e2e-mcp",
            name="echo",
            arguments={"text": "run-value"},
        ),
    )
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        result = kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert isinstance(result.direct_result, CallToolOperationResult)
    assert result.direct_result.content[0]["text"] == "run-value"
    assert result.direct_result.raw is not None
    assert result.trace is not None
    assert result.trace.events[-1].kind.value == "execution.finished"

    error_spec = spec.model_copy(
        update={
            "operation": CallToolOperation(
                server="e2e-mcp",
                name="failure",
                arguments={},
            )
        }
    )
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        error_result = kit.run(error_spec)
    assert error_result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert isinstance(error_result.direct_result, CallToolOperationResult)
    assert error_result.direct_result.is_error is True


def _run_sync_direct_operation(operation: DirectOperation) -> ExecutionResult:
    spec = DirectExecutionSpec(
        servers=(ServerBinding(server=_stdio_server(), alias="e2e-mcp"),),
        operation=operation,
    )
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        return kit.run(spec)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "result_type"),
    [
        pytest.param(ListToolsOperation(server="e2e-mcp"), ListToolsOperationResult, id="list-tools"),
        pytest.param(ListResourcesOperation(server="e2e-mcp"), ListResourcesOperationResult, id="list-resources"),
        pytest.param(ListResourceTemplatesOperation(server="e2e-mcp"), ListResourceTemplatesOperationResult, id="list-resource-templates"),
        pytest.param(ListPromptsOperation(server="e2e-mcp"), ListPromptsOperationResult, id="list-prompts"),
        pytest.param(CallToolOperation(server="e2e-mcp", name="echo", arguments={"text": "parity"}), CallToolOperationResult, id="call-tool"),
        pytest.param(ReadResourceOperation(server="e2e-mcp", uri="memory://document"), ReadResourceOperationResult, id="read-resource"),
        pytest.param(GetPromptOperation(server="e2e-mcp", name="greeting"), GetPromptOperationResult, id="get-prompt"),
        pytest.param(PingOperation(server="e2e-mcp"), PingOperationResult, id="ping"),
    ],
)
async def test_sync_async_direct_operation_parity_roundtrips_json(
    operation: DirectOperation,
    result_type: type[DirectOperationResultBase],
) -> None:
    sync_result = await asyncio.to_thread(_run_sync_direct_operation, operation)
    async_spec = DirectExecutionSpec(
        servers=(ServerBinding(server=_stdio_server(), alias="e2e-mcp"),),
        operation=operation,
    )
    async with AsyncMCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        async_result = await kit.run(async_spec)

    assert sync_result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert async_result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert isinstance(sync_result.direct_result, result_type)
    assert isinstance(async_result.direct_result, result_type)
    assert sync_result.direct_result.raw is not None
    assert async_result.direct_result.raw is not None
    sync_json = sync_result.direct_result.model_dump(mode="json")
    async_json = async_result.direct_result.model_dump(mode="json")
    assert "raw" not in sync_json
    assert "raw" not in async_json
    assert sync_json == async_json
    adapter: TypeAdapter[DirectOperationResult] = TypeAdapter(DirectOperationResult)
    assert adapter.validate_json(json.dumps(sync_json)).model_dump(mode="json") == sync_json
    assert adapter.validate_json(json.dumps(async_json)).model_dump(mode="json") == async_json


@pytest.mark.asyncio
async def test_sync_async_direct_list_tools_cursor_without_following_pages() -> None:
    operation = ListToolsOperation(server="e2e-mcp", cursor="page-2", all_pages=False)
    sync_result = await asyncio.to_thread(_run_sync_direct_operation, operation)
    async_spec = DirectExecutionSpec(
        servers=(ServerBinding(server=_stdio_server(), alias="e2e-mcp"),),
        operation=operation,
    )
    async with AsyncMCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        async_result = await kit.run(async_spec)

    assert isinstance(sync_result.direct_result, ListToolsOperationResult)
    assert isinstance(async_result.direct_result, ListToolsOperationResult)
    assert [tool.name for tool in sync_result.direct_result.tools] == ["failure"]
    assert [tool.name for tool in async_result.direct_result.tools] == ["failure"]
    assert sync_result.direct_result.model_dump(mode="json") == async_result.direct_result.model_dump(mode="json")


@pytest.mark.asyncio
async def test_async_run_real_stdio_json_rpc_error_is_failed_and_traced() -> None:
    # Use a regular JSON-RPC application error code.  The harness reserves
    # -32000 for an injected transport failure.
    server = FaultInjector().protocol_error("tools/call", code=-32042)
    spec = DirectExecutionSpec(
        servers=(ServerBinding(server=server.stdio_server(), alias="fault"),),
        operation=CallToolOperation(
            server="fault",
            name="echo",
            arguments={},
        ),
    )
    async with AsyncMCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    assert result.error is not None and result.error.code is ErrorCode.PROTOCOL_ERROR
    assert result.direct_result is None
    assert result.trace is not None
    assert result.trace.events[-1].kind.value == "execution.finished"


@pytest.mark.asyncio
async def test_async_run_real_stdio_startup_failure_is_failed_and_traced() -> None:
    spec = DirectExecutionSpec(
        servers=(
            ServerBinding(
                server=StdioServer(name="missing", command="mcp-pal-no-such-executable"),
                alias="missing",
            ),
        ),
        operation=PingOperation(server="missing"),
    )
    async with AsyncMCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    assert result.error is not None
    assert result.error.code in {ErrorCode.TRANSPORT_ERROR, ErrorCode.PROTOCOL_ERROR}
    assert result.direct_result is None
    assert result.trace is not None


@pytest.mark.asyncio
async def test_async_run_real_stdio_operation_timeout_is_timed_out_and_traced() -> None:
    server = FaultInjector().delay("tools/call", 0.5).stdio_server()
    spec = DirectExecutionSpec(
        servers=(ServerBinding(server=server, alias="slow"),),
        operation=CallToolOperation(server="slow", name="echo", arguments={}),
        timeout_seconds=0.05,
    )
    async with AsyncMCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.TIMED_OUT
    assert result.error is not None and result.error.code is ErrorCode.TIMEOUT
    assert result.direct_result is None
    assert result.trace is not None


@pytest.mark.asyncio
async def test_async_run_real_stdio_schema_validation_failure_is_failed_and_traced() -> None:
    server = FaultInjector().invalid_result("tools/call").stdio_server()
    spec = DirectExecutionSpec(
        servers=(ServerBinding(server=server, alias="invalid"),),
        operation=CallToolOperation(server="invalid", name="echo", arguments={}),
        validate_schemas=True,
    )
    async with AsyncMCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    assert result.error is not None and result.error.code is ErrorCode.INVALID_ARGUMENT
    assert result.direct_result is None
    assert result.trace is not None


@pytest.mark.asyncio
async def test_async_run_dispatches_call_tool_to_real_stdio_and_returns_typed_result() -> None:
    spec = DirectExecutionSpec(
        servers=(ServerBinding(server=_stdio_server(), alias="e2e-mcp"),),
        operation=CallToolOperation(
            server="e2e-mcp",
            name="echo",
            arguments={"text": "async-run-value"},
        ),
    )
    async with AsyncMCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert isinstance(result.direct_result, CallToolOperationResult)
    assert result.direct_result.content[0]["text"] == "async-run-value"
    assert result.direct_result.raw is not None
    assert result.trace is not None
    assert result.trace.events[-1].kind.value == "execution.finished"


def test_multiturn_acp_session_calls_one_real_mcp_and_keeps_a_trace() -> None:
    """Two sends retain one ACP conversation and one MCP process."""

    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agent_session(_acp_spec()) as session:
            first = session.send("nonce-first")
            second = session.send("nonce-second")

        assert first.snapshot.outcome is TurnOutcome.COMPLETED, first.error
        assert second.snapshot.outcome is TurnOutcome.COMPLETED, second.error
        assert first.response is not None
        assert isinstance(first.response.content[0], TextContent)
        assert first.response.content[0].text == "nonce-first"
        assert second.response is not None
        assert isinstance(second.response.content[0], TextContent)
        assert second.response.content[0].text == "nonce-second"
        assert first.snapshot.session_id == second.snapshot.session_id
        assert session.result.trace is not None
        assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert len(session.result.turns) == 2


@pytest.mark.asyncio
async def test_separate_worker_success_returns_a_reopenable_trace(tmp_path: Path) -> None:
    """A UI/API process can submit while another OS process owns execution."""

    database = (tmp_path / "trace.sqlite").resolve()
    with _persistent_worker(database):
        store = SQLiteExecutionStore(database)
        producer = AsyncMCPTestKit(store=store, embedded_worker=False, env={})
        try:
            handle = producer.submit(
                DirectExecutionSpec(
                    servers=(ServerBinding(server=_stdio_server()),),
                    operation=CallToolOperation(name="echo", arguments={"text": "durable-value"}),
                )
            )
            result = await handle.result(timeout=15)
            assert isinstance(result.direct_result, CallToolOperationResult)
            assert result.direct_result.content[0]["text"] == "durable-value"
            assert result.direct_result.raw is None
            error_handle = producer.submit(
                DirectExecutionSpec(
                    servers=(ServerBinding(server=_stdio_server()),),
                    operation=CallToolOperation(name="failure", arguments={}),
                )
            )
            error_result = await error_handle.result(timeout=15)
            assert isinstance(error_result.direct_result, CallToolOperationResult)
            assert error_result.direct_result.is_error is True
            assert error_result.direct_result.raw is None
            failed_handle = producer.submit(
                DirectExecutionSpec(
                    servers=(
                        ServerBinding(
                            server=FaultInjector().protocol_error("tools/call", code=-32042).stdio_server(),
                        ),
                    ),
                    operation=CallToolOperation(name="echo", arguments={}),
                )
            )
            failed_result = await failed_handle.result(timeout=15)
            assert failed_result.snapshot.outcome is ExecutionOutcome.FAILED
            assert failed_result.direct_result is None
        finally:
            await producer.aclose()

    reopened = SQLiteExecutionStore(database)
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert result.trace is not None
    assert result.trace.execution_id == result.snapshot.execution_id
    assert result.trace.events[-1].kind.value == "execution.finished"
    success_terminal = next(
        event
        for event in reversed(tuple(reopened.iter_events(result.snapshot.execution_id)))
        if event.kind.value == "execution.finished"
    )
    terminal_payload = success_terminal.payload
    assert result.trace.events[-1].payload == terminal_payload
    persisted_result = terminal_payload.get("direct_result")
    assert isinstance(persisted_result, Mapping)
    assert persisted_result["kind"] == "call_tool"
    assert "raw" not in persisted_result
    adapter: TypeAdapter[DirectOperationResult] = TypeAdapter(DirectOperationResult)
    persisted_typed: DirectOperationResult = adapter.validate_python(persisted_result)
    assert isinstance(persisted_typed, CallToolOperationResult)
    assert persisted_typed.content[0]["text"] == "durable-value"
    assert persisted_typed.raw is None
    error_terminal = next(
        event
        for event in reversed(tuple(reopened.iter_events(error_result.snapshot.execution_id)))
        if event.kind.value == "execution.finished"
    )
    error_persisted = error_terminal.payload.get("direct_result")
    assert isinstance(error_persisted, Mapping)
    error_typed: DirectOperationResult = adapter.validate_python(error_persisted)
    assert isinstance(error_typed, CallToolOperationResult)
    assert error_typed.is_error is True
    assert error_typed.raw is None
    failed_terminal = next(
        event
        for event in reversed(tuple(reopened.iter_events(failed_result.snapshot.execution_id)))
        if event.kind.value == "execution.finished"
    )
    assert "direct_result" not in failed_terminal.payload
    assert reopened.get_snapshot(result.snapshot.execution_id) == result.snapshot


@pytest.mark.asyncio
async def test_separate_worker_persists_declared_artifact_bytes(tmp_path: Path) -> None:
    source = tmp_path / "workspace-source"
    source.mkdir()
    (source / "result.txt").write_text("persisted-e2e-artifact", encoding="utf-8")
    database = (tmp_path / "artifacts.sqlite").resolve()

    with _persistent_worker(database):
        store = SQLiteExecutionStore(database)
        producer = AsyncMCPTestKit(store=store, embedded_worker=False, env={})
        try:
            handle = producer.submit(
                DirectExecutionSpec(
                    servers=(ServerBinding(server=_stdio_server()),),
                    operation=PingOperation(),
                    workspace=WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(source)),
                    artifact_policy=ArtifactPolicy.ALWAYS,
                    declared_artifacts=("result.txt",),
                )
            )
            result = await handle.result(timeout=15)
        finally:
            try:
                await producer.aclose()
            finally:
                store.close()

    reopened = SQLiteExecutionStore(database)
    try:
        assert len(result.artifacts) == 1
        assert reopened.artifacts.get(result.artifacts[0]) == b"persisted-e2e-artifact"
        assert result.artifacts[0].sha256 == hashlib.sha256(b"persisted-e2e-artifact").hexdigest()
    finally:
        reopened.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="process-group liveness assertion is POSIX-specific")
@pytest.mark.xfail(
    strict=True,
    reason="known regression: a durable cancellation request is not observed while another process owns active work",
)
async def test_separate_worker_cancel_interrupts_owned_stdio_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "mcp.pid"
    database = (tmp_path / "cancel.sqlite").resolve()
    server = _stdio_server(
        _HANGING_SERVER,
        environment={"MCP_PAL_E2E_PID_FILE": str(pid_file)},
    )

    with _persistent_worker(database):
        producer = AsyncMCPTestKit(
            store=SQLiteExecutionStore(database), embedded_worker=False, env={}
        )
        try:
            handle = producer.submit(
                DirectExecutionSpec(
                    servers=(ServerBinding(server=server),),
                    operation=PingOperation(),
                    timeout_seconds=30,
                )
            )
            await asyncio.to_thread(_wait_for_file, pid_file)
            pid = int(pid_file.read_text(encoding="utf-8"))
            await handle.cancel()
            result = await handle.result(timeout=5)
            deadline = time.monotonic() + 3
            while _process_exists(pid) and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
        finally:
            await producer.aclose()

    assert result.snapshot.outcome is ExecutionOutcome.CANCELLED
    assert result.trace is not None
    assert not _process_exists(pid)
