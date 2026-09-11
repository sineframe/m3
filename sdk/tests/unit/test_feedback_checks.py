"""Matcher feedback recording contracts."""

from __future__ import annotations

import gc
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp.server.lowlevel import Server
from mcp.types import ListToolsResult

from mcp_pal import MCPTestKit, _check_recording
from mcp_pal._check_recording import (
    _safe_json,
    _safe_text,
    _subject_binding,
    _subject_execution_id,
    bind_execution,
    bind_subject,
)
from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.execution_trace import ExecutionTraceRecorder
from mcp_pal.matchers import check, expect
from mcp_pal.matrix import ServerCase, ToolCase, ToolMatrix
from mcp_pal.storage import InMemoryExecutionStore, SQLiteExecutionStore
from mcp_pal.types import ExecutionOutcome, InProcessServer, StdioServer


def _trace(store: InMemoryExecutionStore, execution_id: str):
    recorder = ExecutionTraceRecorder(store, execution_id)
    return recorder.finalize(ExecutionOutcome.COMPLETED)


def test_matcher_records_pass_and_failure_against_exact_execution() -> None:
    store = InMemoryExecutionStore()
    trace = _trace(store, "matcher-execution")
    assert bind_execution(trace.execution_id, store)

    expect(trace).to_have_trace()
    try:
        expect(trace).to_have_text("missing")
    except AssertionError:
        pass

    records = store.evaluations(trace.execution_id)
    assert [record.status.value for record in records] == ["passed", "failed"]
    assert records[0].name == "mcp_pal.matcher.to_have_trace.v1"
    assert records[1].details["subject"]["execution_id"] == "matcher-execution"
    assert (
        records[0]
        .details["identity"]["function"]
        .endswith("test_matcher_records_pass_and_failure_against_exact_execution")
    )
    assert records[0].details["identity"]["occurrence"] == 1


def test_grouped_aliases_record_one_outer_check_each() -> None:
    store = InMemoryExecutionStore()
    trace = _trace(store, "grouped-execution")
    assert bind_execution(trace.execution_id, store)

    try:
        with check() as checks:
            checks.expect(trace).to_not_have_tool_call("missing")
            checks.expect(trace).to_have_text("missing")
    except AssertionError:
        pass

    records = store.evaluations(trace.execution_id)
    assert len(records) == 2
    assert records[0].name == "mcp_pal.matcher.to_not_have_tool_call.v1"
    assert records[1].name == "mcp_pal.matcher.to_have_text.v1"


def test_unbound_subject_does_not_create_feedback() -> None:
    store = InMemoryExecutionStore()
    trace = _trace(store, "unbound-execution")
    expect(trace).to_have_trace()
    assert store.evaluations(trace.execution_id) == ()


def test_duplicate_execution_id_across_stores_is_unavailable() -> None:
    first = InMemoryExecutionStore()
    second = InMemoryExecutionStore()
    trace = _trace(first, "ambiguous-execution")
    _trace(second, "ambiguous-execution")
    assert bind_execution(trace.execution_id, first)
    assert bind_execution(trace.execution_id, second)
    expect(trace).to_have_trace()
    assert first.evaluations(trace.execution_id) == ()
    assert second.evaluations(trace.execution_id) == ()


def test_duplicate_execution_id_across_distinct_sqlite_instances_is_unavailable(
    tmp_path,
) -> None:
    path = tmp_path / "same-database.sqlite"
    first = SQLiteExecutionStore(path)
    second = SQLiteExecutionStore(path)
    try:
        trace = _trace(first, "ambiguous-sqlite-execution")
        assert bind_execution(trace.execution_id, first)
        assert bind_execution(trace.execution_id, second)
        expect(trace).to_have_trace()
        assert first.evaluations(trace.execution_id) == ()
        assert second.evaluations(trace.execution_id) == ()
    finally:
        first.close()
        second.close()


def test_exact_subject_binding_wins_over_ambiguous_id_fallback() -> None:
    first = InMemoryExecutionStore()
    second = InMemoryExecutionStore()
    trace = _trace(first, "subject-owned-execution")
    _trace(second, "subject-owned-execution")
    assert bind_execution(trace.execution_id, first)
    assert bind_execution(trace.execution_id, second)
    assert bind_subject(trace, trace.execution_id, first)
    expect(trace).to_have_trace()
    assert len(first.evaluations(trace.execution_id)) == 1
    assert second.evaluations(trace.execution_id) == ()


def test_live_id_binding_is_not_evicted_after_many_other_bindings() -> None:
    store = InMemoryExecutionStore()
    trace = _trace(store, "long-lived-execution")
    stores = [InMemoryExecutionStore() for _ in range(4100)]
    try:
        assert bind_execution(trace.execution_id, store)
        for index, other in enumerate(stores):
            assert bind_execution(f"other-{index}", other)
        expect(trace).to_have_trace()
        assert len(store.evaluations(trace.execution_id)) == 1
    finally:
        _check_recording.unbind_execution(trace.execution_id, store)
        for index, other in enumerate(stores):
            _check_recording.unbind_execution(f"other-{index}", other)


def test_execution_id_cycle_is_traversed_without_recursion() -> None:
    class Node:
        pass

    subject: Any = Node()
    child: Any = Node()
    subject.trace = child
    child.result = subject
    child.execution_id = "cycle-execution"
    assert _subject_execution_id(subject) == "cycle-execution"


def test_sets_are_serialized_in_deterministic_order() -> None:
    assert _safe_json({"values": {"zeta", "alpha", "middle"}}) == {
        "values": ["alpha", "middle", "zeta"]
    }


def test_long_matcher_messages_mark_truncation() -> None:
    value = _safe_text("x" * 5000, None)
    assert len(value) == 4000
    assert value.endswith("…[truncated]")


def test_recording_failure_does_not_change_matcher_failure_and_is_diagnostic(
    monkeypatch,
) -> None:
    store = InMemoryExecutionStore()
    trace = _trace(store, "recording-failure")
    assert bind_execution(trace.execution_id, store)
    from mcp_pal._test_runs import activate_test, reset_test, test_attempt

    state = test_attempt(
        "run-recording-failure", "test-recording-failure", worker_id="master"
    )
    token = activate_test(state)
    try:

        def fail_redaction(*_args, **_kwargs):
            raise RuntimeError("sensitive internal detail")

        monkeypatch.setattr(_check_recording, "redact_for_persistence", fail_redaction)
        with pytest.raises(AssertionError, match="missing"):
            expect(trace).to_have_text("missing")
    finally:
        reset_test(token)
    assert state["diagnostics"] == {"mcp_pal": ["matcher_recording_failed"]}
    assert "sensitive" not in str(state)


def test_matcher_feedback_survives_sqlite_reopen(tmp_path) -> None:
    path = tmp_path / "feedback.sqlite"
    store = SQLiteExecutionStore(path)
    trace = _trace(store, "sqlite-execution")
    assert bind_execution(trace.execution_id, store)
    store.close()
    expect(trace).to_have_trace()

    reopened = SQLiteExecutionStore(path)
    records = reopened.evaluations(trace.execution_id)
    assert len(records) == 1
    assert records[0].name == "mcp_pal.matcher.to_have_trace.v1"
    reopened.close()


def test_callable_arguments_are_named_without_repr() -> None:
    store = InMemoryExecutionStore()
    trace = _trace(store, "predicate-execution")
    assert bind_execution(trace.execution_id, store)

    def predicate(_value: object) -> bool:
        return True

    expect(trace).to_have_tool_call(predicate=predicate, count=0)
    record = store.evaluations(trace.execution_id)[0]
    predicate_value = record.details["arguments"]["predicate"]
    assert predicate_value["callable"].endswith("predicate")
    assert predicate_value["implementation"] == "unknown"


def test_invalid_matcher_arguments_are_recorded_as_error() -> None:
    store = InMemoryExecutionStore()
    trace = _trace(store, "invalid-arguments")
    assert bind_execution(trace.execution_id, store)
    try:
        expect(trace).to_have_tool_call(count=True)  # type: ignore[arg-type]
    except ValueError:
        pass
    record = store.evaluations(trace.execution_id)[0]
    assert record.status.value == "error"


def _empty_server() -> Server:
    async def list_tools(_context: object, _params: object) -> ListToolsResult:
        return ListToolsResult(tools=[])

    return Server("feedback-fixture", on_list_tools=list_tools)


def test_sync_kit_opt_in_binds_direct_execution_after_client_close() -> None:
    store = InMemoryExecutionStore()
    kit = MCPTestKit(store=store, record_checks=True)
    client = kit.direct(InProcessServer(name="feedback", factory=_empty_server))
    with client:
        client.list_tools()
    trace = client.final_trace
    kit.close()
    del kit
    gc.collect()
    expect(trace).to_have_trace()
    assert store.evaluations(trace.execution_id)


def test_transient_kit_result_retains_store_until_trace_is_collected() -> None:
    kit = MCPTestKit(record_checks=True)
    client = kit.direct(InProcessServer(name="feedback", factory=_empty_server))
    with client:
        client.list_tools()
    trace = client.final_trace
    kit.close()
    del client
    del kit
    gc.collect()
    binding = _subject_binding(trace, trace.execution_id)
    assert binding is not None
    expect(trace).to_have_trace()


def test_tool_matrix_case_result_retains_feedback_after_temporary_kit_lifecycle() -> (
    None
):
    fixture = Path(__file__).parents[1] / "fixtures" / "matrix_stdio_server.py"
    server = StdioServer(
        name="matrix-feedback",
        command=sys.executable,
        args=(str(fixture),),
        cwd=str(Path(__file__).parents[2].parent),
    )
    matrix = ToolMatrix(
        servers=(
            ServerCase(
                name="matrix-feedback",
                server=server,
                tools=(ToolCase(name="echo", arguments={"text": "feedback"}),),
            ),
        )
    )
    result = None
    kit = MCPTestKit(record_checks=True)
    try:
        result = matrix.cases()[0].run(kit=kit)
    finally:
        kit.close()
    assert result is not None
    assert result.trace is not None
    expect(result.trace).to_have_trace()


@pytest.mark.asyncio
async def test_async_kit_opt_in_binds_direct_execution_after_client_close() -> None:
    store = InMemoryExecutionStore()
    kit = AsyncMCPTestKit(store=store, record_checks=True)
    client = kit.direct(InProcessServer(name="feedback", factory=_empty_server))
    async with client:
        await client.list_tools()
    final_trace = client.final_trace
    assert final_trace is not None
    expect(final_trace).to_have_trace()
    assert store.evaluations(final_trace.execution_id)
    await kit.aclose()
