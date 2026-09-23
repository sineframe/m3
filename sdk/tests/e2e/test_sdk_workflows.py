"""User-shaped, black-box E2E tests for the public Python SDK.

These deliberately use real stdio subprocesses and, for persistence, a second
OS process. They are examples MCP authors can adapt without depending on MCP
Pal internals or serialized scenario files.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import IO

import pytest
from pydantic import TypeAdapter

from m3 import MCPTestKit
from m3._types.specs import AgentSpec
from m3.async_api import AsyncMCPTestKit
from m3.errors import UnsupportedFeature
from m3.execution_trace import ExecutionTraceRecorder
from m3.observability import ObservationState, TransportEntry
from m3.storage import SQLiteExecutionStore
from m3.sync_api import (
    InitializationResult,
    PromptResult,
    ResourceReadResult,
    ToolCallResult,
)
from m3.testing import FaultInjector
from m3.types import (
    ACPAgent,
    ArtifactPolicy,
    CallTool,
    CallToolResult,
    DirectOperation,
    DirectResult,
    DirectSpec,
    ErrorCode,
    ExecutionOutcome,
    ExecutionResult,
    FrozenModel,
    FullToolPolicy,
    GetPrompt,
    GetPromptResult,
    ListPrompts,
    ListPromptsResult,
    ListResources,
    ListResourcesResult,
    ListTemplates,
    ListTemplatesResult,
    ListTools,
    ListToolsResult,
    OpaqueContent,
    Ping,
    PingResult,
    ReadResource,
    ReadResourceResult,
    RestrictiveToolPolicy,
    SecretReference,
    ServerBinding,
    StdioServer,
    TextContent,
    TransportKind,
    TurnOutcome,
    UserMessage,
    WorkspaceKind,
    WorkspacePolicy,
)

pytestmark = [pytest.mark.e2e, pytest.mark.process_lifecycle]

_SDK_ROOT = Path(__file__).parents[2]
_REPOSITORY_ROOT = _SDK_ROOT.parent
_FIXTURES = _SDK_ROOT / "tests" / "fixtures"
_MATRIX_SERVER = _FIXTURES / "matrix_stdio_server.py"
_OBSERVING_ACP_AGENT = _FIXTURES / "observing_acp_agent.py"
_PERSISTENT_WORKER = _FIXTURES / "persistent_sdk_worker.py"
_HANGING_SERVER = _FIXTURES / "hanging_stdio_server.py"
_WORKER_READY_TIMEOUT = 30.0
_WORKER_SHUTDOWN_TIMEOUT = 15.0
_WORKER_KILL_TIMEOUT = 5.0
_WORKER_OUTPUT_LIMIT = 16_384
_MCP_MARKER_TIMEOUT = 30.0


def _stdio_server(
    path: Path = _MATRIX_SERVER, *, environment: dict[str, str] | None = None
) -> StdioServer:
    return StdioServer(
        name="e2e-mcp",
        command=sys.executable,
        args=(str(path),),
        cwd=str(_REPOSITORY_ROOT),
        environment=environment or {},
    )


def _acp_spec(*, acp_marker: Path, mcp_marker: Path) -> AgentSpec:
    manifest = {
        "schema_version": "m3.harness.v1",
        "protocol": "acp",
        "protocol_version": 1,
        "command": sys.executable,
        "args": [
            str(_OBSERVING_ACP_AGENT),
            "--observation-marker",
            str(acp_marker),
            "--target",
            sys.executable,
            "--target-args-json",
            '["-m","m3.fixtures.structured_cli"]',
        ],
        "env": {},
    }
    return AgentSpec(
        harness=ACPAgent(model="agent-default", manifest=manifest),
        servers=(
            ServerBinding(
                server=_stdio_server(
                    environment={"M3_E2E_MCP_MARKER": str(mcp_marker)},
                ),
                alias="e2e-mcp",
            ),
        ),
        tool_policy=RestrictiveToolPolicy(allowed_tools=("e2e-mcp:echo",)),
    )


def _scenario_acp_spec(mode: str, *, mcp_marker: Path) -> AgentSpec:
    """Build a public agent spec around the test-only ACP subprocess."""

    fixture = _FIXTURES / "acp_scenario_agent.py"
    return AgentSpec(
        harness=ACPAgent(
            model="fixture",
            manifest={
                "schema_version": "m3.harness.v1",
                "protocol": "acp",
                "protocol_version": 1,
                "command": sys.executable,
                "args": [str(fixture), mode],
                "env": {},
            },
        ),
        servers=(
            ServerBinding(
                server=_stdio_server(
                    environment={"M3_E2E_MCP_MARKER": str(mcp_marker)}
                ),
                alias="e2e-mcp",
            ),
        ),
        tool_policy=FullToolPolicy(acknowledge_risk=True),
    )


def _scenario_acp_cancel_spec(
    *, acp_pid_marker: Path, mcp_pid_marker: Path
) -> AgentSpec:
    """Build a real ACP/MCP pair that blocks during MCP initialization."""

    fixture = _FIXTURES / "acp_scenario_agent.py"
    return AgentSpec(
        harness=ACPAgent(
            model="fixture",
            manifest={
                "schema_version": "m3.harness.v1",
                "protocol": "acp",
                "protocol_version": 1,
                "command": sys.executable,
                "args": [str(fixture), "hang"],
                "env": {"M3_ACP_PID_FILE": str(acp_pid_marker)},
            },
        ),
        servers=(
            ServerBinding(
                server=_stdio_server(
                    _HANGING_SERVER,
                    environment={"M3_E2E_PID_FILE": str(mcp_pid_marker)},
                ),
                alias="e2e-mcp",
            ),
        ),
        tool_policy=FullToolPolicy(acknowledge_risk=True),
        message=UserMessage(content=(TextContent(text="cancel-me"),)),
    )


def _authenticated_acp_spec(*, acp_marker: Path, mcp_marker: Path) -> AgentSpec:
    """ACP plus the real stdio proxy, with an MCP credential in a reference."""

    return AgentSpec(
        harness=ACPAgent(
            model="fixture",
            manifest={
                "schema_version": "m3.harness.v1",
                "protocol": "acp",
                "protocol_version": 1,
                "command": sys.executable,
                "args": [str(_FIXTURES / "acp_scenario_agent.py"), "auth"],
                "env": {"M3_ACP_MARKER": str(acp_marker)},
            },
        ),
        servers=(
            ServerBinding(
                server=StdioServer(
                    name="auth-mcp",
                    command=sys.executable,
                    args=(str(_FIXTURES / "auth_stdio_server.py"),),
                    cwd=str(_REPOSITORY_ROOT),
                    environment={
                        "M3_AUTH_TOKEN": SecretReference(
                            source="environment", name="M3_AUTH_TOKEN"
                        ),
                        "M3_E2E_MCP_MARKER": str(mcp_marker),
                    },
                ),
                alias="auth-mcp",
            ),
        ),
        tool_policy=FullToolPolicy(acknowledge_risk=True),
    )


def _capture_stream(
    stream: IO[str],
    output: list[str],
    output_done: threading.Event,
    lines: queue.Queue[str] | None = None,
) -> None:
    """Drain a worker pipe continuously so a noisy child cannot block startup."""

    captured = 0
    try:
        for line in iter(stream.readline, ""):
            if captured < _WORKER_OUTPUT_LIMIT:
                remaining = _WORKER_OUTPUT_LIMIT - captured
                chunk = line[:remaining]
                output.append(chunk)
                captured += len(chunk)
            if lines is not None:
                try:
                    lines.put_nowait(line)
                except queue.Full:
                    # Readiness only needs the first handful of lines.  Keep
                    # draining the OS pipe even if a broken worker is noisy.
                    pass
    except (OSError, ValueError):
        # Cleanup may close a pipe while its reader is waking up.  The process
        # status and the output captured before close remain useful diagnostics.
        pass
    finally:
        output_done.set()


def _output_text(output: list[str]) -> str:
    text = "".join(output).strip()
    return text if text else "<empty>"


def _wait_for_line(
    process: subprocess.Popen[str],
    stdout_lines: queue.Queue[str],
    stdout_output: list[str],
    stderr_output: list[str],
    stdout_done: threading.Event,
    stderr_done: threading.Event,
    timeout: float,
) -> str:
    """Wait for readiness while reporting early exits and bounded diagnostics."""

    deadline = time.monotonic() + timeout
    while True:
        returncode = process.poll()
        if returncode is not None:
            # The reader threads usually observe EOF before poll() does, but
            # allow a short handoff so the failure includes the final stderr.
            stderr_done.wait(0.2)
            raise AssertionError(
                "persistent SDK worker exited before becoming ready "
                f"(exit code {returncode}); stderr: {_output_text(stderr_output)}; "
                f"stdout: {_output_text(stdout_output)}"
            )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            returncode = process.poll()
            raise AssertionError(
                "timed out waiting for persistent SDK worker readiness "
                f"after {timeout:.1f}s (exit code {returncode!r}); "
                f"stderr: {_output_text(stderr_output)}; stdout: {_output_text(stdout_output)}"
            )
        try:
            line = stdout_lines.get(timeout=min(remaining, 0.05))
        except queue.Empty:
            continue
        if line.strip() == "READY":
            return line.strip()


@contextmanager
def _persistent_worker(
    database: Path,
    *,
    command: Sequence[str] | None = None,
    ready_timeout: float = _WORKER_READY_TIMEOUT,
) -> Iterator[subprocess.Popen[str]]:
    worker_command = list(
        command or (sys.executable, str(_PERSISTENT_WORKER), str(database.resolve()))
    )
    process = subprocess.Popen(
        worker_command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name != "nt",
    )
    assert process.stdout is not None and process.stderr is not None
    stdout_output: list[str] = []
    stderr_output: list[str] = []
    stdout_lines: queue.Queue[str] = queue.Queue(maxsize=100)
    stdout_done = threading.Event()
    stderr_done = threading.Event()
    stdout_reader = threading.Thread(
        target=_capture_stream,
        args=(process.stdout, stdout_output, stdout_done, stdout_lines),
        name="m3-worker-stdout",
        daemon=True,
    )
    stderr_reader = threading.Thread(
        target=_capture_stream,
        args=(process.stderr, stderr_output, stderr_done),
        name="m3-worker-stderr",
        daemon=True,
    )
    stdout_reader.start()
    stderr_reader.start()
    ready = False
    try:
        assert (
            _wait_for_line(
                process,
                stdout_lines,
                stdout_output,
                stderr_output,
                stdout_done,
                stderr_done,
                ready_timeout,
            )
            == "READY"
        )
        ready = True
        yield process
    finally:
        if not ready and process.poll() is None:
            # A readiness failure owns a worker that never entered its normal
            # stdin-driven lifecycle.  Kill it promptly so diagnostics do not
            # turn a bounded startup assertion into a shutdown timeout.
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        elif process.stdin is not None and process.poll() is None:
            try:
                process.stdin.write("\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        try:
            process.wait(timeout=_WORKER_SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=_WORKER_KILL_TIMEOUT)
        finally:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            stdout_reader.join(timeout=1)
            stderr_reader.join(timeout=1)


def test_persistent_worker_early_exit_reports_exit_code_and_stderr(
    tmp_path: Path,
) -> None:
    script = tmp_path / "early-exit-worker.py"
    script.write_text(
        "import sys\n"
        "print('worker failed during startup', file=sys.stderr, flush=True)\n"
        "raise SystemExit(23)\n",
        encoding="utf-8",
    )

    with pytest.raises(
        AssertionError, match=r"exit code 23.*worker failed during startup"
    ):
        with _persistent_worker(
            tmp_path / "worker.sqlite",
            command=(sys.executable, str(script)),
            ready_timeout=1,
        ):
            raise AssertionError("the worker unexpectedly became ready")


def test_persistent_worker_timeout_includes_stderr_diagnostics_and_cleans_up(
    tmp_path: Path,
) -> None:
    script = tmp_path / "stalled-worker.py"
    script.write_text(
        "import time\n"
        "import sys\n"
        "print('worker is stalled', file=sys.stderr, flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match=r"timed out.*stderr: worker is stalled"):
        with _persistent_worker(
            tmp_path / "worker.sqlite",
            command=(sys.executable, str(script)),
            ready_timeout=0.05,
        ):
            raise AssertionError("the worker unexpectedly became ready")


def _wait_for_file(path: Path, timeout: float = _MCP_MARKER_TIMEOUT) -> None:
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
            assert {tool.name for tool in client.list_all_tools()} == {
                "echo",
                "failure",
            }
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
    spec = DirectSpec(
        servers=(ServerBinding(server=_stdio_server(), alias="e2e-mcp"),),
        operation=CallTool(
            server="e2e-mcp",
            name="echo",
            arguments={"text": "run-value"},
        ),
    )
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        result = kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert isinstance(result.direct_result, CallToolResult)
    assert result.direct_result.content[0]["text"] == "run-value"
    assert result.direct_result.raw is not None
    assert result.trace is not None
    assert result.trace.events[-1].kind.value == "execution.finished"
    transports = result.trace_view.transports
    assert len(transports) == 1
    assert transports[0].server_binding == "e2e-mcp"
    assert transports[0].connection_id is not None and transports[0].connection_id.root
    assert transports[0].configured.value is TransportKind.STDIO
    assert transports[0].instrumented.value is TransportKind.STDIO

    error_spec = spec.model_copy(
        update={
            "operation": CallTool(
                server="e2e-mcp",
                name="failure",
                arguments={},
            )
        }
    )
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        error_result = kit.run(error_spec)
    assert error_result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert isinstance(error_result.direct_result, CallToolResult)
    assert error_result.direct_result.is_error is True


def _run_sync_direct_operation(operation: DirectOperation) -> ExecutionResult:
    spec = DirectSpec(
        servers=(ServerBinding(server=_stdio_server(), alias="e2e-mcp"),),
        operation=operation,
    )
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        return kit.run(spec)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "result_type"),
    [
        pytest.param(ListTools(server="e2e-mcp"), ListToolsResult, id="list-tools"),
        pytest.param(
            ListResources(server="e2e-mcp"), ListResourcesResult, id="list-resources"
        ),
        pytest.param(
            ListTemplates(server="e2e-mcp"),
            ListTemplatesResult,
            id="list-resource-templates",
        ),
        pytest.param(
            ListPrompts(server="e2e-mcp"), ListPromptsResult, id="list-prompts"
        ),
        pytest.param(
            CallTool(server="e2e-mcp", name="echo", arguments={"text": "parity"}),
            CallToolResult,
            id="call-tool",
        ),
        pytest.param(
            ReadResource(server="e2e-mcp", uri="memory://document"),
            ReadResourceResult,
            id="read-resource",
        ),
        pytest.param(
            GetPrompt(server="e2e-mcp", name="greeting"),
            GetPromptResult,
            id="get-prompt",
        ),
        pytest.param(Ping(server="e2e-mcp"), PingResult, id="ping"),
    ],
)
async def test_sync_async_direct_operation_parity_roundtrips_json(
    operation: DirectOperation,
    result_type: type[FrozenModel],
) -> None:
    sync_result = await asyncio.to_thread(_run_sync_direct_operation, operation)
    async_spec = DirectSpec(
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
    adapter: TypeAdapter[DirectResult] = TypeAdapter(DirectResult)
    assert (
        adapter.validate_json(json.dumps(sync_json)).model_dump(mode="json")
        == sync_json
    )
    assert (
        adapter.validate_json(json.dumps(async_json)).model_dump(mode="json")
        == async_json
    )


@pytest.mark.asyncio
async def test_sync_async_direct_list_tools_cursor_without_following_pages() -> None:
    operation = ListTools(server="e2e-mcp", cursor="page-2", all_pages=False)
    sync_result = await asyncio.to_thread(_run_sync_direct_operation, operation)
    async_spec = DirectSpec(
        servers=(ServerBinding(server=_stdio_server(), alias="e2e-mcp"),),
        operation=operation,
    )
    async with AsyncMCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        async_result = await kit.run(async_spec)

    assert isinstance(sync_result.direct_result, ListToolsResult)
    assert isinstance(async_result.direct_result, ListToolsResult)
    assert [tool.name for tool in sync_result.direct_result.tools] == ["failure"]
    assert [tool.name for tool in async_result.direct_result.tools] == ["failure"]
    assert sync_result.direct_result.model_dump(
        mode="json"
    ) == async_result.direct_result.model_dump(mode="json")


@pytest.mark.asyncio
async def test_async_run_real_stdio_json_rpc_error_is_failed_and_traced() -> None:
    # Use a regular JSON-RPC application error code.  The harness reserves
    # -32000 for an injected transport failure.
    server = FaultInjector().protocol_error("tools/call", code=-32042)
    spec = DirectSpec(
        servers=(ServerBinding(server=server.stdio_server(), alias="fault"),),
        operation=CallTool(
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
    spec = DirectSpec(
        servers=(
            ServerBinding(
                server=StdioServer(name="missing", command="m3-no-such-executable"),
                alias="missing",
            ),
        ),
        operation=Ping(server="missing"),
    )
    async with AsyncMCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    assert result.error is not None
    assert result.error.code in {ErrorCode.TRANSPORT_ERROR, ErrorCode.PROTOCOL_ERROR}
    assert result.direct_result is None
    assert result.trace is not None
    assert result.trace_view.transports == ()


@pytest.mark.asyncio
async def test_async_run_real_stdio_operation_timeout_is_timed_out_and_traced() -> None:
    server = FaultInjector().delay("tools/call", 0.5).stdio_server()
    spec = DirectSpec(
        servers=(ServerBinding(server=server, alias="slow"),),
        operation=CallTool(server="slow", name="echo", arguments={}),
        timeout_seconds=0.05,
    )
    async with AsyncMCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.TIMED_OUT
    assert result.error is not None and result.error.code is ErrorCode.TIMEOUT
    assert result.direct_result is None
    assert result.trace is not None


@pytest.mark.asyncio
async def test_async_run_real_stdio_schema_validation_failure_is_failed_and_traced() -> (
    None
):
    server = FaultInjector().invalid_result("tools/call").stdio_server()
    spec = DirectSpec(
        servers=(ServerBinding(server=server, alias="invalid"),),
        operation=CallTool(server="invalid", name="echo", arguments={}),
        validate_schemas=True,
    )
    async with AsyncMCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    assert result.error is not None and result.error.code is ErrorCode.INVALID_ARGUMENT
    assert result.direct_result is None
    assert result.trace is not None


@pytest.mark.asyncio
async def test_async_run_dispatches_call_tool_to_real_stdio_and_returns_typed_result() -> (
    None
):
    spec = DirectSpec(
        servers=(ServerBinding(server=_stdio_server(), alias="e2e-mcp"),),
        operation=CallTool(
            server="e2e-mcp",
            name="echo",
            arguments={"text": "async-run-value"},
        ),
    )
    async with AsyncMCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert isinstance(result.direct_result, CallToolResult)
    assert result.direct_result.content[0]["text"] == "async-run-value"
    assert result.direct_result.raw is not None
    assert result.trace is not None
    assert result.trace.events[-1].kind.value == "execution.finished"


def test_multiturn_acp_session_calls_one_real_mcp_and_keeps_a_trace(
    tmp_path: Path,
) -> None:
    """Three public sends retain one ACP/MCP lifecycle and complete trace."""

    acp_marker = tmp_path / "acp-observations.jsonl"
    mcp_marker = tmp_path / "mcp-observations.jsonl"
    nonces = ("nonce-first", "nonce-second", "nonce-third")

    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agent_session(
            _acp_spec(acp_marker=acp_marker, mcp_marker=mcp_marker)
        ) as session:
            turns = [session.send(nonce) for nonce in nonces]

        assert len(turns) == len(nonces)
        assert all(turn.snapshot.outcome is TurnOutcome.COMPLETED for turn in turns), [
            turn.error for turn in turns
        ]
        assert [
            turn.response.text if turn.response is not None else None for turn in turns
        ] == list(nonces)
        assert all(
            turn.response is not None
            and len(turn.response.content) == 1
            and isinstance(turn.response.content[0], TextContent)
            for turn in turns
        )
        assert len({str(turn.evidence["session_id"]) for turn in turns}) == 1
        assert all(turn.evidence["transport"] == "acp" for turn in turns)
        assert session.result.trace is not None
        assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert len(session.result.turns) == 3

    acp_observations = [
        json.loads(line) for line in acp_marker.read_text(encoding="utf-8").splitlines()
    ]
    assert len({item["pid"] for item in acp_observations}) == 1
    assert [item["method"] for item in acp_observations].count("initialize") == 1
    assert [item["method"] for item in acp_observations].count("session/new") == 1
    assert [item["method"] for item in acp_observations].count("session/prompt") == 3

    mcp_observations = [
        json.loads(line) for line in mcp_marker.read_text(encoding="utf-8").splitlines()
    ]
    assert len({item["pid"] for item in mcp_observations}) == 1
    assert [item["method"] for item in mcp_observations].count("initialize") == 1
    assert [item["method"] for item in mcp_observations].count("tools/list") == 1
    mcp_calls = [item for item in mcp_observations if item["method"] == "tools/call"]
    assert len(mcp_calls) == 3
    assert [item["arguments"]["text"] for item in mcp_calls] == list(nonces)

    trace = session.result.trace
    assert trace.completeness == "complete"
    assert trace.limitations == ()
    wire_calls = [
        event
        for event in trace.events
        if event.kind.value == "tool.call_requested"
        and event.payload.get("evidence_mode") == "wire_observed"
    ]
    wire_results = [
        event
        for event in trace.events
        if event.kind.value == "tool.result_received"
        and event.payload.get("evidence_mode") == "wire_observed"
    ]
    assert len(wire_calls) == len(wire_results) == 3
    assert [event.payload["arguments"]["text"] for event in wire_calls] == list(nonces)
    assert [
        event.payload["result"]["content"][0]["text"] for event in wire_results
    ] == list(nonces)
    reported_calls = [
        event
        for event in trace.events
        if event.kind.value == "tool.call_requested"
        and event.provenance.origin.value == "harness_reported"
        and event.provenance.source == "acp"
    ]
    reported_results = [
        event
        for event in trace.events
        if event.kind.value == "tool.result_received"
        and event.provenance.origin.value == "harness_reported"
        and event.provenance.source == "acp"
    ]
    assert len(reported_calls) == len(reported_results) == 3
    assert [event.turn_id for event in reported_calls] == [
        event.turn_id for event in reported_results
    ]
    assert len({event.turn_id for event in reported_calls}) == 3
    assert {event.server_binding for event in reported_calls} == {"e2e-mcp"}, repr(
        [dict(event.payload) for event in reported_calls]
    )
    assert {event.payload.get("tool") for event in reported_calls} == {"echo"}, [
        dict(event.payload) for event in reported_calls
    ]
    assert [event.payload["call_id"] for event in reported_calls] == [
        event.payload["call_id"] for event in reported_results
    ]
    assert [event.payload["response_to_sequence"] for event in wire_results] == [
        event.payload["request_sequence"] for event in wire_calls
    ]
    assert sum(event.kind.value == "session.created" for event in trace.events) == 1
    assert sum(event.kind.value == "mcp.initialized" for event in trace.events) == 1
    assert sum(event.kind.value == "execution.finished" for event in trace.events) == 1
    assert trace.events[-1].kind.value == "execution.finished"


def test_agent_transport_entry_is_observed_and_survives_sqlite_reopen(
    tmp_path: Path,
) -> None:
    """The public agent path records configured/instrumented stdio transport."""

    acp_marker = tmp_path / "acp-transport.jsonl"
    mcp_marker = tmp_path / "mcp-transport.jsonl"
    database = tmp_path / "transport.sqlite"
    store = SQLiteExecutionStore(database)
    try:
        with MCPTestKit(store=store, env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
            result = kit.run(
                _acp_spec(acp_marker=acp_marker, mcp_marker=mcp_marker).model_copy(
                    update={
                        "message": UserMessage(
                            content=(TextContent(text="transport-e2e"),)
                        )
                    }
                )
            )
            assert result.trace is not None
            assert result.trace_view.tool_calls
            assert result.trace_view.tool_calls[0].tool.value == "echo"
        assert result.trace is not None
        view = result.trace_view
        entries = [
            entry
            for entry in view.transports
            if isinstance(entry, TransportEntry) and entry.server_binding == "e2e-mcp"
        ]
        assert len(entries) == 1
        entry = entries[0]
        assert entry.phase == "connected"
        assert entry.connection_id is not None and entry.connection_id.root
        assert entry.configured.state is ObservationState.OBSERVED
        assert entry.instrumented.state is ObservationState.OBSERVED
        assert entry.configured.value is TransportKind.STDIO
        assert entry.instrumented.value is TransportKind.STDIO
    finally:
        store.close()

    reopened = SQLiteExecutionStore(database)
    try:
        restored = reopened.get_trace_view(result.snapshot.execution_id)
        assert restored == view
        assert restored.transports == view.transports
    finally:
        reopened.close()


def test_acp_tool_error_recovers_on_same_real_session_and_trace(tmp_path: Path) -> None:
    """An MCP ``isError`` result is a turn observation, not an ACP crash."""

    mcp_marker = tmp_path / "mcp-observations.jsonl"
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agent_session(
            _scenario_acp_spec("recover", mcp_marker=mcp_marker)
        ) as session:
            first = session.send("first")
            second = session.send("second")

        assert first.snapshot.outcome is TurnOutcome.COMPLETED
        assert second.snapshot.outcome is TurnOutcome.COMPLETED
        assert first.response is not None and first.response.text == "recovered:first"
        assert (
            second.response is not None and second.response.text == "recovered:second"
        )
        assert (
            str(first.evidence["session_id"])
            == str(second.evidence["session_id"])
            != ""
        )
        assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert session.result.activity_health.value == "mixed"
        trace = session.result.trace
        assert trace is not None and trace.completeness == "complete"

    wire_results = [
        event
        for event in trace.events
        if event.kind.value == "tool.result_received"
        and event.payload.get("evidence_mode") == "wire_observed"
    ]
    assert len(wire_results) == 2
    assert wire_results[0].payload["result"]["isError"] is True
    assert wire_results[1].payload["result"]["isError"] is False
    assert trace.events[-1].kind.value == "execution.finished"
    _wait_for_file(mcp_marker)
    observations = [
        json.loads(line) for line in mcp_marker.read_text(encoding="utf-8").splitlines()
    ]
    assert len({item["pid"] for item in observations}) == 1
    calls = [item for item in observations if item["method"] == "tools/call"]
    assert [item["name"] for item in calls] == ["failure", "echo"]
    assert [item["arguments"]["text"] for item in calls] == ["first", "second"]


def test_acp_rejects_attachment_without_poisoning_following_turn(
    tmp_path: Path,
) -> None:
    """Content validation happens before a turn and leaves the ACP session usable."""

    mcp_marker = tmp_path / "mcp-observations.jsonl"
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agent_session(
            _scenario_acp_spec("recover", mcp_marker=mcp_marker)
        ) as session:
            with pytest.raises(
                UnsupportedFeature, match="message contains unsupported content"
            ):
                session.send(
                    UserMessage(
                        content=(OpaqueContent(provider="fixture", payload={"x": 1}),)
                    )
                )
            result = session.send("after-attachment")
            assert result.snapshot.outcome is TurnOutcome.COMPLETED
            assert (
                result.response is not None
                and result.response.text == "recovered:after-attachment"
            )
        assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert session.result.trace is not None


@pytest.mark.asyncio
async def test_acp_secret_reference_reaches_only_stdio_handoff_and_persists_redacted_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public ACP execution authenticates MCP without exposing its credential."""

    canary = "acp-auth-server-canary"
    monkeypatch.setenv("M3_AUTH_TOKEN", canary)
    acp_marker = tmp_path / "acp-secret-config.json"
    mcp_marker = tmp_path / "mcp-auth.json"
    database = (tmp_path / "acp-secret.sqlite").resolve()
    blobs = (tmp_path / "acp-secret-blobs").resolve()
    spec = _authenticated_acp_spec(acp_marker=acp_marker, mcp_marker=mcp_marker)

    store = SQLiteExecutionStore(database, blob_root=blobs)
    async with AsyncMCPTestKit(store=store, env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        result = await kit.run(
            spec.model_copy(
                update={
                    "message": UserMessage(
                        content=(TextContent(text="credentialed-turn"),)
                    ),
                }
            )
        )
    store.close()

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    mcp_observations = json.loads(mcp_marker.read_text(encoding="utf-8"))
    assert mcp_observations["authorized"] is True
    assert "tools/call" in mcp_observations["methods"]
    acp_config = json.loads(acp_marker.read_text(encoding="utf-8"))
    encoded_config = json.dumps(acp_config, sort_keys=True)
    assert canary not in encoded_config
    assert acp_config["mcpServers"][0]["env"] == []
    assert result.trace is not None
    public_values = (
        result,
        result.model_dump(mode="json"),
        result.trace,
        result.trace.model_dump(mode="json"),
    )
    assert all(canary not in repr(value) for value in public_values)

    reopened = SQLiteExecutionStore(database, blob_root=blobs)
    try:
        persisted_events = tuple(reopened.iter_events(result.snapshot.execution_id))
        persisted_snapshot = reopened.get_snapshot(result.snapshot.execution_id)
        assert persisted_snapshot is not None
        assert all(
            canary not in repr(value)
            for value in (persisted_snapshot, persisted_events)
        )
    finally:
        reopened.close()
    raw_paths = [
        database,
        database.with_name(database.name + "-wal"),
        database.with_name(database.name + "-shm"),
        *blobs.rglob("*"),
        *tmp_path.rglob("*capture*"),
    ]
    assert all(
        canary.encode() not in path.read_bytes() for path in raw_paths if path.is_file()
    )


@pytest.mark.skipif(
    os.name == "nt", reason="process-group cleanup assertion is POSIX-specific"
)
def test_acp_process_loss_is_failed_partial_trace_and_cleans_mcp(
    tmp_path: Path,
) -> None:
    """A dead ACP child terminalizes the run and does not leak its MCP child."""

    mcp_marker = tmp_path / "mcp-observations.jsonl"
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agent_session(
            _scenario_acp_spec("loss", mcp_marker=mcp_marker)
        ) as session:
            # Keep the black-box test itself bounded if an ACP implementation
            # regresses from EOF detection to a hung prompt future.
            result = session.send("lose-process", timeout=5)
            assert result.snapshot.outcome is TurnOutcome.FAILED
            assert (
                result.error is not None
                and result.error.code is ErrorCode.TRANSPORT_ERROR
            )
        assert session.result.snapshot.outcome is ExecutionOutcome.FAILED
        trace = session.result.trace
        assert trace is not None
        assert trace.completeness == "partial"
        assert trace.limitations == ("partial_trace",)
        assert trace.events[-1].kind.value == "execution.finished"
        view = session.result.trace_view
        assert view.processes
        assert view.processes[-1].exit_code.state is ObservationState.OBSERVED
        assert view.processes[-1].exit_code.value == 17

    _wait_for_file(mcp_marker)
    observations = [
        json.loads(line) for line in mcp_marker.read_text(encoding="utf-8").splitlines()
    ]
    pid = next(item["pid"] for item in observations if item["method"] == "initialize")
    deadline = time.monotonic() + 3
    while _process_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _process_exists(pid)


@pytest.mark.asyncio
async def test_separate_worker_success_returns_a_reopenable_trace(
    tmp_path: Path,
) -> None:
    """A UI/API process can submit while another OS process owns execution."""

    database = (tmp_path / "trace.sqlite").resolve()
    with _persistent_worker(database):
        store = SQLiteExecutionStore(database)
        producer = AsyncMCPTestKit(store=store, embedded_worker=False, env={})
        try:
            handle = producer.submit(
                DirectSpec(
                    servers=(ServerBinding(server=_stdio_server()),),
                    operation=CallTool(
                        name="echo", arguments={"text": "durable-value"}
                    ),
                )
            )
            result = await handle.result(timeout=15)
            assert isinstance(result.direct_result, CallToolResult)
            assert result.direct_result.content[0]["text"] == "durable-value"
            assert result.direct_result.raw is None
            error_handle = producer.submit(
                DirectSpec(
                    servers=(ServerBinding(server=_stdio_server()),),
                    operation=CallTool(name="failure", arguments={}),
                )
            )
            error_result = await error_handle.result(timeout=15)
            assert isinstance(error_result.direct_result, CallToolResult)
            assert error_result.direct_result.is_error is True
            assert error_result.direct_result.raw is None
            failed_handle = producer.submit(
                DirectSpec(
                    servers=(
                        ServerBinding(
                            server=FaultInjector()
                            .protocol_error("tools/call", code=-32042)
                            .stdio_server(),
                        ),
                    ),
                    operation=CallTool(name="echo", arguments={}),
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
    adapter: TypeAdapter[DirectResult] = TypeAdapter(DirectResult)
    persisted_typed: DirectResult = adapter.validate_python(persisted_result)
    assert isinstance(persisted_typed, CallToolResult)
    assert persisted_typed.content[0]["text"] == "durable-value"
    assert persisted_typed.raw is None
    error_terminal = next(
        event
        for event in reversed(
            tuple(reopened.iter_events(error_result.snapshot.execution_id))
        )
        if event.kind.value == "execution.finished"
    )
    error_persisted = error_terminal.payload.get("direct_result")
    assert isinstance(error_persisted, Mapping)
    error_typed: DirectResult = adapter.validate_python(error_persisted)
    assert isinstance(error_typed, CallToolResult)
    assert error_typed.is_error is True
    assert error_typed.raw is None
    failed_terminal = next(
        event
        for event in reversed(
            tuple(reopened.iter_events(failed_result.snapshot.execution_id))
        )
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
                DirectSpec(
                    servers=(ServerBinding(server=_stdio_server()),),
                    operation=Ping(),
                    workspace=WorkspacePolicy(
                        kind=WorkspaceKind.COPY, source=str(source)
                    ),
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
        assert (
            result.artifacts[0].sha256
            == hashlib.sha256(b"persisted-e2e-artifact").hexdigest()
        )
    finally:
        reopened.close()


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.name == "nt", reason="process-group liveness assertion is POSIX-specific"
)
@pytest.mark.parametrize("iteration", range(10))
async def test_separate_worker_cancel_interrupts_owned_stdio_process(
    tmp_path: Path, iteration: int
) -> None:
    pid_file = tmp_path / f"mcp-{iteration}.pid"
    database = (tmp_path / f"cancel-{iteration}.sqlite").resolve()
    server = _stdio_server(
        _HANGING_SERVER,
        environment={"M3_E2E_PID_FILE": str(pid_file)},
    )

    with _persistent_worker(database):
        producer = AsyncMCPTestKit(
            store=SQLiteExecutionStore(database), embedded_worker=False, env={}
        )
        try:
            handle = producer.submit(
                DirectSpec(
                    servers=(ServerBinding(server=server),),
                    operation=Ping(),
                    timeout_seconds=30,
                )
            )
            await asyncio.to_thread(_wait_for_file, pid_file)
            pid = int(pid_file.read_text(encoding="utf-8"))
            cancel_started = time.monotonic()
            await handle.cancel()
            result = await asyncio.wait_for(handle.result(timeout=5), timeout=5)
            assert time.monotonic() - cancel_started < 5
            deadline = time.monotonic() + 3
            while _process_exists(pid) and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
        finally:
            await producer.aclose()

    assert result.snapshot.outcome is ExecutionOutcome.CANCELLED
    assert result.trace is not None
    assert result.trace.completeness == "partial"
    assert not _process_exists(pid)

    reopened = SQLiteExecutionStore(database)
    try:
        snapshot = reopened.get_snapshot(result.snapshot.execution_id)
        assert snapshot is not None and snapshot.outcome is ExecutionOutcome.CANCELLED
        events = tuple(reopened.iter_events(result.snapshot.execution_id))
        assert sum(event.kind.value == "execution.finished" for event in events) == 1
        terminal = events[-1]
        assert terminal.payload["outcome"] == ExecutionOutcome.CANCELLED.value
        assert terminal.payload["completeness"] == "partial"
        command = reopened.get_command(f"command-{result.snapshot.execution_id.root}")
        assert command is not None and command.status == "cancelled"
        assert reopened.claim_next("post-cancel-probe") is None
        reopened_trace = ExecutionTraceRecorder(
            reopened, result.snapshot.execution_id
        ).finalize(ExecutionOutcome.CANCELLED)
        assert reopened_trace.completeness == "partial"
    finally:
        reopened.close()


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.name == "nt", reason="process-group liveness assertion is POSIX-specific"
)
@pytest.mark.parametrize("iteration", range(2))
async def test_separate_worker_cancel_interrupts_owned_acp_and_mcp_processes(
    tmp_path: Path, iteration: int
) -> None:
    """Durable cancellation reaps a real ACP owner and its real MCP child."""

    acp_pid_file = tmp_path / f"acp-{iteration}.pid"
    mcp_pid_file = tmp_path / f"mcp-acp-{iteration}.pid"
    database = (tmp_path / f"cancel-acp-{iteration}.sqlite").resolve()
    spec = _scenario_acp_cancel_spec(
        acp_pid_marker=acp_pid_file,
        mcp_pid_marker=mcp_pid_file,
    )
    acp_pid: int | None = None
    mcp_pid: int | None = None
    with _persistent_worker(database):
        producer = AsyncMCPTestKit(
            store=SQLiteExecutionStore(database), embedded_worker=False, env={}
        )
        try:
            handle = producer.submit(spec)
            await asyncio.wait_for(
                asyncio.to_thread(_wait_for_file, acp_pid_file),
                timeout=_MCP_MARKER_TIMEOUT + 5,
            )
            await asyncio.wait_for(
                asyncio.to_thread(_wait_for_file, mcp_pid_file),
                timeout=_MCP_MARKER_TIMEOUT + 5,
            )
            acp_pid = int(acp_pid_file.read_text(encoding="utf-8"))
            mcp_pid = int(mcp_pid_file.read_text(encoding="utf-8"))
            cancel_started = time.monotonic()
            await handle.cancel()
            result = await asyncio.wait_for(handle.result(timeout=8), timeout=8)
            assert time.monotonic() - cancel_started < 8
        finally:
            await producer.aclose()

    assert acp_pid is not None and not _process_exists(acp_pid)
    assert mcp_pid is not None and not _process_exists(mcp_pid)
    assert result.snapshot.outcome is ExecutionOutcome.CANCELLED
    assert result.trace is not None and result.trace.completeness == "partial"

    reopened = SQLiteExecutionStore(database)
    try:
        snapshot = reopened.get_snapshot(result.snapshot.execution_id)
        assert snapshot is not None and snapshot.outcome is ExecutionOutcome.CANCELLED
        events = tuple(reopened.iter_events(result.snapshot.execution_id))
        assert sum(event.kind.value == "execution.finished" for event in events) == 1
        assert events[-1].payload["completeness"] == "partial"
        command = reopened.get_command(f"command-{result.snapshot.execution_id.root}")
        assert command is not None and command.status == "cancelled"
        assert reopened.claim_next("post-cancel-acp-probe") is None
        reopened_trace = ExecutionTraceRecorder(
            reopened, result.snapshot.execution_id
        ).finalize(ExecutionOutcome.CANCELLED)
        assert reopened_trace.completeness == "partial"
    finally:
        reopened.close()
