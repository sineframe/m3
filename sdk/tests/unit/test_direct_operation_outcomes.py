"""Bounded direct-operation terminal outcome matrix."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from m3.errors import ProtocolError
from m3.execution_runtime import AsyncExecutionController, AsyncExecutionHandle
from m3.storage import SQLiteExecutionStore
from m3.trace.redaction import RedactionConfig
from m3.types import (
    CallTool,
    CallToolResult,
    DirectSpec,
    ErrorCode,
    ExecutionOutcome,
    Ping,
    PingResult,
    ServerBinding,
    StdioServer,
)
from m3.workspace import WorkspaceError, WorkspaceManager


def _spec(
    *,
    operation: object = None,
    timeout: float | None = None,
    validate_schemas: bool = False,
) -> DirectSpec:
    return DirectSpec(
        servers=(
            ServerBinding(
                server=StdioServer(name="outcome", command="unused"), alias="outcome"
            ),
        ),
        operation=operation or Ping(server="outcome"),
        timeout_seconds=timeout,
        validate_schemas=validate_schemas,
    )


class _OutcomeClient:
    def __init__(self, mode: str = "success") -> None:
        self.mode = mode
        self.started = asyncio.Event()
        self.closed = False

    async def __aenter__(self) -> _OutcomeClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.closed = True

    async def ping(self) -> object:
        return SimpleNamespace(raw=SimpleNamespace(method="ping"), result_type="pong")

    async def call_tool(self, _name: str, _arguments: object) -> object:
        self.started.set()
        if self.mode == "blocked":
            await asyncio.Event().wait()
        if self.mode == "protocol":
            raise ProtocolError(
                "provider-secret",
                details={
                    "operation": "tools/call",
                    "phase": "protocol",
                    "error_kind": "client_exception",
                    "exception_type": "TypeError",
                    "provider_message": "provider-secret",
                },
            )
        if self.mode == "protocol_rejection":
            raise ProtocolError(
                "provider-secret",
                details={
                    "operation": "roots/list",
                    "phase": "protocol",
                    "error_kind": "mcp_protocol",
                    "protocol_code": -32602,
                    "provider_message": "provider-secret",
                },
            )
        return SimpleNamespace(
            raw=SimpleNamespace(method="tools/call", secret="direct-secret")
            if self.mode == "secret"
            else SimpleNamespace(method="tools/call"),
            content=({"type": "text", "text": "direct-secret"},)
            if self.mode == "secret"
            else ({"type": "text", "text": "ok"},),
            structured_content={"secret": "direct-secret"}
            if self.mode == "secret"
            else {"value": 1},
            is_error=self.mode == "is_error",
        )


class _OutcomeKit:
    def __init__(self, client: _OutcomeClient) -> None:
        self.client = client
        self.direct_options: dict[str, object] = {}

    def direct(self, _server: object, **options: object) -> _OutcomeClient:
        self.direct_options = options
        return self.client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_outcome", "expected_code", "is_error"),
    [
        ("success", ExecutionOutcome.COMPLETED, None, False),
        ("is_error", ExecutionOutcome.COMPLETED, None, True),
    ],
)
async def test_direct_call_tool_terminal_outcomes(
    mode: str,
    expected_outcome: ExecutionOutcome,
    expected_code: ErrorCode | None,
    is_error: bool,
) -> None:
    client = _OutcomeClient(mode)
    kit = _OutcomeKit(client)
    controller = AsyncExecutionController(kit)
    result = await controller.run(
        _spec(
            operation=CallTool(server="outcome", name="echo", arguments={}),
            timeout=2,
        )
    )

    assert result.snapshot.outcome is expected_outcome
    assert result.trace is not None
    if expected_code is None:
        assert isinstance(result.direct_result, CallToolResult)
        assert result.direct_result.is_error is is_error
    else:
        assert result.direct_result is None
        assert result.error is not None
        assert result.error.code is expected_code
    await controller.close()


@pytest.mark.asyncio
async def test_active_direct_operation_cancellation_is_terminal() -> None:
    client = _OutcomeClient("blocked")
    controller = AsyncExecutionController(_OutcomeKit(client))
    handle = controller.submit(
        _spec(operation=CallTool(server="outcome", name="echo", arguments={}))
    )
    await client.started.wait()

    await handle.cancel()
    result = await handle.result(timeout=2)

    assert result.snapshot.outcome is ExecutionOutcome.CANCELLED
    assert result.error is not None and result.error.code is ErrorCode.CANCELLED
    assert result.direct_result is None
    assert result.trace is not None
    assert client.closed is True
    await controller.close()


@pytest.mark.asyncio
async def test_protocol_failure_retains_safe_diagnostics_in_execution_result() -> None:
    controller = AsyncExecutionController(_OutcomeKit(_OutcomeClient("protocol")))
    handle = controller.submit(
        _spec(operation=CallTool(server="outcome", name="create_element", arguments={}))
    )
    result = await handle.result()

    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    assert result.direct_result is None
    assert result.error is not None
    assert result.error.code is ErrorCode.PROTOCOL_ERROR
    assert result.error.message == "MCP client operation failed"
    assert result.error.details == {
        "operation": "tools/call",
        "phase": "protocol",
        "error_kind": "client_exception",
        "exception_type": "TypeError",
    }
    assert result.trace is not None
    assert result.trace.events[-1].payload["error"] == result.error.model_dump(
        mode="json"
    )
    report = handle._store.get_report(result.snapshot.execution_id)
    assert report is not None
    assert report.error == result.error
    assert "provider-secret" not in repr(result.error)
    await controller.close()


@pytest.mark.asyncio
async def test_protocol_diagnostics_survive_sqlite_report_and_handle_rehydration(
    tmp_path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "protocol-error.sqlite")
    operation = _spec(
        operation=CallTool(
            server="outcome", name="create_element", arguments={"points": []}
        )
    )
    owner_controller = AsyncExecutionController(
        _OutcomeKit(_OutcomeClient("protocol")), store=store, worker=False
    )
    owner = AsyncExecutionHandle(
        owner_controller,
        operation,
        store=store,
        persistent=True,
    )
    await owner._start_from_worker()
    live = await owner.result(timeout=2)

    report = store.get_report(live.snapshot.execution_id)
    assert live.error is not None
    assert report is not None
    assert report.error == live.error

    reader_controller = AsyncExecutionController(
        _OutcomeKit(_OutcomeClient()), store=store, worker=False
    )
    reader = AsyncExecutionHandle(
        reader_controller,
        operation,
        store=store,
        persistent=True,
        execution_id=live.snapshot.execution_id,
    )
    restored = await reader.result(timeout=2)
    assert restored.error == live.error
    assert restored.error.details["exception_type"] == "TypeError"

    await owner_controller.close()
    await reader_controller.close()
    store.close()


@pytest.mark.asyncio
async def test_remote_protocol_rejection_is_distinct_from_client_failure() -> None:
    controller = AsyncExecutionController(
        _OutcomeKit(_OutcomeClient("protocol_rejection"))
    )
    result = await controller.run(
        _spec(operation=CallTool(server="outcome", name="create_element", arguments={}))
    )

    assert result.error is not None
    assert result.error.message == "MCP protocol operation failed"
    assert result.error.details == {
        "operation": "roots/list",
        "phase": "protocol",
        "error_kind": "mcp_protocol",
        "protocol_code": -32602,
    }
    assert "provider-secret" not in repr(result.error)
    await controller.close()


@pytest.mark.asyncio
async def test_terminal_event_persists_redacted_typed_direct_result_without_raw() -> (
    None
):
    controller = AsyncExecutionController(
        _OutcomeKit(_OutcomeClient("secret")),
        redaction_config=RedactionConfig(
            secrets=frozenset({"direct-secret"}), include_environment=False
        ),
    )
    result = await controller.run(
        _spec(operation=CallTool(server="outcome", name="echo", arguments={}))
    )

    assert isinstance(result.direct_result, CallToolResult)
    assert result.direct_result.raw is not None
    assert result.direct_result.raw.secret == "direct-secret"
    assert result.trace is not None
    terminal = result.trace.events[-1]
    persisted = terminal.payload["direct_result"]
    assert "raw" not in persisted
    assert persisted["content"][0]["text"] == "[REDACTED]"
    assert persisted["structured_content"]["secret"] == "[REDACTED]"
    assert "direct-secret" not in json.dumps(terminal.model_dump(mode="json"))
    await controller.close()


@pytest.mark.asyncio
async def test_workspace_cleanup_failure_keeps_direct_result_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_cleanup(_manager: WorkspaceManager) -> None:
        raise WorkspaceError("cleanup failed")

    monkeypatch.setattr(WorkspaceManager, "cleanup", fail_cleanup)
    client = _OutcomeClient("success")
    controller = AsyncExecutionController(_OutcomeKit(client))
    result = await controller.run(_spec())

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert isinstance(result.direct_result, PingResult)
    assert result.error is not None and result.error.code is ErrorCode.CLEANUP_FAILED
    assert result.trace is not None
    assert result.trace.completeness == "partial"
    assert "cleanup_failed" in result.trace.limitations
    await controller.close()
