"""Phase 7 assertion contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from mcp_pal.direct_client import ToolCallResult
from mcp_pal.matchers import check, expect


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
