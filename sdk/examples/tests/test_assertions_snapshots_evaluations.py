"""Author-facing assertions, snapshots, and deterministic evaluations."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from mcp_pal.async_api import AsyncMCPTestKit

from mcp_pal import (
    ArtifactId,
    ArtifactRef,
    CallToolOperation,
    Capability,
    CapabilityStatus,
    DirectExecutionSpec,
    ErrorCode,
    ErrorInfo,
    EvaluationStatus,
    ExecutionOutcome,
    LifecycleState,
    MCPTestKit,
    RequiredEvaluationError,
    ServerBinding,
    StdioServer,
    canonical_snapshot,
    check,
    expect,
)

_QUOTE_CONTENT = {"type": "text", "text": '{"amount": 7.0, "currency": "USD"}'}
_QUOTE_CONTENT_RESULT = {**_QUOTE_CONTENT, "annotations": None, "_meta": None}


def test_text_structured_content_and_grouped_assertions(
    example_server: StdioServer,
) -> None:
    with MCPTestKit(env={}) as kit, kit.direct(example_server) as client:
        quote = client.call_tool("shipping_quote", {"weight_kg": 1, "zone": "local"})

    expect(quote).to_have_text('{"amount": 7.0, "currency": "USD"}')
    expect(quote).to_have_text_containing('"currency": "USD"')
    expect(quote).to_have_text_matching(r'\{"amount": 7\.0, .*\}')
    expect(quote).to_have_text_matching_regex(r'"currency":\s*"USD"')
    expect(quote).to_not_have_text("EUR")
    expect(quote).to_not_have_text_containing("failure")
    expect(quote).to_have_structured_content({"amount": 7.0, "currency": "USD"})
    expect(quote).to_have_content([_QUOTE_CONTENT_RESULT])
    expect(quote).to_have_content([_QUOTE_CONTENT_RESULT], ordered=False)
    expect(quote).to_have_ordered_content([_QUOTE_CONTENT_RESULT])
    expect(quote).to_have_unordered_content([_QUOTE_CONTENT_RESULT])

    with check() as checks:
        checks.expect(quote).to_have_text_containing("USD")
        checks.expect(quote).to_have_structured_content(
            {"amount": 7.0, "currency": "USD"}
        )


def test_tool_call_arguments_results_counts_errors_and_order(
    example_server: StdioServer,
) -> None:
    with MCPTestKit(env={}) as kit:
        client = kit.direct(example_server)
        with client:
            client.call_tool("shipping_quote", {"weight_kg": 1, "zone": "local"})
            client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "regional"})
            client.call_tool("always_fails")

        trace = client.final_trace

    assert trace is not None
    view = trace.view()
    calls = view.tool_calls
    assert [call.tool.value for call in calls] == [
        "shipping_quote",
        "shipping_quote",
        "always_fails",
    ]
    assert calls[0].arguments.value == {
        "weight_kg": 1,
        "zone": "local",
    }
    assert calls[1].arguments.value == {
        "weight_kg": 2,
        "zone": "regional",
    }
    assert calls[0].result.value is not None and calls[0].result.value.is_error is False
    assert calls[2].result.value is not None and calls[2].result.value.is_error is True


def test_trace_duration_protocol_transport_and_capability_assertions(
    example_server: StdioServer,
) -> None:
    with MCPTestKit(env={}) as kit:
        client = kit.direct(example_server)
        with client:
            client.ping()

        trace = client.final_trace

    assert trace is not None
    view = trace.view()
    expect(view).to_have_protocol_version("2025-11-25")
    assert view.runtime.transport.state.value in {
        "observed",
        "unavailable",
        "not_emitted",
    }
    expect(view).to_have_capability("tools")
    expect(view).to_have_trace(completeness="partial", limitation="capture_incomplete")
    expect(view).to_have_event("protocol")
    expect(view).to_have_event("lifecycle", count=2)
    expect(view).to_have_duration(min_ms=0)
    expect(view).to_have_duration_between(0, 30_000)
    expect(view).to_have_duration(expected_ms=0, tolerance_ms=30_000)
    expect(view).to_eventually(lambda value: expect(value).to_have_event("protocol"))
    expect(
        SimpleNamespace(
            capabilities=(Capability(name="tools", status=CapabilityStatus.READY),)
        )
    ).to_have_capability("tools", status=CapabilityStatus.READY)


def test_execution_outcome_lifecycle_error_artifact_and_workspace_assertions(
    example_server: StdioServer,
) -> None:
    spec = DirectExecutionSpec(
        servers=(ServerBinding(server=example_server, alias="example"),),
        operation=CallToolOperation(
            server="example",
            name="shipping_quote",
            arguments={"weight_kg": 1, "zone": "local"},
        ),
    )
    with MCPTestKit(env={}) as kit:
        execution = kit.run(spec)

    expect(execution).to_have_lifecycle(LifecycleState.FINISHED)
    expect(execution).to_have_outcome(ExecutionOutcome.COMPLETED)
    expect(execution).to_have_terminal_outcome(ExecutionOutcome.COMPLETED)
    expect(execution).to_be_completed()
    expect(execution).to_have_trace(
        completeness="partial", limitation="capture_incomplete"
    )

    artifact = ArtifactRef(
        artifact_id=ArtifactId("example-result"),
        execution_id=execution.snapshot.execution_id,
        name="result.json",
        media_type="application/json",
        size_bytes=0,
        sha256="0" * 64,
    )
    with_artifact = execution.model_copy(update={"artifacts": (artifact,)})
    assert with_artifact.artifacts == (artifact,)

    with_error = execution.model_copy(
        update={
            "error": ErrorInfo(
                code=ErrorCode.PROTOCOL_ERROR, message="example failure"
            ),
        }
    )
    assert with_error.error == ErrorInfo(
        code=ErrorCode.PROTOCOL_ERROR, message="example failure"
    )

    workspace = SimpleNamespace(
        workspace_diff={"added": ["report.json"], "modified": [], "deleted": []}
    )
    expect(workspace).to_have_workspace_diff(
        added=["report.json"], modified=[], deleted=[]
    )
    expect(workspace).to_have_workspace_diff(
        {"added": ["report.json"], "modified": [], "deleted": []}
    )


def test_canonical_snapshot_is_stable_and_json_compatible(
    example_server: StdioServer,
) -> None:
    with MCPTestKit(env={}) as kit, kit.direct(example_server) as client:
        quote = client.call_tool("shipping_quote", {"weight_kg": 1, "zone": "local"})

    snapshot = canonical_snapshot(quote)
    assert snapshot == {
        "content": [_QUOTE_CONTENT_RESULT],
        "is_error": False,
        "structured_content": {"amount": 7.0, "currency": "USD"},
    }


def test_sync_evaluations_pass_fail_and_persist(example_server: StdioServer) -> None:
    with MCPTestKit(env={}) as kit:
        with kit.direct(example_server) as client:
            quote = client.call_tool(
                "shipping_quote", {"weight_kg": 1, "zone": "local"}
            )

        kit.register_evaluator(
            "usd-quote",
            lambda context: context.subject["structured_content"]["currency"] == "USD",
        )
        passed = kit.evaluate(quote, "usd-quote", required=True)
        assert passed.status is EvaluationStatus.PASSED
        assert kit.evaluation_results() == (passed,)

        kit.register_evaluator("must-fail", lambda _context: False)
        with pytest.raises(RequiredEvaluationError):
            kit.evaluate(quote, "must-fail", required=True)
        assert {result.name: result.status for result in kit.evaluation_results()} == {
            "usd-quote": EvaluationStatus.PASSED,
            "must-fail": EvaluationStatus.FAILED,
        }


@pytest.mark.asyncio
async def test_async_evaluation_callback_is_awaited(
    example_server: StdioServer,
) -> None:
    async with AsyncMCPTestKit(env={}) as kit:
        async with kit.direct(example_server) as client:
            quote = await client.call_tool(
                "shipping_quote", {"weight_kg": 1, "zone": "local"}
            )

        async def has_usd(context: Any) -> bool:
            subject = context.subject
            return subject["structured_content"]["currency"] == "USD"

        kit.register_evaluator("async-usd-quote", has_usd)
        evaluation = await kit.evaluate(quote, "async-usd-quote", required=True)

    assert evaluation.status is EvaluationStatus.PASSED
