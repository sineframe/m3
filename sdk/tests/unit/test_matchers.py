"""Assertion contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from m3.async_api import AsyncMCPTestKit
from m3.direct_client import ToolCallResult
from m3.matchers import check, expect
from m3.observability import ToolResult
from m3.testing import MockMCPServer
from m3.types import CallToolResult, ToolAnnotations, ToolInfo


def test_text_structured_regex_negative_and_ordered_content_matchers() -> None:
    result = ToolCallResult(
        content=(
            {"type": "text", "text": "answer: 42"},
            {"type": "text", "text": "done"},
        ),
        structured_content={"value": 42},
    )
    expect(result).to_have_text_containing("42")
    expect(result).to_have_text_matching(r"answer:\s+42")
    expect(result).to_not_have_text("failure")
    expect(result).to_have_structured_content({"value": 42})
    expect(result).to_have_ordered_content(
        [{"type": "text", "text": "answer: 42"}, {"type": "text", "text": "done"}]
    )
    expect(result).to_have_unordered_content(
        [{"type": "text", "text": "done"}, {"type": "text", "text": "answer: 42"}]
    )


def test_assertion_failure_is_structural_and_redacts_secret_fields() -> None:
    result = ToolCallResult(structured_content={"token": "dont-leak-this", "value": 1})
    with pytest.raises(AssertionError) as caught:
        expect(result).to_have_structured_content({"value": 2})
    assert "dont-leak-this" not in str(caught.value)
    assert "expected" in str(caught.value)


def test_grouped_checks_report_all_failures() -> None:
    with pytest.raises(AssertionError, match="2 grouped assertion"):
        with check() as checks:
            checks.expect("actual").to_have_text("first")
            checks.expect("actual").to_have_text("second")


def test_eventual_uses_subject_waiter_without_polling() -> None:
    class Handle:
        def wait_for(self, predicate: object, *, timeout: float) -> None:
            assert timeout == 2.0
            assert callable(predicate)
            assert predicate("ready") is True

    expect(Handle()).to_eventually(
        lambda value: expect(value).to_have_text("ready"), timeout=2.0
    )


def test_tool_call_negative_and_ambiguous_api_are_available() -> None:
    expect(object()).to_not_have_tool_call("missing")


def test_negative_assertions_on_live_handles_require_a_boundary() -> None:
    class LiveHandle:
        def wait_for(self, predicate: object, *, timeout: float) -> None:
            del predicate, timeout

    with pytest.raises(AssertionError, match="terminal/frozen"):
        expect(LiveHandle()).to_not_have_text("future")
    with pytest.raises(AssertionError, match="terminal/frozen"):
        expect(LiveHandle()).to_not_have_tool_call("future")

    # Explicit current-snapshot mode is the opt-in escape hatch for live data.
    expect(LiveHandle()).to_not_have_text("future", current_snapshot=True)
    expect(LiveHandle()).to_not_have_tool_call("future", current_snapshot=True)


def test_terminal_trace_and_frozen_snapshot_allow_negative_assertions() -> None:
    expect(SimpleNamespace(is_terminal=True, text="done")).to_not_have_text("failure")


def test_grouped_live_negative_assertions_are_reported_without_polling() -> None:
    class LiveHandle:
        def wait_for(self, predicate: object, *, timeout: float) -> None:
            del predicate, timeout

    with pytest.raises(AssertionError, match="2 grouped assertion"):
        with check() as checks:
            checks.expect(LiveHandle()).to_not_have_text("future")
            checks.expect(LiveHandle()).to_not_have_tool_call("future")


def _annotated(name: str, **hints: object) -> ToolInfo:
    values = {
        "read_only_hint": False,
        "destructive_hint": False,
        "idempotent_hint": True,
        "open_world_hint": False,
        **hints,
    }
    return ToolInfo(name=name, annotations=ToolAnnotations(**values))


def test_explicit_tool_annotations_accept_annotation_title_and_list_offenders() -> None:
    expect((_annotated("search", title="Search"),)).to_have_explicit_tool_annotations()

    tools = (
        ToolInfo(name="untitled", annotations=ToolAnnotations(read_only_hint=True)),
        _annotated("delete_user", title="Delete", destructive_hint=None),
    )
    with pytest.raises(AssertionError) as caught:
        expect(tools).to_have_explicit_tool_annotations()
    message = str(caught.value)
    assert (
        '"untitled" (title, destructive_hint, idempotent_hint, open_world_hint)'
        in message
    )
    assert '"delete_user" (destructive_hint)' in message

    with pytest.raises(AssertionError, match="no tools listed"):
        expect(()).to_have_explicit_tool_annotations()
    with pytest.raises(ValueError, match="unknown hint"):
        expect(tools).to_have_explicit_tool_annotations(hints=("bogus",))


def test_tool_annotations_by_pattern_require_declared_matches() -> None:
    tools = (
        _annotated("delete_user", title="Delete", destructive_hint=True),
        _annotated("search", title="Search"),
    )
    expect(tools).to_have_tool_annotations("delete_*", destructive_hint=True)

    undeclared = (*tools, ToolInfo(name="delete_all"))
    with pytest.raises(AssertionError) as caught:
        expect(undeclared).to_have_tool_annotations("delete_*", destructive_hint=True)
    assert '"delete_all" (destructive_hint: expected True, declared undeclared)' in str(
        caught.value
    )
    assert "delete_user" not in str(caught.value)

    with pytest.raises(AssertionError, match="no tools match pattern"):
        expect(tools).to_have_tool_annotations("drop_*", destructive_hint=True)
    with pytest.raises(ValueError, match="at least one hint"):
        expect(tools).to_have_tool_annotations("delete_*")


def test_response_size_limit_is_inclusive_and_never_echoes_content() -> None:
    result = CallToolResult(content=({"type": "text", "text": "secret-body"},))
    size = len('{"content":[{"text":"secret-body","type":"text"}]}')
    expect(result).to_have_response_size_at_most(max_bytes=size)
    with pytest.raises(AssertionError) as caught:
        expect(result).to_have_response_size_at_most(max_bytes=size - 1)
    assert f"{size} bytes" in str(caught.value)
    assert "secret-body" not in str(caught.value)

    large = CallToolResult(
        content=({"type": "text", "text": "x" * 4000},),
        structured_content={"rows": []},
    )
    with pytest.raises(AssertionError, match="limit max_tokens=500"):
        expect(large).to_have_response_size_at_most(max_tokens=500)

    for invalid in (0, True):
        with pytest.raises(ValueError, match="max_bytes must be a positive integer"):
            expect(result).to_have_response_size_at_most(max_bytes=invalid)
    with pytest.raises(AssertionError, match="subject is not a tool call result"):
        expect("text").to_have_response_size_at_most(max_bytes=10)


@pytest.mark.asyncio
async def test_response_size_measures_mcp_wire_spelling_on_direct_and_trace_results() -> (
    None
):
    mock = MockMCPServer()

    @mock.tool("greet")
    def greet(_arguments: dict[str, object]) -> str:
        return "hi"

    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.direct(mock.in_process()) as client:
            direct = await client.call_tool("greet", {})

    wire_size = len('{"content":[{"text":"hi","type":"text"}]}')
    trace = ToolResult.model_validate({"content": [{"kind": "text", "text": "hi"}]})
    for result in (direct, trace):
        expect(result).to_have_response_size_at_most(max_bytes=wire_size)
        with pytest.raises(AssertionError, match=f"is {wire_size} bytes"):
            expect(result).to_have_response_size_at_most(max_bytes=wire_size - 1)

    with_nulls = CallToolResult(structured_content={"next": None})
    nulls_size = len('{"content":[],"structuredContent":{"next":null}}')
    with pytest.raises(AssertionError, match=f"is {nulls_size} bytes"):
        expect(with_nulls).to_have_response_size_at_most(max_bytes=nulls_size - 1)
