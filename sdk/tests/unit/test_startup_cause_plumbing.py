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
from m3.harness.native import ProcessOwner, launch_secrets
from m3.harness.observations import startup_evidence
from m3.server_group import HarnessServerConfig, ServerGroupSnapshot
from m3.transport.capture_proxy import McpCaptureManager
from m3.types import (
    ACPAgent,
    ErrorCode,
    ErrorInfo,
    EventKind,
    LifecyclePhase,
    ServerBinding,
    StdioServer,
    TransportKind,
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
    # Startup evidence belongs to the startup phase, not to a turn that never
    # ran: the result has no turns and the trace invents none.
    assert process[0].turn_id is None
    assert process[0].lifecycle_phase is LifecyclePhase.STARTUP
    assert session.result.turns == ()
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


def test_launch_secrets_without_capture_take_sensitive_literals() -> None:
    spec = _spec()
    local = HarnessServerConfig(
        key="fs",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="fs",
        command="server",
        environment={"API_TOKEN": "literal-token-1", "NODE_ENV": "production"},
        headers={"Authorization": "Bearer literal-header"},
    )
    secrets = launch_secrets(
        HarnessLaunch(spec, ServerGroupSnapshot(), (local,), spec.tool_policy)
    )
    assert secrets == {"literal-token-1", "Bearer literal-header"}


@pytest.mark.asyncio
async def test_startup_stderr_evidence_redacts_what_the_capture_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The adapter only sees configurations the capture manager rewrote: the
    # stdio environment is gone and the URL is the loopback proxy's, which
    # still carries the original path. Values the manager resolved or
    # classified are redacted wherever the harness repeats them; the URL
    # keeps only its origin, so a literal path key is hidden too.
    monkeypatch.setenv("ZAPIER_MCP_KEY_TEST", "zapResolved-77aa")
    manager = McpCaptureManager(tmp_path, trusted_private_keys={"remote", "local"})
    try:
        instrumented = await manager.instrument(
            (
                HarnessServerConfig(
                    key="remote",
                    transport=TransportKind.STREAMABLE_HTTP,
                    required=True,
                    available=True,
                    connection_id="remote",
                    endpoint=(
                        "http://127.0.0.1:1/api/mcp/s/ZapLiteralKey9137/"
                        "${ZAPIER_MCP_KEY_TEST}/mcp"
                    ),
                ),
                HarnessServerConfig(
                    key="local",
                    transport=TransportKind.STDIO,
                    required=True,
                    available=True,
                    connection_id="local",
                    command=sys.executable,
                    environment={
                        "API_TOKEN": "ghp_patvalue123",
                        "NODE_ENV": "production",
                    },
                ),
            )
        )
        assert "ZapLiteralKey9137" in instrumented[0].endpoint
        assert instrumented[1].environment == {}
        adapter = _FailingHandshake(
            "import sys; sys.stderr.write("
            f"'error sending request for url ({instrumented[0].endpoint}); "
            "key zapResolved-77aa rejected; pat ghp_patvalue123; "
            "Authorization: Bearer unknownXYZ; env production\\n'); sys.exit(3)"
        )
        spec = _spec()
        launch = HarnessLaunch(
            spec,
            ServerGroupSnapshot(),
            instrumented,
            spec.tool_policy,
            capture=manager,
        )
        with pytest.raises(HarnessStartupError) as exc_info:
            await adapter.open(launch)
    finally:
        await manager.close()
    evidence = startup_evidence(exc_info.value)
    assert evidence is not None
    stderr = evidence.observations[0].stderr
    assert stderr is not None
    for secret in ("ZapLiteralKey9137", "zapResolved-77aa", "ghp_patvalue123"):
        assert secret not in stderr
    assert stderr.startswith("error sending request for url (http://127.0.0.1:")
    assert stderr.endswith(
        "/…); key [REDACTED] rejected; pat [REDACTED]; "
        "Authorization: Bearer [REDACTED]; env production\n"
    )


@pytest.mark.asyncio
async def test_turn_process_observation_keeps_whole_lines_of_a_stopped_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Long ambient credentials (an SSH agent socket, a JWT) must not shorten
    # the evidence.
    monkeypatch.setenv("AUTH_TOKEN", "ambient-" + "x" * 120)
    adapter = NativeRPCAdapter(executable=sys.executable)
    adapter._runtime_secrets = {"adapter-secret-canary"}
    retained = bytearray(b"fatal: adapter-secret-canary rejected")
    drain: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
    process = SimpleNamespace(
        owner=SimpleNamespace(stderr_task=drain, stderr_retained=retained)
    )

    # A running child's drain is never awaited or read.
    assert adapter._stderr_fields(process) == {"stderr_state": "unavailable"}  # type: ignore[arg-type]

    drain.set_result(bytes(retained))
    assert adapter._stderr_fields(process) == {  # type: ignore[arg-type]
        "stderr": "fatal: [REDACTED] rejected",
        "stderr_state": "observed",
    }

    # A drain cancelled because a detached descendant held the pipe can stop
    # inside a write: its unfinished last line, which could hold the start
    # of a secret, is dropped.
    cancelled: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
    cancelled.cancel()
    owner = SimpleNamespace(
        stderr_task=cancelled,
        stderr_retained=bytearray(b"fatal: auth failed\nkey adapter-secret-can"),
    )
    assert adapter._stderr_fields(SimpleNamespace(owner=owner)) == {  # type: ignore[arg-type]
        "stderr": "fatal: auth failed\n",
        "stderr_state": "observed",
    }

    # Long stderr keeps its end, in whole lines: the cut first line could
    # hold the end of a secret.
    long = b"x" * 40_000 + b"cret-canary\n" + b"line\n" * 3000 + b"fatal: last\n"
    owner = SimpleNamespace(stderr_task=drain, stderr_retained=bytearray(long))
    stderr = adapter._stderr_fields(SimpleNamespace(owner=owner))["stderr"]  # type: ignore[arg-type]
    assert stderr.startswith("line\n") and stderr.endswith("fatal: last\n")
    assert "cret-canary" not in stderr
