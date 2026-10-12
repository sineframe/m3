"""Native diagnostics keep their cause without copying launch secrets."""

import asyncio
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from m3._types.specs import AgentSpec
from m3.agent_session import AdapterTurn, AsyncAgentSession
from m3.execution_trace import ExecutionTraceRecorder
from m3.harness._native_errors import NativeHarnessStartupError, native_error_info
from m3.harness._private_policy import PrivateToolCalls
from m3.harness.claude import ClaudeCodeHarnessAdapter
from m3.harness.codex import CodexHarnessAdapter
from m3.harness.contracts import HarnessLaunch, HarnessTurnRequest
from m3.harness.opencode import OpenCodeHarnessAdapter
from m3.policy import ToolPolicyEvidence
from m3.server_group import HarnessServerConfig, ServerGroupSnapshot
from m3.storage import SQLiteExecutionStore
from m3.trace.redaction import RedactionConfig, RedactionError
from m3.transport.capture_proxy import McpCaptureManager
from m3.types import (
    ClaudeCode,
    Codex,
    EventKind,
    RestrictiveToolPolicy,
    ServerBinding,
    StdioServer,
    TransportKind,
    TurnOutcome,
)


def _launch() -> HarnessLaunch:
    return HarnessLaunch(
        AgentSpec(
            harness=Codex(model="fixture"),
            servers=(
                ServerBinding(server=StdioServer(name="slow", command="fixture")),
            ),
        ),
        ServerGroupSnapshot(),
        (
            HarnessServerConfig(
                key="slow",
                transport=TransportKind.STDIO,
                required=True,
                available=True,
                connection_id="slow-connection",
                command="fixture",
                environment={"OPAQUE": "server-private-value"},
                headers={"X-Custom": "header-private-value"},
            ),
        ),
        RestrictiveToolPolicy(),
    )


def test_codex_startup_retains_redacted_native_cause(tmp_path: Path) -> None:
    adapter = CodexHarnessAdapter(environment={"OPAQUE": "adapter-private-value"})
    launch = _launch()
    adapter.environment_for_launch(launch, tmp_path)
    error = adapter._thread_start_failure(
        {
            "code": -32603,
            "message": "required MCP servers failed to initialize: slow: "
            "handshake rejected server-private-value header-private-value adapter-private-value",
        },
        launch,
    )
    assert "handshake rejected" in str(error)
    assert error.details["native_code"] == -32603
    assert error.details["phase"] == "thread/start"
    assert "private-value" not in repr(error.details)
    assert "private-value" not in str(error)


@pytest.mark.asyncio
async def test_codex_turn_retains_pinned_redacted_native_cause() -> None:
    adapter = CodexHarnessAdapter()
    adapter._native_error_redaction = RedactionConfig(
        secrets=frozenset({"pinned-secret"}), include_environment=False
    )
    adapter.consume_frame(
        {
            "method": "turn/completed",
            "params": {
                "turn": {
                    "status": "failed",
                    "error": {"message": "handshake denied pinned-secret", "code": 42},
                }
            },
        },
        1,
        datetime.now(timezone.utc),
        0.0,
        [],
    )
    assert adapter._terminal_error is not None
    assert "handshake denied" in adapter._terminal_error.message
    assert "pinned-secret" not in adapter._terminal_error.model_dump_json()
    assert adapter._terminal_error.details["native_code"] == 42


@pytest.mark.asyncio
async def test_opencode_unknown_error_retains_redacted_cause() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json={
                "info": {
                    "error": {
                        "name": "UnknownError",
                        "data": {"message": "authentication refused pinned-secret"},
                    }
                },
                "parts": [],
            },
        )

    adapter = OpenCodeHarnessAdapter()
    adapter._native_error_redaction = RedactionConfig(
        secrets=frozenset({"pinned-secret"}), include_environment=False
    )
    adapter._launch = SimpleNamespace(  # type: ignore[assignment]
        spec=SimpleNamespace(harness=SimpleNamespace(model="opencode/fixture")),
        configurations=(),
    )
    async with httpx.AsyncClient(
        base_url="http://fixture", transport=httpx.MockTransport(respond)
    ) as client:
        adapter._client = client
        result = await adapter._send(
            HarnessTurnRequest.from_message("fixture"), 1, "fixture-session"
        )
    assert result.error is not None
    assert "authentication refused" in result.error.message
    assert "pinned-secret" not in result.error.model_dump_json()
    assert result.error.details["native_code"] == "UnknownError"
    assert result.turn_evidence is not None
    assert "pinned-secret" not in repr(result.turn_evidence)


@pytest.mark.parametrize("secret", ["boundary-secret", "a" * 200])
def test_redaction_precedes_native_diagnostic_truncation(secret: str) -> None:
    # The original credential crosses the 4096-character output boundary.
    error = native_error_info(
        {"message": "x" * 4090 + secret + " remaining diagnosis", "code": 41},
        summary="native failed",
        harness="fixture",
        phase="initialize",
        redaction=RedactionConfig(
            secrets=frozenset({secret}), include_environment=False
        ),
    )
    assert error.details["native_message_truncated"] is True
    assert len(error.details["native_message"]) == 4096
    assert secret[:6] not in error.model_dump_json()
    assert error.details["native_code"] == 41
    assert len(error.message) <= 4096
    assert error.details["message_truncated"] is True


def test_missing_registry_does_not_publish_native_text_or_string_code() -> None:
    error = native_error_info(
        {"message": "unregistered-secret", "code": "unregistered-secret"},
        summary="native failed",
        harness="fixture",
        phase="initialize",
        redaction=None,
    )
    assert error.message == "native failed"
    assert "unregistered-secret" not in error.model_dump_json()
    assert error.details["native_diagnostic"] == "unavailable_registry"
    assert "native_code" not in error.details


def test_oversized_diagnostics_fail_closed_with_value_free_evidence() -> None:
    error = native_error_info(
        {"message": "unknown-secret" * 65536, "code": 2**10000},
        summary="native failed",
        harness="fixture",
        phase="initialize",
        redaction=RedactionConfig(include_environment=False),
    )
    assert error.message == "native failed"
    assert error.details["native_diagnostic"] == "oversized"
    assert "native_code" not in error.details


def test_launch_registry_stays_pinned_after_environment_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "absent-login"))
    adapter = CodexHarnessAdapter(environment={"OPAQUE": "launch-secret"})
    launch = _launch()
    adapter.environment_for_launch(launch, tmp_path)
    adapter.environment["OPAQUE"] = "future-secret"
    monkeypatch.setenv("OPAQUE", "ambient-future-secret")
    error = adapter._thread_start_failure(
        {"message": "handshake rejected launch-secret"}, launch
    )
    assert error.details["native_message"] == "handshake rejected [REDACTED]"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 500])
async def test_opencode_http_error_preserves_status_and_safe_native_body(
    status: int,
) -> None:
    adapter = OpenCodeHarnessAdapter()
    adapter._native_error_redaction = RedactionConfig(
        secrets=frozenset({"pinned-secret"}), include_environment=False
    )
    adapter._launch = SimpleNamespace(  # type: ignore[assignment]
        spec=SimpleNamespace(harness=SimpleNamespace(model="opencode/fixture"))
    )
    async with httpx.AsyncClient(
        base_url="http://fixture",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status,
                json={
                    "error": {
                        "code": "denied",
                        "message": "provider refused pinned-secret",
                    }
                },
            )
        ),
    ) as client:
        adapter._client = client
        result = await adapter._send(
            HarnessTurnRequest.from_message("fixture"), 1, "fixture-session"
        )
    assert result.error is not None
    assert result.error.details["http_status"] == status
    assert result.error.details["native_code"] == "denied"
    assert result.error.details["native_message"] == "provider refused [REDACTED]"
    assert "pinned-secret" not in repr(result)


class _ProjectedFailureHarness:
    supported_content_kinds = frozenset({"text"})

    def __init__(self, *, startup: bool = False, terminal: bool = True) -> None:
        self.startup = startup
        self.terminal = terminal
        self.error = native_error_info(
            {"message": "handshake rejected pinned-secret", "code": -32000},
            summary="native failed",
            harness="fixture",
            phase="initialize" if startup else "turn/message",
            redaction=RedactionConfig(
                secrets=frozenset({"pinned-secret"}), include_environment=False
            ),
        )

    async def start(self, spec: AgentSpec) -> None:
        if self.startup:
            raise NativeHarnessStartupError(self.error)

    async def send(self, message, *, timeout=None, metadata=None) -> AdapterTurn:
        return AdapterTurn(
            error=self.error, terminal=self.terminal, outcome=TurnOutcome.FAILED
        )

    async def close(self) -> None:
        pass

    async def cancel(self) -> None:
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [False, True])
async def test_projected_cause_survives_turn_events_and_sqlite_roundtrip(
    tmp_path: Path, terminal: bool
) -> None:
    database = tmp_path / "errors.sqlite"
    store = SQLiteExecutionStore(database)
    recorder = ExecutionTraceRecorder(store, "native-error-execution")
    adapter = _ProjectedFailureHarness(terminal=terminal)
    async with AsyncAgentSession(
        _launch().spec, adapter, trace_recorder=recorder
    ) as session:
        turn = await session.send("fixture")
        assert turn.error == adapter.error
        if terminal:
            assert session.result.error is not None
            assert session.result.error.message == adapter.error.message
            assert session.result.error.details == adapter.error.details
    events = recorder.events()
    finished = [
        event
        for event in events
        if event.kind is EventKind.TURN_STATE_CHANGED and "error" in event.payload
    ]
    assert len(finished) == 1
    assert finished[0].payload["error"]["details"]["native_code"] == -32000
    assert "pinned-secret" not in repr(events)
    store.close()
    reopened = SQLiteExecutionStore(database)
    try:
        stored = reopened.turns("native-error-execution")
        assert len(stored) == 1
        assert stored[0][1] is not None
        assert stored[0][1].error is not None
        assert stored[0][1].error.message == adapter.error.message
        assert stored[0][1].error.details == adapter.error.details
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_projected_startup_cause_survives_exception_result_and_diagnostic() -> (
    None
):
    adapter = _ProjectedFailureHarness(startup=True)
    session = AsyncAgentSession(_launch().spec, adapter)
    await session.__aenter__()
    turn = await session.send("start native harness")
    assert turn.error is not None
    assert turn.error.details["native_code"] == -32000
    assert turn.error.details["phase"] == "initialize"
    await session.aclose()
    assert session.result.error is not None
    assert (
        session.result.error.details["native_message"]
        == "handshake rejected [REDACTED]"
    )
    diagnostics = [
        event
        for event in session._trace_recorder.events()
        if event.kind is EventKind.DIAGNOSTIC
        and event.payload.get("code") == "startup_failed"
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].payload["details"]["native_code"] == -32000
    assert "pinned-secret" not in repr(turn.error.details)
    assert "pinned-secret" not in repr(diagnostics)


@pytest.mark.asyncio
async def test_long_projected_startup_cause_stays_inside_public_message_limit() -> None:
    adapter = _ProjectedFailureHarness(startup=True)
    adapter.error = native_error_info(
        {"message": "x" * 6000},
        summary="native failed",
        harness="fixture",
        phase="initialize",
        redaction=RedactionConfig(include_environment=False),
    )
    session = AsyncAgentSession(_launch().spec, adapter)
    await session.__aenter__()
    turn = await session.send("start native harness")
    await session.aclose()
    assert turn.error is not None
    assert len(turn.error.message) <= 4096
    assert turn.error.details["cause_truncated"] is True
    assert session.result.error is not None
    assert len(session.result.error.message) <= 4096
    assert len(session.result.error.details["native_message"]) == 4096
    assert session.result.error.details["cause_truncated"] is True


def test_redaction_failure_drops_supplied_summary_and_details() -> None:
    error = native_error_info(
        {"message": "https://[invalid/known-secret"},
        summary="known-secret failure",
        harness="fixture",
        phase="initialize",
        redaction=RedactionConfig(
            secrets=frozenset({"known-secret"}), include_environment=False
        ),
        details={"reason": "known-secret", "payload": {"token": "known-secret"}},
    )
    assert error.details["native_diagnostic"] == "redaction_failed"
    assert error.message == "Native harness diagnostic unavailable"
    assert "known-secret" not in error.model_dump_json()
    assert "payload" not in error.details


@pytest.mark.asyncio
async def test_claude_error_result_retains_safe_native_cause_and_evidence(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "claude-error-fixture.py"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "if '--help' in sys.argv:\n"
        "    print('--input-format stream-json --output-format stream-json')\n"
        "    raise SystemExit(0)\n"
        "for line in sys.stdin:\n"
        "    print(json.dumps({'type': 'result', 'is_error': True, "
        "'subtype': 'error_during_execution', "
        "'errors': ['handshake rejected server-private-value header-private-value adapter-private-value']}), flush=True)\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    original = _launch()
    launch = HarnessLaunch(
        original.spec.model_copy(
            update={"harness": ClaudeCode(model="fixture", executable=str(executable))}
        ),
        original.servers,
        original.configurations,
        original.tool_policy,
    )
    adapter = ClaudeCodeHarnessAdapter(
        executable=str(executable), environment={"OPAQUE": "adapter-private-value"}
    )
    session = await adapter.open(launch)
    try:
        result = await session.send(HarnessTurnRequest.from_message("fixture"))
        assert result.error is not None
        assert "handshake rejected" in result.error.message
        assert result.error.details["native_code"] == "error_during_execution"
        assert "private-value" not in repr(result)
    finally:
        await adapter.close()


class _NativeFrames:
    owner = None

    def __init__(self, secret: str) -> None:
        self.frames = iter(
            (
                {"id": 2, "result": {"turn": {"id": secret}}},
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "type": "mcpToolCall",
                            "id": "call-1",
                            "server": "slow",
                            "tool": secret,
                            "arguments": {"value": secret},
                            "result": {"content": [{"type": "text", "text": secret}]},
                        }
                    },
                },
                {
                    "method": "item/agentMessage/delta",
                    "params": {"delta": "answer " + secret},
                },
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": secret,
                        "turn": {
                            "id": secret,
                            "status": "completed",
                        },
                    },
                },
            )
        )

    async def write(self, payload) -> None:
        pass

    async def next(self, timeout=None):
        return next(self.frames)


@pytest.mark.asyncio
async def test_secret_equal_to_thread_and_tool_identity_keeps_private_lifecycle() -> (
    None
):
    secret = "collision-native-id"
    adapter = CodexHarnessAdapter()
    adapter._thread_id = secret
    adapter._request_id = 1
    adapter._launch = _launch()
    adapter._native_error_redaction = RedactionConfig(
        secrets=frozenset({secret}), include_environment=False
    )
    result = await asyncio.wait_for(
        adapter._send(
            HarnessTurnRequest.from_message("fixture"), 1, _NativeFrames(secret)
        ),  # type: ignore[arg-type]
        timeout=1,
    )
    assert result.status == "completed"
    assert adapter._turn_id == secret
    assert result.response is not None and secret not in result.response.text
    assert result.tool_calls[0]["tool"] == "[REDACTED]"
    assert result._private_tool_calls is not None
    assert result._private_tool_calls.calls[0]["tool"] == secret
    assert secret not in repr(result)
    assert result.turn_evidence is not None
    for observation in result.turn_evidence.observations:
        assert secret not in observation.model_dump_json()
    policy_spec = _launch().spec.model_copy(
        update={
            "tool_policy": RestrictiveToolPolicy(allowed_tools=(f"slow:{secret}",)),
        }
    )
    controller = AsyncAgentSession(policy_spec, _ProjectedFailureHarness())
    controller._tool_policy_evidence = ToolPolicyEvidence(
        requested="restrictive", enforced="native", observed="runtime"
    )
    assert (
        controller._evaluate_reported_tool_calls(
            AdapterTurn(
                tool_calls=result.tool_calls,
                _private_tool_calls=result._private_tool_calls,
            )
        )
        == ()
    )


def test_private_policy_identity_envelope_cannot_be_serialized() -> None:
    private = PrivateToolCalls(
        ({"server": "private-server", "tool": "private-tool"},),
        RedactionConfig(include_environment=False),
    )
    assert "private-tool" not in repr(private)
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(private)


@pytest.mark.asyncio
async def test_capture_keeps_original_private_tool_identity_when_file_redacts_it(
    tmp_path: Path,
) -> None:
    secret = "collision-native-id"
    capture = McpCaptureManager(root=tmp_path)
    writer = capture.writer_for("connection", "stdio")
    writer.add_secrets({secret})
    writer.write(
        transport="stdio",
        direction="client_to_server",
        payload={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": secret, "arguments": {}},
        },
    )
    try:
        public = capture.snapshots()
        assert public[0].events[0].tool == "[REDACTED]"
        private = capture._private_forwarded_calls()
        assert private.calls[0]["tool"] == secret
        assert secret not in repr(private)
        controller = AsyncAgentSession(_launch().spec, _ProjectedFailureHarness())
        controller._server_manager = SimpleNamespace(
            capture=capture,
            snapshot=lambda: SimpleNamespace(
                records=(SimpleNamespace(connection_id="connection", key="slow"),),
            ),
        )
        actual = controller._pending_captured_tool_calls()
        assert len(actual) == 1 and actual[0].name == secret
        assert controller._pending_captured_tool_calls() == ()
    finally:
        await capture.close()


@pytest.mark.parametrize("secret", ['quoted"credential\nwith-newline', "凭据-secret"])
def test_opencode_raw_json_redacts_decoded_credentials(secret: str) -> None:
    adapter = OpenCodeHarnessAdapter()
    adapter._native_error_redaction = RedactionConfig(
        secrets=frozenset({secret}), include_environment=False
    )
    body = json.dumps({"error": {"message": "denied " + secret}}).encode()
    observation = adapter._raw_frame_observation(
        1, body, "application/json", datetime.now(timezone.utc), 0.0
    )
    assert observation.text is not None
    assert json.loads(observation.text)["error"]["message"] == "denied [REDACTED]"
    assert observation.raw_evidence is not None
    assert (
        json.loads(observation.raw_evidence.as_bytes())["error"]["message"]
        == "denied [REDACTED]"
    )


def test_opencode_raw_binary_redacts_before_base64_encoding() -> None:
    adapter = OpenCodeHarnessAdapter()
    adapter._native_error_redaction = RedactionConfig(
        secrets=frozenset({"binary-secret"}), include_environment=False
    )
    observation = adapter._raw_frame_observation(
        1,
        b"\xffbinary-secret",
        "application/octet-stream",
        datetime.now(timezone.utc),
        0.0,
    )
    assert observation.raw_evidence is not None
    assert observation.raw_evidence.as_bytes() == b"\xff[REDACTED]"


@pytest.mark.asyncio
async def test_codex_initialize_rejection_preserves_native_phase_and_cause() -> None:
    class RejectedInitialize:
        def __init__(self) -> None:
            self.written = []

        async def write(self, payload) -> None:
            self.written.append(payload)

        async def next(self, timeout=None):
            assert len(self.written) == 1
            return {
                "id": 1,
                "error": {
                    "code": -32600,
                    "message": "unsupported client pinned-secret",
                },
            }

    adapter = CodexHarnessAdapter()
    adapter._native_error_redaction = RedactionConfig(
        secrets=frozenset({"pinned-secret"}), include_environment=False
    )
    process = RejectedInitialize()
    with pytest.raises(NativeHarnessStartupError) as caught:
        await adapter.initialize(process, _launch())  # type: ignore[arg-type]
    assert caught.value.details["phase"] == "initialize"
    assert caught.value.details["native_code"] == -32600
    assert caught.value.details["native_message"] == "unsupported client [REDACTED]"
    assert len(process.written) == 1


@pytest.mark.asyncio
async def test_codex_protocol_turn_error_survives_the_rpc_exception_boundary() -> None:
    adapter = CodexHarnessAdapter()
    adapter._thread_id = "thread"
    adapter._request_id = 1
    adapter._native_error_redaction = RedactionConfig(
        secrets=frozenset({"pinned-secret"}), include_environment=False
    )
    process = _NativeFrames("unused")
    process.frames = iter(
        (
            {
                "id": 2,
                "error": {"code": -32000, "message": "provider refused pinned-secret"},
            },
        )
    )
    result = await adapter._send(
        HarnessTurnRequest.from_message("fixture"),
        1,
        process,  # type: ignore[arg-type]
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.details["native_code"] == -32000
    assert result.error.details["phase"] == "turn/start"
    assert result.error.details["native_message"] == "provider refused [REDACTED]"
    assert "pinned-secret" not in repr(result)


def test_unparseable_native_login_does_not_claim_a_complete_error_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source-login"
    source.mkdir()
    (source / "auth.json").write_text("{unparseable native-login-secret}")
    monkeypatch.setenv("CODEX_HOME", str(source))
    adapter = CodexHarnessAdapter()
    launch = _launch()
    (tmp_path / "isolated").mkdir()
    adapter.environment_for_launch(launch, tmp_path / "isolated")
    assert adapter._native_error_redaction is None
    error = adapter._thread_start_failure(
        {"message": "failed parsing native-login-secret"}, launch
    )
    assert error.details["native_diagnostic"] == "unavailable_registry"
    assert "native-login-secret" not in str(error)


def test_projection_preserves_content_kind_and_tool_provenance_collisions() -> None:
    from m3.harness._native_errors import project_native_turn
    from m3.harness.contracts import HarnessTurnResult
    from m3.types import OpaqueContent, TextContent, TurnResponse

    redaction = RedactionConfig(
        secrets=frozenset({"text", "mcp"}), include_environment=False
    )
    result = HarnessTurnResult(
        sequence=1,
        status="completed",
        response=TurnResponse(
            content=(
                TextContent(text="text"),
                OpaqueContent(provider="fixture", payload={"kind": "text"}),
            ),
            metadata={"native": "text"},
        ),
        tool_calls=({"server": "mcp", "tool": "text", "provenance": "mcp"},),
    )
    safe = project_native_turn(result, redaction)
    assert safe.status == "completed"
    assert safe.response is not None
    assert safe.response.content[0].kind == "text"
    assert safe.response.text == "[REDACTED]"
    assert isinstance(safe.response.content[1], OpaqueContent)
    assert safe.response.content[1].payload == {"kind": "[REDACTED]"}
    assert safe.response.metadata == {"native": "[REDACTED]"}
    assert safe.tool_calls == (
        {"server": "[REDACTED]", "tool": "[REDACTED]", "provenance": "mcp"},
    )
    assert safe._private_tool_calls is not None
    assert safe._private_tool_calls.calls[0]["tool"] == "text"


@pytest.mark.parametrize("secret", ["summary", "native_message", "native_code"])
def test_error_projection_preserves_schema_keys_matching_credentials(
    secret: str,
) -> None:
    error = native_error_info(
        {"message": secret, "code": secret},
        summary="Native harness failed",
        harness="codex",
        phase="turn",
        redaction=RedactionConfig(
            secrets=frozenset({secret}), include_environment=False
        ),
    )
    assert error.message == "Native harness failed: [REDACTED]"
    assert error.details["native_message"] == "[REDACTED]"
    assert error.details["native_code"] == "[REDACTED]"


def test_plan_status_is_native_text_not_a_framework_status_enum() -> None:
    from m3.harness._native_errors import project_native_observation
    from m3.harness.observations import PlanObservedObservation

    observation = PlanObservedObservation(
        observation_id="fixture",
        harness_kind="codex",
        turn_sequence=1,
        wall_time=datetime.now(timezone.utc),
        monotonic_offset_ms=0,
        status="plan-secret",
    )
    safe = project_native_observation(
        observation,
        RedactionConfig(secrets=frozenset({"plan-secret"}), include_environment=False),
        0,
    )
    assert isinstance(safe, PlanObservedObservation)
    assert safe.status == "[REDACTED]"


@pytest.mark.asyncio
async def test_invalid_login_registry_closes_all_public_payload_and_partial_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "login"
    source.mkdir()
    secret = "unknown-malformed-login-credential"
    (source / "auth.json").write_text("{unparseable " + secret + "}")
    monkeypatch.setenv("CODEX_HOME", str(source))
    root = tmp_path / "isolated"
    root.mkdir()
    adapter = CodexHarnessAdapter(environment={"KNOWN": "known-explicit-credential"})
    adapter.environment_for_launch(_launch(), root)
    adapter._thread_id = secret
    adapter._request_id = 1
    adapter._launch = _launch()
    result = await adapter._send(
        HarnessTurnRequest.from_message("fixture"), 1, _NativeFrames(secret)
    )  # type: ignore[arg-type]
    assert result.status == "failed"
    assert result.response is None
    assert result.tool_calls == ()
    assert result.error is not None
    assert result.error.details["native_diagnostic"] == "unavailable_registry"
    assert secret not in repr(result)
    assert result.turn_evidence is not None
    assert secret not in result.turn_evidence.model_dump_json()
    # Cancellation reads the same pinned unavailable state, rather than
    # promoting the unrelated explicit env value to an authoritative registry.
    adapter._partial_capture = lambda: result.turn_evidence
    partial = adapter.take_partial_turn_evidence()
    assert partial is not None and secret not in partial.model_dump_json()
    assert result._private_tool_calls is not None
    with pytest.raises(RedactionError):
        result._private_tool_calls.project({"server": secret})


@pytest.mark.parametrize("source", ["reported", "captured"])
def test_private_destructive_identity_survives_provenance_policy_merge(
    source: str,
) -> None:
    from m3.policy import ToolDescriptor

    secret = "destructive-native-tool"
    controller = AsyncAgentSession(
        _launch().spec.model_copy(
            update={
                "tool_policy": RestrictiveToolPolicy(allowed_tools=(f"slow:{secret}",)),
            }
        ),
        _ProjectedFailureHarness(),
    )
    controller._tool_policy_evidence = ToolPolicyEvidence(
        requested="restrictive", enforced="native", observed="runtime"
    )
    private = PrivateToolCalls(
        ({"server": "slow", "tool": secret, "destructive": True, "provenance": "mcp"},)
        if source == "reported"
        else (),
        RedactionConfig(secrets=frozenset({secret}), include_environment=False),
    )
    violations = controller._evaluate_reported_tool_calls(
        AdapterTurn(tool_calls=(), _private_tool_calls=private),
        captured_tool_calls=(
            ToolDescriptor(server="slow", name=secret, destructive=True),
        )
        if source == "captured"
        else (),
    )
    assert len(violations) == 1
    assert violations[0]["tool"] == secret
    assert violations[0]["reason"] == "destructive tool requires confirmation"
    assert secret not in repr(private.project(violations))


@pytest.mark.parametrize(
    "payload",
    [
        {"error": "Claude rejected claude-secret"},
        {"error": {"message": "Claude rejected claude-secret"}},
        {
            "subtype": "error_during_execution",
            "errors": ["Claude rejected claude-secret"],
        },
        {
            "subtype": "error_during_execution",
            "result": "Claude rejected claude-secret",
        },
    ],
)
def test_claude_recognized_native_cause_shapes_retain_the_message(
    payload: dict,
) -> None:
    from m3.harness.claude import _claude_native_error

    error = native_error_info(
        _claude_native_error(payload),
        summary="Claude turn failed",
        harness="claude_code",
        phase="turn/result",
        redaction=RedactionConfig(
            secrets=frozenset({"claude-secret"}), include_environment=False
        ),
    )
    assert error.details["native_message"] == "Claude rejected [REDACTED]"
    assert "claude-secret" not in error.model_dump_json()


@pytest.mark.asyncio
async def test_unavailable_registry_partial_capture_remains_closed_after_adapter_close() -> (
    None
):
    secret = "unknown-login-cancellation-secret"

    class CancelledFrames:
        owner = None

        def __init__(self) -> None:
            self.frames = iter(
                (
                    {"id": 2, "result": {"turn": {"id": "turn"}}},
                    {"method": "item/agentMessage/delta", "params": {"delta": secret}},
                )
            )

        async def write(self, payload) -> None:
            pass

        async def next(self, timeout=None):
            frame = next(self.frames, None)
            if frame is None:
                raise asyncio.CancelledError
            return frame

    adapter = CodexHarnessAdapter()
    adapter._thread_id = "thread"
    adapter._request_id = 1
    adapter._native_error_registry_unavailable = True
    with pytest.raises(asyncio.CancelledError):
        await adapter._send(
            HarnessTurnRequest.from_message("fixture"), 1, CancelledFrames()
        )  # type: ignore[arg-type]
    await adapter.close()
    partial = adapter.take_partial_turn_evidence()
    assert partial is not None
    assert partial.status == "cancelled"
    assert secret not in partial.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [True, False])
@pytest.mark.parametrize(
    "malformed",
    ["synthetic\nidentity", "", "x" * 257, None, 123],
    ids=["control", "empty", "oversized", "missing", "nonstring"],
)
@pytest.mark.parametrize("valid", [True, False])
async def test_custom_adapter_captured_private_identity_never_leaks_in_policy_results(
    tmp_path: Path, registered: bool, malformed, valid: bool
) -> None:
    from m3.server_group import ServerRecord
    from m3.storage import InMemoryExecutionStore
    from m3.types import Readiness, TextContent, TurnResponse

    secret = "writer-only-private-tool"
    capture = McpCaptureManager(root=tmp_path)
    writer = capture.writer_for("slow-connection")
    if registered:
        writer.add_secrets({secret})
    original = _launch()
    record = ServerRecord(
        key="slow",
        server=original.spec.servers[0].server,
        required=True,
        available=True,
        connection_id="slow-connection",
        tools=("allowed", secret),
    )
    snapshot = ServerGroupSnapshot(records=(record,))

    class Manager:
        async def start(self):
            return snapshot

        def snapshot(self):
            return snapshot

        def configurations(self):
            return original.configurations

        async def close(self):
            await capture.close()

    manager = Manager()
    manager.capture = capture

    class LegacyAdapter(_ProjectedFailureHarness):
        last_policy_evidence = ToolPolicyEvidence(
            requested="restrictive", enforced="portable", observed="preflight"
        )

        async def preflight(self, launch):
            return Readiness(ready=True)

        async def send(self, message, *, timeout=None, metadata=None):
            if valid:
                writer.write(
                    transport="stdio",
                    direction="client_to_server",
                    payload={
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": secret, "arguments": {}},
                    },
                )
            writer.write(
                transport="stdio",
                direction="client_to_server",
                payload={
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": malformed},
                },
            )
            return AdapterTurn(response=TurnResponse(content=(TextContent(text="4"),)))

    spec = original.spec.model_copy(
        update={"tool_policy": RestrictiveToolPolicy(allowed_tools=("slow:allowed",))}
    )
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "private-capture-result")
    async with AsyncAgentSession(
        spec, LegacyAdapter(), server_manager=manager, trace_recorder=recorder
    ) as session:
        result = await session.send("fixture")
    assert result.snapshot.outcome is TurnOutcome.FAILED
    assert result.response is not None and result.response.text == "4"
    assert result.error is not None
    assert secret not in result.error.model_dump_json()
    violations = result.error.details["policy_violations"]
    if valid:
        denied = next(
            item
            for item in violations
            if item["reason"] == "tool denied by restrictive policy"
        )
        assert denied["tool"] == ("[REDACTED]" if registered else "unavailable")
    invalid = next(
        item for item in violations if item["reason"] == "tool_identity_invalid"
    )
    assert invalid["tool"] == "unavailable"
    # Unknown wire values remain unchanged without a writer registry.
    # Derived policy violations must still withhold private identities.
    for event in store.events(recorder.execution_id):
        if event.kind is EventKind.TURN_STATE_CHANGED or event.payload.get(
            "policy_violation"
        ):
            assert secret not in event.model_dump_json()


def test_unavailable_registry_summary_omits_configured_credential_identity() -> None:
    from dataclasses import replace

    secret = "synthetic-credential-collision"
    original = _launch()
    launch = replace(
        original,
        configurations=(replace(original.configurations[0], key=secret),),
    )
    adapter = CodexHarnessAdapter(environment={"OPAQUE": secret})
    error = adapter._thread_start_failure(
        {
            "code": -32603,
            "message": "required MCP servers failed to initialize: "
            + secret
            + ": handshake failed",
        },
        launch,
    )
    assert isinstance(error, NativeHarnessStartupError)
    assert secret not in str(error)
    assert secret not in error.error.model_dump_json()
    assert (
        error.error.message
        == "Codex thread/start failed (JSON-RPC -32603): required MCP server failed to initialize"
    )
    assert error.error.details["phase"] == "thread/start"
    assert error.error.details["native_code"] == -32603
    assert error.error.details["reason"] == "required_mcp_failed_to_initialize"
    assert error.error.details["native_diagnostic"] == "unavailable_registry"
