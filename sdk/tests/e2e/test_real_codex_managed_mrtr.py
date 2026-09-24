"""Managed MRTR delivery through the installed, unmodified Codex App Server."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from fixtures.codex_mrtr_server import ADDRESS_SCHEMA, BUSINESS_ADDRESS, HOME_ADDRESS
from fixtures.codex_responses_provider import (
    ModelOutput,
    ResponsesRun,
    function_tools,
    open_codex_responses_provider,
)

from m3 import expect_form, maybe_url, round_of, sequence
from m3.async_api import AsyncMCPTestKit
from m3.elicitation import ElicitationResponse
from m3.errors import (
    ManagedInputConflict,
    ManagedInputStateError,
    ManagedInputValidationError,
)
from m3.harness.codex import CodexHarnessAdapter
from m3.harness.contracts import HarnessAdapterRegistry, HarnessLaunch
from m3.storage import SQLiteExecutionStore
from m3.storage.managed_input import ManagedInputRecord
from m3.sync_api import MCPTestKit
from m3.types import Codex, ExecutionOutcome, HarnessSpec, StdioServer, TurnOutcome

pytestmark = [pytest.mark.e2e, pytest.mark.process_lifecycle]

_ROOT = Path(__file__).parents[2]
_MCP_SERVER = _ROOT / "tests" / "fixtures" / "codex_mrtr_server.py"


def _require_codex() -> str:
    executable = os.environ.get("M3_CODEX_EXECUTABLE") or shutil.which("codex")
    if executable is None:
        if os.environ.get("M3_REQUIRE_CODEX_MRTR") == "1":
            pytest.fail("Codex 0.156.1 is required for the MRTR CI gate")
        pytest.skip("Codex 0.156.1 is unavailable on PATH; set M3_CODEX_EXECUTABLE")
    try:
        version = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        if os.environ.get("M3_REQUIRE_CODEX_MRTR") == "1":
            pytest.fail("Codex 0.156.1 could not be executed for the MRTR CI gate")
        pytest.skip("Codex could not be executed")
    expected = os.environ.get("M3_CODEX_MRTR_VERSION", "codex-cli 0.156.1")
    if version != expected:
        if os.environ.get("M3_REQUIRE_CODEX_MRTR") == "1":
            pytest.fail(f"expected {expected!r}, found {version!r}")
        pytest.skip(f"Codex version {expected!r} is required; found {version!r}")
    return executable


class FixtureCodexHarnessAdapter(CodexHarnessAdapter):
    """Point only the fixture's temporary Codex home at a local Responses server."""

    def __init__(self, *, executable: str, provider_url: str) -> None:
        super().__init__(executable=executable)
        self._provider_url = provider_url
        self.closed_stderr = ""
        self.native_frames: list[Any] = []
        self.native_writes: list[Any] = []
        self.turn_results: list[Any] = []

    async def next_frame(self, process: Any, timeout: float | None) -> Any:
        frame = await super().next_frame(process, timeout)
        if frame is not None:
            self.native_frames.append(dict(frame))
        return frame

    async def _write_frame(self, process: Any, payload: Any) -> None:
        self.native_writes.append(dict(payload))
        await super()._write_frame(process, payload)

    async def send(self, *args: Any, **kwargs: Any) -> Any:
        result = await super().send(*args, **kwargs)
        self.turn_results.append(result)
        return result

    async def close(self) -> None:
        process = self._process
        await super().close()
        owner = getattr(process, "owner", None)
        stderr_task = getattr(owner, "stderr_task", None)
        if (
            stderr_task is not None
            and stderr_task.done()
            and not stderr_task.cancelled()
        ):
            try:
                self.closed_stderr = stderr_task.result().decode(
                    "utf-8", errors="replace"
                )
            except Exception:
                self.closed_stderr = "<stderr capture failed>"

    def environment_for_launch(
        self, launch: HarnessLaunch, root: Path
    ) -> dict[str, str]:
        environment = dict(super().environment_for_launch(launch, root))
        codex_home = Path(environment["CODEX_HOME"])
        config_path = codex_home / "config.toml"
        config = config_path.read_text(encoding="utf-8")
        config = (
            'model = "m3-fixture-model"\n'
            'model_provider = "m3-fixture"\n\n'
            + config
            + "\n[model_providers.m3-fixture]\n"
            'name = "m3 local deterministic provider"\n'
            f"base_url = {json.dumps(self._provider_url)}\n"
            'wire_api = "responses"\n'
            "requires_openai_auth = false\n"
            "supports_websockets = false\n"
            "request_max_retries = 0\n"
            "stream_max_retries = 0\n"
        )
        config_path.write_text(config, encoding="utf-8")
        config_path.chmod(0o600)
        return environment


def _server(marker: Path) -> StdioServer:
    return StdioServer(
        name="fixture",
        command=sys.executable,
        args=(str(_MCP_SERVER),),
        cwd=str(_ROOT.parent),
        environment={"M3_CODEX_MRTR_WIRE_MARKER": str(marker)},
    )


def _entry(executable: str) -> dict[str, object]:
    return {
        "harness": "codex",
        "models": ["m3-fixture-model"],
        "executable": executable,
    }


@pytest.mark.asyncio
async def test_installed_codex_uses_local_deterministic_provider_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _require_codex()
    isolated_source_home = tmp_path / "empty-codex-source-home"
    isolated_source_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(isolated_source_home))
    provider = ResponsesRun()
    provider.enqueue(ModelOutput(text="local deterministic response"))
    with open_codex_responses_provider(provider) as provider_url:

        def make_adapter(harness: HarnessSpec) -> FixtureCodexHarnessAdapter:
            if not isinstance(harness, Codex):
                raise TypeError("Codex fixture received a non-Codex harness")
            return FixtureCodexHarnessAdapter(
                executable=harness.executable or executable,
                provider_url=provider_url,
            )

        registry = HarnessAdapterRegistry({"codex": make_adapter})
        marker = tmp_path / "provider-smoke-wire.jsonl"
        async with AsyncMCPTestKit(
            env={}, cwd=str(_ROOT.parent), adapter_registry=registry
        ) as kit:
            async with kit.agents([_entry(executable)])[0].session(
                server=_server(marker)
            ) as session:
                turn = await session.send("Say hello.")

    assert turn.snapshot.outcome.value == "completed", {
        "error": turn.error,
        "mcp_wire": _wire_records(marker),
        "provider_requests": [request.body for request in provider.requests],
    }
    assert turn.response is not None
    assert "local deterministic response" in turn.response.text
    assert len(provider.requests) == 1
    assert "authorization" not in provider.requests[0].headers


@pytest.mark.asyncio
async def test_installed_codex_fails_safely_for_identical_unkeyed_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _require_codex()
    isolated_source_home = tmp_path / "empty-codex-source-home"
    isolated_source_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(isolated_source_home))
    marker = tmp_path / "ambiguous-wire.jsonl"
    provider = ResponsesRun()
    provider.enqueue(
        ModelOutput(function_name="mcp__fixture::ambiguous_round", arguments={})
    )
    provider.enqueue(ModelOutput(text="The ambiguous operation was stopped."))
    plan = round_of(
        expect_form(
            "first_address",
            message="Enter an address.",
            schema=ADDRESS_SCHEMA,
        ).accept(HOME_ADDRESS),
        expect_form(
            "second_address",
            message="Enter an address.",
            schema=ADDRESS_SCHEMA,
        ).accept(BUSINESS_ADDRESS),
    )
    adapter: FixtureCodexHarnessAdapter | None = None
    with open_codex_responses_provider(provider) as provider_url:

        def make_adapter(harness: HarnessSpec) -> FixtureCodexHarnessAdapter:
            nonlocal adapter
            if not isinstance(harness, Codex):
                raise TypeError("Codex fixture received a non-Codex harness")
            adapter = FixtureCodexHarnessAdapter(
                executable=harness.executable or executable,
                provider_url=provider_url,
            )
            return adapter

        registry = HarnessAdapterRegistry({"codex": make_adapter})
        async with AsyncMCPTestKit(
            env={}, cwd=str(_ROOT.parent), adapter_registry=registry
        ) as kit:
            async with kit.agents([_entry(executable)])[0].session(
                server=_server(marker),
                tools=["fixture:ambiguous_round"],
                permission_policy="allow",
            ) as session:
                turn = await session.send(
                    "Use fixture ambiguous_round once.", elicitation=plan
                )

    assert turn.snapshot.outcome is TurnOutcome.FAILED, turn.error
    assert adapter is not None
    assert adapter.turn_results
    action_turn = adapter.turn_results[-1]
    assert action_turn.error is not None
    native_prompt_summary = [
        {
            "id": frame.get("id"),
            "params": frame.get("params"),
        }
        for frame in adapter.native_frames
        if frame.get("method") == "mcpServer/elicitation/request"
    ]
    assert action_turn.error.details.get("reason") == "ambiguous_native_prompt", (
        action_turn.error.model_dump(mode="json"),
        native_prompt_summary,
        _wire_calls(marker),
    )
    elicitation_frames = [
        frame
        for frame in adapter.native_frames
        if frame.get("method") == "mcpServer/elicitation/request"
        and not (
            isinstance(frame.get("params"), dict)
            and isinstance(frame["params"].get("_meta"), dict)
            and frame["params"]["_meta"].get("codex_approval_kind")
            == "mcp_tool_call"
        )
    ]
    assert len(elicitation_frames) == 2
    elicitation_ids = {frame.get("id") for frame in elicitation_frames}
    assert all(
        frame.get("id") not in elicitation_ids
        for frame in adapter.native_writes
        if "result" in frame or "error" in frame
    )
    calls = [call for call in _wire_calls(marker) if call["name"] == "ambiguous_round"]
    assert len(calls) == 1
    assert calls[0].get("requestState") is None
    assert "inputResponses" not in calls[0]
    assert provider.requests
    assert len(provider.requests) == 1
    assert "authorization" not in provider.requests[0].headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_round_limit", "expected_finish_reason"),
    ((9, "interrupted"), (10, "completed")),
)
async def test_installed_codex_adapter_enforces_round_limit_and_native_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requested_round_limit: int,
    expected_finish_reason: str,
) -> None:
    executable = _require_codex()
    isolated_source_home = tmp_path / "empty-codex-source-home"
    isolated_source_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(isolated_source_home))
    marker = tmp_path / "ten-round-wire.jsonl"
    provider = ResponsesRun()
    provider.enqueue(
        ModelOutput(function_name="mcp__fixture::ten_rounds", arguments={"rounds": 10})
    )
    provider.enqueue(ModelOutput(text="Codex completed the failed tool round."))
    plan = sequence(
        *(expect_form(f"round-{index}").accept({}) for index in range(1, 10))
    )
    with open_codex_responses_provider(provider) as provider_url:

        def make_adapter(harness: HarnessSpec) -> FixtureCodexHarnessAdapter:
            if not isinstance(harness, Codex):
                raise TypeError("Codex fixture received a non-Codex harness")
            return FixtureCodexHarnessAdapter(
                executable=harness.executable or executable,
                provider_url=provider_url,
            )

        registry = HarnessAdapterRegistry({"codex": make_adapter})
        async with AsyncMCPTestKit(
            env={}, cwd=str(_ROOT.parent), adapter_registry=registry
        ) as kit:
            async with kit.agents([_entry(executable)])[0].session(
                server=_server(marker),
                tools=["fixture:ten_rounds"],
                permission_policy="allow",
            ) as session:
                turn = await session.send(
                    "Use fixture ten_rounds for ten rounds, then report success.",
                    elicitation=plan,
                    elicitation_round_limit=requested_round_limit,
                )
            result = session.result

    assert turn.snapshot.outcome is TurnOutcome.FAILED, turn.error
    assert result.trace_view is not None
    assert result.trace_view.runtime.kind == "codex"
    assert result.trace_view.runtime.finish_reason.value == expected_finish_reason
    calls = [
        call for call in result.trace_view.tool_calls if call.tool.value == "ten_rounds"
    ]
    assert len(calls) == 1
    attempts = [call for call in _wire_calls(marker) if call["name"] == "ten_rounds"]
    assert len(attempts) == 10
    assert attempts[-1]["requestState"] == "9"
    assert attempts[-1]["inputResponses"] == {
        "round-9": {"action": "accept", "content": {}}
    }
    if requested_round_limit == 10:
        assert calls[0].tool_status.value == "tool_error"
        assert calls[0].attempts[-1].input_required is True
        assert calls[0].reported.state.value == "observed"
        assert calls[0].result.value is not None
        assert calls[0].result.value.error.value is not None
        assert calls[0].result.value.error.value.message.endswith(
            "input_required did not complete within 10 MRTR rounds"
        )
    # Codex is interrupted for an M3 limit below its native ten-round cap,
    # so it correctly never asks the provider to generate another response.
    assert len(provider.requests) == (2 if requested_round_limit == 10 else 1)
    assert "authorization" not in provider.requests[0].headers


def _wire_calls(marker: Path) -> list[dict[str, Any]]:
    if not marker.exists():
        return []
    return [
        {"id": record.get("id"), **record["params"]}
        for record in _wire_records(marker)
        if record.get("method") == "tools/call"
        and isinstance(record.get("params"), dict)
    ]


def _wire_records(marker: Path) -> list[dict[str, Any]]:
    if not marker.exists():
        return []
    return [
        json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()
    ]


@asynccontextmanager
async def _async_direct_codex_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: ResponsesRun,
    marker: Path,
) -> AsyncIterator[tuple[Any, list[FixtureCodexHarnessAdapter]]]:
    executable = _require_codex()
    isolated_source_home = tmp_path / "empty-codex-source-home"
    isolated_source_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(isolated_source_home))
    adapters: list[FixtureCodexHarnessAdapter] = []
    with open_codex_responses_provider(provider) as provider_url:

        def make_adapter(harness: HarnessSpec) -> FixtureCodexHarnessAdapter:
            if not isinstance(harness, Codex):
                raise TypeError("Codex fixture received a non-Codex harness")
            adapter = FixtureCodexHarnessAdapter(
                executable=harness.executable or executable,
                provider_url=provider_url,
            )
            adapters.append(adapter)
            return adapter

        registry = HarnessAdapterRegistry({"codex": make_adapter})
        async with AsyncMCPTestKit(
            env={}, cwd=str(_ROOT.parent), adapter_registry=registry
        ) as kit:
            agent = kit.agents([_entry(executable)])[0]
            yield agent, adapters


def _assert_one_successful_tool(
    result: Any,
    tool_name: str,
    *,
    attempt_count: int = 2,
) -> None:
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    assert result.trace_view is not None
    calls = [
        call for call in result.trace_view.tool_calls if call.tool.value == tool_name
    ]
    assert len(calls) == 1
    assert calls[0].tool_status.value == "success"
    assert len(calls[0].attempts) == attempt_count


@pytest.mark.asyncio
async def test_async_agent_run_uses_action_bound_form_plan_with_one_logical_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "async-agent-run-wire.jsonl"
    server = _server(marker)
    provider = ResponsesRun()
    provider.enqueue(
        ModelOutput(
            function_name="mcp__fixture::book_shipment",
            arguments={"weight_kg": 2, "zone": "local"},
        )
    )
    provider.enqueue(ModelOutput(text="Book shipment completed."))
    plan = expect_form(
        "shipping_address",
        message="Enter the delivery address.",
        schema=ADDRESS_SCHEMA,
        server=server,
        operation_kind="tool",
        operation_name="book_shipment",
    ).accept(HOME_ADDRESS)

    async with _async_direct_codex_agent(tmp_path, monkeypatch, provider, marker) as (
        agent,
        adapters,
    ):
        result = await agent.run(
            "Book one local shipment and report its status.",
            server=server,
            tools=["fixture:book_shipment"],
            elicitation=plan,
            timeout=60,
            permission_policy="allow",
        )

    _assert_one_successful_tool(result, "book_shipment")
    calls = _wire_calls(marker)
    assert len(calls) == 2
    assert calls[1]["requestState"] == "shipping-address"
    assert calls[1]["inputResponses"] == {
        "shipping_address": {"action": "accept", "content": HOME_ADDRESS}
    }
    assert adapters
    assert len(adapters[0].turn_results) == 1
    assert provider.requests
    assert "authorization" not in provider.requests[0].headers


@pytest.mark.asyncio
async def test_async_agent_submit_uses_maybe_url_plan_and_keyed_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "async-agent-submit-wire.jsonl"
    server = _server(marker)
    provider = ResponsesRun()
    provider.enqueue(ModelOutput(function_name="mcp__fixture::url_round", arguments={}))
    provider.enqueue(ModelOutput(text="Checkout was reviewed."))
    plan = maybe_url(
        "checkout",
        message="Continue checkout.",
        url="https://example.test/checkout/123",
        elicitation_id="checkout-123",
        server=server,
        operation_kind="tool",
        operation_name="url_round",
    ).accept()

    async with _async_direct_codex_agent(tmp_path, monkeypatch, provider, marker) as (
        agent,
        adapters,
    ):
        handle = agent.submit(
            "Review the fixture checkout URL and report the result.",
            server=server,
            tools=["fixture:url_round"],
            elicitation=plan,
            timeout=60,
            permission_policy="allow",
        )
        result = await handle.result(timeout=90)

    _assert_one_successful_tool(result, "url_round")
    calls = _wire_calls(marker)
    assert len(calls) == 2
    assert calls[1]["requestState"] == "url-state"
    assert calls[1]["inputResponses"] == {
        "checkout": {"action": "accept", "content": {}}
    }
    assert adapters
    assert len(adapters[0].turn_results) == 1
    assert provider.requests
    assert "authorization" not in provider.requests[0].headers


@pytest.mark.asyncio
async def test_async_session_send_scopes_plan_to_each_turn_and_skips_maybe_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "async-session-send-wire.jsonl"
    server = _server(marker)
    provider = ResponsesRun()
    provider.enqueue(
        ModelOutput(
            function_name="mcp__fixture::book_shipment",
            arguments={"weight_kg": 1, "zone": "local"},
        )
    )
    provider.enqueue(ModelOutput(text="First shipment completed."))
    provider.enqueue(
        ModelOutput(function_name="mcp__fixture::shipping_quote", arguments={})
    )
    provider.enqueue(ModelOutput(text="Second quote completed."))
    first_plan = expect_form(
        "shipping_address",
        message="Enter the delivery address.",
        schema=ADDRESS_SCHEMA,
        server=server,
        operation_kind="tool",
        operation_name="book_shipment",
    ).accept(HOME_ADDRESS)
    second_plan = maybe_url(
        "optional_verification",
        message="Complete optional verification.",
        url="https://example.test/verify/optional",
        server=server,
        operation_kind="tool",
        operation_name="shipping_quote",
    ).accept()

    async with _async_direct_codex_agent(tmp_path, monkeypatch, provider, marker) as (
        agent,
        adapters,
    ):
        async with agent.session(
            server=server,
            tools=["fixture:book_shipment", "fixture:shipping_quote"],
            timeout=60,
            permission_policy="allow",
        ) as session:
            first = await session.send(
                "Book one shipment.",
                elicitation=first_plan,
                timeout=60,
            )
            second = await session.send(
                "Get the shipping quote.",
                elicitation=second_plan,
                timeout=60,
            )
        result = session.result

    assert first.snapshot.outcome is TurnOutcome.COMPLETED, first.error
    assert second.snapshot.outcome is TurnOutcome.COMPLETED, second.error
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert result.trace_view is not None
    assert [
        event.request_key for event in result.trace_view.for_turn(first).elicitations
    ] == ["shipping_address"]
    assert not result.trace_view.for_turn(second).elicitations
    calls = _wire_calls(marker)
    assert [call["name"] for call in calls] == [
        "book_shipment",
        "book_shipment",
        "shipping_quote",
    ]
    assert calls[1]["inputResponses"] == {
        "shipping_address": {"action": "accept", "content": HOME_ADDRESS}
    }
    assert "requestState" not in calls[2]
    assert "inputResponses" not in calls[2]
    assert adapters
    assert len(adapters[0].turn_results) == 2
    assert provider.requests
    assert "authorization" not in provider.requests[0].headers


def _reopened_rounds(path: Path, execution_id: str) -> tuple[ManagedInputRecord, ...]:
    store = SQLiteExecutionStore(path)
    try:
        return cast(
            tuple[ManagedInputRecord, ...],
            store.managed_input_store.list_rounds(execution_id),
        )
    finally:
        store.close()


async def _wait_async_pending(
    handle: Any,
    *,
    timeout: float = 30.0,
    adapter: FixtureCodexHarnessAdapter | None = None,
    provider: ResponsesRun | None = None,
    marker: Path | None = None,
) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        pending = await handle.pending_elicitation()
        if pending is not None:
            return pending
        if handle._terminal.is_set():
            result = await handle.result()
            raise AssertionError(
                "Codex execution ended before managed input was requested: "
                f"outcome={result.snapshot.outcome.value}, error={result.error!r}; "
                f"mcp_wire={_wire_records(marker) if marker else []!r}; "
                f"provider_requests="
                f"{[request.body for request in provider.requests] if provider else []!r}; "
                f"provider_responses="
                f"{provider.responses if provider else []!r}; "
                f"native_frames={adapter.native_frames if adapter else []!r}; "
                f"app_server_stderr={adapter.closed_stderr if adapter else ''!r}"
            )
        await asyncio.sleep(0.05)
    raise AssertionError("Codex did not create a managed elicitation round")


def _wait_sync_pending(handle: Any, *, timeout: float = 30.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = handle.pending_elicitation()
        if pending is not None:
            return pending
        time.sleep(0.05)
    raise AssertionError("Codex did not create a managed elicitation round")


async def _run_managed_async(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tool: str,
    arguments: dict[str, Any],
    response_sets: list[dict[str, ElicitationResponse]],
) -> tuple[Any, ResponsesRun, Path, list[Any], Path, str]:
    executable = _require_codex()
    marker = tmp_path / f"managed-{tool}-wire.jsonl"
    store_path = tmp_path / f"managed-{tool}.sqlite"
    # CodexHarnessAdapter copies an existing native auth.json when present.
    # Point that explicit source at an empty private directory for this local,
    # credential-free fixture run.
    isolated_source_home = tmp_path / "empty-codex-source-home"
    isolated_source_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(isolated_source_home))
    provider = ResponsesRun()
    provider.enqueue(
        ModelOutput(
            function_name=f"mcp__fixture::{tool}",
            arguments=arguments,
        )
    )
    provider.enqueue(ModelOutput(text="completed by deterministic provider"))
    adapter: FixtureCodexHarnessAdapter | None = None
    with open_codex_responses_provider(provider) as provider_url:

        def make_adapter(harness: HarnessSpec) -> FixtureCodexHarnessAdapter:
            nonlocal adapter
            if not isinstance(harness, Codex):
                raise TypeError("Codex fixture received a non-Codex harness")
            adapter = FixtureCodexHarnessAdapter(
                executable=harness.executable or executable,
                provider_url=provider_url,
            )
            return adapter

        registry = HarnessAdapterRegistry({"codex": make_adapter})
        async with AsyncMCPTestKit(
            env={},
            cwd=str(_ROOT.parent),
            adapter_registry=registry,
            store=SQLiteExecutionStore(store_path),
        ) as kit:
            agent = kit.agents([_entry(executable)])[0]
            handle = agent.submit(
                f"Call fixture tool {tool} once, then report success.",
                server=_server(marker),
                tools=[f"fixture:{tool}"],
                human_input="managed",
                permission_policy="allow",
            )
            pending_rounds: list[Any] = []
            for index, responses in enumerate(response_sets):
                pending = await _wait_async_pending(
                    handle,
                    adapter=adapter,
                    provider=provider,
                    marker=marker,
                )
                pending_rounds.append(pending)
                assert set(pending.requests) == set(responses)
                if tool == "multi_round" and index == 0:
                    with pytest.raises(ManagedInputValidationError, match="schema"):
                        await handle.respond_elicitation(
                            pending.round_id,
                            {
                                "address": ElicitationResponse(
                                    action="accept", content={"street": 42}
                                )
                            },
                            idempotency_key="managed-codex-invalid-response",
                        )
                    still_pending = await handle.pending_elicitation()
                    assert still_pending is not None
                    assert still_pending.round_id == pending.round_id
                await handle.respond_elicitation(
                    pending.round_id,
                    responses,
                    idempotency_key=f"managed-codex-{tool}-{index}",
                )
                # Replaying the same response key and bytes is idempotent at
                # the durable API boundary and must not create another wire
                # delivery to Codex.
                await handle.respond_elicitation(
                    pending.round_id,
                    responses,
                    idempotency_key=f"managed-codex-{tool}-{index}",
                )
                with pytest.raises(ManagedInputConflict, match="idempotency"):
                    await handle.respond_elicitation(
                        pending.round_id,
                        {
                            key: ElicitationResponse(action="decline")
                            for key in responses
                        },
                        idempotency_key=f"managed-codex-{tool}-{index}",
                    )
            result = await handle.result(45)
    assert adapter is not None
    assert provider.requests, "Codex never contacted the local deterministic provider"
    assert "authorization" not in provider.requests[0].headers
    assert "mcp__fixture::" + tool in function_tools(provider.requests[0])
    return (
        result,
        provider,
        marker,
        pending_rounds,
        store_path,
        result.snapshot.execution_id.root,
    )


@pytest.mark.asyncio
async def test_installed_codex_real_managed_async_preserves_multi_round_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        result,
        provider,
        marker,
        rounds,
        store_path,
        execution_id,
    ) = await _run_managed_async(
        tmp_path,
        monkeypatch,
        tool="multi_round",
        arguments={},
        response_sets=[
            {"address": ElicitationResponse(action="accept", content=HOME_ADDRESS)},
            {"contact": ElicitationResponse(action="accept", content={})},
        ],
    )
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert len(provider.requests) >= 2
    calls = [call for call in _wire_calls(marker) if call["name"] == "multi_round"]
    assert len(calls) == 3
    assert [call.get("requestState") for call in calls] == [
        None,
        "multi-address-state",
        "multi-contact-state",
    ]
    assert calls[0].get("inputResponses", {}) == {}
    assert [call["_meta"]["progressToken"] for call in calls] == [1, 2, 3]

    def stable_operation(call: dict[str, Any]) -> dict[str, Any]:
        stable = {
            key: value
            for key, value in call.items()
            if key not in {"id", "requestState", "inputResponses"}
        }
        metadata = dict(stable["_meta"])
        metadata.pop("progressToken")
        stable["_meta"] = metadata
        return stable

    assert stable_operation(calls[0]) == stable_operation(calls[1])
    assert stable_operation(calls[1]) == stable_operation(calls[2])
    assert set(calls[1]["inputResponses"]) == {"address"}
    assert set(calls[2]["inputResponses"]) == {"contact"}
    assert [set(round_.requests) for round_ in rounds] == [{"address"}, {"contact"}]
    assert result.trace_view is not None
    logical_calls = [
        call
        for call in result.trace_view.tool_calls
        if call.tool.value == "multi_round"
    ]
    assert len(logical_calls) == 1
    assert len(logical_calls[0].attempts) == 3
    records = _reopened_rounds(store_path, execution_id)
    assert [record.status for record in records] == ["resolved", "resolved"]
    assert [record.pending.request_state for record in records] == [
        "multi-address-state",
        "multi-contact-state",
    ]
    assert (
        records[0].pending.logical_operation_id
        == records[1].pending.logical_operation_id
    )
    assert [record.response_idempotency_key for record in records] == [
        "managed-codex-multi_round-0",
        "managed-codex-multi_round-1",
    ]


@pytest.mark.asyncio
async def test_installed_codex_real_managed_async_preserves_url_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        result,
        provider,
        marker,
        rounds,
        store_path,
        execution_id,
    ) = await _run_managed_async(
        tmp_path,
        monkeypatch,
        tool="url_round",
        arguments={},
        response_sets=[{"checkout": ElicitationResponse(action="accept")}],
    )
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert provider.requests
    assert len(rounds) == 1
    assert rounds[0].requests["checkout"].mode == "url"
    assert rounds[0].requests["checkout"].url == "https://example.test/checkout/123"
    calls = [call for call in _wire_calls(marker) if call["name"] == "url_round"]
    assert len(calls) == 2
    assert calls[1]["requestState"] == "url-state"
    assert calls[1]["inputResponses"] == {
        "checkout": {"action": "accept", "content": {}}
    }
    assert result.trace_view is not None
    assert result.trace_view.elicitations[0].mode == "url"
    assert _reopened_rounds(store_path, execution_id)[0].status == "resolved"


@pytest.mark.asyncio
async def test_installed_codex_process_loss_fails_pending_round_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _require_codex()
    marker = tmp_path / "managed-loss-wire.jsonl"
    store_path = tmp_path / "managed-loss.sqlite"
    isolated_source_home = tmp_path / "empty-codex-source-home"
    isolated_source_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(isolated_source_home))
    provider = ResponsesRun()
    provider.enqueue(
        ModelOutput(
            function_name="mcp__fixture::book_shipment",
            arguments={"weight_kg": 1, "zone": "local"},
        )
    )
    provider.enqueue(ModelOutput(text="not reached after process loss"))
    adapter: FixtureCodexHarnessAdapter | None = None
    with open_codex_responses_provider(provider) as provider_url:

        def make_adapter(harness: HarnessSpec) -> FixtureCodexHarnessAdapter:
            nonlocal adapter
            if not isinstance(harness, Codex):
                raise TypeError("Codex fixture received a non-Codex harness")
            adapter = FixtureCodexHarnessAdapter(
                executable=harness.executable or executable,
                provider_url=provider_url,
            )
            return adapter

        registry = HarnessAdapterRegistry({"codex": make_adapter})
        async with AsyncMCPTestKit(
            env={},
            cwd=str(_ROOT.parent),
            adapter_registry=registry,
            store=SQLiteExecutionStore(store_path),
        ) as kit:
            agent = kit.agents([_entry(executable)])[0]
            handle = agent.submit(
                "Use fixture book_shipment once, then report success.",
                server=_server(marker),
                tools=["fixture:book_shipment"],
                human_input="managed",
                permission_policy="allow",
            )
            pending = await _wait_async_pending(
                handle,
                adapter=adapter,
                provider=provider,
                marker=marker,
            )
            assert set(pending.requests) == {"shipping_address"}
            assert adapter is not None
            app_server_process = adapter._process
            assert app_server_process is not None
            process_owner = app_server_process.owner
            assert process_owner is not None
            process = process_owner.process
            assert process is not None and process.returncode is None
            process.terminate()
            await process.wait()
            result = await handle.result(30)
            with pytest.raises(ManagedInputStateError, match="cannot accept responses"):
                await handle.respond_elicitation(
                    pending.round_id,
                    {
                        "shipping_address": ElicitationResponse(
                            action="accept", content=HOME_ADDRESS
                        )
                    },
                    idempotency_key="managed-codex-late-response",
                )

    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    records = _reopened_rounds(store_path, result.snapshot.execution_id.root)
    assert len(records) == 1
    assert records[0].status == "failed"
    calls = [call for call in _wire_calls(marker) if call["name"] == "book_shipment"]
    assert len(calls) == 1
    assert calls[0].get("requestState") is None
    assert provider.requests
    assert len(provider.requests) == 1


def test_installed_codex_real_managed_sync_persists_keyed_round_and_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _require_codex()
    marker = tmp_path / "managed-sync-wire.jsonl"
    store_path = tmp_path / "managed-sync.sqlite"
    isolated_source_home = tmp_path / "empty-codex-source-home"
    isolated_source_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(isolated_source_home))
    provider = ResponsesRun()
    provider.enqueue(
        ModelOutput(
            function_name="mcp__fixture::book_shipment",
            arguments={"weight_kg": 1, "zone": "local"},
        )
    )
    provider.enqueue(ModelOutput(text="completed by deterministic provider"))
    with open_codex_responses_provider(provider) as provider_url:

        def make_adapter(harness: HarnessSpec) -> FixtureCodexHarnessAdapter:
            if not isinstance(harness, Codex):
                raise TypeError("Codex fixture received a non-Codex harness")
            return FixtureCodexHarnessAdapter(
                executable=harness.executable or executable,
                provider_url=provider_url,
            )

        registry = HarnessAdapterRegistry({"codex": make_adapter})
        with MCPTestKit(
            env={},
            cwd=str(_ROOT.parent),
            adapter_registry=registry,
            store=SQLiteExecutionStore(store_path),
        ) as kit:
            agent = kit.agents([_entry(executable)])[0]
            handle = agent.submit(
                "Use fixture book_shipment once and then report success.",
                server=_server(marker),
                tools=["fixture:book_shipment"],
                human_input="managed",
                permission_policy="allow",
            )
            pending = _wait_sync_pending(handle)
            assert set(pending.requests) == {"shipping_address"}
            handle.respond_elicitation(
                pending.round_id,
                {
                    "shipping_address": ElicitationResponse(
                        action="accept",
                        content=HOME_ADDRESS,
                    )
                },
                idempotency_key="managed-codex-sync-0",
            )
            result = handle.result(45)

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert provider.requests
    assert "authorization" not in provider.requests[0].headers
    calls = [call for call in _wire_calls(marker) if call["name"] == "book_shipment"]
    assert len(calls) == 2
    assert calls[0].get("requestState") is None
    assert calls[1]["requestState"] == "shipping-address"
    assert set(calls[1]["inputResponses"]) == {"shipping_address"}
    assert (
        calls[0]["arguments"]
        == calls[1]["arguments"]
        == {
            "weight_kg": 1,
            "zone": "local",
        }
    )
    assert result.trace_view is not None
    logical_calls = [
        call
        for call in result.trace_view.tool_calls
        if call.tool.value == "book_shipment"
    ]
    assert len(logical_calls) == 1
    assert len(logical_calls[0].attempts) == 2
    records = _reopened_rounds(store_path, result.snapshot.execution_id.root)
    assert len(records) == 1
    assert records[0].status == "resolved"
    assert records[0].response_idempotency_key == "managed-codex-sync-0"
