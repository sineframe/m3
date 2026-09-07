"""Pure, framework-neutral assertions over MCP Pal value projections."""

from __future__ import annotations

import difflib as _difflib
import inspect as _inspect
import json as _json
import math as _math
import re as _re
from collections.abc import (
    Callable as _Callable,
)
from collections.abc import (
    Mapping as _Mapping,
)
from collections.abc import (
    Sequence as _Sequence,
)
from numbers import Real as _Real
from typing import (
    Any as _Any,
)
from typing import (
    Generic as _Generic,
)
from typing import (
    Literal as _Literal,
)
from typing import (
    TypeVar as _TypeVar,
)

from .errors import (
    TraceNotFinalized as _TraceNotFinalized,
)
from .errors import (
    TraceUnavailable as _TraceUnavailable,
)
from .observability import (
    ArtifactEntry as _ArtifactEntry,
)
from .observability import (
    InitializationEntry as _InitializationEntry,
)
from .observability import (
    Observation as _Observation,
)
from .observability import (
    ObservationState as _ObservationState,
)
from .observability import (
    ToolCallEntry as _ToolCallEntry,
)
from .observability import (
    ToolResult as _ToolResult,
)
from .observability import (
    TraceView as _TraceView,
)
from .observability import (
    WorkspaceEntry as _WorkspaceEntry,
)
from .trace.redaction import (
    RedactionConfig as _RedactionConfig,
)
from .trace.redaction import (
    redact_for_persistence as _redact_for_persistence,
)
from .types import (
    ArtifactRef as _ArtifactRef,
)
from .types import (
    CapabilityStatus as _CapabilityStatus,
)
from .types import (
    EventKind as _EventKind,
)
from .types import (
    EventOrigin as _EventOrigin,
)
from .types import (
    ExecutionOutcome as _ExecutionOutcome,
)
from .types import (
    ExecutionResult as _ExecutionResult,
)
from .types import (
    ExecutionState as _ExecutionState,
)
from .types import (
    ExecutionStatus as _ExecutionStatus,
)
from .types import (
    TraceResult as _TraceResult,
)
from .types import (
    TurnId as _TurnId,
)
from .types import (
    TurnStatus as _TurnStatus,
)
from .types import (
    TurnResponse as _TurnResponse,
)
from .types import (
    TurnResult as _TurnResult,
)
from .types import (
    TurnState as _TurnState,
)

_TurnSelector = _TurnResult | _TurnState | _TurnId | str

_SubjectT = _TypeVar("_SubjectT")
_FailureSink = _Callable[[AssertionError], None]
_UNAVAILABLE = object()


def _plain(value: _Any) -> _Any:
    if value is _UNAVAILABLE:
        return {"state": "unavailable"}
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


def _normalize_turn_selector(value: _Any) -> str:
    """Normalize the supported public turn selector forms."""
    if isinstance(value, _TurnResult):
        return value.snapshot.turn_id.root
    if isinstance(value, _TurnState):
        return value.turn_id.root
    if isinstance(value, _TurnId):
        return value.root
    if isinstance(value, str):
        return value
    raise TypeError(
        "turn selector must be a TurnResult, TurnState, TurnId, or str"
    )


def _redact(value: _Any, depth: int = 0) -> _Any:
    if depth > 5:
        return "<omitted>"
    if isinstance(value, _Mapping):
        result: dict[str, _Any] = {}
        for key, item in list(value.items())[:64]:
            name = str(key)
            if any(
                token in name.lower()
                for token in ("secret", "token", "password", "authorization", "cookie")
            ):
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
        return _json.dumps(projected, sort_keys=True, default=str, allow_nan=False)[
            :2000
        ]
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
        return subject.structured_content
    if isinstance(subject, _Mapping):
        return subject
    return None


def _observation_value(value: _Any) -> _Any:
    if isinstance(value, _Observation) and value.state is _ObservationState.OBSERVED:
        return value.value
    return _UNAVAILABLE


def _as_view(value: _Any) -> _TraceView:
    if isinstance(value, _TraceView):
        return value
    if isinstance(value, _TraceResult):
        return value.view()
    if isinstance(value, _ExecutionResult):
        return value.trace_view
    trace = getattr(value, "trace", None)
    if isinstance(trace, (_TraceView, _TraceResult)):
        return _as_view(trace)
    final_trace = getattr(value, "final_trace", None)
    if isinstance(final_trace, (_TraceView, _TraceResult)):
        return _as_view(final_trace)
    result = getattr(value, "result", None)
    if isinstance(result, (_TraceView, _TraceResult, _ExecutionResult)):
        return _as_view(result)
    raise _TraceUnavailable("subject does not expose a finalized trace")


def _is_trace_like(subject: _Any) -> bool:
    if isinstance(subject, (_TraceView, _TraceResult, _ExecutionResult)):
        return True
    if _has_direct_trace_attribute(subject):
        return True
    try:
        result = _inspect.getattr_static(subject, "result")
    except AttributeError:
        result = None
    if isinstance(result, (_TraceView, _TraceResult, _ExecutionResult)):
        return True
    try:
        turns = _inspect.getattr_static(subject, "turns")
    except AttributeError:
        return False
    return isinstance(turns, _Sequence) and not isinstance(turns, (str, bytes))


def _has_direct_trace_attribute(subject: _Any) -> bool:
    for name in ("trace", "final_trace"):
        try:
            _inspect.getattr_static(subject, name)
        except AttributeError:
            continue
        return True
    return False


def _views(subject: _Any) -> tuple[_TraceView, ...]:
    if isinstance(subject, _ExecutionResult) and subject.trace is None:
        if subject.turns:
            return tuple(_as_view(turn) for turn in subject.turns)
        raise _TraceUnavailable("execution has no trace evidence")
    try:
        return (_as_view(subject),)
    except (_TraceNotFinalized, _TraceUnavailable):
        if _has_direct_trace_attribute(subject):
            raise
        turns = getattr(subject, "turns", None)
        if isinstance(turns, _Sequence) and not isinstance(turns, (str, bytes)):
            views = tuple(_as_view(turn) for turn in turns)
            if views:
                return views
        if _is_trace_like(subject):
            raise
        return ()


def _tool_result_projection(result: _ToolResult) -> dict[str, _Any]:
    projection: dict[str, _Any] = {
        "content": tuple(_plain(block) for block in result.content),
    }
    if result.structured_content.state is _ObservationState.OBSERVED:
        projection["structured_content"] = _observation_value(result.structured_content)
    if result.is_error:
        projection["is_error"] = True
    if result.error.state is _ObservationState.OBSERVED:
        projection["error"] = _observation_value(result.error)
    return projection


def _result_projection(observation: _Observation[_Any]) -> _Any:
    value = _observation_value(observation)
    if value is _UNAVAILABLE:
        return _UNAVAILABLE
    return _tool_result_projection(value) if isinstance(value, _ToolResult) else value


def _reported_result_projection(value: _Any) -> _Any:
    """Project reported JSON result data into the wire-result shape."""
    if not isinstance(value, _Mapping):
        return value
    if not any(
        key in value
        for key in (
            "content",
            "structured_content",
            "structuredContent",
            "is_error",
            "isError",
            "error",
        )
    ):
        return value
    content = value.get("content", ())
    if not isinstance(content, _Sequence) or isinstance(content, (str, bytes)):
        content = ()
    normalized_content = []
    for block in content:
        if isinstance(block, _Mapping) and "type" in block and "kind" not in block:
            block = dict(block)
            block["kind"] = block.pop("type")
        normalized_content.append(_plain(block))
    projection: dict[str, _Any] = {"content": tuple(normalized_content)}
    if "structured_content" in value or "structuredContent" in value:
        projection["structured_content"] = value.get(
            "structured_content", value.get("structuredContent")
        )
    if "is_error" in value or "isError" in value:
        projection["is_error"] = value.get("is_error", value.get("isError"))
    if "error" in value:
        projection["error"] = value["error"]
    return projection


def _result_expected_projection(value: _Any) -> _Any:
    """Accept public MCP spelling aliases in mapping expectations."""
    if isinstance(value, _ToolResult):
        return _tool_result_projection(value)
    if not isinstance(value, _Mapping):
        return value
    result = dict(value)
    if "structuredContent" in result and "structured_content" not in result:
        result["structured_content"] = result.pop("structuredContent")
    if "isError" in result and "is_error" not in result:
        result["is_error"] = result.pop("isError")
    if "content" in result and isinstance(result["content"], _Sequence):
        content = []
        for block in result["content"]:
            if isinstance(block, _Mapping) and "kind" not in block:
                block = dict(block)
                if "type" in block:
                    block["kind"] = block.pop("type")
                elif "text" in block:
                    block["kind"] = "text"
            content.append(block)
        result["content"] = content
    return result


def _source_projection(entry: _ToolCallEntry, evidence: str) -> dict[str, _Any] | None:
    if evidence == "wire":
        source = _observation_value(entry.wire)
        if source is _UNAVAILABLE:
            return None
        assert source is not None
        return {
            "server": _observation_value(source.server),
            "tool": _observation_value(source.tool),
            "arguments": _observation_value(source.arguments),
            "result": _result_projection(source.result),
            "status": entry.tool_status.value,
            "latency_ms": _observation_value(source.latency_ms),
            "turn": entry.turn_id,
            "entry": entry,
            "evidence": evidence,
        }
    source = _observation_value(entry.reported)
    if source is _UNAVAILABLE:
        return None
    assert source is not None
    status = _observation_value(source.status)
    result = _observation_value(source.result)
    return {
        "server": _observation_value(source.server),
        "tool": _observation_value(source.tool),
        "arguments": _observation_value(source.arguments),
        "result": _reported_result_projection(result),
        "status": getattr(status, "value", status),
        "latency_ms": _UNAVAILABLE,
        "turn": entry.turn_id,
        "entry": entry,
        "evidence": evidence,
    }


def _resolved_projection(entry: _ToolCallEntry) -> dict[str, _Any]:
    return {
        "server": _observation_value(entry.server),
        "tool": _observation_value(entry.tool),
        "arguments": _observation_value(entry.arguments),
        "result": _result_projection(entry.result),
        "status": entry.tool_status.value,
        "latency_ms": _observation_value(entry.server_latency_ms),
        "turn": entry.turn_id,
        "entry": entry,
        "evidence": "any",
    }


def _entry_matches_evidence(entry: _Any, evidence: str) -> bool:
    """Keep discovery evidence isolated from the requested call source."""
    if evidence == "any":
        return True
    origins = {getattr(item.origin, "value", item.origin) for item in entry.provenance}
    expected = (
        _EventOrigin.WIRE_OBSERVED.value
        if evidence == "wire"
        else _EventOrigin.HARNESS_REPORTED.value
    )
    return expected in origins


def _matches(
    actual: _Any,
    expected: _Any,
    *,
    partial: bool = False,
    unordered: bool = False,
    regex: bool = False,
    numeric_tolerance: float | None = None,
) -> bool:
    if (
        numeric_tolerance is not None
        and isinstance(actual, _Real)
        and isinstance(expected, _Real)
        and not isinstance(actual, bool)
        and not isinstance(expected, bool)
    ):
        return abs(float(actual) - float(expected)) <= numeric_tolerance
    if regex and isinstance(actual, str) and isinstance(expected, str):
        return _re.fullmatch(expected, actual) is not None
    if isinstance(expected, _Mapping) and isinstance(actual, _Mapping):
        if not partial and set(actual) != set(expected):
            return False
        return all(
            key in actual
            and _matches(
                actual[key],
                value,
                partial=partial,
                unordered=unordered,
                regex=regex,
                numeric_tolerance=numeric_tolerance,
            )
            for key, value in expected.items()
        )
    if (
        isinstance(actual, _Sequence)
        and isinstance(expected, _Sequence)
        and not isinstance(actual, (str, bytes))
        and not isinstance(expected, (str, bytes))
    ):
        if unordered:
            remaining = list(actual)
            for expected_value in expected:
                match_index = next(
                    (
                        index
                        for index, actual_value in enumerate(remaining)
                        if _matches(
                            actual_value,
                            expected_value,
                            partial=partial,
                            unordered=unordered,
                            regex=regex,
                            numeric_tolerance=numeric_tolerance,
                        )
                    ),
                    None,
                )
                if match_index is None:
                    return False
                remaining.pop(match_index)
            return not remaining
        return len(actual) == len(expected) and all(
            _matches(
                actual_value,
                expected_value,
                partial=partial,
                unordered=unordered,
                regex=regex,
                numeric_tolerance=numeric_tolerance,
            )
            for actual_value, expected_value in zip(actual, expected)
        )
    return bool(_plain(actual) == _plain(expected))


def _snapshot(subject: _Any) -> _Any:
    if isinstance(subject, (_ExecutionState, _TurnState)):
        return subject
    return getattr(subject, "snapshot", None)


def _duration_ms(subject: _Any) -> float | None:
    try:
        views = _views(subject)
    except (_TraceNotFinalized, _TraceUnavailable):
        views = ()
    if views:
        return sum(view.summary.timing.duration_ms for view in views)
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
    return None


def _is_terminal_subject(subject: _Any) -> bool:
    if isinstance(subject, _TraceView):
        return True
    if isinstance(subject, (_TraceResult, _ExecutionResult, _TurnResult)):
        if isinstance(subject, _TraceResult):
            try:
                subject.view()
                return True
            except (_TraceNotFinalized, _TraceUnavailable):
                return False
        if isinstance(subject, _ExecutionResult) and subject.trace is not None:
            try:
                subject.trace.view()
                return True
            except (_TraceNotFinalized, _TraceUnavailable):
                return False
        snapshot = _snapshot(subject)
        return (
            getattr(
                getattr(snapshot, "lifecycle", None),
                "value",
                getattr(snapshot, "lifecycle", None),
            )
            == "finished"
        )
    if _is_trace_like(subject):
        try:
            return bool(_views(subject))
        except (_TraceNotFinalized, _TraceUnavailable):
            return False
    snapshot = _snapshot(subject)
    if snapshot is not None:
        lifecycle = getattr(snapshot, "lifecycle", None)
        if getattr(lifecycle, "value", lifecycle) == "finished":
            return True
    return bool(
        getattr(subject, "is_terminal", False) or getattr(subject, "terminal", False)
    )


def _negative_boundary(subject: _Any, *, current_snapshot: bool) -> bool:
    if current_snapshot or _is_terminal_subject(subject):
        return True
    # Value projections without a live waiter are immutable/frozen by
    # construction. Handles expose wait_for and must opt into a snapshot.
    return not callable(getattr(subject, "wait_for", None))


class Expectation(_Generic[_SubjectT]):
    """One-shot assertion facade; matchers never persist evaluations."""

    def __init__(
        self,
        subject: _SubjectT,
        sink: _FailureSink | None = None,
        redaction_config: _RedactionConfig | None = None,
    ) -> None:
        self.subject = subject
        self._sink = sink
        self._redaction_config = redaction_config or _RedactionConfig.from_environment()

    def _fail(self, message: str) -> None:
        try:
            view = _as_view(self.subject)
        except (_TraceNotFinalized, _TraceUnavailable):
            view = None
        if view is not None:
            message = f"{message}; trace_id={_safe(view.trace_id, self._redaction_config)}, execution_id={_safe(view.execution_id, self._redaction_config)}"
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

    def _typed_views(self) -> tuple[_TraceView, ...]:
        try:
            return _views(self.subject)
        except (_TraceNotFinalized, _TraceUnavailable) as error:
            self._fail(
                "finalized trace unavailable; "
                f"reason={_safe(type(error).__name__, self._redaction_config)}"
            )
            return ()

    def to_have_text(self, expected: str) -> None:
        actual = _text(self.subject)
        self._require(
            actual == expected,
            f"text mismatch\n{_diff(expected, actual, self._redaction_config)}",
        )

    def to_have_text_containing(self, expected: str) -> None:
        actual = _text(self.subject)
        self._require(
            expected in actual,
            f"text does not contain {_safe(expected, self._redaction_config)}; actual={_safe(actual, self._redaction_config)}",
        )

    def to_have_text_matching(self, pattern: str, *, flags: int = 0) -> None:
        actual = _text(self.subject)
        self._require(
            _re.search(pattern, actual, flags) is not None,
            f"text does not match /{_safe(pattern, self._redaction_config)}/; actual={_safe(actual, self._redaction_config)}",
        )

    def to_have_text_matching_regex(self, pattern: str, *, flags: int = 0) -> None:
        self.to_have_text_matching(pattern, flags=flags)

    def to_not_have_text(
        self, unexpected: str, *, current_snapshot: bool = False
    ) -> None:
        if not _negative_boundary(self.subject, current_snapshot=current_snapshot):
            self._fail(
                "negative text assertion requires a terminal/frozen boundary or current_snapshot=True"
            )
            return
        self._require(
            unexpected not in _text(self.subject),
            f"text unexpectedly contained {_safe(unexpected, self._redaction_config)}",
        )

    def to_not_have_text_containing(
        self, unexpected: str, *, current_snapshot: bool = False
    ) -> None:
        self.to_not_have_text(unexpected, current_snapshot=current_snapshot)

    def to_have_structured_content(self, expected: object) -> None:
        actual = _structured(self.subject)
        self._require(
            _matches(actual, expected),
            f"structured content mismatch\n{_diff(expected, actual, self._redaction_config)}",
        )

    def to_have_content(self, expected: _Any, *, ordered: bool = True) -> None:
        actual = getattr(self.subject, "content", None)
        if (
            actual is None
            and isinstance(self.subject, _TurnResult)
            and self.subject.response is not None
        ):
            actual = self.subject.response.content
        expected_values = (
            list(expected) if isinstance(expected, (list, tuple)) else [expected]
        )
        actual_values = list(actual or ())
        if ordered:
            matched = len(actual_values) == len(expected_values) and all(
                _matches(a, e) for a, e in zip(actual_values, expected_values)
            )
        else:
            remaining = list(actual_values)
            matched = len(remaining) == len(expected_values)
            if matched:
                for expected_value in expected_values:
                    match_index = next(
                        (
                            index
                            for index, actual_value in enumerate(remaining)
                            if _matches(actual_value, expected_value)
                        ),
                        None,
                    )
                    if match_index is None:
                        matched = False
                        break
                    remaining.pop(match_index)
        self._require(
            matched,
            f"content blocks mismatch\n{_diff(expected_values, actual_values, self._redaction_config)}",
        )

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
        result_partial: bool = False,
        arguments_observed_null: bool = False,
        result_observed_null: bool = False,
        status: str | None = None,
        count: int | None = None,
        min_count: int | None = None,
        max_count: int | None = None,
        turn: _TurnSelector | None = None,
        predicate: _Callable[[_Mapping[str, _Any]], bool] | None = None,
        server_name: str | None = None,
        evidence: _Literal["wire", "reported", "any"] = "wire",
        min_latency_ms: float | None = None,
        max_latency_ms: float | None = None,
        current_snapshot: bool = False,
    ) -> None:
        if count is not None and (
            isinstance(count, bool) or not isinstance(count, int) or count < 0
        ):
            raise ValueError("tool-call count must be a non-negative integer")
        if min_count is not None and (
            isinstance(min_count, bool)
            or not isinstance(min_count, int)
            or min_count < 0
        ):
            raise ValueError("tool-call min_count must be a non-negative integer")
        if max_count is not None and (
            isinstance(max_count, bool)
            or not isinstance(max_count, int)
            or max_count < 0
        ):
            raise ValueError("tool-call max_count must be a non-negative integer")
        if count is not None and (min_count is not None or max_count is not None):
            raise ValueError("count cannot be combined with min_count or max_count")
        if min_count is not None and max_count is not None and min_count > max_count:
            raise ValueError("min_count cannot exceed max_count")
        for name_value, value in (
            ("arguments_tolerance", arguments_tolerance),
            ("min_latency_ms", min_latency_ms),
            ("max_latency_ms", max_latency_ms),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, _Real)
                or not _math.isfinite(float(value))
                or value < 0
            ):
                raise ValueError(f"{name_value} must be a finite non-negative number")
        if (
            min_latency_ms is not None
            and max_latency_ms is not None
            and min_latency_ms > max_latency_ms
        ):
            raise ValueError("min_latency_ms cannot exceed max_latency_ms")
        if arguments_observed_null and arguments is not None:
            raise ValueError(
                "arguments_observed_null cannot be combined with arguments"
            )
        if result_observed_null and result is not None:
            raise ValueError("result_observed_null cannot be combined with result")
        if (count == 0 or max_count == 0) and not _negative_boundary(
            self.subject, current_snapshot=current_snapshot
        ):
            self._fail(
                "negative tool assertion requires a terminal/frozen boundary or current_snapshot=True"
            )
            return
        if evidence not in {"wire", "reported", "any"}:
            raise ValueError("tool-call evidence must be 'wire', 'reported', or 'any'")
        requested_turn = (
            _normalize_turn_selector(turn) if turn is not None else None
        )
        requested_tool = name or tool
        server = server or server_name
        views = self._typed_views()
        entries = [entry for view in views for entry in view.tool_calls]
        calls: list[dict[str, _Any]] = []
        if evidence == "any":
            calls = [_resolved_projection(entry) for entry in entries]
        else:
            for entry in entries:
                projection = _source_projection(entry, evidence)
                if projection is not None:
                    calls.append(projection)
        servers = {
            str(call["server"])
            for call in calls
            if call["tool"] is not _UNAVAILABLE
            and call["tool"] == requested_tool
            and call["server"] is not _UNAVAILABLE
        }
        discovery_evidence = "any" if evidence == "any" else evidence
        for view in views:
            for timeline_entry in view.timeline:
                if (
                    not isinstance(timeline_entry, _InitializationEntry)
                    or timeline_entry.server_binding is None
                    or not _entry_matches_evidence(timeline_entry, discovery_evidence)
                ):
                    continue
                tools = _observation_value(timeline_entry.tools)
                if tools is _UNAVAILABLE:
                    continue
                if any(getattr(item, "name", None) == requested_tool for item in tools):
                    servers.add(timeline_entry.server_binding)
        if requested_tool is not None and server is None and len(servers) > 1:
            self._fail(
                f"ambiguous serverless tool assertion for {_safe(requested_tool, self._redaction_config)}; candidates={_safe(sorted(servers), self._redaction_config)}"
            )
            return
        matches: list[dict[str, _Any]] = []
        for call in calls:
            if requested_tool is not None and call["tool"] != requested_tool:
                continue
            if server is not None and call["server"] != server:
                continue
            arguments_requested = arguments is not None or arguments_observed_null
            if arguments_requested and (
                call["arguments"] is _UNAVAILABLE
                or (arguments_observed_null and call["arguments"] is not None)
                or (
                    not arguments_observed_null
                    and not _matches(
                        call["arguments"],
                        arguments,
                        partial=arguments_partial,
                        unordered=arguments_unordered,
                        regex=arguments_regex,
                        numeric_tolerance=arguments_tolerance,
                    )
                )
            ):
                continue
            if argument_predicate is not None:
                if call["arguments"] is _UNAVAILABLE:
                    continue
                try:
                    predicate_match = bool(argument_predicate(call["arguments"]))
                except Exception as error:
                    self._fail(
                        f"tool argument predicate raised {_safe(type(error).__name__, self._redaction_config)}"
                    )
                    return
                if not predicate_match:
                    continue
            result_requested = result is not None or result_observed_null
            if result_requested and (
                call["result"] is _UNAVAILABLE
                or (result_observed_null and call["result"] is not None)
                or (
                    not result_observed_null
                    and not _matches(
                        call["result"],
                        _result_expected_projection(result),
                        partial=result_partial,
                    )
                )
            ):
                continue
            expected_status = getattr(status, "value", status)
            if status is not None and call["status"] != expected_status:
                continue
            if min_latency_ms is not None and (
                call["latency_ms"] is _UNAVAILABLE
                or call["latency_ms"] < min_latency_ms
            ):
                continue
            if max_latency_ms is not None and (
                call["latency_ms"] is _UNAVAILABLE
                or call["latency_ms"] > max_latency_ms
            ):
                continue
            if requested_turn is not None:
                actual_turn = call["turn"]
                if (
                    actual_turn is _UNAVAILABLE
                    or actual_turn is None
                    or _normalize_turn_selector(actual_turn) != requested_turn
                ):
                    continue
            if predicate is not None:
                try:
                    predicate_match = bool(predicate(call))
                except Exception as error:
                    self._fail(
                        f"tool-call predicate raised {_safe(type(error).__name__, self._redaction_config)}"
                    )
                    return
                if not predicate_match:
                    continue
            matches.append(call)
        if count is not None:
            ok = len(matches) == count
        elif min_count is not None or max_count is not None:
            ok = (min_count is None or len(matches) >= min_count) and (
                max_count is None or len(matches) <= max_count
            )
        else:
            ok = bool(matches)
        self._require(
            ok,
            f"tool call assertion failed; requested={_safe(requested_tool, self._redaction_config)}, server={_safe(server, self._redaction_config)}, candidates={_safe(calls, self._redaction_config)}",
        )

    def to_not_have_tool_call(
        self, tool: str | None = None, *, current_snapshot: bool = False, **kwargs: _Any
    ) -> None:
        if not _negative_boundary(self.subject, current_snapshot=current_snapshot):
            self._fail(
                "negative tool assertion requires a terminal/frozen boundary or current_snapshot=True"
            )
            return
        if not any(key in kwargs for key in ("count", "min_count", "max_count")):
            kwargs["count"] = 0
        self.to_have_tool_call(tool, current_snapshot=current_snapshot, **kwargs)

    def to_have_no_tool_call(
        self, tool: str | None = None, *, current_snapshot: bool = False, **kwargs: _Any
    ) -> None:
        self.to_not_have_tool_call(tool, current_snapshot=current_snapshot, **kwargs)

    def to_have_reported_tool_call(
        self, tool: str | None = None, **kwargs: _Any
    ) -> None:
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
        actual: float | None
        if _is_trace_like(self.subject):
            views = self._typed_views()
            if not views:
                return
            actual = sum(view.summary.timing.duration_ms for view in views)
        else:
            actual = _duration_ms(self.subject)
        if expected_ms is not None:
            min_ms = expected_ms - tolerance_ms
            max_ms = expected_ms + tolerance_ms
        matched = (
            actual is not None
            and (min_ms is None or actual >= min_ms)
            and (max_ms is None or actual <= max_ms)
        )
        self._require(
            matched,
            f"duration assertion failed; expected={_safe(expected_ms, self._redaction_config)}, actual={_safe(actual, self._redaction_config)}",
        )

    def to_have_duration_between(self, min_ms: float, max_ms: float) -> None:
        self.to_have_duration(min_ms=min_ms, max_ms=max_ms)

    def to_have_lifecycle(
        self, lifecycle: _ExecutionStatus | _TurnStatus | str
    ) -> None:
        snapshot = _snapshot(self.subject)
        self._require(
            snapshot is not None and snapshot.lifecycle == lifecycle,
            f"lifecycle mismatch: expected {_safe(lifecycle, self._redaction_config)}, actual={_safe(getattr(snapshot, 'lifecycle', None), self._redaction_config)}",
        )

    def to_have_outcome(self, outcome: _ExecutionOutcome | str) -> None:
        views = self._typed_views()
        if views:
            expected = getattr(outcome, "value", outcome)
            self._require(
                all(view.outcome.value == expected for view in views),
                f"outcome mismatch: expected {_safe(outcome, self._redaction_config)}, actual={_safe([view.outcome for view in views], self._redaction_config)}",
            )
            return
        snapshot = _snapshot(self.subject)
        self._require(
            snapshot is not None and snapshot.outcome == outcome,
            f"outcome mismatch: expected {_safe(outcome, self._redaction_config)}, actual={_safe(getattr(snapshot, 'outcome', None), self._redaction_config)}",
        )

    def to_be_completed(self) -> None:
        self.to_have_outcome(_ExecutionOutcome.COMPLETED)

    def to_have_terminal_outcome(self, outcome: _ExecutionOutcome | str) -> None:
        self.to_have_outcome(outcome)

    def to_have_error(self, expected: _Any = None) -> None:
        error = getattr(self.subject, "error", None)
        self._require(error is not None, "expected an error but subject has none")
        if expected is not None:
            self._require(
                _matches(error, expected),
                f"error mismatch\n{_diff(expected, error, self._redaction_config)}",
            )

    def to_have_protocol_version(self, version: str) -> None:
        views = self._typed_views()
        if views:
            values = []
            for view in views:
                initialization = getattr(view.runtime, "initialization", None)
                if initialization is not None and initialization.value is not None:
                    value = _observation_value(initialization.value.protocol_version)
                    if value is not _UNAVAILABLE:
                        values.append(value)
            self._require(
                version in values,
                f"protocol version mismatch: expected {_safe(version, self._redaction_config)}, actual={_safe(values, self._redaction_config)}",
            )
            return
        actual = getattr(self.subject, "protocol_version", None)
        initialization = getattr(self.subject, "initialization", None)
        actual = actual or getattr(initialization, "protocol_version", None)
        self._require(
            actual == version,
            f"protocol version mismatch: expected {_safe(version, self._redaction_config)}, actual={_safe(actual, self._redaction_config)}",
        )

    def to_have_transport(self, transport: str) -> None:
        views = self._typed_views()
        if views:
            values = [
                _observation_value(getattr(view.runtime, "transport", None))
                for view in views
            ]
            self._require(
                transport in values,
                f"transport mismatch: expected {_safe(transport, self._redaction_config)}, actual={_safe(values, self._redaction_config)}",
            )
            return
        evidence = getattr(self.subject, "transport_evidence", None)
        self._require(
            getattr(evidence, "transport", None) == transport,
            f"transport mismatch: expected {_safe(transport, self._redaction_config)}, actual={_safe(getattr(evidence, 'transport', None), self._redaction_config)}",
        )

    def to_have_capability(
        self, name: str, *, status: _CapabilityStatus | str | None = None
    ) -> None:
        views = self._typed_views()
        if views:
            matched = False
            for view in views:
                initialization = getattr(view.runtime, "initialization", None)
                if initialization is None or initialization.value is None:
                    continue
                capabilities = _observation_value(initialization.value.capabilities)
                if isinstance(capabilities, _Mapping) and name in capabilities:
                    if status is None:
                        matched = True
                    else:
                        capability = capabilities[name]
                        actual_status = (
                            capability.get("status")
                            if isinstance(capability, _Mapping)
                            else capability
                        )
                        matched = actual_status == getattr(status, "value", status)
            self._require(
                matched,
                f"capability assertion failed for {_safe(name, self._redaction_config)}",
            )
            return
        capabilities = getattr(self.subject, "capabilities", ())
        initialization = getattr(self.subject, "initialization", None)
        capabilities = capabilities or (
            getattr(initialization, "capabilities", {})
            if initialization is not None
            else {}
        )
        if isinstance(capabilities, _Mapping):
            found, actual_status = name in capabilities, None
        else:
            items = [
                item for item in capabilities if getattr(item, "name", None) == name
            ]
            found, actual_status = (
                bool(items),
                getattr(items[0], "status", None) if items else None,
            )
        self._require(
            found and (status is None or actual_status == status),
            f"capability assertion failed for {_safe(name, self._redaction_config)}",
        )

    def to_have_artifact(
        self, artifact: _ArtifactRef | str, *, name: str | None = None
    ) -> None:
        views = self._typed_views()
        if views:
            expected = str(getattr(artifact, "artifact_id", artifact))
            artifacts = [
                item.artifact
                for view in views
                for item in view.timeline
                if isinstance(item, _ArtifactEntry)
            ]
            matched = any(
                (name is None or item.name == name)
                and (str(item.artifact_id) == expected or item.name == expected)
                for item in artifacts
            )
            self._require(
                matched,
                f"artifact not found: {_safe(artifact, self._redaction_config)}; available={_safe(artifacts, self._redaction_config)}",
            )
            return
        artifacts_value: _Any = getattr(self.subject, "artifacts", ())
        expected = str(getattr(artifact, "artifact_id", artifact))
        matched = any(
            (name is None or item.name == name)
            and (
                str(getattr(item, "artifact_id", "")) == expected
                or item.name == expected
            )
            for item in artifacts_value
        )
        self._require(
            matched,
            f"artifact not found: {_safe(artifact, self._redaction_config)}; available={_safe(artifacts_value, self._redaction_config)}",
        )

    def to_have_workspace_diff(
        self,
        expected: _Any = None,
        *,
        added: _Any = None,
        modified: _Any = None,
        deleted: _Any = None,
    ) -> None:
        """Assert the generic workspace projection exposed by a result/handle.

        Workspace capture is intentionally adapter-neutral.  The matcher accepts
        either a complete expected projection or explicit changed-entry fields.
        """
        views = self._typed_views()
        if views:
            changes = [
                _observation_value(item.change)
                for view in views
                for item in view.timeline
                if isinstance(item, _WorkspaceEntry)
            ]
            actual = changes[-1] if changes else _UNAVAILABLE
        else:
            actual = getattr(self.subject, "workspace_diff", None)
            if actual is None:
                actual = getattr(self.subject, "workspace", None)
        if expected is not None:
            self._require(
                _matches(actual, expected),
                f"workspace diff mismatch\n{_diff(expected, actual, self._redaction_config)}",
            )
            return
        for field_name, expected_value in (
            ("added", added),
            ("modified", modified),
            ("deleted", deleted),
        ):
            if expected_value is not None:
                actual_value = (
                    actual.get(field_name)
                    if isinstance(actual, _Mapping)
                    else getattr(actual, field_name, None)
                )
                self._require(
                    _matches(actual_value, expected_value),
                    f"workspace {field_name} mismatch\n{_diff(expected_value, actual_value, self._redaction_config)}",
                )

    def to_have_trace(
        self, *, completeness: str | None = None, limitation: str | None = None
    ) -> None:
        views = self._typed_views()
        self._require(bool(views), "subject has no finalized trace")
        for view in views:
            if completeness is not None:
                self._require(
                    view.completeness == completeness,
                    f"trace completeness mismatch: {_safe(view.completeness, self._redaction_config)}",
                )
            if limitation is not None:
                self._require(
                    limitation in view.limitations,
                    f"trace limitation missing: {_safe(limitation, self._redaction_config)}",
                )

    def to_have_event(
        self, kind: _EventKind | str, *, count: int | None = None
    ) -> None:
        if isinstance(kind, _EventKind):
            self._fail(
                "stable EventKind assertions are unavailable from finalized "
                "typed traces; use a public typed entry kind instead"
            )
            return
        if isinstance(kind, str):
            try:
                _EventKind(kind)
            except ValueError:
                pass
            else:
                self._fail(
                    "stable EventKind assertions are unavailable from finalized "
                    "typed traces; use a public typed entry kind instead"
                )
                return
        public_kind = kind
        views = self._typed_views()
        matched = [
            entry
            for view in views
            for entry in view.timeline
            if entry.kind == public_kind
        ]
        self._require(
            (len(matched) == count if count is not None else bool(matched)),
            f"trace event assertion failed for {_safe(kind, self._redaction_config)}; count={len(matched)}",
        )

    def to_eventually(
        self, matcher: _Callable[[_Any], None], *, timeout: float = 5.0
    ) -> None:
        if timeout <= 0:
            raise ValueError("eventual assertion timeout must be positive")
        waiter = getattr(self.subject, "wait_for", None)
        if callable(waiter):
            try:
                waiter(lambda value: _call_matches(matcher, value), timeout=timeout)
            except Exception as error:
                self._fail(
                    f"eventual matcher failed: {_safe(str(error), self._redaction_config)}"
                )
            return
        try:
            matcher(self.subject)
        except AssertionError:
            # Keep static eventual assertions as ordinary assertions, while
            # routing custom matcher messages through the same redaction gate.
            self._fail("eventual matcher failed")
        except Exception as error:
            self._fail(
                f"eventual matcher raised {_safe(type(error).__name__, self._redaction_config)}"
            )


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

    def __enter__(self) -> CheckGroup:
        return self

    def expect(self, subject: _Any) -> Expectation[_Any]:
        return Expectation(subject, self._failures.append, self._redaction_config)

    def __exit__(self, *_: object) -> _Literal[False]:
        if self._failures:
            details = "\n".join(
                f"{index}. {failure}" for index, failure in enumerate(self._failures, 1)
            )
            raise AssertionError(
                f"{len(self._failures)} grouped assertion(s) failed:\n{details}"
            )
        return False


def expect(
    subject: _SubjectT, *, redaction_config: _RedactionConfig | None = None
) -> Expectation[_SubjectT]:
    return Expectation(subject, redaction_config=redaction_config)


def check(*, redaction_config: _RedactionConfig | None = None) -> CheckGroup:
    return CheckGroup(redaction_config)


__all__ = ["CheckGroup", "Expectation", "check", "expect"]
