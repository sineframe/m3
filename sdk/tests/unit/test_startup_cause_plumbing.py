"""Native startup causes survive the shared plumbing without leaking secrets."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from m3._types.specs import AgentSpec
from m3.agent_session import (
    _STARTUP_CAUSE_LIMIT,
    AsyncAgentSession,
    _startup_failure_cause,
    _terminal_turn_error,
)
from m3.errors import MCPError, TransportError, UnsupportedFeature
from m3.execution_runtime import _error_info
from m3.harness._rpc_native import NativeRPCAdapter
from m3.harness.contracts import HarnessLaunch, HarnessStartupError
from m3.harness.native import ProcessOwner
from m3.server_group import ServerGroupSnapshot
from m3.types import (
    ACPAgent,
    ErrorCode,
    ErrorInfo,
    EventKind,
    ServerBinding,
    StdioServer,
)


def _spec() -> AgentSpec:
    return AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="memory", command="echo")),),
        harness=ACPAgent(model="fixture"),
    )


def test_long_causes_are_cut_with_a_visible_marker() -> None:
    cause = _startup_failure_cause(HarnessStartupError("word " * 200))
    assert len(cause) == _STARTUP_CAUSE_LIMIT
    assert cause.endswith("…")

    error = _terminal_turn_error(
        ErrorInfo(
            code=ErrorCode.TRANSPORT_ERROR,
            message="m" * 1000,
            details={"detail": "d" * 1000},
        )
    )
    assert error is not None
    assert error.details["cause"].endswith("…")
    assert len(error.details["cause"]) == _STARTUP_CAUSE_LIMIT
    assert error.details["detail"].endswith("…")
    assert len(error.details["detail"]) == _STARTUP_CAUSE_LIMIT

    exact = "x" * _STARTUP_CAUSE_LIMIT
    assert _startup_failure_cause(HarnessStartupError(exact)) == exact


@pytest.mark.asyncio
async def test_unspawnable_executable_keeps_type_and_errno(tmp_path: Path) -> None:
    missing = tmp_path / "secret-dir" / "no-such-harness"
    owner = ProcessOwner(tmp_path)
    with pytest.raises(HarnessStartupError) as exc_info:
        await owner.spawn([str(missing)], {})
    assert str(exc_info.value) == (
        "harness process could not start: FileNotFoundError (ENOENT)"
    )
    # strerror and filename text can embed paths; neither is surfaced.
    assert "secret-dir" not in str(exc_info.value)


def test_execution_error_keeps_the_bounded_startup_cause() -> None:
    harness = _error_info(HarnessStartupError("Codex thread/start failed (-32603)"))
    assert harness.message == "requested harness is unavailable"
    assert harness.details == {"cause": "Codex thread/start failed (-32603)"}

    unsupported = _error_info(UnsupportedFeature("policy " + "p" * 1000))
    assert unsupported.details["cause"].endswith("…")
    assert len(unsupported.details["cause"]) == _STARTUP_CAUSE_LIMIT

    # The session's startup error already carries a sanitized cause.
    startup = _error_info(
        TransportError(
            "agent session startup failed: x",
            details={"cause": "x", "evidence_observation": "obs-1", "other": "y"},
        )
    )
    assert startup.code is ErrorCode.TRANSPORT_ERROR
    assert startup.details == {"cause": "x", "evidence_observation": "obs-1"}

    # Other transport errors are not guaranteed to be sanitized.
    assert _error_info(TransportError("token=hunter2")).details == {}
    assert _error_info(MCPError("token=hunter2")).details == {}


class _FailingHandshake(NativeRPCAdapter):
    """Spawns a child that writes stderr and exits, then fails the handshake."""

    harness_kind = "fake-native"

    def __init__(self, script: str) -> None:
        super().__init__(executable=sys.executable)
        self._script = script
        self._runtime_secrets = {"adapter-secret-canary"}

    def process_argv(self, launch: Any) -> tuple[str, ...]:
        return ("-c", self._script)

    async def initialize(self, process: Any, launch: Any) -> str:
        # Wait for the child's exit so its stderr is complete: no timing luck.
        await process.owner.process.wait()
        raise HarnessStartupError("fake handshake failed")


@pytest.mark.asyncio
async def test_failed_startup_references_stderr_evidence_not_its_text() -> None:
    adapter = _FailingHandshake(
        "import sys; sys.stderr.write('boom adapter-secret-canary\\n'); sys.exit(3)"
    )
    session = AsyncAgentSession(_spec(), adapter)
    with pytest.raises(TransportError) as exc_info:
        await session.__aenter__()

    error = session.result.error
    assert error is not None
    assert error.details["cause"] == "fake handshake failed"
    reference = error.details["evidence_observation"]
    assert reference == "fake-native-process-startup-failed"
    assert exc_info.value.details["evidence_observation"] == reference

    events = session._trace_recorder.events()
    process = [e for e in events if e.payload.get("observation_id") == reference]
    assert [e.kind for e in process] == [EventKind.PROCESS_EXITED]
    assert process[0].payload["exit_code"] == 3
    assert process[0].payload["stderr"].startswith("boom ")
    diagnostics = [
        e.payload for e in events if e.payload.get("code") == "startup_failed"
    ]
    assert [d["evidence_observation"] for d in diagnostics] == [reference]

    # The stderr text and the adapter's secret appear on no surface.
    surfaces = repr((error, exc_info.value.details, diagnostics))
    assert "boom" not in surfaces
    assert "adapter-secret-canary" not in repr(events)


@pytest.mark.asyncio
async def test_startup_evidence_without_a_child_is_absent() -> None:
    class _Unspawnable(_FailingHandshake):
        async def preflight(self, launch: Any) -> Any:
            return SimpleNamespace(ready=True)

    adapter = _Unspawnable("pass")
    adapter.executable = "/nonexistent/m3-no-such-harness"
    session = AsyncAgentSession(_spec(), adapter)
    with pytest.raises(TransportError):
        await session.__aenter__()
    error = session.result.error
    assert error is not None
    assert "could not start: FileNotFoundError (ENOENT)" in error.details["cause"]
    assert "evidence_observation" not in error.details


@pytest.mark.asyncio
async def test_unclaimed_startup_evidence_is_not_reused_by_a_later_open() -> None:
    adapter = _FailingHandshake("import sys; sys.exit(3)")
    spec = _spec()
    launch = HarnessLaunch(spec, ServerGroupSnapshot(), (), spec.tool_policy)
    with pytest.raises(HarnessStartupError):
        await adapter.open(launch)
    # Nobody claimed that failure's evidence; a later failed open that never
    # spawns a child must not hand it over as its own.
    adapter.executable = "/nonexistent/m3-no-such-harness"
    with pytest.raises(HarnessStartupError):
        await adapter.open(launch)
    assert adapter.take_startup_evidence() is None


@pytest.mark.asyncio
async def test_turn_process_observation_reads_only_a_finished_drain() -> None:
    adapter = NativeRPCAdapter(executable=sys.executable)
    adapter._runtime_secrets = {"adapter-secret-canary"}
    drain: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
    process = SimpleNamespace(owner=SimpleNamespace(stderr_task=drain))

    # A running child's drain is never awaited or read.
    assert adapter._stderr_fields(process) == {"stderr_state": "unavailable"}  # type: ignore[arg-type]

    drain.set_result(b"fatal: adapter-secret-canary rejected")
    fields = adapter._stderr_fields(process)  # type: ignore[arg-type]
    assert fields["stderr_state"] == "observed"
    assert "fatal:" in fields["stderr"]
    assert "adapter-secret-canary" not in fields["stderr"]

    failed: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
    failed.set_exception(RuntimeError("drain broke"))
    assert adapter._stderr_fields(  # type: ignore[arg-type]
        SimpleNamespace(owner=SimpleNamespace(stderr_task=failed))
    ) == {"stderr_state": "unavailable"}
