"""Classify one agent execution and append it to the trial inbox.

Every trial gets exactly one raw state, checked in this order:

1. execution_failed      the execution did not complete (resolved by classify.py)
2. control_failed        control echo did not succeed on the wire (not measured)
3. works                 tool reached the server with the expected args and returned the
                         expected status (default success; tool_error for isError evals)
                         (and, when a marker is set, the final answer contains it)
4. result_lost           marker evals only: wire success but the marker is missing
5. mangled               tool reached the server with other args / not successfully
6. rejected_client_side  harness reported the call, but it never reached the server
7. silently_dropped      no evidence of the call at all

classifier_error is recorded when a matcher raises something other than an
assertion; classify.py treats it as not measured.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from m3 import ExecutionOutcome, expect

# Benchmark root: _lib/trial.py -> parents[1]. Each trial appends one record here.
ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "results" / "_inbox" / "records.jsonl"


def _passes(check: Callable[[], object]) -> bool:
    try:
        check()
    except AssertionError:
        return False
    return True


def _plain(value: Any) -> Any:
    """Convert M3 frozen models/mappings into JSON-safe values."""
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _observed(observation: Any) -> Any:
    return _plain(getattr(observation, "value", None))


def final_text(result: Any) -> str | None:
    """Final answer text: the turn response, else the last assistant message.

    Some harnesses (Codex) leave the turn response empty and only stream
    assistant messages; the last non-empty one is the completed answer.
    """
    turns = getattr(result, "turns", None) or ()
    if turns:
        text = getattr(getattr(turns[-1], "response", None), "text", None)
        if text:
            return text
    for entry in reversed(result.trace_view.messages):
        if _plain(entry.role) != "assistant":
            continue
        text = "".join(
            getattr(block, "text", "") or ""
            for block in entry.content
            if getattr(block, "kind", None) == "text"
        )
        if text.strip():
            return text
    return None


def _tool_call_evidence(result: Any, tool: str) -> list[dict[str, Any]]:
    entries = []
    for entry in result.trace_view.tool_calls:
        if _observed(entry.tool) != tool:
            continue
        reported = getattr(entry.reported, "value", None)
        entries.append(
            {
                "server": _observed(entry.server),
                "arguments": _observed(entry.arguments),
                "status": _plain(entry.tool_status),
                "correlation": _plain(entry.correlation),
                "conflicts": _plain(entry.conflicts),
                "reported_arguments": _observed(reported.arguments)
                if reported
                else None,
                "reported_status": _observed(reported.status) if reported else None,
            }
        )
    return entries


def classify(
    result: Any,
    *,
    tool: str,
    arguments: dict[str, Any],
    server: str = "quirk",
    control_gate: bool = True,
    marker: str | None = None,
    markers: tuple[str, ...] = (),
    expected_status: str = "success",
) -> str:
    if result.snapshot.outcome is not ExecutionOutcome.COMPLETED:
        return "execution_failed"
    if control_gate and not _passes(
        lambda: expect(result).to_have_tool_call(
            "echo", server="control", status="success", evidence="wire"
        )
    ):
        return "control_failed"
    if _passes(
        lambda: expect(result).to_have_tool_call(
            tool,
            server=server,
            arguments=arguments,
            arguments_partial=True,
            status=expected_status,
            evidence="wire",
        )
    ):
        wanted_markers = ((marker,) if marker is not None else ()) + markers
        if any(value not in (final_text(result) or "") for value in wanted_markers):
            return "result_lost"
        return "works"
    if _passes(
        lambda: expect(result).to_have_tool_call(tool, server=server, evidence="wire")
    ):
        return "mangled"
    if _passes(lambda: expect(result).to_have_reported_tool_call(tool, server=server)):
        return "rejected_client_side"
    return "silently_dropped"


def _safe_classify(result: Any, **kwargs: Any) -> tuple[str, str | None]:
    try:
        return classify(result, **kwargs), None
    except Exception as error:  # a matcher bug must not lose the trial
        return "classifier_error", f"{type(error).__name__}: {error}"


def record_trial(
    agent: Any,
    result: Any,
    *,
    quirk: str,
    backlog_id: str,
    tool: str,
    arguments: dict[str, Any],
    server: str = "quirk",
    control_gate: bool = True,
    marker: str | None = None,
    markers: tuple[str, ...] = (),
    expected_status: str = "success",
    extra_tools: tuple[tuple[str, dict[str, Any]], ...] = (),
) -> str:
    state, classifier_error = _safe_classify(
        result,
        tool=tool,
        arguments=arguments,
        server=server,
        control_gate=control_gate,
        marker=marker,
        markers=markers,
        expected_status=expected_status,
    )
    extra = {}
    for extra_tool, extra_arguments in extra_tools:
        extra[extra_tool] = _safe_classify(
            result,
            tool=extra_tool,
            arguments=extra_arguments,
            server=server,
            control_gate=control_gate,
        )[0]
    snapshot = result.snapshot
    identity = getattr(snapshot, "agent", None)
    harness_identity = getattr(identity, "harness", None)
    text = final_text(result)
    record = {
        "quirk": quirk,
        "backlog_id": backlog_id,
        "harness": agent.harness,
        "model": agent.model,
        "trial": agent.trial,
        "version": getattr(harness_identity, "resolved_version", None),
        "digest": getattr(harness_identity, "digest", None),
        "observed_model": getattr(
            getattr(identity, "model", None), "observed_id", None
        ),
        "execution_id": str(
            getattr(snapshot.execution_id, "root", snapshot.execution_id)
        ),
        "outcome": snapshot.outcome.value if snapshot.outcome is not None else None,
        "error": (
            json.dumps(_plain(result.error), sort_keys=True)
            if result.error is not None
            else None
        ),
        "state": state,
        "expected_status": expected_status,
        "expected_markers": list(((marker,) if marker is not None else ()) + markers),
        "classifier_error": classifier_error,
        "extra": extra,
        "tool_calls": _safe_evidence(result, tool),
        "final_text": text[:2000] if text else None,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    INBOX.parent.mkdir(parents=True, exist_ok=True)
    with INBOX.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return state


def _safe_evidence(result: Any, tool: str) -> list[dict[str, Any]] | str:
    try:
        return _tool_call_evidence(result, tool)
    except Exception as error:
        return f"{type(error).__name__}: {error}"
