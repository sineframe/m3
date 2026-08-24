"""Pure, framework-neutral assertions over MCP Pal value projections."""

from __future__ import annotations

import difflib as _difflib
import json as _json
import math as _math
import re as _re
from collections.abc import Callable as _Callable, Mapping as _Mapping, Sequence as _Sequence
from numbers import Real as _Real
from typing import Any as _Any, Generic as _Generic, Literal as _Literal, TypeVar as _TypeVar

from .types import (
    ArtifactRef as _ArtifactRef,
    CapabilityStatus as _CapabilityStatus,
    CanonicalEvent as _CanonicalEvent,
    EventDirection as _EventDirection,
    EventKind as _EventKind,
    EventOrigin as _EventOrigin,
    ExecutionOutcome as _ExecutionOutcome,
    ExecutionResult as _ExecutionResult,
    LifecycleState as _LifecycleState,
    TraceResult as _TraceResult,
    ExecutionSnapshot as _ExecutionSnapshot,
    TurnSnapshot as _TurnSnapshot,
    TurnLifecycle as _TurnLifecycle,
    TurnResponse as _TurnResponse,
    TurnResult as _TurnResult,
)
from .trace.redaction import RedactionConfig as _RedactionConfig, redact_for_persistence as _redact_for_persistence

_SubjectT = _TypeVar("_SubjectT")
_FailureSink = _Callable[[AssertionError], None]


def _plain(value: _Any) -> _Any:
    if isinstance(value, _Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _plain(value.model_dump(mode="json"))
        except Exception:
            return repr(value)
    if hasattr(value, "root"):
        return str(value.root)
    if hasattr(value, "value") and isinstance(value.value, str):
        return value.value
    return value


def _redact(value: _Any, depth: int = 0) -> _Any:
    if depth > 5:
        return "<omitted>"
    if isinstance(value, _Mapping):
        result: dict[str, _Any] = {}
        for key, item in list(value.items())[:64]:
            name = str(key)
            if any(token in name.lower() for token in ("secret", "token", "password", "authorization", "cookie")):
                result[name] = "<redacted>"
            else:
                result[name] = _redact(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth + 1) for item in value[:64]]
    if isinstance(value, str):
        return value[:512]
    return value


def _safe(value: _Any, config: _RedactionConfig | None = None) -> str:
    try:
        projected = _redact_for_persistence(_plain(value), config=config)
        return _json.dumps(projected, sort_keys=True, default=str, allow_nan=False)[:2000]
    except Exception:
        return "<unavailable>"


def _diff(expected: _Any, actual: _Any, config: _RedactionConfig | None = None) -> str:
    result = "\n".join(
        _difflib.unified_diff(
            _safe(expected, config).splitlines(),
            _safe(actual, config).splitlines(),
            fromfile="expected",
            tofile="actual",
            lineterm="",
        )
    )
    return result[:4000] + ("\n<diff truncated>" if len(result) > 4000 else "")


def _text(subject: _Any) -> str:
    if isinstance(subject, str):
        return subject
    value = getattr(subject, "text", None)
    if isinstance(value, str):
        return value
    if isinstance(subject, _TurnResult) and subject.response is not None:
        return subject.response.text
    response = getattr(subject, "response", None)
    if isinstance(response, _TurnResponse):
        return response.text
    content = getattr(subject, "content", None)
    if isinstance(content, _Sequence) and not isinstance(content, (str, bytes)):
        chunks: list[str] = []
        for block in content:
            if isinstance(block, _Mapping) and isinstance(block.get("text"), str):
                chunks.append(str(block["text"]))
            elif isinstance(getattr(block, "text", None), str):
                chunks.append(str(block.text))
        return "".join(chunks)
    return ""


def _structured(subject: _Any) -> _Any:
    if hasattr(subject, "structured_content"):
        return getattr(subject, "structured_content")
    if isinstance(subject, _Mapping):
        return subject
    return None


def _trace(subject: _Any) -> _TraceResult | None:
    if isinstance(subject, _TraceResult):
        return subject
    value = getattr(subject, "trace", None)
    return value if isinstance(value, _TraceResult) else None


def _events(subject: _Any) -> tuple[_CanonicalEvent, ...]:
    trace = _trace(subject)
    if trace is not None:
        return trace.events
    if isinstance(subject, _ExecutionResult):
        if subject.trace is not None:
            return subject.trace.events
    direct_events = getattr(subject, "events", None)
    if isinstance(direct_events, _Sequence) and not isinstance(direct_events, (str, bytes)):
        return tuple(event for event in direct_events if isinstance(event, _CanonicalEvent))
    turns = getattr(subject, "turns", None)
    if isinstance(turns, _Sequence):
        events: list[_CanonicalEvent] = []
        for turn in turns:
            trace = _trace(turn)
            if trace is not None:
                events.extend(trace.events)
        return tuple(events)
    return ()


def _event_payload(event: _CanonicalEvent) -> _Mapping[str, _Any]:
    return event.payload


def _discovered_tools(subject: _Any, evidence: str) -> dict[str, set[str]]:
    discovered: dict[str, set[str]] = {}
    for event in _events(subject):
        if evidence == "reported" and event.provenance.origin is not _EventOrigin.HARNESS_REPORTED:
            continue
        if evidence == "wire" and event.provenance.origin is not _EventOrigin.WIRE_OBSERVED:
            continue
        if event.kind not in {_EventKind.MCP_RESPONSE, _EventKind.MCP_INITIALIZED}:
            continue
        result = event.payload.get("result", event.payload)
        tools = result.get("tools") if isinstance(result, _Mapping) else None
        if not isinstance(tools, _Sequence) or isinstance(tools, (str, bytes)):
            continue
        server = event.server_binding
        if server is None:
            continue
        for item in tools:
            if isinstance(item, _Mapping) and isinstance(item.get("name"), str):
                discovered.setdefault(str(item["name"]), set()).add(server)
    return discovered


def _tool_calls(subject: _Any, *, evidence: str = "wire") -> list[dict[str, _Any]]:
    requests: dict[tuple[str, int], dict[str, _Any]] = {}
    calls: list[dict[str, _Any]] = []
    for event in _events(subject):
        if evidence == "reported" and event.provenance.origin is not _EventOrigin.HARNESS_REPORTED:
            continue
        if evidence == "wire" and event.provenance.origin is not _EventOrigin.WIRE_OBSERVED:
            continue
        correlation = event.correlation
        if (
            event.kind is _EventKind.TOOL_CALL_REQUESTED
            and correlation is not None
            and correlation.request_sequence is not None
            and correlation.direction is _EventDirection.CLIENT_TO_SERVER
        ):
            params = _event_payload(event).get("params", {})
            params_map = params if isinstance(params, _Mapping) else {}
            key = (str(event.connection_id), correlation.request_sequence)
            call: dict[str, _Any] = {
                "event": event,
                "server": event.server_binding,
                "tool": params_map.get("name"),
                "arguments": params_map.get("arguments", {}),
                "request_sequence": correlation.request_sequence,
                "result": None,
                "status": "incomplete",
                "latency_ms": None,
                "evidence": evidence,
            }
            requests[key] = call
            calls.append(call)
        elif event.kind in {
            _EventKind.TOOL_RESULT_RECEIVED,
            _EventKind.MCP_ERROR,
            _EventKind.MCP_CANCELLATION_REQUESTED,
            _EventKind.MCP_CANCELLATION_COMPLETED,
        } and correlation is not None and correlation.request_sequence is not None:
            if event.kind in {_EventKind.TOOL_RESULT_RECEIVED, _EventKind.MCP_ERROR} and correlation.direction is not _EventDirection.SERVER_TO_CLIENT:
                continue
            key = (str(event.connection_id), correlation.request_sequence)
            matched_call = requests.get(key)
            if matched_call is None:
                continue
            if event.kind in {_EventKind.MCP_CANCELLATION_REQUESTED, _EventKind.MCP_CANCELLATION_COMPLETED}:
                matched_call["status"] = "cancelled"
                continue
            payload = _event_payload(event)
            if event.kind is _EventKind.MCP_ERROR:
                matched_call["status"] = "protocol_error"
                matched_call["result"] = payload.get("error")
            else:
                result = payload.get("result", payload)
                matched_call["result"] = result
                is_error = isinstance(result, _Mapping) and bool(result.get("isError", result.get("is_error", False)))
                matched_call["status"] = "tool_error" if is_error else "success"
            matched_call["latency_ms"] = max(0.0, event.monotonic_offset_ms - matched_call["event"].monotonic_offset_ms)
        elif event.kind is _EventKind.TRANSPORT_DISCONNECTED:
            for call in calls:
                if call["status"] == "incomplete":
                    call["status"] = "transport_error"
    return calls


def _matches(
    actual: _Any,
    expected: _Any,
    *,
    partial: bool = False,
    unordered: bool = False,
    regex: bool = False,
    numeric_tolerance: float | None = None,
) -> bool:
    if numeric_tolerance is not None and isinstance(actual, _Real) and isinstance(expected, _Real) and not isinstance(actual, bool) and not isinstance(expected, bool):
        return abs(float(actual) - float(expected)) <= numeric_tolerance
    if regex and isinstance(actual, str) and isinstance(expected, str):
        return _re.fullmatch(expected, actual) is not None
    if isinstance(expected, _Mapping) and isinstance(actual, _Mapping):
        if not partial and set(actual) != set(expected):
            return False
        return all(
            key in actual
            and _matches(actual[key], value, partial=partial, unordered=unordered, regex=regex, numeric_tolerance=numeric_tolerance)
            for key, value in expected.items()
        )
    if isinstance(actual, _Sequence) and isinstance(expected, _Sequence) and not isinstance(actual, (str, bytes)) and not isinstance(expected, (str, bytes)):
        if unordered:
            remaining = list(actual)
            for expected_value in expected:
                match_index = next((index for index, actual_value in enumerate(remaining) if _matches(actual_value, expected_value, partial=partial, unordered=unordered, regex=regex, numeric_tolerance=numeric_tolerance)), None)
                if match_index is None:
                    return False
                remaining.pop(match_index)
            return not remaining
        return len(actual) == len(expected) and all(
            _matches(actual_value, expected_value, partial=partial, unordered=unordered, regex=regex, numeric_tolerance=numeric_tolerance)
            for actual_value, expected_value in zip(actual, expected)
        )
    return bool(_plain(actual) == _plain(expected))


def _snapshot(subject: _Any) -> _Any:
    if isinstance(subject, (_ExecutionSnapshot, _TurnSnapshot)):
        return subject
    return getattr(subject, "snapshot", None)


def _duration_ms(subject: _Any) -> float | None:
    value = getattr(subject, "duration_ms", None)
    if isinstance(value, _Real) and not isinstance(value, bool):
        duration = float(value)
        return duration if _math.isfinite(duration) and duration >= 0.0 else None
    snapshot = _snapshot(subject)
    created = getattr(snapshot, "created_at", None)
    finished = getattr(snapshot, "finished_at", None)
    if created is not None and finished is not None:
        try:
            delta = finished - created
            total_seconds = getattr(delta, "total_seconds", None)
            if callable(total_seconds):
                seconds = total_seconds()
                if isinstance(seconds, _Real) and not isinstance(seconds, bool):
                    duration = max(0.0, float(seconds) * 1000.0)
                    if _math.isfinite(duration):
                        return duration
        except (AttributeError, TypeError):
            pass
    events = _events(subject)
    if events:
        return max(0.0, events[-1].monotonic_offset_ms - events[0].monotonic_offset_ms)
    return None


def _is_terminal_subject(subject: _Any) -> bool:
    if isinstance(subject, (_TraceResult, _ExecutionResult, _TurnResult)):
        if isinstance(subject, _TraceResult):
            return True
        snapshot = _snapshot(subject)
        return getattr(getattr(snapshot, "lifecycle", None), "value", getattr(snapshot, "lifecycle", None)) == "finished"
    snapshot = _snapshot(subject)
    if snapshot is not None:
        lifecycle = getattr(snapshot, "lifecycle", None)
        if getattr(lifecycle, "value", lifecycle) == "finished":
            return True
    return bool(getattr(subject, "is_terminal", False) or getattr(subject, "terminal", False))


def _negative_boundary(subject: _Any, *, current_snapshot: bool) -> bool:
    if current_snapshot or _is_terminal_subject(subject):
        return True
    # Value projections without a live waiter are immutable/frozen by
    # construction. Handles expose wait_for and must opt into a snapshot.
    return not callable(getattr(subject, "wait_for", None))


class Expectation(_Generic[_SubjectT]):
    """One-shot assertion facade; matchers never persist evaluations."""

    def __init__(self, subject: _SubjectT, sink: _FailureSink | None = None, redaction_config: _RedactionConfig | None = None) -> None:
        self.subject = subject
        self._sink = sink
        self._redaction_config = redaction_config or _RedactionConfig.from_environment()

    def _fail(self, message: str) -> None:
        trace = _trace(self.subject)
        if trace is not None:
            message = f"{message}; trace_id={_safe(trace.trace_id, self._redaction_config)}, execution_id={_safe(trace.execution_id, self._redaction_config)}"
        snapshot = getattr(self.subject, "snapshot", None)
        if snapshot is not None and hasattr(snapshot, "execution_id"):
            message = f"{message}; execution_id={_safe(snapshot.execution_id, self._redaction_config)}"
        error = AssertionError(message)
        if self._sink is not None:
            self._sink(error)
            return
        raise error

    def _require(self, condition: bool, message: str) -> None:
        if not condition:
            self._fail(message)

    def to_have_text(self, expected: str) -> None:
        actual = _text(self.subject)
        self._require(actual == expected, f"text mismatch\n{_diff(expected, actual, self._redaction_config)}")

    def to_have_text_containing(self, expected: str) -> None:
        actual = _text(self.subject)
        self._require(expected in actual, f"text does not contain {_safe(expected, self._redaction_config)}; actual={_safe(actual, self._redaction_config)}")

    def to_have_text_matching(self, pattern: str, *, flags: int = 0) -> None:
        actual = _text(self.subject)
        self._require(_re.search(pattern, actual, flags) is not None, f"text does not match /{_safe(pattern, self._redaction_config)}/; actual={_safe(actual, self._redaction_config)}")

    def to_have_text_matching_regex(self, pattern: str, *, flags: int = 0) -> None:
        self.to_have_text_matching(pattern, flags=flags)

    def to_not_have_text(self, unexpected: str, *, current_snapshot: bool = False) -> None:
        if not _negative_boundary(self.subject, current_snapshot=current_snapshot):
            self._fail("negative text assertion requires a terminal/frozen boundary or current_snapshot=True")
            return
        self._require(unexpected not in _text(self.subject), f"text unexpectedly contained {_safe(unexpected, self._redaction_config)}")

    def to_not_have_text_containing(self, unexpected: str, *, current_snapshot: bool = False) -> None:
        self.to_not_have_text(unexpected, current_snapshot=current_snapshot)

    def to_have_structured_content(self, expected: object) -> None:
        actual = _structured(self.subject)
        self._require(_matches(actual, expected), f"structured content mismatch\n{_diff(expected, actual, self._redaction_config)}")

    def to_have_content(self, expected: _Any, *, ordered: bool = True) -> None:
        actual = getattr(self.subject, "content", None)
        if actual is None and isinstance(self.subject, _TurnResult) and self.subject.response is not None:
            actual = self.subject.response.content
        expected_values = list(expected) if isinstance(expected, (list, tuple)) else [expected]
        actual_values = list(actual or ())
        if ordered:
            matched = len(actual_values) == len(expected_values) and all(_matches(a, e) for a, e in zip(actual_values, expected_values))
        else:
            remaining = list(actual_values)
            matched = len(remaining) == len(expected_values)
            if matched:
                for expected_value in expected_values:
                    match_index = next((index for index, actual_value in enumerate(remaining) if _matches(actual_value, expected_value)), None)
                    if match_index is None:
                        matched = False
                        break
                    remaining.pop(match_index)
        self._require(matched, f"content blocks mismatch\n{_diff(expected_values, actual_values, self._redaction_config)}")

    def to_have_ordered_content(self, expected: _Any) -> None:
        self.to_have_content(expected, ordered=True)

    def to_have_unordered_content(self, expected: _Any) -> None:
        self.to_have_content(expected, ordered=False)

    def to_have_tool_call(
        self,
        tool: str | None = None,
        *,
        name: str | None = None,
        server: str | None = None,
        arguments: _Any = None,
        arguments_partial: bool = False,
        arguments_unordered: bool = False,
        arguments_regex: bool = False,
        arguments_tolerance: float | None = None,
        argument_predicate: _Callable[[_Any], bool] | None = None,
        result: _Any = None,
        status: str | None = None,
        count: int | None = None,
        min_count: int | None = None,
        max_count: int | None = None,
        turn: _Any = None,
        predicate: _Callable[[_Mapping[str, _Any]], bool] | None = None,
        server_name: str | None = None,
        evidence: _Literal["wire", "reported", "any"] = "wire",
        min_latency_ms: float | None = None,
        max_latency_ms: float | None = None,
        current_snapshot: bool = False,
    ) -> None:
        if count == 0 or max_count == 0:
            if not _negative_boundary(self.subject, current_snapshot=current_snapshot):
                self._fail("negative tool assertion requires a terminal/frozen boundary or current_snapshot=True")
                return
        if evidence not in {"wire", "reported", "any"}:
            raise ValueError("tool-call evidence must be 'wire', 'reported', or 'any'")
        requested_tool = name or tool
        server = server or server_name
        evidence_values = ("wire", "reported") if evidence == "any" else (evidence,)
        calls = [call for selected in evidence_values for call in _tool_calls(self.subject, evidence=selected)]
        discovered: dict[str, set[str]] = {}
        for selected in evidence_values:
            for discovered_name, discovered_servers in _discovered_tools(self.subject, selected).items():
                discovered.setdefault(discovered_name, set()).update(discovered_servers)
        servers = {str(call["server"]) for call in calls if call["tool"] == requested_tool and call["server"] is not None}
        servers.update(discovered.get(str(requested_tool), set()))
        if requested_tool is not None and server is None and len(servers) > 1:
            self._fail(f"ambiguous serverless tool assertion for {_safe(requested_tool, self._redaction_config)}; candidates={_safe(sorted(servers), self._redaction_config)}")
            return
        matches: list[dict[str, _Any]] = []
        for call in calls:
            if requested_tool is not None and call["tool"] != requested_tool:
                continue
            if server is not None and call["server"] != server:
                continue
            if arguments is not None and not _matches(
                call["arguments"],
                arguments,
                partial=arguments_partial,
                unordered=arguments_unordered,
                regex=arguments_regex,
                numeric_tolerance=arguments_tolerance,
            ):
                continue
            if argument_predicate is not None:
                try:
                    predicate_match = bool(argument_predicate(call["arguments"]))
                except Exception as error:
                    self._fail(f"tool argument predicate raised {_safe(type(error).__name__, self._redaction_config)}")
                    return
                if not predicate_match:
                    continue
            if result is not None and not _matches(call["result"], result):
                continue
            if status is not None and call["status"] != status:
                continue
            if min_latency_ms is not None and (call["latency_ms"] is None or call["latency_ms"] < min_latency_ms):
                continue
            if max_latency_ms is not None and (call["latency_ms"] is None or call["latency_ms"] > max_latency_ms):
                continue
            if turn is not None and call["event"].turn_id != turn:
                continue
            if predicate is not None:
                try:
                    predicate_match = bool(predicate(call))
                except Exception as error:
                    self._fail(f"tool-call predicate raised {_safe(type(error).__name__, self._redaction_config)}")
                    return
                if not predicate_match:
                    continue
            matches.append(call)
        if count is not None:
            ok = len(matches) == count
        elif min_count is not None or max_count is not None:
            ok = (min_count is None or len(matches) >= min_count) and (max_count is None or len(matches) <= max_count)
        else:
            ok = bool(matches)
        self._require(ok, f"tool call assertion failed; requested={_safe(requested_tool, self._redaction_config)}, server={_safe(server, self._redaction_config)}, candidates={_safe(calls, self._redaction_config)}")

    def to_not_have_tool_call(self, tool: str | None = None, *, current_snapshot: bool = False, **kwargs: _Any) -> None:
        if not _negative_boundary(self.subject, current_snapshot=current_snapshot):
            self._fail("negative tool assertion requires a terminal/frozen boundary or current_snapshot=True")
            return
        self.to_have_tool_call(tool, count=0, current_snapshot=current_snapshot, **kwargs)

    def to_have_no_tool_call(self, tool: str | None = None, *, current_snapshot: bool = False, **kwargs: _Any) -> None:
        self.to_not_have_tool_call(tool, current_snapshot=current_snapshot, **kwargs)

    def to_have_reported_tool_call(self, tool: str | None = None, **kwargs: _Any) -> None:
        """Explicitly match provider/harness-reported calls without wire evidence."""
        kwargs["evidence"] = "reported"
        self.to_have_tool_call(tool, **kwargs)

    def to_have_duration(
        self,
        expected_ms: float | None = None,
        *,
        min_ms: float | None = None,
        max_ms: float | None = None,
        tolerance_ms: float = 0.0,
    ) -> None:
        actual = _duration_ms(self.subject)
        if expected_ms is not None:
            min_ms = expected_ms - tolerance_ms
            max_ms = expected_ms + tolerance_ms
        matched = actual is not None and (min_ms is None or actual >= min_ms) and (max_ms is None or actual <= max_ms)
        self._require(matched, f"duration assertion failed; expected={_safe(expected_ms, self._redaction_config)}, actual={_safe(actual, self._redaction_config)}")

    def to_have_duration_between(self, min_ms: float, max_ms: float) -> None:
        self.to_have_duration(min_ms=min_ms, max_ms=max_ms)

    def to_have_lifecycle(self, lifecycle: _LifecycleState | _TurnLifecycle | str) -> None:
        snapshot = _snapshot(self.subject)
        self._require(snapshot is not None and snapshot.lifecycle == lifecycle, f"lifecycle mismatch: expected {_safe(lifecycle, self._redaction_config)}, actual={_safe(getattr(snapshot, 'lifecycle', None), self._redaction_config)}")

    def to_have_outcome(self, outcome: _ExecutionOutcome | str) -> None:
        snapshot = _snapshot(self.subject)
        self._require(snapshot is not None and snapshot.outcome == outcome, f"outcome mismatch: expected {_safe(outcome, self._redaction_config)}, actual={_safe(getattr(snapshot, 'outcome', None), self._redaction_config)}")

    def to_be_completed(self) -> None:
        self.to_have_outcome(_ExecutionOutcome.COMPLETED)

    def to_have_terminal_outcome(self, outcome: _ExecutionOutcome | str) -> None:
        self.to_have_outcome(outcome)

    def to_have_error(self, expected: _Any = None) -> None:
        error = getattr(self.subject, "error", None)
        self._require(error is not None, "expected an error but subject has none")
        if expected is not None:
            self._require(_matches(error, expected), f"error mismatch\n{_diff(expected, error, self._redaction_config)}")

    def to_have_protocol_version(self, version: str) -> None:
        actual = getattr(self.subject, "protocol_version", None)
        initialization = getattr(self.subject, "initialization", None)
        actual = actual or getattr(initialization, "protocol_version", None)
        self._require(actual == version, f"protocol version mismatch: expected {_safe(version, self._redaction_config)}, actual={_safe(actual, self._redaction_config)}")

    def to_have_transport(self, transport: str) -> None:
        evidence = getattr(self.subject, "transport_evidence", None)
        self._require(getattr(evidence, "transport", None) == transport, f"transport mismatch: expected {_safe(transport, self._redaction_config)}, actual={_safe(getattr(evidence, 'transport', None), self._redaction_config)}")

    def to_have_capability(self, name: str, *, status: _CapabilityStatus | str | None = None) -> None:
        capabilities = getattr(self.subject, "capabilities", ())
        initialization = getattr(self.subject, "initialization", None)
        capabilities = capabilities or (getattr(initialization, "capabilities", {}) if initialization is not None else {})
        if isinstance(capabilities, _Mapping):
            found, actual_status = name in capabilities, None
        else:
            items = [item for item in capabilities if getattr(item, "name", None) == name]
            found, actual_status = bool(items), getattr(items[0], "status", None) if items else None
        self._require(found and (status is None or actual_status == status), f"capability assertion failed for {_safe(name, self._redaction_config)}")

    def to_have_artifact(self, artifact: _ArtifactRef | str, *, name: str | None = None) -> None:
        artifacts = getattr(self.subject, "artifacts", ())
        expected = str(getattr(artifact, "artifact_id", artifact))
        matched = any((name is None or item.name == name) and (str(getattr(item, "artifact_id", "")) == expected or item.name == expected) for item in artifacts)
        self._require(matched, f"artifact not found: {_safe(artifact, self._redaction_config)}; available={_safe(artifacts, self._redaction_config)}")

    def to_have_workspace_diff(self, expected: _Any = None, *, added: _Any = None, modified: _Any = None, deleted: _Any = None) -> None:
        """Assert the generic workspace projection exposed by a result/handle.

        Workspace capture is intentionally adapter-neutral.  The matcher accepts
        either a complete expected projection or explicit changed-entry fields.
        """
        actual = getattr(self.subject, "workspace_diff", None)
        if actual is None:
            actual = getattr(self.subject, "workspace", None)
        if expected is not None:
            self._require(_matches(actual, expected), f"workspace diff mismatch\n{_diff(expected, actual, self._redaction_config)}")
            return
        for field_name, expected_value in (("added", added), ("modified", modified), ("deleted", deleted)):
            if expected_value is not None:
                actual_value = actual.get(field_name) if isinstance(actual, _Mapping) else getattr(actual, field_name, None)
                self._require(_matches(actual_value, expected_value), f"workspace {field_name} mismatch\n{_diff(expected_value, actual_value, self._redaction_config)}")

    def to_have_trace(self, *, completeness: str | None = None, limitation: str | None = None) -> None:
        trace = _trace(self.subject)
        self._require(trace is not None, "subject has no trace")
        if trace is not None:
            if completeness is not None:
                self._require(trace.completeness == completeness, f"trace completeness mismatch: {_safe(trace.completeness, self._redaction_config)}")
            if limitation is not None:
                self._require(limitation in trace.limitations, f"trace limitation missing: {_safe(limitation, self._redaction_config)}")

    def to_have_event(self, kind: _EventKind | str, *, count: int | None = None) -> None:
        matched = [event for event in _events(self.subject) if event.kind == kind]
        self._require((len(matched) == count if count is not None else bool(matched)), f"trace event assertion failed for {_safe(kind, self._redaction_config)}; count={len(matched)}")

    def to_eventually(self, matcher: _Callable[[_Any], None], *, timeout: float = 5.0) -> None:
        if timeout <= 0:
            raise ValueError("eventual assertion timeout must be positive")
        waiter = getattr(self.subject, "wait_for", None)
        if callable(waiter):
            try:
                waiter(lambda value: _call_matches(matcher, value), timeout=timeout)
            except Exception as error:
                self._fail(f"eventual matcher failed: {_safe(str(error), self._redaction_config)}")
            return
        try:
            matcher(self.subject)
        except AssertionError:
            # Keep static eventual assertions as ordinary assertions, while
            # routing custom matcher messages through the same redaction gate.
            self._fail("eventual matcher failed")
        except Exception as error:
            self._fail(f"eventual matcher raised {_safe(type(error).__name__, self._redaction_config)}")


def _call_matches(matcher: _Callable[[_Any], None], value: _Any) -> bool:
    try:
        matcher(value)
    except Exception:
        return False
    return True


class CheckGroup:
    """Collect assertion failures and report them together on context exit."""

    def __init__(self, redaction_config: _RedactionConfig | None = None) -> None:
        self._failures: list[AssertionError] = []
        self._redaction_config = redaction_config or _RedactionConfig.from_environment()

    def __enter__(self) -> "CheckGroup":
        return self

    def expect(self, subject: _Any) -> Expectation[_Any]:
        return Expectation(subject, self._failures.append, self._redaction_config)

    def __exit__(self, *_: object) -> _Literal[False]:
        if self._failures:
            details = "\n".join(f"{index}. {failure}" for index, failure in enumerate(self._failures, 1))
            raise AssertionError(f"{len(self._failures)} grouped assertion(s) failed:\n{details}")
        return False


def expect(subject: _SubjectT, *, redaction_config: _RedactionConfig | None = None) -> Expectation[_SubjectT]:
    return Expectation(subject, redaction_config=redaction_config)


def check(*, redaction_config: _RedactionConfig | None = None) -> CheckGroup:
    return CheckGroup(redaction_config)


__all__ = ["CheckGroup", "Expectation", "check", "expect"]
