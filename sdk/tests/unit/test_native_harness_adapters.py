from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import AsyncIterator, Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, cast

import httpx
import pytest

from mcp_pal.agent_session import AsyncAgentSession
from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.errors import UnsupportedFeature
from mcp_pal.execution_trace import ExecutionTraceRecorder
from mcp_pal.harness import default_adapters
from mcp_pal.harness.acp import AcpHarnessAdapter
from mcp_pal.harness.claude import ClaudeCodeHarnessAdapter
from mcp_pal.harness.contracts import (
    HarnessLaunch,
    HarnessStartupError,
    HarnessTurnRequest,
    HarnessTurnResult,
)
from mcp_pal.harness.native import MAX_FRAME_BYTES, _server_configuration, write_config
from mcp_pal.harness.observation_sink import HarnessObservationSink
from mcp_pal.harness.observations import (
    MessageChunkObservation,
    MetadataObservedObservation,
    RawFrameObservation,
    ReasoningChunkObservation,
    ToolCallObservedObservation,
    ToolResultObservedObservation,
    UsageObservedObservation,
)
from mcp_pal.harness.opencode import OpenCodeHarnessAdapter, opencode_configuration
from mcp_pal.server_group import (
    HarnessServerConfig,
    ServerGroupSnapshot,
    ServerRecord,
)
from mcp_pal.storage import InMemoryExecutionStore, SQLiteExecutionStore
from mcp_pal.types import (
    ACPAgent,
    AgentSpec,
    ClaudeCode,
    ErrorCode,
    EventDirection,
    EventKind,
    EventOrigin,
    EventSource,
    ExecutionOutcome,
    NativeToolPolicy,
    OpenCode,
    RequestLink,
    RestrictiveToolPolicy,
    SecretReference,
    ServerBinding,
    StdioServer,
    TextContent,
    TransportKind,
    TurnId,
)

pytestmark = pytest.mark.process_lifecycle


def _spec() -> AgentSpec:
    return AgentSpec(
        harness=ACPAgent(model="fixture"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
    )


def _launch() -> HarnessLaunch:
    spec = _spec()
    return HarnessLaunch(spec, ServerGroupSnapshot(), (), spec.tool_policy)


def _response_text(result: HarnessTurnResult) -> str:
    response = result.response
    assert response is not None
    block = response.content[0]
    assert isinstance(block, TextContent)
    return block.text


def test_opencode_config_has_dedicated_legacy_and_v2_dialects() -> None:
    legacy = opencode_configuration(_launch(), dialect="legacy")
    v2 = opencode_configuration(_launch(), dialect="v2")
    assert "mcpServers" not in legacy
    assert legacy["mcp"] == {}
    assert v2["mcp"] == {"servers": {}}


def test_opencode_nonempty_config_is_dialect_exact() -> None:
    base = _launch()
    launch = HarnessLaunch(
        base.spec,
        base.servers,
        (
            HarnessServerConfig(
                key="stdio",
                transport=TransportKind.STDIO,
                required=True,
                available=True,
                connection_id="s",
                command="python",
                args=("-m", "server"),
                environment={"MODE": "test"},
                cwd="/workspace",
            ),
            HarnessServerConfig(
                key="http",
                transport=TransportKind.STREAMABLE_HTTP,
                required=True,
                available=True,
                connection_id="h",
                endpoint="https://example.test/mcp",
                headers={"X-Test": "yes"},
            ),
        ),
        base.tool_policy,
    )
    legacy = opencode_configuration(launch, dialect="legacy")
    v2 = opencode_configuration(launch, dialect="v2")
    assert legacy == {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "stdio": {
                "type": "local",
                "command": ["python", "-m", "server"],
                "environment": {"MODE": "test"},
                "cwd": "/workspace",
                "enabled": True,
            },
            "http": {
                "type": "remote",
                "url": "https://example.test/mcp",
                "headers": {"X-Test": "yes"},
                "enabled": True,
            },
        },
    }
    assert v2 == {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "servers": {
                "stdio": {
                    "type": "local",
                    "command": ["python", "-m", "server"],
                    "environment": {"MODE": "test"},
                    "cwd": "/workspace",
                },
                "http": {
                    "type": "remote",
                    "url": "https://example.test/mcp",
                    "headers": {"X-Test": "yes"},
                },
            },
        },
    }

    def assert_no_legacy_key(value: object) -> None:
        if isinstance(value, dict):
            assert "mcpServers" not in value
            for nested in value.values():
                assert_no_legacy_key(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_no_legacy_key(nested)

    assert_no_legacy_key(legacy)
    assert_no_legacy_key(v2)
    sse = HarnessServerConfig(
        key="sse",
        transport=TransportKind.SSE,
        required=True,
        available=True,
        connection_id="sse",
        endpoint="https://example.test/events",
        headers={"X-Test": "yes"},
    )
    sse_launch = HarnessLaunch(base.spec, base.servers, (sse,), base.tool_policy)
    assert opencode_configuration(sse_launch, dialect="legacy")["mcp"]["sse"] == {
        "type": "remote",
        "url": "https://example.test/events",
        "headers": {"X-Test": "yes"},
        "enabled": True,
    }
    with pytest.raises(HarnessStartupError, match="does not support SSE"):
        opencode_configuration(sse_launch, dialect="v2")


def test_opencode_config_keeps_secret_references_as_env_substitutions() -> None:
    base = _launch()
    launch = HarnessLaunch(
        base.spec,
        base.servers,
        (
            HarnessServerConfig(
                key="stdio",
                transport=TransportKind.STDIO,
                required=True,
                available=True,
                connection_id="s",
                command="python",
                args=("-m", "server"),
                environment={
                    "MCP_API_KEY": SecretReference(
                        source="environment", name="MCP_API_KEY"
                    )
                },
            ),
        ),
        base.tool_policy,
    )
    config = opencode_configuration(launch, dialect="v2")
    assert config["mcp"]["servers"]["stdio"]["environment"] == {
        "MCP_API_KEY": "{env:MCP_API_KEY}"
    }
    assert "credential-canary" not in repr(config)


def test_opencode_model_reference_normalizes_qualified_and_explicit_forms() -> None:
    adapter = OpenCodeHarnessAdapter(executable="fixture")
    adapter._launch = HarnessLaunch(
        _spec().model_copy(update={"harness": OpenCode(model="provider/model")}),
        ServerGroupSnapshot(),
        (),
        _spec().tool_policy,
    )
    assert adapter._model_reference() == {"providerID": "provider", "modelID": "model"}
    adapter._launch = HarnessLaunch(
        _spec().model_copy(
            update={"harness": OpenCode(model="model", provider="provider")}
        ),
        ServerGroupSnapshot(),
        (),
        _spec().tool_policy,
    )
    assert adapter._model_reference() == {"providerID": "provider", "modelID": "model"}
    adapter._launch = HarnessLaunch(
        _spec().model_copy(
            update={"harness": OpenCode(model="other/model", provider="provider")}
        ),
        ServerGroupSnapshot(),
        (),
        _spec().tool_policy,
    )
    with pytest.raises(Exception, match="do not agree"):
        adapter._model_reference()


class _HTTPResponse:
    def __init__(self, value: object, status_code: int = 200) -> None:
        self.value, self.status_code = value, status_code
        self.headers: dict[str, str] = {}

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        yield (
            self.value
            if isinstance(self.value, bytes)
            else json.dumps(self.value).encode()
        )


class _TurnStream:
    def __init__(
        self, response: object, status: int = 200, close: bool = False
    ) -> None:
        self.response, self.status, self.close = response, status, close

    async def __aenter__(self) -> _HTTPResponse:
        if self.close:
            raise OSError("connection closed")
        return _HTTPResponse(self.response, self.status)

    async def __aexit__(self, *_args: object) -> None:
        return None


class _TurnFixture:
    def __init__(
        self, response: object, status: int = 200, close: bool = False
    ) -> None:
        self.response, self.status, self.close = response, status, close

    def stream(self, *_args: object, **_kwargs: object) -> _TurnStream:
        return _TurnStream(self.response, self.status, self.close)


class _HistoryFixture(_TurnFixture):
    def __init__(self, response: object, before: object, after: object) -> None:
        super().__init__(response)
        self.get_requests: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self._history = [
            before if isinstance(before, _HTTPResponse) else _HTTPResponse(before),
            after if isinstance(after, _HTTPResponse) else _HTTPResponse(after),
        ]

    async def get(self, *_args: object, **_kwargs: object) -> _HTTPResponse:
        self.get_requests.append((_args, _kwargs))
        return self._history.pop(0)


class _MultiTurnHistoryFixture(_TurnFixture):
    def __init__(self, responses: list[object], histories: list[object]) -> None:
        super().__init__(responses[0])
        self._responses = list(responses)
        self.get_requests: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self._history = [_HTTPResponse(history) for history in histories]

    def stream(self, *_args: object, **_kwargs: object) -> _TurnStream:
        return _TurnStream(self._responses.pop(0))

    async def get(self, *_args: object, **_kwargs: object) -> _HTTPResponse:
        self.get_requests.append((_args, _kwargs))
        return self._history.pop(0)


class _NoGetFixture(_TurnFixture):
    def __init__(self, response: object) -> None:
        super().__init__(response)
        self.get_calls = 0

    async def get(self, *_args: object, **_kwargs: object) -> _HTTPResponse:
        self.get_calls += 1
        raise AssertionError("history should not be fetched for this POST")


class _TimeoutHistoryFixture(_TurnFixture):
    async def get(self, *_args: object, **_kwargs: object) -> _HTTPResponse:
        raise httpx.ReadTimeout("history request timed out")


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "dialect"), (("normal", "legacy"), ("v2", "v2")))
async def test_opencode_startup_sends_selected_model_without_catalog_preflight(
    tmp_path: Path, mode: str, dialect: Literal["legacy", "v2"]
) -> None:
    marker = tmp_path / f"{dialect}.json"
    base = _launch()
    harness = OpenCode(model="provider/model", provider="provider", dialect=dialect)
    spec = base.spec.model_copy(update={"harness": harness})
    launch = HarnessLaunch(spec, base.servers, base.configurations, base.tool_policy)
    adapter = OpenCodeHarnessAdapter(
        executable=str(
            Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py"
        ),
        environment={"MCP_PAL_MARKER": str(marker), "MCP_PAL_OPENCODE_MODE": mode},
    )
    observed: dict[str, Any] = {}
    try:
        session = await adapter.open(launch)
        result = await session.send(HarnessTurnRequest.from_message("hello"))
        assert result.status == "completed"
        observed = await _wait_for_marker(
            marker, lambda value: len(value.get("message_urls", [])) == 1
        )
        assert observed["api_hits"][0] == "POST /session"
        assert any(
            hit.endswith("/message") and hit.startswith("POST ")
            for hit in observed["api_hits"]
        )
        assert observed["message_bodies"] == [
            {
                "model": {"providerID": "provider", "modelID": "model"},
                "parts": [{"type": "text", "text": "hello"}],
            }
        ]
    finally:
        await adapter.close()
    await _assert_pid_dead(observed["serve_pid"])
    await _assert_pid_dead(observed["child_pid"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    (
        {"info": {"finish": "stop"}, "parts": [{"type": "text", "text": "hello"}]},
        {
            "info": {"parentID": "user-1", "finish": "stop"},
            "parts": [
                {
                    "type": "tool",
                    "tool": "echo",
                    "callID": "call-1",
                    "state": {"status": "completed", "output": {"ok": True}},
                }
            ],
        },
    ),
    ids=("text-only", "post-tool"),
)
async def test_opencode_does_not_fetch_history_for_text_or_post_tool(
    payload: object,
) -> None:
    base = _launch()
    adapter = OpenCodeHarnessAdapter(executable="fixture")
    adapter._launch = HarnessLaunch(
        base.spec.model_copy(update={"harness": OpenCode(model="fixture")}),
        base.servers,
        (),
        base.tool_policy,
    )
    client = _NoGetFixture(payload)
    adapter._client = cast(Any, client)
    result = await adapter._send(HarnessTurnRequest.from_message("hello"), 1, "session")
    assert result.status == "completed"
    assert client.get_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("history_body", (b"{", b"x" * (MAX_FRAME_BYTES + 1)))
async def test_opencode_invalid_history_is_captured_as_incomplete(
    history_body: bytes,
) -> None:
    base = _launch()
    adapter = OpenCodeHarnessAdapter(executable="fixture")
    adapter._launch = HarnessLaunch(
        base.spec.model_copy(update={"harness": OpenCode(model="fixture")}),
        base.servers,
        (),
        base.tool_policy,
    )
    post = {
        "info": {"parentID": "user-1", "finish": "stop"},
        "parts": [{"type": "text", "text": "done"}],
    }
    adapter._client = cast(Any, _HistoryFixture(post, history_body, []))
    result = await adapter._send(HarnessTurnRequest.from_message("hello"), 1, "session")
    assert result.status == "completed"
    assert result.turn_evidence is not None
    assert "capture_incomplete" in result.turn_evidence.limitations
    assert not any(
        isinstance(item, ToolCallObservedObservation)
        for item in result.turn_evidence.observations
    )


@pytest.mark.asyncio
async def test_opencode_history_http_error_retains_status_and_raw_body() -> None:
    base = _launch()
    adapter = OpenCodeHarnessAdapter(executable="fixture")
    adapter._launch = HarnessLaunch(
        base.spec.model_copy(update={"harness": OpenCode(model="fixture")}),
        base.servers,
        (),
        base.tool_policy,
    )
    post = {
        "info": {"parentID": "user-1", "finish": "stop"},
        "parts": [{"type": "text", "text": "done"}],
    }
    error_response = _HTTPResponse(b'{"error":"history unavailable"}', status_code=500)
    adapter._client = cast(Any, _HistoryFixture(post, error_response, []))
    result = await adapter._send(HarnessTurnRequest.from_message("hello"), 1, "session")
    assert result.status == "completed"
    assert result.turn_evidence is not None
    metadata = [
        item
        for item in result.turn_evidence.observations
        if isinstance(item, MetadataObservedObservation)
    ]
    assert any(
        item.name == "http_history_after.status_code" and item.value == 500
        for item in metadata
    )
    assert any(
        isinstance(item, RawFrameObservation)
        and item.observation_id == "opencode-history-after-1"
        for item in result.turn_evidence.observations
    )


@pytest.mark.asyncio
async def test_opencode_history_timeout_does_not_fabricate_reported_calls() -> None:
    base = _launch()
    adapter = OpenCodeHarnessAdapter(executable="fixture")
    adapter._launch = HarnessLaunch(
        base.spec.model_copy(update={"harness": OpenCode(model="fixture")}),
        base.servers,
        (),
        base.tool_policy,
    )
    post = {
        "info": {"parentID": "user-1", "finish": "stop"},
        "parts": [{"type": "text", "text": "done"}],
    }
    client = _TimeoutHistoryFixture(post)
    adapter._client = cast(Any, client)
    result = await adapter._send(HarnessTurnRequest.from_message("hello"), 1, "session")
    assert result.status == "completed"
    assert result.turn_evidence is not None
    assert "capture_incomplete" in result.turn_evidence.limitations
    assert not any(
        isinstance(item, ToolCallObservedObservation)
        for item in result.turn_evidence.observations
    )


@pytest.mark.asyncio
async def test_opencode_canary_is_absent_from_public_turn_result_trace_and_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = "opencode-public-canary-9c4e"
    monkeypatch.setenv("OPENCODE_API_KEY", canary)
    marker = tmp_path / "redaction.json"
    fixture = str(Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py")
    spec = AgentSpec(
        harness=OpenCode(
            model="fixture",
            provider="opencode",
            executable=fixture,
            credential_references={
                "OPENCODE_API_KEY": SecretReference(
                    source="environment", name="OPENCODE_API_KEY"
                )
            },
        ),
        servers=(
            ServerBinding(
                server=StdioServer(
                    name="fixture",
                    command=sys.executable,
                    args=(
                        str(
                            Path(__file__).parents[1]
                            / "fixtures"
                            / "matrix_stdio_server.py"
                        ),
                    ),
                    cwd=str(Path(__file__).parents[2]),
                ),
            ),
        ),
    )
    async with AsyncMCPTestKit(env={}, cwd=str(tmp_path)) as kit:
        runs: list[tuple[Any, Any, Path]] = []
        for mode, message in (
            ("success", "success"),
            ("provider-error", "provider-error"),
            ("finish-error", "finish-error"),
        ):
            run_marker = tmp_path / f"{mode}.json"
            adapter = OpenCodeHarnessAdapter(
                executable=fixture,
                environment={
                    "MCP_PAL_MARKER": str(run_marker),
                    "MCP_PAL_OPENCODE_MODE": "redaction",
                },
            )
            session = kit.agent_session(spec, adapter=adapter)
            async with session:
                turn = await session.send(message, timeout=3)
            runs.append((turn, session.result, run_marker))
        success, result, marker = runs[0]
        provider_error, provider_result, _provider_marker = runs[1]
        finish_error, finish_result, _finish_marker = runs[2]
    public_values = (
        result,
        result.model_dump(mode="json"),
        result.trace,
        result.trace.model_dump(mode="json") if result.trace is not None else None,
        success,
        provider_error,
        finish_error,
        provider_result,
        finish_result,
        *(
            path.read_text(encoding="utf-8") if path.exists() else ""
            for _turn, _result, path in runs
        ),
    )
    assert all(canary not in repr(value) for value in public_values)
    private_marker = marker.with_name(marker.name + ".child.json")
    child_env = json.loads(private_marker.read_text(encoding="utf-8"))
    assert child_env["OPENCODE_API_KEY"] == canary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    (
        "text",
        "tool-only",
        "mixed-text-tool",
        "malformed-tool",
        "provider-error",
        "finish-error",
        "numeric-tokens-cost",
        "hostile-usage-types",
        "hostile-identifiers",
        "parts-not-list",
        "info-not-mapping",
        "malformed-json",
        "oversized-body",
        "http-error",
        "connection-loss",
    ),
    ids=lambda value: value,
)
async def test_opencode_official_response_matrix(case: str) -> None:
    payload: object = {
        "info": {"finish": "stop"},
        "parts": [{"type": "text", "text": "hello"}],
    }
    if case == "tool-only":
        payload = {
            "info": {"finish": "stop"},
            "parts": [{"type": "tool", "tool": "echo", "callID": "c1"}],
        }
    elif case == "mixed-text-tool":
        payload = {
            "info": {"finish": "stop"},
            "parts": [
                {"type": "text", "text": "hello"},
                {"type": "tool", "tool": "echo", "callID": "c1"},
            ],
        }
    elif case == "malformed-tool":
        payload = {"info": {"finish": "stop"}, "parts": [{"type": "tool", "tool": 1}]}
    elif case == "provider-error":
        payload = {"info": {"error": {"canary": "secret"}}, "parts": []}
    elif case == "finish-error":
        payload = {"info": {"finish": "error"}, "parts": []}
    elif case == "numeric-tokens-cost":
        payload = {
            "info": {"finish": "stop", "tokens": {"input": 1}, "cost": 0.2},
            "parts": [{"type": "text", "text": "ok"}],
        }
    elif case == "hostile-usage-types":
        payload = {
            "info": {"finish": "stop", "tokens": {"input": "canary"}, "cost": "canary"},
            "parts": [{"type": "text", "text": "ok"}],
        }
    elif case == "hostile-identifiers":
        payload = {
            "info": {
                "finish": "stop",
                "providerID": "bad\nprovider",
                "modelID": "m" * 257,
            },
            "parts": [{"type": "text", "text": "ok"}],
        }
    elif case == "parts-not-list":
        payload = {"info": {}, "parts": {}}
    elif case == "info-not-mapping":
        payload = {"info": "bad", "parts": []}
    elif case == "malformed-json":
        payload = b"{"
    elif case == "oversized-body":
        payload = b"x" * (1024 * 1024 + 1)
    adapter = OpenCodeHarnessAdapter(executable="fixture")
    base = _launch()
    adapter._launch = HarnessLaunch(
        base.spec.model_copy(
            update={"harness": OpenCode(model="fixture", provider="opencode")}
        ),
        base.servers,
        (),
        base.tool_policy,
    )
    adapter._client = cast(
        Any,
        _TurnFixture(
            payload, 500 if case == "http-error" else 200, case == "connection-loss"
        ),
    )
    result = await adapter._send(HarnessTurnRequest.from_message("hello"), 1, "session")
    assert "canary" not in repr(result)
    expected_status = (
        "completed"
        if case
        in {
            "text",
            "tool-only",
            "mixed-text-tool",
            "numeric-tokens-cost",
            "hostile-usage-types",
            "hostile-identifiers",
        }
        else "failed"
    )
    assert result.status == expected_status
    expected_code = {
        "malformed-tool": ErrorCode.PROTOCOL_ERROR,
        "parts-not-list": ErrorCode.PROTOCOL_ERROR,
        "info-not-mapping": ErrorCode.PROTOCOL_ERROR,
        "malformed-json": ErrorCode.PROTOCOL_ERROR,
        "oversized-body": ErrorCode.PROTOCOL_ERROR,
        "provider-error": ErrorCode.TRANSPORT_ERROR,
        "finish-error": ErrorCode.TRANSPORT_ERROR,
        "http-error": ErrorCode.TRANSPORT_ERROR,
        "connection-loss": ErrorCode.TRANSPORT_ERROR,
    }.get(case)
    if expected_code is not None:
        assert result.error is not None and result.error.code is expected_code
        assert result.response is None
    else:
        assert result.error is None
    expected_text = {
        "text": "hello",
        "mixed-text-tool": "hello",
        "numeric-tokens-cost": "ok",
        "hostile-usage-types": "ok",
        "hostile-identifiers": "ok",
    }.get(case)
    if expected_text is not None:
        assert result.response is not None and result.response.text == expected_text
        assert all(
            block.text
            for block in result.response.content
            if isinstance(block, TextContent)
        )
    if case == "tool-only":
        assert result.response is None
        assert result.tool_calls == ({"tool": "echo", "call_id": "c1", "server": None},)
    if case == "mixed-text-tool":
        assert result.tool_calls == ({"tool": "echo", "call_id": "c1", "server": None},)
    if case == "numeric-tokens-cost":
        assert result.evidence["tokens_input"] == 1
        assert result.evidence["cost_observed"] == 0.2
    if case == "hostile-usage-types":
        assert (
            "tokens_input" not in result.evidence
            and "cost_observed" not in result.evidence
        )
    if case == "hostile-identifiers":
        assert (
            "provider_observed" not in result.evidence
            and "model_observed" not in result.evidence
        )


@pytest.mark.asyncio
async def test_opencode_response_maps_to_r5_typed_turn_evidence() -> None:
    payload = {
        "info": {
            "id": "assistant-1",
            "sessionID": "session",
            "role": "assistant",
            "providerID": "provider",
            "modelID": "model",
            "finish": "stop",
            "tokens": {
                "input": 2,
                "output": 3,
                "reasoning": 1,
                "cache": {"read": 4, "write": 5},
            },
            "cost": 0.25,
        },
        "parts": [
            {"type": "text", "text": "answer", "id": "message-1"},
            {"type": "reasoning", "text": "because", "id": "reasoning-1"},
            {
                "type": "tool",
                "tool": "echo",
                "callID": "call-1",
                "input": {"value": "hello"},
                "state": {
                    "status": "completed",
                    "output": {"content": [{"type": "text", "text": "hello"}]},
                },
            },
        ],
    }
    adapter = OpenCodeHarnessAdapter(executable="fixture")
    base = _launch()
    adapter._launch = HarnessLaunch(
        base.spec.model_copy(
            update={"harness": OpenCode(model="fixture", provider="opencode")}
        ),
        base.servers,
        (),
        base.tool_policy,
    )
    adapter._client = cast(Any, _TurnFixture(payload))
    result = await adapter._send(HarnessTurnRequest.from_message("hello"), 1, "session")
    assert result.turn_evidence is not None
    observations = result.turn_evidence.observations
    assert isinstance(observations[0], RawFrameObservation)
    assert any(isinstance(item, MessageChunkObservation) for item in observations)
    assert any(isinstance(item, ReasoningChunkObservation) for item in observations)
    assert any(isinstance(item, ToolCallObservedObservation) for item in observations)
    assert any(isinstance(item, ToolResultObservedObservation) for item in observations)
    usage = next(
        item for item in observations if isinstance(item, UsageObservedObservation)
    )
    assert usage.input_tokens == 2
    assert usage.cache_read_tokens == 4
    assert usage.cache_write_tokens == 5
    assert usage.cost == 0.25
    metadata = [
        item for item in observations if isinstance(item, MetadataObservedObservation)
    ]
    assert {item.name for item in metadata} >= {
        "provider",
        "model",
        "finish",
        "message_id",
        "session_id",
    }
    assert not any(item.kind == "process_observed" for item in observations)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected_status", "has_result", "has_error"),
    (
        ({"status": "pending"}, "incomplete", False, False),
        ({"status": "running", "input": {"value": "x"}}, "incomplete", False, False),
        ({"status": "completed", "output": None}, "success", True, False),
        ({"status": "error", "error": "denied"}, "tool_error", False, True),
    ),
)
async def test_opencode_official_tool_states_preserve_missing_vs_null(
    state: Mapping[str, object],
    expected_status: str,
    has_result: bool,
    has_error: bool,
) -> None:
    adapter = OpenCodeHarnessAdapter(executable="fixture")
    base = _launch()
    adapter._launch = HarnessLaunch(
        base.spec.model_copy(update={"harness": OpenCode(model="fixture")}),
        base.servers,
        (),
        base.tool_policy,
    )
    adapter._client = cast(
        Any,
        _TurnFixture(
            {
                "info": {"id": "msg-1", "sessionID": "session", "finish": "stop"},
                "parts": [
                    {
                        "type": "tool",
                        "tool": "echo",
                        "callID": "call-1",
                        "state": state,
                    }
                ],
            }
        ),
    )
    result = await adapter._send(HarnessTurnRequest.from_message("hello"), 1, "session")
    assert result.turn_evidence is not None
    tool_result = next(
        item
        for item in result.turn_evidence.observations
        if isinstance(item, ToolResultObservedObservation)
    )
    assert tool_result.status == expected_status
    assert ("result" in tool_result.model_fields_set) is has_result
    assert ("error_message" in tool_result.model_fields_set) is has_error
    if has_result:
        assert tool_result.result is None
    if has_error:
        assert tool_result.is_error is True


@pytest.mark.asyncio
async def test_opencode_completed_without_output_is_incomplete_not_success() -> None:
    adapter = OpenCodeHarnessAdapter(executable="fixture")
    base = _launch()
    adapter._launch = HarnessLaunch(
        base.spec.model_copy(update={"harness": OpenCode(model="fixture")}),
        base.servers,
        (),
        base.tool_policy,
    )
    adapter._client = cast(
        Any,
        _TurnFixture(
            {
                "info": {"finish": "stop"},
                "parts": [
                    {
                        "type": "tool",
                        "tool": "echo",
                        "callID": "call-1",
                        "state": {"status": "completed"},
                    }
                ],
            }
        ),
    )
    result = await adapter._send(HarnessTurnRequest.from_message("hello"), 1, "session")
    assert result.turn_evidence is not None
    call = next(
        item
        for item in result.turn_evidence.observations
        if isinstance(item, ToolCallObservedObservation)
    )
    tool_result = next(
        item
        for item in result.turn_evidence.observations
        if isinstance(item, ToolResultObservedObservation)
    )
    assert call.status == "incomplete"
    assert tool_result.status == "incomplete"
    assert "result" not in tool_result.model_fields_set


@pytest.mark.asyncio
async def test_opencode_history_cursor_attributes_new_parts_and_preserves_raw_evidence() -> (
    None
):
    base = _launch()
    adapter = OpenCodeHarnessAdapter(executable="fixture")
    adapter._launch = HarnessLaunch(
        base.spec.model_copy(update={"harness": OpenCode(model="fixture")}),
        base.servers,
        (),
        base.tool_policy,
    )
    post = {
        "info": {
            "id": "assistant-final",
            "sessionID": "session",
            "parentID": "user-2",
            "finish": "stop",
        },
        "parts": [{"type": "text", "id": "final-text", "text": "done"}],
    }
    old = {
        "info": {"id": "assistant-old", "sessionID": "session", "role": "assistant"},
        "parts": [
            {
                "type": "tool",
                "id": "old-part",
                "tool": "echo",
                "callID": "old-call",
                "state": {"status": "completed", "output": {"nonce": "old"}},
            }
        ],
    }
    new_tool = {
        "info": {
            "id": "assistant-tool",
            "sessionID": "session",
            "role": "assistant",
            "parentID": "user-2",
        },
        "parts": [
            {
                "type": "tool",
                "id": "new-part",
                "tool": "echo",
                "callID": "new-call",
                "state": {
                    "status": "completed",
                    "input": {"nonce": "new"},
                    "output": {"nonce": "new"},
                },
            }
        ],
    }
    final = {"info": post["info"], "parts": post["parts"]}
    client = _HistoryFixture(post, [old, new_tool, final], [])
    adapter._client = cast(Any, client)
    result = await adapter._send(HarnessTurnRequest.from_message("hello"), 1, "session")
    assert result.turn_evidence is not None
    calls = [
        item
        for item in result.turn_evidence.observations
        if isinstance(item, ToolCallObservedObservation)
    ]
    assert [item.call_id for item in calls] == ["new-call"]
    raw_frames = [
        item
        for item in result.turn_evidence.observations
        if isinstance(item, RawFrameObservation)
    ]
    assert {item.observation_id for item in raw_frames} == {
        "opencode-http-1",
        "opencode-history-after-1",
    }
    metadata = [
        item
        for item in result.turn_evidence.observations
        if isinstance(item, MetadataObservedObservation)
    ]
    names = {item.name for item in metadata}
    assert "http.status_code" in names
    assert sum(item.name == "http.status_code" for item in metadata) == 1
    assert "http_history_after.status_code" in names
    assert len(client.get_requests) == 1
    assert client.get_requests[0][1]["params"] == {"limit": 256}


@pytest.mark.asyncio
async def test_opencode_history_matching_candidate_with_cursor_is_incomplete() -> None:
    """A matching bounded page cannot prove older same-parent parts absent."""

    base = _launch()
    adapter = OpenCodeHarnessAdapter(executable="fixture")
    adapter._launch = HarnessLaunch(
        base.spec.model_copy(update={"harness": OpenCode(model="fixture")}),
        base.servers,
        (),
        base.tool_policy,
    )
    post = {
        "info": {
            "id": "assistant-final",
            "sessionID": "session",
            "parentID": "user-2",
            "finish": "stop",
        },
        "parts": [{"type": "text", "id": "final-text", "text": "done"}],
    }
    matching = {
        "info": {
            "id": "assistant-tool",
            "sessionID": "session",
            "role": "assistant",
            "parentID": "user-2",
        },
        "parts": [
            {
                "type": "tool",
                "id": "new-part",
                "tool": "echo",
                "callID": "new-call",
                "state": {"status": "completed", "output": {"ok": True}},
            }
        ],
    }
    history_response = _HTTPResponse([matching])
    history_response.headers["x-next-cursor"] = "older-page"
    client = _HistoryFixture(post, history_response, [])
    adapter._client = cast(Any, client)

    result = await adapter._send(HarnessTurnRequest.from_message("hello"), 1, "session")

    assert "capture_incomplete" in result.trace_limitations
    assert result.turn_evidence is not None
    assert "capture_incomplete" in result.turn_evidence.limitations


@pytest.mark.asyncio
async def test_opencode_history_get_records_request_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter((10.0, 10.25))

    class Clock:
        @staticmethod
        def monotonic() -> float:
            return next(ticks)

    import mcp_pal.harness.opencode as opencode_module

    monkeypatch.setattr(opencode_module, "time", Clock)
    history = _HTTPResponse([])
    client = _HistoryFixture({}, history, [])

    snapshot = await OpenCodeHarnessAdapter._history_snapshot(
        client, "session", turn_started=9.0
    )

    assert snapshot is not None
    assert snapshot.start_offset_ms == 1000.0
    assert snapshot.end_offset_ms == 1250.0


@pytest.mark.asyncio
async def test_opencode_history_cursor_never_attaches_prior_turn_and_deduplicates_new_parts() -> (
    None
):
    base = _launch()
    adapter = OpenCodeHarnessAdapter(executable="fixture")
    adapter._launch = HarnessLaunch(
        base.spec.model_copy(update={"harness": OpenCode(model="fixture")}),
        base.servers,
        (),
        base.tool_policy,
    )
    old = {
        "info": {
            "id": "assistant-old",
            "sessionID": "session",
            "role": "assistant",
            "parentID": "user-old",
        },
        "parts": [
            {
                "type": "tool",
                "id": "old-part",
                "tool": "echo",
                "callID": "old-call",
                "state": {"status": "completed", "output": {"nonce": "old"}},
            }
        ],
    }
    turn_one_tool = {
        "info": {
            "id": "assistant-tool-1",
            "sessionID": "session",
            "role": "assistant",
            "parentID": "user-1",
        },
        "parts": [
            {
                "type": "tool",
                "id": "part-1",
                "tool": "echo",
                "callID": "call-1",
                "state": {
                    "status": "completed",
                    "input": {"nonce": "one"},
                    "output": {"nonce": "one"},
                },
            }
        ],
    }
    turn_two_tool = {
        "info": {
            "id": "assistant-tool-2",
            "sessionID": "session",
            "role": "assistant",
            "parentID": "user-2",
        },
        "parts": [
            {
                "type": "tool",
                "id": "part-2",
                "tool": "echo",
                "callID": "call-2",
                "state": {
                    "status": "completed",
                    "input": {"nonce": "two"},
                    "output": {"nonce": "two"},
                },
            }
        ],
    }
    post_one = {
        "info": {
            "id": "assistant-final-1",
            "sessionID": "session",
            "parentID": "user-1",
            "finish": "stop",
        },
        "parts": [{"type": "text", "id": "text-1", "text": "one"}],
    }
    post_two = {
        "info": {
            "id": "assistant-final-2",
            "sessionID": "session",
            "parentID": "user-2",
            "finish": "stop",
        },
        "parts": [{"type": "text", "id": "text-2", "text": "two"}],
    }
    final_one = {"info": post_one["info"], "parts": post_one["parts"]}
    final_two = {"info": post_two["info"], "parts": post_two["parts"]}
    client = _MultiTurnHistoryFixture(
        [post_one, post_two],
        [
            [old, turn_one_tool, final_one],
            [old, turn_one_tool, final_one, turn_two_tool, final_two],
        ],
    )
    adapter._client = cast(Any, client)

    first = await adapter._send(HarnessTurnRequest.from_message("one"), 1, "session")
    second = await adapter._send(HarnessTurnRequest.from_message("two"), 2, "session")
    assert first.turn_evidence is not None and second.turn_evidence is not None
    first_calls = [
        item
        for item in first.turn_evidence.observations
        if isinstance(item, ToolCallObservedObservation)
    ]
    second_calls = [
        item
        for item in second.turn_evidence.observations
        if isinstance(item, ToolCallObservedObservation)
    ]
    assert [item.call_id for item in first_calls] == ["call-1"]
    assert [item.call_id for item in second_calls] == ["call-2"]
    assert all(item.call_id != "old-call" for item in (*first_calls, *second_calls))
    assert len(second_calls) == 1
    assert len(client.get_requests) == 2
    assert all(
        request[1]["params"] == {"limit": 256} for request in client.get_requests
    )


@pytest.mark.asyncio
async def test_opencode_server_tool_projects_as_one_correlated_public_call() -> None:
    base = _launch()
    adapter = OpenCodeHarnessAdapter(executable="fixture")
    configuration = HarnessServerConfig(
        key="fixture",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="connection",
        command="fixture",
    )
    adapter._launch = HarnessLaunch(
        base.spec.model_copy(update={"harness": OpenCode(model="fixture")}),
        base.servers,
        (configuration,),
        base.tool_policy,
    )
    response = {
        "info": {"sessionID": "session", "finish": "stop"},
        "parts": [
            {
                "type": "tool",
                "tool": "fixture_echo",
                "callID": "provider-call",
                "state": {
                    "status": "completed",
                    "input": {"nonce": "deterministic"},
                    "output": {"nonce": "deterministic"},
                },
            }
        ],
    }
    adapter._client = cast(Any, _TurnFixture(response))
    turn = await adapter._send(HarnessTurnRequest.from_message("hello"), 1, "session")
    assert turn.turn_evidence is not None

    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "opencode-public")
    sink = HarnessObservationSink(recorder, turn_id=TurnId("actual-turn"))
    for observation in turn.turn_evidence.observations:
        sink.emit(observation)
    provenance = EventSource(origin=EventOrigin.WIRE_OBSERVED, source="direct")
    recorder.emit(
        EventKind.MCP_REQUEST,
        turn_id=TurnId("actual-turn"),
        server_binding="fixture",
        connection_id="connection",
        correlation=RequestLink(
            jsonrpc_id=1,
            direction=EventDirection.CLIENT_TO_SERVER,
            request_sequence=1,
        ),
        payload={
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"nonce": "deterministic"}},
        },
        provenance=provenance,
    )
    recorder.emit(
        EventKind.MCP_RESPONSE,
        turn_id=TurnId("actual-turn"),
        server_binding="fixture",
        connection_id="connection",
        correlation=RequestLink(
            jsonrpc_id=1,
            direction=EventDirection.SERVER_TO_CLIENT,
            request_sequence=1,
        ),
        payload={"result": {"content": [{"type": "text", "text": "deterministic"}]}},
        provenance=provenance,
    )
    trace = recorder.finalize(ExecutionOutcome.COMPLETED)
    calls = trace.view().tool_calls
    assert len(calls) == 1
    call = calls[0]
    assert call.turn_id == TurnId("actual-turn")
    assert call.correlation.value == "correlated"
    assert call.wire.state.value == "observed"
    assert call.reported.state.value == "observed"
    assert call.tool.value == "echo"
    assert call.result.value.content[0].text == "deterministic"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_opencode_sqlite_reopen_preserves_public_trace_view(
    tmp_path: Path,
) -> None:
    base = _launch()
    adapter = OpenCodeHarnessAdapter(executable="fixture")
    adapter._launch = HarnessLaunch(
        base.spec.model_copy(update={"harness": OpenCode(model="fixture")}),
        base.servers,
        (),
        base.tool_policy,
    )
    post = {
        "info": {
            "id": "final",
            "sessionID": "session",
            "parentID": "user-1",
            "providerID": "provider",
            "modelID": "model",
            "finish": "stop",
            "tokens": {"input": 1, "output": 2},
            "cost": 0.5,
        },
        "parts": [{"type": "text", "id": "text", "text": "done"}],
    }
    history = {
        "info": {
            "id": "tool-message",
            "sessionID": "session",
            "role": "assistant",
            "parentID": "user-1",
        },
        "parts": [
            {
                "type": "tool",
                "id": "tool-part",
                "tool": "fixture_echo",
                "callID": "provider-call",
                "state": {
                    "status": "completed",
                    "input": {"nonce": "sqlite"},
                    "output": {"nonce": "sqlite"},
                },
            }
        ],
    }
    client = _HistoryFixture(post, [history], [])
    adapter._client = cast(Any, client)
    turn = await adapter._send(HarnessTurnRequest.from_message("hello"), 1, "session")
    assert turn.turn_evidence is not None

    database = tmp_path / "opencode.sqlite"
    blobs = tmp_path / "blobs"
    store = SQLiteExecutionStore(database, blob_root=blobs)
    recorder = ExecutionTraceRecorder(store, "opencode-sqlite")
    sink = HarnessObservationSink(recorder, turn_id=TurnId("actual-turn"))
    for observation in turn.turn_evidence.observations:
        sink.emit(observation)
    provenance = EventSource(origin=EventOrigin.WIRE_OBSERVED, source="direct")
    for kind, direction, payload in (
        (
            EventKind.MCP_REQUEST,
            EventDirection.CLIENT_TO_SERVER,
            {
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"nonce": "sqlite"}},
            },
        ),
        (
            EventKind.MCP_RESPONSE,
            EventDirection.SERVER_TO_CLIENT,
            {"result": {"content": [{"type": "text", "text": "sqlite"}]}},
        ),
    ):
        recorder.emit(
            kind,
            turn_id=TurnId("actual-turn"),
            server_binding="fixture",
            connection_id="connection",
            correlation=RequestLink(
                jsonrpc_id=1,
                direction=direction,
                request_sequence=1,
            ),
            payload=payload,
            provenance=provenance,
        )
    trace = recorder.finalize(ExecutionOutcome.COMPLETED)
    first_view = trace.view()
    store.close()
    reopened = SQLiteExecutionStore(database, blob_root=blobs)
    reopened_trace = ExecutionTraceRecorder(reopened, trace.execution_id).finalize(
        ExecutionOutcome.COMPLETED
    )
    second_view = reopened_trace.view()
    assert second_view.runtime == first_view.runtime
    assert second_view.raw_messages == first_view.raw_messages
    assert second_view.tool_calls == first_view.tool_calls
    assert second_view.runtime.provider_id.value == "provider"
    assert second_view.runtime.model_id.value == "model"
    assert second_view.runtime.finish_reason.value == "stop"
    assert second_view.runtime.usage.state.value == "observed"
    assert second_view.runtime.http_lifecycle.value["status_code"] == 200
    assert second_view.tool_calls[0].turn_id == TurnId("actual-turn")
    reopened.close()


@pytest.mark.asyncio
async def test_opencode_malformed_metadata_projects_unavailable_not_omitted() -> None:
    base = _launch()
    adapter = OpenCodeHarnessAdapter(executable="fixture")
    adapter._launch = HarnessLaunch(
        base.spec.model_copy(update={"harness": OpenCode(model="fixture")}),
        base.servers,
        (),
        base.tool_policy,
    )
    payload = {
        "info": {
            "id": ["bad"],
            "sessionID": "session",
            "providerID": ["bad"],
            "modelID": ["bad"],
            "finish": 3,
            "tokens": {"input": "bad"},
            "cost": "bad",
        },
        "parts": [{"type": "text", "id": "bad\npart", "text": "done"}],
    }
    adapter._client = cast(Any, _TurnFixture(payload))
    turn = await adapter._send(HarnessTurnRequest.from_message("hello"), 1, "session")
    assert turn.turn_evidence is not None
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "malformed-metadata")
    sink = HarnessObservationSink(recorder, turn_id=TurnId("turn-1"))
    for observation in turn.turn_evidence.observations:
        sink.emit(observation)
    view = recorder.finalize(ExecutionOutcome.COMPLETED).view()
    assert view.runtime.provider_id.state.value == "unavailable"
    assert view.runtime.model_id.state.value == "unavailable"
    assert view.runtime.finish_reason.state.value == "unavailable"
    assert view.runtime.usage.state.value == "unavailable"
    assert len(view.messages) == 1
    assert view.messages[0].message_id.state.value == "unavailable"
    assert view.messages[0].message_id.reason.value == "malformed_source"


@pytest.mark.parametrize(
    ("case", "version", "requested", "ready"),
    (
        ("stable-auto", "1.18.15", "auto", True),
        ("v2-auto", "2.0.0", "auto", True),
        ("unknown-version", "future", "auto", False),
        ("legacy-explicit-match", "1.18.15", "legacy", True),
        ("v2-explicit-match", "2.0.0", "v2", True),
        ("legacy-on-v2-mismatch", "2.0.0", "legacy", False),
        ("v2-on-stable-mismatch", "1.18.15", "v2", False),
    ),
    ids=lambda value: value if isinstance(value, str) and "-" in value else None,
)
@pytest.mark.asyncio
async def test_opencode_readiness_detects_versioned_dialect(
    tmp_path: Path, case: str, version: str, requested: str, ready: bool
) -> None:
    executable = _executable(
        tmp_path / f"{case}.py",
        f"""\
import sys
if "--version" in sys.argv: print({version!r}); raise SystemExit(0)
if "--help" in sys.argv: print("serve"); raise SystemExit(0)
""",
    )
    adapter = OpenCodeHarnessAdapter(executable=executable)
    launch = _launch()
    launch = HarnessLaunch(
        launch.spec.model_copy(
            update={
                "harness": OpenCode(
                    model="fixture",
                    dialect=cast(Literal["auto", "legacy", "v2"], requested),
                )
            }
        ),
        launch.servers,
        launch.configurations,
        launch.tool_policy,
    )
    readiness = await adapter.preflight(launch)
    assert readiness.ready is ready, case
    if not ready:
        assert readiness.reason


@pytest.mark.asyncio
async def test_opencode_dialect_probe_does_not_receive_ambient_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "probe-env.json"
    executable = _executable(
        tmp_path / "probe.py",
        f"""\
import json, os, sys
if "--version" in sys.argv:
    with open({str(marker)!r}, "w", encoding="utf-8") as output:
        json.dump({{name: bool(os.environ.get(name)) for name in ("OPENCODE_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY")}}, output)
    print("1.18.15")
    raise SystemExit(0)
if "--help" in sys.argv:
    print("serve")
    raise SystemExit(0)
""",
    )
    monkeypatch.setenv("OPENCODE_API_KEY", "ambient-opencode")
    monkeypatch.setenv("OPENROUTER_API_KEY", "ambient-openrouter")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-anthropic")
    adapter = OpenCodeHarnessAdapter(
        executable=executable, environment={"MCP_PAL_PROBE_MARKER": str(marker)}
    )
    readiness = await adapter.preflight(_launch())
    assert readiness.ready
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "OPENCODE_API_KEY": False,
        "OPENROUTER_API_KEY": False,
        "ANTHROPIC_API_KEY": False,
    }


@pytest.mark.asyncio
async def test_opencode_open_uses_cached_dialect_without_second_version_probe(
    tmp_path: Path,
) -> None:
    version_marker = tmp_path / "version-count"
    marker = tmp_path / "server.json"
    fixture = str(Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py")
    adapter = OpenCodeHarnessAdapter(
        executable=fixture,
        environment={
            "MCP_PAL_MARKER": str(marker),
            "MCP_PAL_VERSION_MARKER": str(version_marker),
        },
    )
    try:
        await adapter.open(_launch())
    finally:
        await adapter.close()
    assert version_marker.read_text(encoding="utf-8") == "1"


def _workspace_launch(
    workspace: Path, *, configurations: tuple[HarnessServerConfig, ...] = ()
) -> HarnessLaunch:
    base = _launch()
    return HarnessLaunch(
        base.spec,
        base.servers,
        configurations,
        base.tool_policy,
        workspace_root=str(workspace),
    )


def _executable(path: Path, source: str) -> str:
    path.write_text("#!/usr/bin/env python3\n" + source, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o100)
    return str(path)


def _claude_workspace_fixture(path: Path) -> str:
    return _executable(
        path,
        """\
import json, os, sys
if "--help" in sys.argv:
    print("stream-json")
    raise SystemExit(0)
config = sys.argv[sys.argv.index("--mcp-config") + 1]
for line in sys.stdin:
    json.loads(line)
    print(json.dumps({"type": "result", "result": json.dumps({
        "cwd": os.getcwd(), "home": os.environ["HOME"],
        "config": config, "config_exists": os.path.exists(config),
    })}), flush=True)
    """,
    )


def _claude_policy_fixture(path: Path) -> str:
    return _executable(
        path,
        """\
import json, os, sys
if "--help" in sys.argv:
    print("--input-format stream-json --output-format stream-json")
    raise SystemExit(0)
marker = os.environ.get("MCP_PAL_MARKER")
if marker:
    with open(marker, "w") as output:
        json.dump(sys.argv, output)
for line in sys.stdin:
    json.loads(line)
    print(json.dumps({"type":"result", "result":"ok"}), flush=True)
""",
    )


def _opencode_workspace_fixture(path: Path) -> str:
    return _executable(
        path,
        """\
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
if "--version" in sys.argv:
    print("1.18.15")
    raise SystemExit(0)
if "--help" in sys.argv or "serve" in sys.argv and "--help" in sys.argv:
    print("serve")
    raise SystemExit(0)
session = "workspace-session"
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args): pass
    def send_json(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status); self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(data)
    def do_POST(self):
        if self.path == "/session":
            with open(os.environ["MCP_PAL_MARKER"], "w") as output:
                json.dump({"cwd": os.getcwd(), "home": os.environ["HOME"], "directory": self.headers.get("x-opencode-directory"), "config": os.environ["OPENCODE_CONFIG"]}, output)
            self.send_json({"id": session}); return
        if self.path == "/session/" + session + "/message":
            size = int(self.headers.get("Content-Length", "0")); value = json.loads(self.rfile.read(size))
            self.send_json({"info": {"finish": "stop"}, "parts": [{"type": "text", "text": value["parts"][0]["text"]}]}); return
        self.send_json({}, 404)
    def do_GET(self):
        self.send_json({}, 404)
    def do_DELETE(self): self.send_json({}, 204)
server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print("http://127.0.0.1:%d" % server.server_address[1], flush=True)
server.serve_forever()
""",
    )


def _acp_workspace_fixture(path: Path) -> str:
    return _executable(
        path,
        """\
import json, os, sys
def send(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    request = json.loads(line); method = request.get("method"); ident = request.get("id")
    if method == "initialize":
        send({"jsonrpc":"2.0", "id":ident, "result":{"protocolVersion":1}})
    elif method == "session/new":
        with open(os.environ["MCP_PAL_MARKER"], "w") as output:
            json.dump({"cwd":os.getcwd(), "home":os.environ["HOME"], "session_cwd":request["params"]["cwd"], "servers":request["params"]["mcpServers"]}, output)
        send({"jsonrpc":"2.0", "id":ident, "result":{"sessionId":"workspace-session"}})
    elif method == "session/prompt":
        send({"jsonrpc":"2.0", "method":"session/update", "params":{"sessionId":"workspace-session", "update":{"sessionUpdate":"agent_message_chunk", "content":{"type":"text", "text":"ok"}}}})
        send({"jsonrpc":"2.0", "id":ident, "result":{"stopReason":"end_turn"}})
""",
    )


def _acp_startup_secret_fixture(path: Path, marker: Path) -> str:
    canary = "acp-server-secret-canary"
    return _executable(
        path,
        f"""\
import json, sys
CANARY = {canary!r}
def send(value): print(json.dumps(value), flush=True)
# Deliberately race startup redaction: this is emitted before initialize.
send({{"jsonrpc":"2.0", "method":"startup/notice", "params":{{"canary":CANARY}}}})
for line in sys.stdin:
    request = json.loads(line); method = request.get("method"); ident = request.get("id")
    if method == "initialize":
        send({{"jsonrpc":"2.0", "id":ident, "result":{{"protocolVersion":1}}}})
    elif method == "session/new":
        with open({str(marker)!r}, "w", encoding="utf-8") as output:
            json.dump(request["params"], output)
        send({{"jsonrpc":"2.0", "id":ident, "result":{{"sessionId":"secret-session"}}}})
    elif method == "session/prompt":
        send({{"jsonrpc":"2.0", "method":"session/update", "params":{{"sessionId":"secret-session", "update":{{"sessionUpdate":"agent_message_chunk", "content":{{"type":"text", "text":CANARY}}}}}}}})
        send({{"jsonrpc":"2.0", "id":ident, "result":{{"stopReason":"end_turn"}}}})
""",
    )


@pytest.mark.asyncio
async def test_claude_uses_one_stream_process_across_turns() -> None:
    executable = str(
        Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py"
    )
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_launch())
    first = await session.send(HarnessTurnRequest.from_message("one"))
    second = await session.send(HarnessTurnRequest.from_message("two"))
    assert first.response is not None and _response_text(first) == "one"
    assert second.response is not None and _response_text(second) == "two"
    assert session.snapshot().turns == 2
    await adapter.close()


@pytest.mark.asyncio
async def test_claude_rich_stream_maps_to_typed_observations_and_runtime() -> None:
    executable = str(
        Path(__file__).parents[1] / "fixtures" / "claude_observability_fixture.py"
    )
    base = _launch()
    spec = base.spec.model_copy(
        update={"harness": ClaudeCode(model="fixture", executable=executable)}
    )
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        session = kit.agent_session(spec, adapter=adapter)
        async with session:
            result = await session.send("nonce")
        assert result.error is None
        assert session.result.trace is not None
        view = session.result.trace.view()
        assert view.runtime.kind == "claude_code"
        assert view.runtime.session_id.value == "claude-session"
        assert view.runtime.model_id.value == "fixture-model"
        assert view.runtime.stop_reason.value == "end_turn"
        assert view.runtime.service_tier.value == "standard"
        assert view.runtime.api_duration_ms.value == 12.5
        assert view.runtime.encrypted_reasoning.value is True
        assert view.runtime.usage.value.cache_read_tokens.value == 4
        assert view.runtime.usage.value.cost.value == 0.25
        assert len(view.tool_calls) == 1
        assert view.tool_calls[0].provider_call_id.value == "toolu-1"
        assert len(view.reasoning) == 2
        assert view.reasoning[0].content.state.value == "observed"
        assert view.reasoning[1].content.state.value == "encrypted"
        assert view.raw_messages


@pytest.mark.asyncio
async def test_claude_partial_stream_replays_without_suffix_deduplication() -> None:
    executable = str(
        Path(__file__).parents[1]
        / "fixtures"
        / "claude_partial_observability_fixture.py"
    )
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_launch())
    result = await session.send(HarnessTurnRequest.from_message("nonce"))
    assert result.error is None
    messages = [
        item
        for item in result.turn_evidence.observations
        if item.kind == "message_chunk"
    ]
    assert [item.text for item in messages] == ["same", "same", "samesame"]
    assert [item.complete for item in messages] == [False, False, True]
    calls = [
        item
        for item in result.turn_evidence.observations
        if item.kind == "tool_call_observed"
    ]
    assert len(calls) == 1
    assert calls[0].arguments == {"text": "ok"}
    assert not any(
        item.phase == "exited"
        for item in result.turn_evidence.observations
        if item.kind == "process_observed"
    )
    raw = " ".join(
        item.raw_evidence.content
        for item in result.turn_evidence.observations
        if item.kind == "raw_frame" and item.raw_evidence is not None
    )
    assert "secret-signature-canary" not in raw
    await adapter.close()


@pytest.mark.asyncio
async def test_claude_partial_only_tool_reconstructs_input_json() -> None:
    executable = str(
        Path(__file__).parents[1]
        / "fixtures"
        / "claude_partial_observability_fixture.py"
    )
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_launch())
    result = await session.send(HarnessTurnRequest.from_message("partial-only"))
    assert result.error is None
    calls = [
        item
        for item in result.turn_evidence.observations
        if item.kind == "tool_call_observed"
    ]
    assert len(calls) == 1
    assert calls[0].arguments == {"text": "ok"}
    assert calls[0].status == "incomplete"
    await adapter.close()


@pytest.mark.asyncio
async def test_claude_builtin_underscore_tool_is_not_mcp_traffic() -> None:
    executable = str(
        Path(__file__).parents[1]
        / "fixtures"
        / "claude_partial_observability_fixture.py"
    )
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_launch())
    result = await session.send(HarnessTurnRequest.from_message("builtin"))
    assert result.error is None
    calls = [
        item
        for item in result.turn_evidence.observations
        if item.kind == "tool_call_observed"
    ]
    assert len(calls) == 1
    assert calls[0].tool == "Read_File"
    assert calls[0].server is None
    assert result.evidence["mcp_traffic_observed"] is False
    await adapter.close()


@pytest.mark.asyncio
async def test_claude_credential_descriptor_enters_spec_literal_reaches_child_only_at_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = "claude-credential-canary"
    monkeypatch.setenv("ANTHROPIC_API_KEY", canary)
    executable = _executable(
        tmp_path / "claude-credential.py",
        """\
import json, os, sys
if "--help" in sys.argv:
    print("--input-format stream-json --output-format stream-json")
    raise SystemExit(0)
for line in sys.stdin:
    json.loads(line)
    print(json.dumps({"type":"result", "result": os.environ.get("ANTHROPIC_API_KEY", "")}), flush=True)
""",
    )
    base = _launch()
    spec = base.spec.model_copy(
        update={
            "harness": ClaudeCode(
                model="fixture",
                executable=executable,
                credential_references={
                    "ANTHROPIC_API_KEY": SecretReference(
                        source="environment", name="ANTHROPIC_API_KEY"
                    )
                },
            )
        }
    )
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    session = await adapter.open(
        HarnessLaunch(spec, base.servers, base.configurations, base.tool_policy)
    )
    result = await session.send(HarnessTurnRequest.from_message("credential"))
    assert _response_text(result) == canary
    assert isinstance(spec.harness, ClaudeCode)
    assert (
        spec.harness.credential_references["ANTHROPIC_API_KEY"].name
        == "ANTHROPIC_API_KEY"
    )
    assert canary not in json.dumps(spec.model_dump(mode="json"))
    await adapter.close()


@pytest.mark.asyncio
async def test_claude_native_policy_is_preflighted_and_applied_to_argv(
    tmp_path: Path,
) -> None:
    executable = _claude_policy_fixture(tmp_path / "claude-policy.py")
    for mode in ("mcp_only", "mcp_read_only", "full"):
        marker = tmp_path / f"{mode}.json"
        base = _launch()
        policy = NativeToolPolicy(
            harness="claude-code",
            policy={
                "mode": mode,
                "server": "stdio",
                "read_only_tools": ("Read", "Glob"),
            },
            nonportable_reason="Claude Code CLI tool allowlist",
        )
        spec = base.spec.model_copy(
            update={
                "harness": ClaudeCode(model="fixture", executable=executable),
                "tool_policy": policy,
            }
        )
        adapter = ClaudeCodeHarnessAdapter(
            executable=executable, environment={"MCP_PAL_MARKER": str(marker)}
        )
        configuration = HarnessServerConfig(
            key="stdio",
            transport=TransportKind.STDIO,
            required=True,
            available=True,
            connection_id="stdio",
            command="python",
        )
        servers = ServerGroupSnapshot(
            (
                ServerRecord(
                    "stdio",
                    spec.servers[0].server,
                    True,
                    True,
                    "stdio",
                    TransportKind.STDIO,
                ),
            )
        )
        launch = HarnessLaunch(spec, servers, (configuration,), policy)
        readiness = await adapter.preflight(launch)
        assert readiness.ready and adapter.last_policy_evidence is not None
        assert adapter.last_policy_evidence.enforced == "native"
        session = await adapter.open(launch)
        await session.send(HarnessTurnRequest.from_message("policy"))
        await adapter.close()
        argv = json.loads(marker.read_text(encoding="utf-8"))
        if mode == "mcp_only":
            assert argv[argv.index("--tools") + 1] == ""
            assert argv[argv.index("--allowedTools") + 1] == "mcp__stdio__*"
        elif mode == "mcp_read_only":
            assert argv[argv.index("--tools") + 1] == "Read,Glob"
            assert argv[argv.index("--allowedTools") + 1] == "Read,Glob,mcp__stdio__*"
        else:
            assert "--dangerously-skip-permissions" in argv


@pytest.mark.asyncio
async def test_opencode_native_policy_is_preflighted_and_rendered_for_each_mode(
    tmp_path: Path,
) -> None:
    executable = str(
        Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py"
    )
    configuration = HarnessServerConfig(
        key="stdio",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="stdio",
        command="python",
    )
    server = StdioServer(name="stdio", command="python")
    servers = ServerGroupSnapshot(
        (ServerRecord("stdio", server, True, True, "stdio", TransportKind.STDIO),)
    )
    for mode in ("mcp_only", "mcp_read_only", "full"):
        policy = NativeToolPolicy(
            harness="opencode",
            policy={
                "mode": mode,
                "server": "stdio",
                "read_only_tools": ("read", "glob"),
            },
            nonportable_reason="OpenCode permissions",
        )
        spec = AgentSpec(
            harness=OpenCode(model="fixture"),
            servers=(ServerBinding(server=server, alias="stdio"),),
            tool_policy=policy,
        )
        launch = HarnessLaunch(spec, servers, (configuration,), policy)
        adapter = OpenCodeHarnessAdapter(
            executable=executable, environment={"MCP_PAL_OPENCODE_MODE": "legacy"}
        )
        readiness = await adapter.preflight(launch)
        assert readiness.ready and adapter.last_policy_evidence is not None
        assert adapter.last_policy_evidence.enforced == "native"
        rendered = opencode_configuration(launch, dialect="legacy")
        rendered_v2 = opencode_configuration(launch, dialect="v2")
        assert rendered_v2["mcp"]["servers"]["stdio"]["type"] == "local"
        assert rendered_v2["tools"] == rendered["tools"]
        assert rendered_v2["permission"] == rendered["permission"]
        if mode == "full":
            assert rendered["tools"] == {"*": True}
            assert rendered["permission"] == {"*": "allow"}
        elif mode == "mcp_only":
            assert rendered["tools"] == {"*": False, "stdio_*": True}
            assert rendered["permission"] == {"*": "deny", "stdio_*": "allow"}
        else:
            assert rendered["tools"] == {
                "*": False,
                "stdio_*": True,
                "read": True,
                "glob": True,
            }
            assert rendered["permission"] == {
                "*": "deny",
                "stdio_*": "allow",
                "read": "allow",
                "glob": "allow",
            }


@pytest.mark.asyncio
async def test_claude_cleanup_is_idempotent() -> None:
    executable = str(
        Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py"
    )
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    await adapter.open(_launch())
    await adapter.close()
    await adapter.close()


@pytest.mark.asyncio
async def test_claude_keeps_config_until_owned_process_cleanup() -> None:
    executable = str(
        Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py"
    )
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    await adapter.open(_launch())
    config = adapter._config
    assert config is not None and config.exists()
    assert config.stat().st_mode & 0o777 == 0o600
    await adapter.close()
    assert not config.exists()


@pytest.mark.asyncio
async def test_opencode_uses_one_serve_and_session_across_turns() -> None:
    executable = str(
        Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py"
    )
    adapter = OpenCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_launch())
    try:
        first = await session.send(HarnessTurnRequest.from_message("one"))
        second = await session.send(HarnessTurnRequest.from_message("two"))
        assert first.response is not None and _response_text(first) == "one"
        assert second.response is not None and _response_text(second) == "two"
        assert session.snapshot().turns == 2
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_opencode_three_turns_retain_one_session_and_selected_model(
    tmp_path: Path,
) -> None:
    executable = str(
        Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py"
    )
    marker = tmp_path / "three-turns.json"
    base = _launch()
    spec = base.spec.model_copy(
        update={
            "harness": OpenCode(
                model="fixture", provider="opencode", executable=executable
            )
        }
    )
    launch = HarnessLaunch(spec, base.servers, base.configurations, base.tool_policy)
    adapter, session = await _open_opencode_fixture(marker, launch=launch)
    try:
        for text in ("one", "two", "three"):
            result = await session.send(HarnessTurnRequest.from_message(text))
            assert result.status == "completed"
        observed = await _wait_for_marker(
            marker, lambda value: len(value.get("message_urls", [])) == 3
        )
        assert observed["session_post_count"] == 1
        assert observed["session_id"] == session.session_id
        assert (
            observed["message_urls"] == [f"/session/{session.session_id}/message"] * 3
        )
        assert observed["message_bodies"] == [
            {
                "model": {"providerID": "opencode", "modelID": "fixture"},
                "parts": [{"type": "text", "text": text}],
            }
            for text in ("one", "two", "three")
        ]
        assert observed["child_pids"] == [observed["child_pid"]]
        assert observed["serve_pid"] != observed["child_pid"]
        assert _pid_alive(observed["serve_pid"])
        assert _pid_alive(observed["child_pid"])
    finally:
        await asyncio.wait_for(adapter.close(), timeout=4.0)
    await _assert_pid_dead(observed["serve_pid"])
    await _assert_pid_dead(observed["child_pid"])


async def _open_opencode_fixture(
    marker: Path,
    *,
    mode: str = "normal",
    launch: HarnessLaunch | None = None,
) -> tuple[OpenCodeHarnessAdapter, Any]:
    executable = str(
        Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py"
    )
    environment = {"MCP_PAL_MARKER": str(marker), "MCP_PAL_OPENCODE_MODE": mode}
    adapter = OpenCodeHarnessAdapter(executable=executable, environment=environment)
    session = await adapter.open(launch or _launch())
    return adapter, session


async def _wait_for_marker(
    path: Path, predicate: Any, timeout: float = 5.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            value = None
        if isinstance(value, dict) and predicate(value):
            return value
        await asyncio.sleep(0.01)
    raise AssertionError(f"fixture marker did not reach expected state: {path}")


def _pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


async def _assert_pid_dead(pid: object, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        await asyncio.sleep(0.02)
    assert not _pid_alive(pid), f"fixture process leaked: pid={pid}"


@pytest.mark.asyncio
async def test_opencode_timeout_reaps(tmp_path: Path) -> None:
    for iteration in range(10):
        marker = tmp_path / f"timeout-{iteration}.json"
        adapter, session = await _open_opencode_fixture(marker, mode="blocking-message")
        try:
            task = asyncio.create_task(
                session.send(
                    HarnessTurnRequest.from_message("timeout", timeout_seconds=0.05)
                )
            )
            observed = await _wait_for_marker(
                marker, lambda value: value.get("message_entered") is True
            )
            result = await asyncio.wait_for(task, timeout=3.0)
            assert result.status == "timed_out"
            assert result.turn_evidence is not None
            assert not any(
                item.kind == "process_observed"
                for item in result.turn_evidence.observations
            )
            serve_pid, child_pid = observed["serve_pid"], observed["child_pid"]
        finally:
            await asyncio.wait_for(adapter.close(), timeout=4.0)
        await _assert_pid_dead(serve_pid)
        await _assert_pid_dead(child_pid)


@pytest.mark.asyncio
async def test_opencode_cancel_reaps(tmp_path: Path) -> None:
    for iteration in range(10):
        marker = tmp_path / f"cancel-{iteration}.json"
        adapter, session = await _open_opencode_fixture(marker, mode="blocking-message")
        task: asyncio.Task[Any] = asyncio.create_task(
            session.send(HarnessTurnRequest.from_message("cancel"))
        )
        try:
            observed = await _wait_for_marker(
                marker, lambda value: value.get("message_entered") is True
            )
            await asyncio.wait_for(adapter.cancel(), timeout=4.0)
            try:
                result = await asyncio.wait_for(task, timeout=3.0)
            except (asyncio.CancelledError, OSError, RuntimeError):
                result = None
            if result is not None:
                assert result.status in ("failed", "timed_out")
                assert result.error is not None
            serve_pid, child_pid = observed["serve_pid"], observed["child_pid"]
        finally:
            await asyncio.wait_for(adapter.close(), timeout=4.0)
        await _assert_pid_dead(serve_pid)
        await _assert_pid_dead(child_pid)


@pytest.mark.asyncio
async def test_opencode_connection_loss_close_bounded(tmp_path: Path) -> None:
    for iteration in range(10):
        marker = tmp_path / f"close-{iteration}.json"
        adapter, session = await _open_opencode_fixture(marker, mode="socket-close")
        try:
            task = asyncio.create_task(
                session.send(
                    HarnessTurnRequest.from_message("close", timeout_seconds=2)
                )
            )
            observed = await _wait_for_marker(
                marker, lambda value: value.get("message_entered") is True
            )
            result = await asyncio.wait_for(task, timeout=3.0)
            assert result.status == "failed"
            assert result.error is not None
            assert result.error.code.value == "transport_error"
            serve_pid, child_pid = observed["serve_pid"], observed["child_pid"]
        finally:
            await asyncio.wait_for(adapter.close(), timeout=4.0)
        await _assert_pid_dead(serve_pid)
        await _assert_pid_dead(child_pid)


@pytest.mark.asyncio
async def test_opencode_close_idempotent_removes_owned_control(tmp_path: Path) -> None:
    marker = tmp_path / "close-idempotent.json"
    adapter, _session = await _open_opencode_fixture(marker)
    root = adapter._root
    await adapter.close()
    await adapter.close()
    assert adapter._owner is None and root is not None and not root.exists()


@pytest.mark.asyncio
async def test_opencode_workspace_control_isolation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = tmp_path / "workspace-marker.json"
    configuration = HarnessServerConfig(
        key="fixture",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="fixture",
        command=sys.executable,
        args=("-c", "print('mcp')"),
        environment={"MODE": "test"},
        cwd=str(workspace),
    )
    launch = _workspace_launch(workspace, configurations=(configuration,))
    expected_config = opencode_configuration(launch, dialect="legacy")
    adapter, _session = await _open_opencode_fixture(marker, launch=launch)
    root = adapter._root
    try:
        observed = await _wait_for_marker(
            marker, lambda value: value.get("config_content") is not None
        )
        assert root is not None and root.resolve() != workspace.resolve()
        assert Path(observed["cwd"]).resolve() == workspace.resolve()
        assert Path(observed["directory"]).resolve() == workspace.resolve()
        for key in (
            "home",
            "xdg_config_home",
            "xdg_data_home",
            "xdg_state_home",
            "opencode_config",
        ):
            assert Path(observed[key]).resolve() != workspace.resolve()
        assert Path(observed["opencode_config"]).resolve().parent == root.resolve()
        assert observed["config_exists"] is True
        assert observed["config_content"] == expected_config
    finally:
        await asyncio.wait_for(adapter.close(), timeout=4.0)
    assert root is not None and not root.exists()
    assert not Path(observed["opencode_config"]).exists()


def test_opencode_credential_reference_round_trips_as_descriptor_only() -> None:
    value = OpenCode(
        model="fixture",
        credential_references={
            "OPENCODE_API_KEY": SecretReference(
                source="environment", name="OPENCODE_API_KEY"
            )
        },
    )
    descriptor = value.model_dump(mode="json")
    assert descriptor == {
        "name": "opencode",
        "model": "fixture",
        "executable": None,
        "kind": "opencode",
        "provider": None,
        "dialect": "auto",
        "credential_references": {
            "OPENCODE_API_KEY": {"source": "environment", "name": "OPENCODE_API_KEY"}
        },
    }
    assert all(key not in repr(descriptor) for key in ("resolved", "value"))
    restored = OpenCode.model_validate(descriptor)
    assert restored == value
    assert "canary" not in repr(value.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_opencode_referenced_environment_credential_reaches_isolated_child_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "credential-canary")
    monkeypatch.setenv("OPENROUTER_API_KEY", "ambient-router-canary")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-anthropic-canary")
    marker = tmp_path / "credential.json"
    fixture = str(Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py")
    base = _launch()
    spec = base.spec.model_copy(
        update={
            "harness": OpenCode(
                model="fixture",
                provider="opencode",
                credential_references={
                    "OPENCODE_API_KEY": SecretReference(
                        source="environment", name="OPENCODE_API_KEY"
                    )
                },
            )
        }
    )
    adapter = OpenCodeHarnessAdapter(
        executable=fixture, environment={"MCP_PAL_MARKER": str(marker)}
    )
    try:
        await adapter.open(HarnessLaunch(spec, base.servers, (), base.tool_policy))
        assert adapter._owner is not None
        private_marker = marker.with_name(marker.name + ".child.json")
        child_env = await _wait_for_marker(
            private_marker,
            lambda value: value.get("OPENCODE_API_KEY") == "credential-canary",
        )
        assert child_env == {
            "OPENCODE_API_KEY": "credential-canary",
            "OPENROUTER_API_KEY": None,
            "ANTHROPIC_API_KEY": None,
            "MCP_API_KEY": None,
        }
        assert "credential-canary" not in repr(adapter)
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_opencode_missing_environment_credential_fails_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_PAL_MISSING", raising=False)
    base = _launch()
    spec = base.spec.model_copy(
        update={
            "harness": OpenCode(
                model="fixture",
                credential_references={
                    "KEY": SecretReference(source="environment", name="MCP_PAL_MISSING")
                },
            )
        }
    )
    adapter = OpenCodeHarnessAdapter(
        executable=str(
            Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py"
        )
    )
    try:
        with pytest.raises(HarnessStartupError):
            await adapter.open(HarnessLaunch(spec, base.servers, (), base.tool_policy))
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_opencode_provider_secret_reference_fails_closed() -> None:
    base = _launch()
    spec = base.spec.model_copy(
        update={
            "harness": OpenCode(
                model="fixture",
                credential_references={
                    "KEY": SecretReference(source="provider", name="provider-key")
                },
            )
        }
    )
    adapter = OpenCodeHarnessAdapter(
        executable=str(
            Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py"
        )
    )
    try:
        with pytest.raises(HarnessStartupError):
            await adapter.open(HarnessLaunch(spec, base.servers, (), base.tool_policy))
    finally:
        await adapter.close()


def test_opencode_invalid_target_environment_name_fails_closed() -> None:
    with pytest.raises(ValueError):
        OpenCode(
            model="fixture",
            credential_references={
                "BAD=NAME": SecretReference(source="environment", name="KEY")
            },
        )


@pytest.mark.asyncio
async def test_missing_native_executable_is_not_ready() -> None:
    adapter = ClaudeCodeHarnessAdapter(executable="mcp-pal-no-such-claude")
    readiness = await adapter.preflight(_launch())
    assert readiness.ready is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_type", "executable"),
    (
        (ClaudeCodeHarnessAdapter, "claude_stream_fixture.py"),
        (OpenCodeHarnessAdapter, "opencode_serve_fixture.py"),
    ),
)
async def test_native_adapters_fail_closed_for_explicit_tool_policy(
    adapter_type: type[ClaudeCodeHarnessAdapter] | type[OpenCodeHarnessAdapter],
    executable: str,
) -> None:
    path = str(Path(__file__).parents[1] / "fixtures" / executable)
    adapter = adapter_type(executable=path)
    spec = _spec().model_copy(
        update={"tool_policy": RestrictiveToolPolicy(allowed_tools=("fixture:read",))}
    )
    session = AsyncAgentSession(spec, adapter)
    with pytest.raises(UnsupportedFeature):
        await session.__aenter__()
    assert session._closed is True


def test_default_registry_selects_real_native_adapters_without_fake_fallback() -> None:
    registry = default_adapters()
    claude = registry.resolve(
        _spec().model_copy(
            update={"harness": ClaudeCode(model="fixture", executable="missing-claude")}
        )
    )
    opencode = registry.resolve(
        _spec().model_copy(
            update={"harness": OpenCode(model="fixture", executable="missing-opencode")}
        )
    )
    assert isinstance(claude, ClaudeCodeHarnessAdapter)
    assert isinstance(opencode, OpenCodeHarnessAdapter)


def test_native_mcp_config_redacts_credential_keys() -> None:
    base = _launch()
    launch = HarnessLaunch(
        base.spec,
        base.servers,
        (
            HarnessServerConfig(
                key="fixture",
                transport=TransportKind.STDIO,
                required=True,
                available=True,
                connection_id="connection-1",
                command="fixture",
                environment={"API_KEY": "secret-value", "SAFE": "safe-value"},
                headers={"Authorization": "Bearer secret-value"},
            ),
        ),
        base.tool_policy,
    )
    config = _server_configuration(launch)
    server = config["mcpServers"]["fixture"]
    assert server["env"]["API_KEY"] == "[REDACTED]"
    assert server["env"]["SAFE"] == "safe-value"
    assert "secret-value" not in repr(config)


def test_native_mcp_config_resolves_environment_reference_only_in_0600_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_PAL_NATIVE_SECRET", "native-secret-value")
    base = _launch()
    launch = HarnessLaunch(
        base.spec,
        base.servers,
        (
            HarnessServerConfig(
                key="fixture",
                transport=TransportKind.STDIO,
                required=True,
                available=True,
                connection_id="connection-1",
                command="fixture",
                environment={
                    "TOKEN": SecretReference(
                        source="environment", name="MCP_PAL_NATIVE_SECRET"
                    )
                },
            ),
        ),
        base.tool_policy,
    )
    config = write_config(tmp_path, launch)
    assert config.stat().st_mode & 0o777 == 0o600
    assert "native-secret-value" in config.read_text(encoding="utf-8")
    assert "native-secret-value" not in repr(launch)
    config.unlink()


def test_native_mcp_config_rejects_provider_reference_without_resolver(
    tmp_path: Path,
) -> None:
    base = _launch()
    launch = HarnessLaunch(
        base.spec,
        base.servers,
        (
            HarnessServerConfig(
                key="fixture",
                transport=TransportKind.STDIO,
                required=True,
                available=True,
                connection_id="connection-1",
                command="fixture",
                environment={
                    "TOKEN": SecretReference(source="provider", name="provider-key")
                },
            ),
        ),
        base.tool_policy,
    )
    with pytest.raises(HarnessStartupError) as caught:
        write_config(tmp_path, launch)
    assert "provider-key" not in str(caught.value)
    assert not (tmp_path / "mcp-config.json").exists()


@pytest.mark.asyncio
async def test_public_kit_preserves_native_claude_conversation_for_three_turns() -> (
    None
):
    executable = str(
        Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py"
    )
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        session = kit.agent_session(_spec(), adapter=adapter)
        async with session:
            for text in ("one", "two", "three"):
                result = await session.send(text)
                assert result.error is None
                assert result.evidence["usage_requested"] is True
                assert result.evidence["usage_enforced"] is False
                assert result.evidence["usage_observed"] is True
                assert result.evidence["usage_unavailable"] is False
        assert len(session.result.turns) == 3
        assert session.result.snapshot.outcome is not None
        assert session.result.snapshot.outcome.value == "completed"


@pytest.mark.asyncio
async def test_public_kit_default_registry_uses_real_claude_adapter() -> None:
    executable = str(
        Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py"
    )
    spec = _spec().model_copy(
        update={"harness": ClaudeCode(model="fixture", executable=executable)}
    )
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        session = kit.agent_session(spec)
        async with session:
            for text in ("one", "two", "three"):
                result = await session.send(text)
                assert result.error is None
        assert type(session.adapter).__name__ == "ClaudeCodeHarnessAdapter"
        assert len(session.result.turns) == 3


@pytest.mark.asyncio
async def test_public_kit_exposes_opencode_usage_evidence() -> None:
    executable = str(
        Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py"
    )
    spec = _spec().model_copy(
        update={"harness": OpenCode(model="fixture", executable=executable)}
    )
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        session = kit.agent_session(spec)
        async with session:
            results = [await session.send(text) for text in ("one", "two")]
            for result in results:
                assert result.error is None
                assert result.evidence["usage_requested"] is True
                assert result.evidence["usage_enforced"] is False
                assert result.evidence["usage_observed"] is True
                assert result.evidence["usage_unavailable"] is False
        assert session.result.trace is not None
        runtime = session.result.trace.view().runtime
        assert runtime.kind == "opencode"
        assert runtime.provider_id.value == "opencode"
        assert runtime.model_id.value == "fixture"
        assert runtime.finish_reason.value == "stop"
        assert runtime.session_id.state.value == "observed"
        assert runtime.usage.state.value == "observed"
        assert runtime.http_lifecycle.state.value == "observed"
        assert runtime.http_lifecycle.value["method"] == "POST"
        assert runtime.http_lifecycle.value["route"] == "/session/{session_id}/message"
        assert runtime.http_lifecycle.value["status_code"] == 200
        assert runtime.http_lifecycle.value["duration_ms"] >= 0
        view = session.result.trace.view()
        assert view.messages
        assert view.raw_messages
        assert view.summary.usage.state.value == "observed"
        assert view.summary.turn_count == 2
        for result in results:
            observed_turn_ids = {
                entry.turn_id
                for entry in view.timeline
                if entry.turn_id == result.snapshot.turn_id
            }
            assert observed_turn_ids == {result.snapshot.turn_id}


@pytest.mark.asyncio
async def test_native_claude_timeout_reaps_process_and_close_is_safe() -> None:
    executable = str(
        Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py"
    )
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_launch())
    result = await session.send(
        HarnessTurnRequest.from_message("sleep", timeout_seconds=0.05)
    )
    assert result.status == "timed_out"
    await adapter.close()
    assert adapter._owner is None


@pytest.mark.asyncio
async def test_native_claude_cancel_reaps_process() -> None:
    executable = str(
        Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py"
    )
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_launch())
    await session.cancel()
    await adapter.close()
    assert adapter._owner is None


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ("huge", "binary"))
async def test_native_claude_hostile_output_is_bounded_and_typed(message: str) -> None:
    executable = str(
        Path(__file__).parents[1] / "fixtures" / "claude_hostile_output_fixture.py"
    )
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_launch())
    result = await session.send(
        HarnessTurnRequest.from_message(message, timeout_seconds=2)
    )
    assert result.error is not None
    assert result.error.code.value == "protocol_error"
    await adapter.close()
    assert adapter._owner is None


@pytest.mark.asyncio
async def test_native_opencode_oversized_http_response_is_bounded_and_typed() -> None:
    executable = str(
        Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py"
    )
    adapter = OpenCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_launch())
    try:
        result = await session.send(
            HarnessTurnRequest.from_message("huge", timeout_seconds=2)
        )
        assert result.error is not None
        assert result.error.code.value == "protocol_error"
    finally:
        await adapter.close()
    assert adapter._owner is None


@pytest.mark.asyncio
async def test_claude_uses_workspace_cwd_and_separate_control_directory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "sdk-workspace"
    workspace.mkdir()
    executable = _claude_workspace_fixture(tmp_path / "claude-workspace.py")
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_workspace_launch(workspace))
    result = await session.send(HarnessTurnRequest.from_message("workspace"))
    observed = json.loads(_response_text(result))
    assert Path(observed["cwd"]).resolve() == workspace.resolve()
    assert Path(observed["home"]).resolve() != workspace.resolve()
    assert Path(observed["config"]).resolve().parent != workspace.resolve()
    assert observed["config_exists"]
    await adapter.close()


@pytest.mark.asyncio
async def test_opencode_uses_workspace_cwd_and_directory_header(tmp_path: Path) -> None:
    workspace = tmp_path / "sdk-workspace"
    workspace.mkdir()
    marker = tmp_path / "opencode-marker.json"
    executable = _opencode_workspace_fixture(tmp_path / "opencode-workspace.py")
    adapter = OpenCodeHarnessAdapter(
        executable=executable, environment={"MCP_PAL_MARKER": str(marker)}
    )
    try:
        session = await adapter.open(_workspace_launch(workspace))
        result = await session.send(HarnessTurnRequest.from_message("workspace"))
        assert result.status == "completed"
    finally:
        await adapter.close()
    observed = json.loads(marker.read_text(encoding="utf-8"))
    assert Path(observed["cwd"]).resolve() == workspace.resolve()
    assert Path(observed["directory"]).resolve() == workspace.resolve()
    assert Path(observed["home"]).resolve() != workspace.resolve()
    assert Path(observed["config"]).resolve().parent != workspace.resolve()


@pytest.mark.asyncio
async def test_acp_uses_workspace_cwd_session_and_explicit_mcp_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "sdk-workspace"
    workspace.mkdir()
    explicit = tmp_path / "explicit-mcp-cwd"
    explicit.mkdir()
    marker = tmp_path / "acp-marker.json"
    executable = _acp_workspace_fixture(tmp_path / "acp-workspace.py")
    monkeypatch.setenv("MCP_PAL_MARKER", str(marker))
    spec = AgentSpec(
        harness=ACPAgent(
            model="fixture",
            manifest={
                "command": executable,
                "protocol": "acp",
                "protocol_version": 1,
                "env": {"MCP_PAL_MARKER": "${MCP_PAL_MARKER}"},
            },
        ),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
    )
    configurations = (
        HarnessServerConfig(
            key="default",
            transport=TransportKind.STDIO,
            required=True,
            available=True,
            connection_id="default",
            command=sys.executable,
            args=("-m", "mcp_pal.transport.stdio_proxy", "--", "fixture"),
        ),
        HarnessServerConfig(
            key="explicit",
            transport=TransportKind.STDIO,
            required=True,
            available=True,
            connection_id="explicit",
            command=sys.executable,
            args=(
                "-m",
                "mcp_pal.transport.stdio_proxy",
                "--cwd",
                str(explicit),
                "--",
                "fixture",
            ),
            cwd=str(explicit),
        ),
    )

    class PolicyCapture:
        def enforces_portable_policy(self, values: Iterable[str]) -> bool:
            return tuple(values) == ("default", "explicit")

    launch = HarnessLaunch(
        spec,
        ServerGroupSnapshot(),
        configurations,
        spec.tool_policy,
        workspace_root=str(workspace),
        capture=PolicyCapture(),
    )
    adapter = AcpHarnessAdapter()
    session = await adapter.open(launch)
    assert (
        await session.send(HarnessTurnRequest.from_message("workspace"))
    ).status == "completed"
    await adapter.close()
    observed = json.loads(marker.read_text(encoding="utf-8"))
    assert Path(observed["cwd"]).resolve() == workspace.resolve()
    assert Path(observed["session_cwd"]).resolve() == workspace.resolve()
    assert Path(observed["home"]).resolve() != workspace.resolve()
    servers = {item["name"]: item for item in observed["servers"]}
    assert servers["default"]["command"] == sys.executable
    assert servers["explicit"]["command"] == sys.executable
    assert servers["default"]["args"].count("mcp_pal.transport.stdio_proxy") == 1
    assert "--cwd" not in servers["default"]["args"]
    assert servers["explicit"]["args"].count("mcp_pal.transport.stdio_proxy") == 1
    assert servers["explicit"]["args"][
        servers["explicit"]["args"].index("--cwd") + 1
    ] == str(explicit)


@pytest.mark.asyncio
async def test_acp_registers_server_canary_before_startup_and_omits_it_from_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A startup frame cannot race registration, and ACP never gets the credential."""

    canary = "acp-server-secret-canary"
    monkeypatch.setenv("MCP_PAL_ACP_SERVER_SECRET", canary)
    marker = tmp_path / "acp-secret-config.json"
    executable = _acp_startup_secret_fixture(tmp_path / "acp-secret.py", marker)
    base = _launch()
    spec = base.spec.model_copy(
        update={
            "harness": ACPAgent(
                model="fixture",
                manifest={
                    "command": executable,
                    "protocol": "acp",
                    "protocol_version": 1,
                },
            ),
        }
    )
    configuration = HarnessServerConfig(
        key="secret",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="secret",
        command="fixture",
        environment={
            "API_KEY": SecretReference(
                source="environment", name="MCP_PAL_ACP_SERVER_SECRET"
            )
        },
    )

    class PolicyCapture:
        def enforces_portable_policy(self, values: Iterable[str]) -> bool:
            return tuple(values) == ("secret",)

    launch = HarnessLaunch(
        spec,
        base.servers,
        (configuration,),
        base.tool_policy,
        workspace_root=str(tmp_path),
        capture=PolicyCapture(),
    )
    adapter = AcpHarnessAdapter()
    session = await adapter.open(launch)
    result = await session.send(HarnessTurnRequest.from_message("hello"))
    try:
        assert result.status == "completed"
        assert result.response is not None
        assert canary not in repr(result)
        assert all(canary not in repr(frame) for frame in session._frames)
        assert marker.exists()
        config = json.loads(marker.read_text(encoding="utf-8"))
        encoded = json.dumps(config, sort_keys=True)
        assert canary not in encoded
        assert config["mcpServers"][0]["env"] == []
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_acp_unresolved_server_reference_fails_before_harness_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "spawned"
    executable = _executable(
        tmp_path / "should-not-start.py",
        f"""\
from pathlib import Path
Path({str(marker)!r}).write_text("spawned", encoding="utf-8")
""",
    )
    monkeypatch.delenv("MCP_PAL_ACP_MISSING", raising=False)
    base = _launch()
    spec = base.spec.model_copy(
        update={
            "harness": ACPAgent(
                model="fixture",
                manifest={
                    "command": executable,
                    "protocol": "acp",
                    "protocol_version": 1,
                },
            ),
        }
    )
    configuration = HarnessServerConfig(
        key="missing",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="missing",
        command="fixture",
        environment={
            "TOKEN": SecretReference(source="environment", name="MCP_PAL_ACP_MISSING")
        },
    )

    class PolicyCapture:
        def enforces_portable_policy(self, values: Iterable[str]) -> bool:
            return tuple(values) == ("missing",)

    launch = HarnessLaunch(
        spec, base.servers, (configuration,), base.tool_policy, capture=PolicyCapture()
    )
    adapter = AcpHarnessAdapter()
    with pytest.raises(HarnessStartupError, match="not ready"):
        await adapter.open(launch)
    assert not marker.exists()
