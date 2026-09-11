"""Private execution-bound recording for MCP Pal matcher checks."""

from __future__ import annotations

import json
import math
import weakref
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock
from typing import Any
from uuid import uuid4

from .trace.redaction import RedactionConfig, redact_for_persistence
from .types import EvaluationContext, EvaluationId, EvaluationResult, EvaluationStatus

_DEFAULT = ContextVar("mcp_pal_record_checks_default", default=False)
_SUPPRESSED = ContextVar("mcp_pal_record_checks_suppressed", default=0)


@dataclass(frozen=True)
class DefaultRecordChecksToken:
    """Token returned by the private pytest integration switch."""

    token: Any


@dataclass
class _Binding:
    execution_id: str
    store_ref: weakref.ReferenceType[Any]
    redaction_config: RedactionConfig | None

    @property
    def store(self) -> Any | None:
        return self.store_ref()


@dataclass
class _ResolvedBinding:
    """A binding whose store is kept alive by the caller's subject.

    ID-only bindings use ``_Binding`` and never retain a store. Subject
    bindings are deliberately resolved into this short-lived value so that a
    result/trace can keep its store alive for exactly as long as that subject.
    """

    execution_id: str
    store: Any
    redaction_config: RedactionConfig | None


@dataclass
class _SubjectBinding:
    subject_ref: weakref.ReferenceType[Any]
    execution_id: str
    store: Any
    redaction_config: RedactionConfig | None


_LOCK = RLock()
_BINDINGS: dict[str, list[_Binding]] = {}
_SUBJECT_BINDINGS: dict[int, _SubjectBinding] = {}


def record_checks_enabled(explicit: bool = False) -> bool:
    return bool(explicit or _DEFAULT.get()) and _SUPPRESSED.get() == 0


def has_recording_binding(subject: Any) -> bool:
    try:
        execution_id = _subject_execution_id(subject)
        return execution_id is not None and (
            _subject_binding(subject, execution_id) is not None
            or _binding_for(execution_id) is not None
        )
    except Exception:
        return False


def set_default_record_checks(enabled: bool) -> DefaultRecordChecksToken:
    return DefaultRecordChecksToken(_DEFAULT.set(bool(enabled)))


def restore_default_record_checks(token: DefaultRecordChecksToken) -> None:
    _DEFAULT.reset(token.token)


@contextmanager
def suppress_recording() -> Iterator[None]:
    token = _SUPPRESSED.set(_SUPPRESSED.get() + 1)
    try:
        yield
    finally:
        _SUPPRESSED.reset(token)


def bind_execution(
    execution_id: Any,
    store: Any,
    redaction_config: RedactionConfig | None = None,
) -> bool:
    """Bind an ID to a weakly-held store for returned values.

    Stores must support weak references for this fallback. A non-weakrefable
    store can still be retained by an exact subject binding, but must not be
    retained by this process-wide ID registry.
    """

    if execution_id is None or store is None:
        return False
    try:
        key = _identifier(execution_id)

        def on_store_collected(
            reference: weakref.ReferenceType[Any], binding_key: str = key
        ) -> None:
            _drop_store_binding(binding_key, reference)

        store_ref = weakref.ref(
            store,
            on_store_collected,
        )
        with _LOCK:
            values = _BINDINGS.setdefault(key, [])
            values[:] = [
                item
                for item in values
                if item.store_ref() is not None and item.store is not store
            ]
            values.append(_Binding(key, store_ref, redaction_config))
        return True
    except Exception:
        return False


def bind_subject(
    subject: Any,
    execution_id: Any,
    store: Any,
    redaction_config: RedactionConfig | None = None,
) -> bool:
    """Attach a store to a returned result/trace for that object lifetime."""

    if subject is None or execution_id is None or store is None:
        return False
    try:
        key = id(subject)

        def on_subject_collected(
            subject_ref: weakref.ReferenceType[Any], subject_key: int = key
        ) -> None:
            _drop_subject(subject_key, subject_ref)

        reference = weakref.ref(
            subject,
            on_subject_collected,
        )
        with _LOCK:
            _SUBJECT_BINDINGS[key] = _SubjectBinding(
                reference, _identifier(execution_id), store, redaction_config
            )
        return True
    except Exception:
        return False


def unbind_execution(execution_id: Any, store: Any | None = None) -> None:
    try:
        key = _identifier(execution_id)
        with _LOCK:
            values = _BINDINGS.get(key, [])
            if store is None:
                _BINDINGS.pop(key, None)
            else:
                values[:] = [item for item in values if item.store is not store]
                if values:
                    _BINDINGS[key] = values
                else:
                    _BINDINGS.pop(key, None)
    except Exception:
        return


def record_matcher(
    subject: Any,
    matcher: str,
    *,
    arguments: dict[str, Any],
    identity: Mapping[str, Any] | None = None,
    status: str,
    message: str | None = None,
    redaction_config: RedactionConfig | None = None,
) -> bool:
    """Persist a matcher result, without changing assertion behavior."""

    try:
        execution_id = _subject_execution_id(subject)
        if execution_id is None:
            return False
        binding = _subject_binding(subject, execution_id) or _binding_for(execution_id)
        if binding is None:
            return False
        # A direct client or session can be created by a shared fixture before
        # the test body starts. The matcher is the exact point where that
        # execution is observed by the current test attempt.
        try:
            from ._test_runs import associate_execution

            associate_execution(execution_id)
        except Exception:
            pass
        config = redaction_config or binding.redaction_config
        turn_id = _subject_turn_id(subject)
        details = redact_for_persistence(
            {
                "matcher": matcher,
                "identity": _safe_json(identity or {}),
                "arguments": _safe_json(arguments),
                "subject": _subject_evidence(subject, matcher, arguments),
            },
            config=config,
            path="$.matcher",
        )
        result = EvaluationResult(
            evaluation_id=EvaluationId(f"matcher-{uuid4().hex}"),
            name=f"mcp_pal.matcher.{matcher}.v1",
            status=EvaluationStatus(status),
            required=False,
            message=_safe_text(message, config) if message else None,
            context=EvaluationContext(
                execution_id=_coerce_execution_id(execution_id),
                turn_id=turn_id,
                subject_kind="matcher",
                metadata={"matcher": matcher},
            ),
            details=details,
        )
        binding.store.save_evaluation(execution_id, result, turn_id=turn_id)
        return True
    except Exception:
        # This is an observational side effect. Never replace or mask the
        # assertion result; expose only a non-sensitive diagnostic code.
        _record_issue("matcher_recording_failed")
        return False


def _identifier(value: Any) -> str:
    try:
        return str(getattr(value, "root", value))
    except Exception:
        return "<unavailable>"


def _coerce_execution_id(value: Any) -> Any:
    from .types import ExecutionId

    return value if isinstance(value, ExecutionId) else ExecutionId(_identifier(value))


def _binding_for(execution_id: Any) -> _ResolvedBinding | None:
    try:
        key = _identifier(execution_id)
        with _LOCK:
            values = _BINDINGS.get(key, [])
            live_values = [item for item in values if item.store is not None]
            if live_values:
                _BINDINGS[key] = live_values
            else:
                _BINDINGS.pop(key, None)
            stores = {id(item.store): item for item in live_values}
            # Duplicate IDs in distinct stores are ambiguous. Insertion order
            # is never a valid ownership signal.
            binding = next(iter(stores.values())) if len(stores) == 1 else None
            if binding is None:
                return None
            return _ResolvedBinding(
                binding.execution_id,
                binding.store,
                binding.redaction_config,
            )
    except Exception:
        return None


def _subject_binding(subject: Any, execution_id: Any) -> _ResolvedBinding | None:
    try:
        with _LOCK:
            binding = _SUBJECT_BINDINGS.get(id(subject))
            if binding is None or binding.subject_ref() is not subject:
                return None
            if binding.execution_id != _identifier(execution_id):
                return None
            return _ResolvedBinding(
                binding.execution_id, binding.store, binding.redaction_config
            )
    except Exception:
        return None


def _drop_store_binding(key: str, store_ref: weakref.ReferenceType[Any]) -> None:
    with _LOCK:
        values = _BINDINGS.get(key)
        if values is None:
            return
        values[:] = [item for item in values if item.store_ref is not store_ref]
        if not values:
            _BINDINGS.pop(key, None)


def _drop_subject(key: int, subject_ref: weakref.ReferenceType[Any]) -> None:
    with _LOCK:
        binding = _SUBJECT_BINDINGS.get(key)
        if binding is not None and binding.subject_ref is subject_ref:
            _SUBJECT_BINDINGS.pop(key, None)


def _subject_execution_id(subject: Any) -> Any | None:
    seen: set[int] = set()
    pending = [subject]
    while pending:
        value = pending.pop(0)
        if value is None or id(value) in seen:
            continue
        seen.add(id(value))
        try:
            identifier = getattr(value, "execution_id", None)
        except Exception:
            identifier = None
        if identifier is not None:
            return identifier
        for name in ("trace", "final_trace", "result", "snapshot"):
            try:
                child = getattr(value, name, None)
            except Exception:
                child = None
            if child is not None and child is not value:
                pending.append(child)
    return None


def _subject_turn_id(subject: Any) -> Any | None:
    for value in (subject, getattr(subject, "snapshot", None)):
        if value is None:
            continue
        try:
            turn_id = getattr(value, "turn_id", None)
        except Exception:
            turn_id = None
        if turn_id is not None:
            return turn_id
    return None


def _subject_evidence(
    subject: Any, matcher: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    trace = None
    pending = [subject]
    seen: set[int] = set()
    while pending:
        value = pending.pop(0)
        if value is None or id(value) in seen:
            continue
        seen.add(id(value))
        try:
            if hasattr(value, "events") and hasattr(value, "trace_id"):
                trace = value
                break
        except Exception:
            pass
        for name in ("trace", "final_trace", "result"):
            try:
                child = getattr(value, name, None)
            except Exception:
                child = None
            if child is not None and child is not value:
                pending.append(child)
    if trace is None:
        return {"state": "unavailable"}
    result: dict[str, Any] = {
        "trace_id": _safe_json(getattr(trace, "trace_id", None)),
        "execution_id": _safe_json(getattr(trace, "execution_id", None)),
    }
    try:
        view = trace.view() if callable(getattr(trace, "view", None)) else trace
        entries = tuple(getattr(view, "timeline", ()))
        wanted = arguments.get("tool") or arguments.get("name")
        server = arguments.get("server") or arguments.get("server_name")
        requested_turn = arguments.get("turn")
        tool_refs: list[str] = []
        event_refs: list[str] = []
        tool_matchers = {
            "to_have_tool_call",
            "to_not_have_tool_call",
            "to_have_no_tool_call",
            "to_have_reported_tool_call",
        }
        for entry in entries:
            entry_id = getattr(entry, "entry_id", None)
            if entry_id is None:
                continue
            if matcher in tool_matchers and getattr(entry, "kind", None) == "tool_call":
                actual_tool = getattr(getattr(entry, "tool", None), "value", None)
                actual_server = getattr(getattr(entry, "server", None), "value", None)
                actual_turn = getattr(entry, "turn_id", None)
                if wanted is not None and actual_tool != wanted:
                    continue
                if server is not None and actual_server != server:
                    continue
                if requested_turn is not None and _identifier(
                    actual_turn
                ) != _identifier(requested_turn):
                    continue
                tool_refs.append(_identifier(entry_id))
            if matcher == "to_have_event" and getattr(
                entry, "kind", None
            ) == arguments.get("kind"):
                event_refs.append(_identifier(entry_id))
        if tool_refs:
            result["tool_call_refs"] = tool_refs
        if event_refs:
            result["event_refs"] = event_refs
    except Exception:
        result["state"] = "partial"
    return result


def _safe_json(value: Any) -> Any:
    if callable(value):
        module = getattr(value, "__module__", "unknown")
        name = getattr(value, "__qualname__", getattr(value, "__name__", "callable"))
        return {"callable": f"{module}.{name}", "implementation": "unknown"}
    if isinstance(value, Mapping):
        entries = ((_safe_key(key), _safe_json(item)) for key, item in value.items())
        return {key: item for key, item in sorted(entries, key=lambda entry: entry[0])}
    if isinstance(value, (set, frozenset)):
        items = [_safe_json(item) for item in value]
        return sorted(items, key=_stable_json_key)
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _safe_json(value.model_dump(mode="json"))
        except Exception:
            return {"state": "unavailable", "type": type(value).__name__}
    if hasattr(value, "root"):
        return _safe_json(value.root)
    if hasattr(value, "value") and isinstance(value.value, str):
        return value.value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return (
            value
            if math.isfinite(value)
            else {"state": "unavailable", "reason": "non_finite"}
        )
    if hasattr(value, "isoformat") and callable(value.isoformat):
        try:
            return value.isoformat()
        except Exception:
            pass
    return {"state": "unavailable", "type": type(value).__name__}


def _safe_key(value: Any) -> str:
    return (
        str(value)
        if isinstance(value, (str, int, bool))
        else f"<{type(value).__name__}>"
    )


def _stable_json_key(value: Any) -> str:
    """Return a deterministic ordering key for already-safe JSON values."""

    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return f"<{type(value).__name__}>"


def _safe_text(value: str, config: RedactionConfig | None) -> str:
    try:
        text = str(redact_for_persistence(value, config=config))
        limit = 4000
        marker = " …[truncated]"
        if len(text) <= limit:
            return text
        return text[: limit - len(marker)] + marker
    except Exception:
        return "<unavailable>"


def _record_issue(code: str) -> None:
    try:
        from ._test_runs import active_test

        current = active_test()
        if current is None:
            return
        diagnostics = current.setdefault("diagnostics", {})
        values = diagnostics.setdefault("mcp_pal", [])
        if code not in values:
            values.append(code)
    except Exception:
        return


__all__ = [
    "DefaultRecordChecksToken",
    "bind_execution",
    "bind_subject",
    "has_recording_binding",
    "record_checks_enabled",
    "record_matcher",
    "restore_default_record_checks",
    "set_default_record_checks",
    "suppress_recording",
    "unbind_execution",
]
