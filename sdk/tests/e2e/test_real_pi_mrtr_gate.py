"""Installed-Pi MRTR delivery gate with a local deterministic provider."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fixtures.pi_openai_provider import ProviderRun, open_provider

from m3 import expect, expect_form, expect_url, round_of, sequence
from m3._types.specs import AgentSpec
from m3.async_api import AsyncMCPTestKit
from m3.elicitation import (
    ElicitationPlan,
    ElicitationResponse,
    PendingElicitationRound,
)
from m3.harness.contracts import HarnessAdapterRegistry, HarnessLaunch
from m3.harness.pi import PiHarnessAdapter
from m3.storage import SQLiteExecutionStore
from m3.storage.managed_input import ManagedInputRecord
from m3.sync_api import MCPTestKit
from m3.types import (
    ExecutionOutcome,
    ExecutionResult,
    FullToolPolicy,
    HarnessSpec,
    Pi,
    ServerBinding,
    StdioServer,
    TurnOutcome,
    TurnResult,
)

pytestmark = [pytest.mark.e2e, pytest.mark.process_lifecycle]

_ROOT = Path(__file__).parents[2]
_FIXTURES = _ROOT / "tests" / "fixtures"
_MCP_SERVER = _FIXTURES / "modern_mrtr_server.py"
_PROVIDER_EXTENSION = _FIXTURES / "pi_openai_provider.ts"


class FixturePiHarnessAdapter(PiHarnessAdapter):
    def process_argv(self, launch: HarnessLaunch) -> tuple[str, ...]:
        return (*super().process_argv(launch), "--extension", str(_PROVIDER_EXTENSION))


def _require_pi() -> str:
    executable = os.environ.get("M3_PI_EXECUTABLE") or shutil.which("pi")
    if executable is None:
        pytest.skip("Pi 0.85.1 is unavailable on PATH; set M3_PI_EXECUTABLE")
    try:
        version = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("Pi 0.85.1 could not be executed at the required runtime")
    if version != "0.85.1":
        pytest.skip(f"Pi version 0.85.1 is required; found {version or 'unknown'}")
    return executable


def _spec(executable: str, marker: Path, *, block_retry: bool = False) -> AgentSpec:
    server = StdioServer(
        name="fixture",
        command=sys.executable,
        args=(str(_MCP_SERVER),),
        cwd=str(_ROOT.parent),
        environment={
            "M3_MRTR_WIRE_MARKER": str(marker),
            **({"M3_MRTR_BLOCK_RETRY": "1"} if block_retry else {}),
        },
    )
    return AgentSpec(
        harness=Pi(
            model="fixture-model",
            provider="m3-fixture",
            executable=executable,
        ),
        servers=(ServerBinding(server=server),),
        tool_policy=FullToolPolicy(acknowledge_risk=True),
    )


def _managed_server(marker: Path) -> StdioServer:
    return StdioServer(
        name="fixture",
        command=sys.executable,
        args=(str(_MCP_SERVER),),
        cwd=str(_ROOT.parent),
        environment={"M3_MRTR_WIRE_MARKER": str(marker)},
    )


def _managed_entry(executable: str) -> dict[str, object]:
    return {
        "harness": "pi",
        "models": ["fixture-model"],
        "provider": "m3-fixture",
        "executable": executable,
    }


def _reopened_rounds(path: Path, execution_id: str) -> tuple[ManagedInputRecord, ...]:
    store = SQLiteExecutionStore(path)
    try:
        return store.managed_input_store.list_rounds(execution_id)
    finally:
        store.close()


async def _run_case(
    tmp_path: Path,
    prompt: str,
    plan: ElicitationPlan | None,
    *,
    timeout: float = 20,
    block_retry: bool = False,
) -> tuple[TurnResult, ExecutionResult, ProviderRun, Path, FixturePiHarnessAdapter]:
    executable = _require_pi()
    marker = tmp_path / "mcp-wire.jsonl"
    provider_run = ProviderRun()
    adapter: FixturePiHarnessAdapter | None = None
    with open_provider(provider_run) as provider_url:

        def make_adapter(harness: HarnessSpec) -> FixturePiHarnessAdapter:
            nonlocal adapter
            if not isinstance(harness, Pi):
                raise TypeError("Pi fixture received a non-Pi harness")
            adapter = FixturePiHarnessAdapter(
                executable=harness.executable or executable,
                environment={
                    "M3_PI_FIXTURE_PROVIDER_URL": provider_url,
                    "M3_MRTR_WIRE_MARKER": str(marker),
                    **({"M3_MRTR_BLOCK_RETRY": "1"} if block_retry else {}),
                },
            )
            return adapter

        registry = HarnessAdapterRegistry({"pi": make_adapter})
        async with AsyncMCPTestKit(
            env={},
            cwd=str(_ROOT.parent),
            adapter_registry=registry,
        ) as kit:
            async with kit.agent_session(
                _spec(executable, marker, block_retry=block_retry)
            ) as session:
                turn = await session.send(prompt, elicitation=plan, timeout=timeout)
            result = session.result
    assert adapter is not None
    return turn, result, provider_run, marker, adapter


@pytest.mark.asyncio
async def test_installed_pi_real_form_round_uses_one_logical_call(
    tmp_path: Path,
) -> None:
    turn, result, provider_run, marker, _adapter = await _run_case(
        tmp_path,
        "Use m3-gate:book_shipment once and then report success.",
        expect_form("shipping_address").accept(
            {"street": "1 Main", "city": "Pune", "postal_code": "411001"}
        ),
    )
    assert turn.snapshot.outcome is TurnOutcome.COMPLETED, turn.error
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert provider_run.requests
    calls = [json.loads(line) for line in marker.read_text().splitlines()]
    tool_calls = [call for call in calls if call["name"] == "book_shipment"]
    assert len(tool_calls) == 2
    assert len({call["id"] for call in tool_calls}) == 2
    assert tool_calls[0]["requestState"] is None
    assert tool_calls[1]["requestState"] == "book-shipment-address"
    assert set(tool_calls[1]["inputResponses"]) == {"shipping_address"}
    assert (
        tool_calls[0]["arguments"]
        == tool_calls[1]["arguments"]
        == {
            "weight_kg": 1,
            "zone": "local",
        }
    )
    expect(result).to_have_tool_call(
        "book_shipment", server="fixture", status="success", count=1
    )
    assert result.trace_view is not None
    calls = [
        call
        for call in result.trace_view.tool_calls
        if call.tool.value == "book_shipment"
    ]
    assert len(calls) == 1
    call = calls[0]
    assert call.result.value is not None
    assert call.result.value.structured_content.value == {
        "status": "booked",
        "address": {
            "street": "1 Main",
            "city": "Pune",
            "postal_code": "411001",
        },
    }
    assert len(call.attempts) == 2
    assert [attempt.jsonrpc_id.value for attempt in call.attempts] == [3, 4]
    assert call.attempts[0].continuation_state.value == "book-shipment-address"
    assert call.attempts[1].request_state.value == "book-shipment-address"
    assert call.attempts[1].input_responses.value == {
        "shipping_address": {
            "action": "accept",
            "content": {
                "street": "1 Main",
                "city": "Pune",
                "postal_code": "411001",
            },
        }
    }
    assert len(result.trace_view.elicitations) == 1
    elicitation = result.trace_view.elicitations[0]
    assert elicitation.request_key == "shipping_address"
    assert elicitation.mode == "form"
    assert elicitation.action == "accept"
    assert elicitation.input_responses.value == {
        "shipping_address": {
            "action": "accept",
            "content": {
                "street": "1 Main",
                "city": "Pune",
                "postal_code": "411001",
            },
        }
    }


@pytest.mark.asyncio
async def test_installed_pi_real_same_session_can_elicit_on_two_turns(
    tmp_path: Path,
) -> None:
    executable = _require_pi()
    marker = tmp_path / "two-turn-wire.jsonl"
    provider_run = ProviderRun()
    adapter: FixturePiHarnessAdapter | None = None
    plan = expect_form("shipping_address").accept(
        {"street": "1 Main", "city": "Pune", "postal_code": "411001"}
    )
    with open_provider(provider_run) as provider_url:

        def make_adapter(harness: HarnessSpec) -> FixturePiHarnessAdapter:
            nonlocal adapter
            if not isinstance(harness, Pi):
                raise TypeError("Pi fixture received a non-Pi harness")
            adapter = FixturePiHarnessAdapter(
                executable=harness.executable or executable,
                environment={
                    "M3_PI_FIXTURE_PROVIDER_URL": provider_url,
                    "M3_MRTR_WIRE_MARKER": str(marker),
                },
            )
            return adapter

        registry = HarnessAdapterRegistry({"pi": make_adapter})
        async with AsyncMCPTestKit(
            env={},
            cwd=str(_ROOT.parent),
            adapter_registry=registry,
        ) as kit:
            async with kit.agent_session(_spec(executable, marker)) as session:
                first = await session.send(
                    "Use m3-gate:book_shipment once and report the first booking.",
                    elicitation=plan,
                    timeout=20,
                )
                second = await session.send(
                    "Use m3-gate:book_shipment once more and report the second booking.",
                    elicitation=plan,
                    timeout=20,
                )
            result = session.result

    assert adapter is not None
    assert first.snapshot.outcome is TurnOutcome.COMPLETED, first.error
    assert second.snapshot.outcome is TurnOutcome.COMPLETED, second.error
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert len(provider_run.requests) >= 4
    calls = [json.loads(line) for line in marker.read_text().splitlines()]
    tool_calls = [call for call in calls if call["name"] == "book_shipment"]
    assert len(tool_calls) == 4
    assert len({call["id"] for call in tool_calls}) == 4
    assert [call["requestState"] for call in tool_calls] == [
        None,
        "book-shipment-address",
        None,
        "book-shipment-address",
    ]
    assert [set(call["inputResponses"]) for call in tool_calls] == [
        set(),
        {"shipping_address"},
        set(),
        {"shipping_address"},
    ]
    assert result.trace_view is not None
    assert len(result.trace_view.elicitations) == 2


@pytest.mark.asyncio
async def test_installed_pi_real_url_round_asserts_without_visiting(
    tmp_path: Path,
) -> None:
    turn, result, _provider_run, marker, _adapter = await _run_case(
        tmp_path,
        "Use m3-gate:checkout once and then report success.",
        expect_url("checkout", url="https://example.test/checkout/123").accept(),
    )
    assert turn.snapshot.outcome is TurnOutcome.COMPLETED, turn.error
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    calls = [json.loads(line) for line in marker.read_text().splitlines()]
    assert [call["name"] for call in calls] == ["checkout", "checkout"]
    assert len({call["id"] for call in calls}) == 2
    assert calls[0]["arguments"] == calls[1]["arguments"] == {}
    assert calls[0]["requestState"] is None
    assert calls[1]["requestState"] == "checkout-state"


@pytest.mark.asyncio
async def test_installed_pi_real_sequential_rounds_preserve_current_responses(
    tmp_path: Path,
) -> None:
    turn, result, _provider_run, marker, _adapter = await _run_case(
        tmp_path,
        "Use m3-gate:multi_round once and then report success.",
        sequence(
            expect_form("address").accept({"city": "Pune"}),
            expect_form("contact").accept({"phone": "+91-555-0100"}),
        ),
    )
    assert turn.snapshot.outcome is TurnOutcome.COMPLETED, turn.error
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    calls = [json.loads(line) for line in marker.read_text().splitlines()]
    assert [call["requestState"] for call in calls] == [None, "", "multi-contact"]
    assert calls[0]["inputResponses"] == {}
    assert set(calls[1]["inputResponses"]) == {"address"}
    assert set(calls[2]["inputResponses"]) == {"contact"}
    assert len({call["id"] for call in calls}) == 3
    assert all(call["arguments"] == {} for call in calls)


@pytest.mark.asyncio
async def test_installed_pi_real_same_round_preserves_all_keyed_responses(
    tmp_path: Path,
) -> None:
    turn, result, _provider_run, marker, _adapter = await _run_case(
        tmp_path,
        "Use m3-gate:same_round once and then report success.",
        round_of(
            expect_form("address").accept({"city": "Pune"}),
            expect_form("contact").accept({"phone": "+91-555-0100"}),
        ),
    )
    assert turn.snapshot.outcome is TurnOutcome.COMPLETED, turn.error
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    calls = [json.loads(line) for line in marker.read_text().splitlines()]
    assert len(calls) == 2
    assert set(calls[1]["inputResponses"]) == {"address", "contact"}
    assert len({call["id"] for call in calls}) == 2
    assert calls[0]["arguments"] == calls[1]["arguments"] == {}


@pytest.mark.asyncio
async def test_installed_pi_real_cancellation_cleans_action_channel(
    tmp_path: Path,
) -> None:
    turn, result, provider_run, marker, adapter = await _run_case(
        tmp_path,
        "Use m3-gate:book_shipment once and wait for the provider.",
        expect_form("shipping_address").accept(
            {"street": "1 Main", "city": "Pune", "postal_code": "411001"}
        ),
        timeout=0.2,
        block_retry=True,
    )
    assert turn.snapshot.outcome is TurnOutcome.TIMED_OUT
    assert result.snapshot.outcome is ExecutionOutcome.TIMED_OUT
    assert provider_run.requests
    calls = [json.loads(line) for line in marker.read_text().splitlines()]
    assert len(calls) == 2
    assert len({call["id"] for call in calls}) == 2
    assert (
        calls[0]["arguments"]
        == calls[1]["arguments"]
        == {
            "weight_kg": 1,
            "zone": "local",
        }
    )
    assert calls[1]["requestState"] == "book-shipment-address"
    assert set(calls[1]["inputResponses"]) == {"shipping_address"}
    assert adapter._action_channel is None


@pytest.mark.asyncio
async def test_installed_pi_real_agent_settled_rejects_unused_required_plan(
    tmp_path: Path,
) -> None:
    turn, result, _provider_run, _marker, adapter = await _run_case(
        tmp_path,
        "Reply with a short ordinary answer and do not use tools.",
        expect_form("unused").accept({"value": True}),
    )
    assert turn.snapshot.outcome is TurnOutcome.FAILED
    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    assert adapter._action_channel is None


@pytest.mark.asyncio
async def test_installed_pi_real_managed_submit_async_uses_keyed_round_response(
    tmp_path: Path,
) -> None:
    executable = _require_pi()
    marker = tmp_path / "managed-async-wire.jsonl"
    provider_run = ProviderRun()
    store_path = tmp_path / "managed-async-form.sqlite"
    adapter: FixturePiHarnessAdapter | None = None
    with open_provider(provider_run) as provider_url:

        def make_adapter(harness: HarnessSpec) -> FixturePiHarnessAdapter:
            nonlocal adapter
            if not isinstance(harness, Pi):
                raise TypeError("Pi fixture received a non-Pi harness")
            adapter = FixturePiHarnessAdapter(
                executable=harness.executable or executable,
                environment={
                    "M3_PI_FIXTURE_PROVIDER_URL": provider_url,
                    "M3_MRTR_WIRE_MARKER": str(marker),
                },
            )
            return adapter

        registry = HarnessAdapterRegistry({"pi": make_adapter})
        async with AsyncMCPTestKit(
            env={},
            cwd=str(_ROOT.parent),
            adapter_registry=registry,
            store=SQLiteExecutionStore(store_path),
        ) as kit:
            agent = kit.agents([_managed_entry(executable)])[0]
            handle = agent.submit(
                "Use m3-gate:book_shipment once and then report success.",
                server=_managed_server(marker),
                tools=["fixture:book_shipment"],
                human_input="managed",
            )
            deadline = asyncio.get_running_loop().time() + 20
            pending = None
            while asyncio.get_running_loop().time() < deadline:
                pending = await handle.pending_elicitation()
                if pending is not None:
                    break
                await asyncio.sleep(0.05)
            assert pending is not None
            assert set(pending.requests) == {"shipping_address"}
            await handle.respond_elicitation(
                pending.round_id,
                {
                    "shipping_address": ElicitationResponse(
                        action="accept",
                        content={
                            "street": "1 Main",
                            "city": "Pune",
                            "postal_code": "411001",
                        },
                    )
                },
                idempotency_key="managed-async-round-0",
            )
            result = await handle.result(20)
    assert adapter is not None
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert len(provider_run.requests) >= 2
    calls = [json.loads(line) for line in marker.read_text().splitlines()]
    tool_calls = [call for call in calls if call["name"] == "book_shipment"]
    assert len(tool_calls) == 2
    assert len({call["id"] for call in tool_calls}) == 2
    assert tool_calls[0]["requestState"] is None
    assert tool_calls[1]["requestState"] == "book-shipment-address"
    assert set(tool_calls[1]["inputResponses"]) == {"shipping_address"}
    assert (
        tool_calls[0]["arguments"]
        == tool_calls[1]["arguments"]
        == {
            "weight_kg": 1,
            "zone": "local",
        }
    )
    assert result.trace_view is not None
    tool_call = next(
        call
        for call in result.trace_view.tool_calls
        if call.tool.value == "book_shipment"
    )
    assert len(tool_call.attempts) == 2
    records = _reopened_rounds(store_path, result.snapshot.execution_id.root)
    assert len(records) == 1
    record = records[0]
    assert record.status == "resolved"
    assert record.response_idempotency_key == "managed-async-round-0"
    assert record.delivery_attempts == 1
    assert record.pending.request_state == "book-shipment-address"
    assert record.operation_parameters["weight_kg"] == 1
    assert record.session_id and record.turn_id


async def _run_public_managed_async(
    tmp_path: Path,
    prompt: str,
    tool: str,
    response_sets: list[dict[str, ElicitationResponse]],
) -> tuple[
    ExecutionResult,
    ProviderRun,
    Path,
    list[PendingElicitationRound],
    Path,
    str,
]:
    executable = _require_pi()
    marker = tmp_path / f"managed-{tool}-wire.jsonl"
    provider_run = ProviderRun()
    store_path = tmp_path / f"managed-{tool}.sqlite"
    with open_provider(provider_run) as provider_url:

        def make_adapter(harness: HarnessSpec) -> FixturePiHarnessAdapter:
            if not isinstance(harness, Pi):
                raise TypeError("Pi fixture received a non-Pi harness")
            return FixturePiHarnessAdapter(
                executable=harness.executable or executable,
                environment={
                    "M3_PI_FIXTURE_PROVIDER_URL": provider_url,
                    "M3_MRTR_WIRE_MARKER": str(marker),
                },
            )

        registry = HarnessAdapterRegistry({"pi": make_adapter})
        async with AsyncMCPTestKit(
            env={},
            cwd=str(_ROOT.parent),
            adapter_registry=registry,
            store=SQLiteExecutionStore(store_path),
        ) as kit:
            agent = kit.agents([_managed_entry(executable)])[0]
            handle = agent.submit(
                prompt,
                server=_managed_server(marker),
                tools=[f"fixture:{tool}"],
                human_input="managed",
            )
            pending_rounds: list[PendingElicitationRound] = []
            for index, response_set in enumerate(response_sets):
                deadline = asyncio.get_running_loop().time() + 20
                pending = None
                while asyncio.get_running_loop().time() < deadline:
                    pending = await handle.pending_elicitation()
                    if pending is not None:
                        break
                    await asyncio.sleep(0.05)
                assert pending is not None
                pending_rounds.append(pending)
                assert set(pending.requests) == set(response_set)
                await handle.respond_elicitation(
                    pending.round_id,
                    response_set,
                    idempotency_key=f"managed-{tool}-round-{index}",
                )
            result = await handle.result(20)
    return (
        result,
        provider_run,
        marker,
        pending_rounds,
        store_path,
        result.snapshot.execution_id.root,
    )


@pytest.mark.asyncio
async def test_installed_pi_real_managed_submit_async_preserves_multi_rounds(
    tmp_path: Path,
) -> None:
    (
        result,
        provider_run,
        marker,
        pending_rounds,
        store_path,
        execution_id,
    ) = await _run_public_managed_async(
        tmp_path,
        "Use m3-gate:multi_round once and then report success.",
        "multi_round",
        [
            {"address": ElicitationResponse(action="accept", content={"city": "Pune"})},
            {
                "contact": ElicitationResponse(
                    action="accept", content={"phone": "+91-555-0100"}
                )
            },
        ],
    )
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert len(provider_run.requests) >= 2
    calls = [json.loads(line) for line in marker.read_text().splitlines()]
    assert [call["requestState"] for call in calls] == [None, "", "multi-contact"]
    assert calls[0]["inputResponses"] == {}
    assert set(calls[1]["inputResponses"]) == {"address"}
    assert set(calls[2]["inputResponses"]) == {"contact"}
    assert len({call["id"] for call in calls}) == 3
    assert [set(round_.requests) for round_ in pending_rounds] == [
        {"address"},
        {"contact"},
    ]
    assert [round_.request_state for round_ in pending_rounds] == ["", "multi-contact"]
    assert result.trace_view is not None
    assert len(result.trace_view.elicitations) == 2
    records = _reopened_rounds(store_path, execution_id)
    assert [record.status for record in records] == ["resolved", "resolved"]
    assert [record.pending.request_state for record in records] == ["", "multi-contact"]
    assert [set(record.responses or {}) for record in records] == [
        {"address"},
        {"contact"},
    ]
    assert (
        records[0].pending.logical_operation_id
        == records[1].pending.logical_operation_id
    )


@pytest.mark.asyncio
async def test_installed_pi_real_managed_submit_async_preserves_url_round(
    tmp_path: Path,
) -> None:
    (
        result,
        provider_run,
        marker,
        pending_rounds,
        store_path,
        execution_id,
    ) = await _run_public_managed_async(
        tmp_path,
        "Use m3-gate:checkout once and then report success.",
        "checkout",
        [{"checkout": ElicitationResponse(action="accept")}],
    )
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert provider_run.requests
    calls = [json.loads(line) for line in marker.read_text().splitlines()]
    assert len(calls) == 2
    assert calls[0]["requestState"] is None
    assert calls[1]["requestState"] == "checkout-state"
    assert calls[0]["arguments"] == calls[1]["arguments"] == {}
    assert len(pending_rounds) == 1
    request = pending_rounds[0].requests["checkout"]
    assert request.mode == "url"
    assert request.url == "https://example.test/checkout/123"
    # The installed MCP transport currently omits the optional URL
    # elicitation id while preserving the URL itself; the bridge never
    # fabricates one or performs navigation.
    assert result.trace_view is not None
    assert result.trace_view.elicitations[0].mode == "url"
    records = _reopened_rounds(store_path, execution_id)
    assert len(records) == 1
    assert records[0].status == "resolved"
    assert set(records[0].responses or {}) == {"checkout"}


def test_installed_pi_real_managed_submit_sync_uses_keyed_round_response(
    tmp_path: Path,
) -> None:
    executable = _require_pi()
    marker = tmp_path / "managed-sync-wire.jsonl"
    provider_run = ProviderRun()
    store_path = tmp_path / "managed-sync.sqlite"
    adapter: FixturePiHarnessAdapter | None = None
    with open_provider(provider_run) as provider_url:

        def make_adapter(harness: HarnessSpec) -> FixturePiHarnessAdapter:
            nonlocal adapter
            if not isinstance(harness, Pi):
                raise TypeError("Pi fixture received a non-Pi harness")
            adapter = FixturePiHarnessAdapter(
                executable=harness.executable or executable,
                environment={
                    "M3_PI_FIXTURE_PROVIDER_URL": provider_url,
                    "M3_MRTR_WIRE_MARKER": str(marker),
                },
            )
            return adapter

        registry = HarnessAdapterRegistry({"pi": make_adapter})
        with MCPTestKit(
            env={},
            cwd=str(_ROOT.parent),
            adapter_registry=registry,
            store=SQLiteExecutionStore(store_path),
        ) as kit:
            agent = kit.agents([_managed_entry(executable)])[0]
            handle = agent.submit(
                "Use m3-gate:book_shipment once and then report success.",
                server=_managed_server(marker),
                tools=["fixture:book_shipment"],
                human_input="managed",
            )
            deadline = time.monotonic() + 20
            pending = None
            while time.monotonic() < deadline:
                pending = handle.pending_elicitation()
                if pending is not None:
                    break
                time.sleep(0.05)
            assert pending is not None
            assert set(pending.requests) == {"shipping_address"}
            handle.respond_elicitation(
                pending.round_id,
                {
                    "shipping_address": ElicitationResponse(
                        action="accept",
                        content={
                            "street": "1 Main",
                            "city": "Pune",
                            "postal_code": "411001",
                        },
                    )
                },
                idempotency_key="managed-sync-round-0",
            )
            result = handle.result(20)
    assert adapter is not None
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert len(provider_run.requests) >= 2
    calls = [json.loads(line) for line in marker.read_text().splitlines()]
    tool_calls = [call for call in calls if call["name"] == "book_shipment"]
    assert len(tool_calls) == 2
    assert len({call["id"] for call in tool_calls}) == 2
    assert tool_calls[0]["requestState"] is None
    assert tool_calls[1]["requestState"] == "book-shipment-address"
    assert set(tool_calls[1]["inputResponses"]) == {"shipping_address"}
    assert (
        tool_calls[0]["arguments"]
        == tool_calls[1]["arguments"]
        == {
            "weight_kg": 1,
            "zone": "local",
        }
    )
    records = _reopened_rounds(store_path, result.snapshot.execution_id.root)
    assert len(records) == 1
    assert records[0].status == "resolved"
    assert records[0].response_idempotency_key == "managed-sync-round-0"
