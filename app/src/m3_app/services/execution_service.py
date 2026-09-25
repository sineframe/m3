"""Transport-neutral application execution service.

The service owns the small amount of application orchestration around the
public SDK execution toolkit and store.  HTTP adapters translate these typed
errors and values into their wire representation; this module has no FastAPI
or response-envelope dependencies.
"""

from __future__ import annotations

import inspect
import math
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import quote

from m3 import (
    DirectSpec,
    EvaluationQuery,
    EvaluationReport,
    EvidenceRef,
    ExecutionId,
    ExecutionOutcome,
    ExecutionPage,
    ExecutionReport,
    ExecutionSpec,
    ExecutionStatus,
    Feedback,
    RawEvidence,
    RawEvidenceIntegrityError,
    RawEvidenceUnavailable,
    TraceUnavailable,
    TraceView,
    build_feedback,
)
from m3._types.specs import AgentSpec, CallTool, ServerBinding
from m3.feedback import project_test_attempts
from m3.observability import ObservationState, ToolCallEntry
from m3.services.profiles import ProfileResolutionError
from m3.storage import ExecutionStore, StorageConflict, StorageError
from m3.suites import Suite
from m3.trace.redaction import REDACTED

_CANCEL_SETTLE_TIMEOUT_SECONDS = 2.0
_CANCEL_SETTLE_POLL_SECONDS = 0.01

# Spec metadata is flat scalars, so replay provenance uses dotted keys.
REPLAYED_FROM_EXECUTION = "replayed_from.execution_id"
REPLAYED_FROM_ENTRY = "replayed_from.entry_id"

# Redaction replaces values in place ("[REDACTED]"); URL query redaction
# percent-encodes the marker. Either form means the recorded value is lossy.
_REDACTION_MARKERS = (REDACTED, quote(REDACTED))


def _contains_redaction(value: object) -> bool:
    """Return whether a recorded JSON value holds a redaction marker."""
    if isinstance(value, str):
        return any(marker in value for marker in _REDACTION_MARKERS)
    if isinstance(value, Mapping):
        return any(
            _contains_redaction(key) or _contains_redaction(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_redaction(item) for item in value)
    return False


def _binding_selector(binding: ServerBinding) -> str | None:
    """Return the selector a trace entry records for this binding."""
    if binding.alias:
        return binding.alias
    if binding.server is not None:
        return binding.server.name
    return binding.profile.server_name if binding.profile is not None else None


class AppExecutionError(RuntimeError):
    """Typed service failure independent of an HTTP transport."""

    def __init__(
        self, code: str, message: str, *, details: dict[str, object] | None = None
    ) -> None:
        self.code = code
        self.message = message
        self.details = dict(details or {})
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class TestResultSummary:
    """Typed projection of a pytest attempt linked to an execution."""

    attempt_id: str
    node_id: str
    description: str
    outcome: str
    verdict: str
    effective_verdict: str
    duration_seconds: float | None


def project_test_results(
    records: Iterable[Mapping[str, object]], execution_id: str
) -> tuple[TestResultSummary, ...]:
    """Project persisted pytest attempts exactly as the v2 app does."""
    summaries: list[TestResultSummary] = []
    for record in records:
        attempt_id = record.get("attempt_id")
        node_id = record.get("node_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            continue
        if not isinstance(node_id, str) or not node_id:
            continue
        execution_ids = record.get("execution_ids", ())
        if not isinstance(execution_ids, (list, tuple, set)):
            continue
        if execution_id not in {str(value) for value in execution_ids}:
            continue
        raw_duration = record.get("duration_seconds")
        duration = (
            float(raw_duration)
            if isinstance(raw_duration, (int, float))
            and not isinstance(raw_duration, bool)
            and math.isfinite(raw_duration)
            else None
        )
        raw_description = record.get("description")
        raw_outcome = record.get("outcome")
        summaries.append(
            TestResultSummary(
                attempt_id=attempt_id,
                node_id=node_id,
                description=raw_description if isinstance(raw_description, str) else "",
                outcome=raw_outcome if isinstance(raw_outcome, str) else "",
                verdict=(
                    str(record["verdict"])
                    if isinstance(record.get("verdict"), str)
                    else "unknown"
                ),
                effective_verdict=(
                    str(record["effective_verdict"])
                    if isinstance(record.get("effective_verdict"), str)
                    else "unknown"
                ),
                duration_seconds=duration,
            )
        )
    summaries.sort(key=lambda item: (item.node_id, item.attempt_id))
    return tuple(summaries)


class AppExecutionStore(Protocol):
    """Minimal public SDK store surface required by the app."""

    def get_report(
        self,
        execution_id: ExecutionId | str,
        *,
        after_sequence: int = -1,
        event_limit: int | None = None,
        artifact_limit: int | None = None,
    ) -> ExecutionReport | None: ...

    def get_execution_spec(
        self, execution_id: ExecutionId | str
    ) -> ExecutionSpec | None: ...
    def get_trace_view(self, execution_id: ExecutionId | str) -> TraceView | None: ...
    def read_raw_evidence(
        self, reference: EvidenceRef, *, max_bytes: int = 1_048_576
    ) -> RawEvidence: ...

    def list_executions(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        lifecycle: ExecutionStatus | str | None = None,
        outcome: ExecutionOutcome | str | None = None,
        run_id: str | None = None,
        suite_id: int | None = None,
        project_id: str | None = None,
    ) -> ExecutionPage: ...

    def get_suite(self, suite_id: int | str) -> Suite | None: ...
    def list_test_results(self, run_id: str) -> tuple[Mapping[str, object], ...]: ...

    def request_cancel(
        self, execution_id: ExecutionId | str, reason: str | None = None
    ) -> bool: ...

    def delete_execution(self, execution_id: ExecutionId | str) -> None: ...
    def aggregate_evaluations(self, query: EvaluationQuery) -> EvaluationReport: ...

    def list_test_runs(self) -> tuple[Mapping[str, object], ...]: ...

    def list_test_run_page(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        suite_id: int | None = None,
        project_id: str | None = None,
        q: str | None = None,
    ) -> tuple[tuple[Mapping[str, object], ...], int]: ...

    def list_suites(self) -> tuple[Suite, ...]: ...


class AppExecutionKit(Protocol):
    """Minimal public synchronous SDK toolkit surface required by the app."""

    def submit(self, spec: ExecutionSpec) -> AppExecutionHandle: ...

    def close(self) -> None: ...


class AppExecutionHandle(Protocol):
    """Minimal handle surface needed for app cancellation and retention."""

    @property
    def execution_id(self) -> ExecutionId | str: ...

    def cancel(self) -> None: ...


class AppExecutionService:
    """Application execution facade over an injected SDK toolkit and store."""

    def __init__(
        self,
        store: AppExecutionStore,
        kit: AppExecutionKit,
        *,
        owns_store: bool = False,
        owns_kit: bool = False,
    ) -> None:
        """Create a service over injected SDK resources.

        By default the caller owns ``store`` and ``kit`` (the HTTP app does
        this during its lifespan).  Set an ownership flag when constructing a
        standalone service/context manager so :meth:`close` shuts that
        resource down exactly once.
        """
        self.store = store
        self.kit = kit
        self._owns_store = owns_store
        self._owns_kit = owns_kit
        self._closed = False
        self._active_handles: dict[str, AppExecutionHandle] = {}

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("execution service is closed")

    def _id(self, value: ExecutionId | str) -> ExecutionId:
        try:
            return value if isinstance(value, ExecutionId) else ExecutionId(value)
        except (TypeError, ValueError) as exc:
            raise AppExecutionError(
                "invalid_execution_id", "execution_id is invalid"
            ) from exc

    def _report(self, execution_id: ExecutionId) -> ExecutionReport:
        report = self.store.get_report(execution_id, event_limit=1)
        if report is None:
            raise AppExecutionError("execution_not_found", "execution was not found")
        if report.snapshot.lifecycle is ExecutionStatus.FINISHED:
            self._active_handles.pop(execution_id.root, None)
        return report

    def create(self, spec: ExecutionSpec) -> ExecutionReport:
        """Submit an already-validated SDK execution specification."""

        self._ensure_open()
        if not isinstance(spec, (DirectSpec, AgentSpec)):
            raise AppExecutionError(
                "invalid_execution_spec", "execution spec is invalid"
            )
        try:
            handle = self.kit.submit(spec)
        except ProfileResolutionError as exc:
            raise AppExecutionError(
                "profile_resolution_failed",
                exc.message,
                details=exc.details,
            ) from exc
        except (StorageConflict, ValueError, RuntimeError) as exc:
            raise AppExecutionError(
                "execution_submission_failed", "execution could not be submitted"
            ) from exc
        raw_execution_id = handle.execution_id
        execution_id = (
            raw_execution_id
            if isinstance(raw_execution_id, ExecutionId)
            else ExecutionId(str(raw_execution_id))
        )
        self._active_handles[execution_id.root] = handle
        return self._report(execution_id)

    def get(self, execution_id: ExecutionId | str) -> ExecutionReport:
        self._ensure_open()
        return self._report(self._id(execution_id))

    def specification(self, execution_id: ExecutionId | str) -> ExecutionSpec | None:
        self._ensure_open()
        identifier = self._id(execution_id)
        self._report(identifier)
        try:
            spec = self.store.get_execution_spec(identifier)
        except (StorageError, TypeError, ValueError) as exc:
            raise AppExecutionError(
                "execution_data_unavailable", "execution data is unavailable"
            ) from exc
        return spec

    def trace_view(self, execution_id: ExecutionId | str) -> TraceView:
        self._ensure_open()
        identifier = self._id(execution_id)
        report = self._report(identifier)
        if report.snapshot.lifecycle is not ExecutionStatus.FINISHED:
            raise AppExecutionError(
                "execution_not_terminal",
                "execution report is available only after termination",
            )
        try:
            view = self.store.get_trace_view(identifier)
        except (TraceUnavailable, StorageError, TypeError, ValueError) as exc:
            raise AppExecutionError(
                "trace_unavailable", "execution trace is unavailable"
            ) from exc
        if view is None:
            raise AppExecutionError(
                "trace_unavailable", "execution trace is unavailable"
            )
        return view

    def replay_tool_call(
        self,
        execution_id: ExecutionId | str,
        entry_id: str,
        arguments: Mapping[str, object] | None = None,
    ) -> ExecutionReport:
        """Submit one recorded tool call again as a new direct execution.

        Only the matching server binding is copied. Environment references
        stay references, so the runtime resolves them from the current
        process. Test-run and suite identity are not copied.
        """

        trace = self.trace_view(execution_id)
        entry = next(
            (item for item in trace.timeline if item.entry_id == entry_id), None
        )
        if entry is None:
            raise AppExecutionError("tool_call_not_found", "tool call was not found")
        if not isinstance(entry, ToolCallEntry):
            raise AppExecutionError(
                "tool_call_not_replayable", "trace entry is not a tool call"
            )
        observed = ObservationState.OBSERVED
        if entry.tool.state is not observed or not entry.tool.value:
            raise AppExecutionError(
                "tool_call_not_replayable", "tool name was not observed"
            )
        if _contains_redaction(entry.tool.value):
            # The body cannot replace the tool name, so this is final.
            raise AppExecutionError(
                "tool_call_not_replayable", "recorded tool name is redacted"
            )
        if arguments is None:
            if entry.arguments.state is not observed or not isinstance(
                entry.arguments.value, Mapping
            ):
                raise AppExecutionError(
                    "tool_call_not_replayable",
                    "tool arguments were not observed; supply arguments",
                )
            if _contains_redaction(entry.arguments.value):
                raise AppExecutionError(
                    "tool_call_not_replayable",
                    "recorded tool arguments are redacted; supply arguments",
                )
            arguments = entry.arguments.value
        try:
            source = self.store.get_execution_spec(trace.execution_id)
        except (StorageError, TypeError, ValueError) as exc:
            raise AppExecutionError(
                "replay_source_unavailable", "source execution spec cannot be loaded"
            ) from exc
        selector = entry.server_binding
        binding = next(
            (
                item
                for item in (source.servers if source is not None else ())
                if selector is not None and _binding_selector(item) == selector
            ),
            None,
        )
        if source is None or binding is None:
            raise AppExecutionError(
                "replay_source_unavailable", "source server binding is unavailable"
            )
        try:
            spec = DirectSpec(
                project_id=source.project_id,
                project_name=source.project_name,
                servers=(binding,),
                protocol=source.protocol,
                operation=CallTool(
                    server=selector, name=entry.tool.value, arguments=arguments
                ),
                metadata={
                    REPLAYED_FROM_EXECUTION: trace.execution_id.root,
                    REPLAYED_FROM_ENTRY: entry_id,
                },
            )
        except ValueError as exc:
            raise AppExecutionError(
                "replay_source_unavailable", "replay spec could not be built"
            ) from exc
        return self.create(spec)

    def read_raw_evidence(
        self, reference: EvidenceRef, *, max_bytes: int = 1_048_576
    ) -> RawEvidence:
        self._ensure_open()
        try:
            return self.store.read_raw_evidence(reference, max_bytes=max_bytes)
        except RawEvidenceUnavailable as exc:
            raise AppExecutionError(
                "raw_evidence_not_found", "raw evidence was not found"
            ) from exc
        except RawEvidenceIntegrityError as exc:
            raise AppExecutionError(
                "raw_evidence_integrity_error",
                "raw evidence integrity could not be verified",
            ) from exc

    def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        lifecycle: ExecutionStatus | str | None = None,
        outcome: ExecutionOutcome | str | None = None,
        project_id: str | None = None,
    ) -> ExecutionPage:
        self._ensure_open()
        try:
            if project_id is not None:
                return self.store.list_executions(
                    limit=limit,
                    offset=offset,
                    lifecycle=lifecycle,
                    outcome=outcome,
                    project_id=project_id,
                )
            return self.store.list_executions(
                limit=limit,
                offset=offset,
                lifecycle=lifecycle,
                outcome=outcome,
            )
        except (TypeError, ValueError) as exc:
            raise AppExecutionError(
                "invalid_execution_filter", "execution filter is invalid"
            ) from exc

    def report(
        self,
        execution_id: ExecutionId | str,
        *,
        after_sequence: int = -1,
        event_limit: int = 100,
        artifact_limit: int = 100,
    ) -> ExecutionReport:
        self._ensure_open()
        identifier = self._id(execution_id)
        try:
            report = self.store.get_report(
                identifier,
                after_sequence=after_sequence,
                event_limit=event_limit,
                artifact_limit=artifact_limit,
            )
        except ValueError as exc:
            raise AppExecutionError(
                "invalid_report_cursor", "report cursor or limit is invalid"
            ) from exc
        if report is None:
            raise AppExecutionError("execution_not_found", "execution was not found")
        return report

    def test_results(self, report: ExecutionReport) -> tuple[TestResultSummary, ...]:
        """Return linked pytest attempts from the execution's run."""
        self._ensure_open()
        identifier = report.snapshot.execution_id
        run_id = report.snapshot.run_id
        if run_id is None:
            return ()
        try:
            records = project_test_attempts(
                cast(ExecutionStore, self.store), run_id.root
            )
        except (StorageError, TypeError, ValueError, AttributeError) as exc:
            raise AppExecutionError(
                "execution_data_unavailable", "execution data is unavailable"
            ) from exc
        return project_test_results(records, identifier.root)

    def cancel(
        self, execution_id: ExecutionId | str, reason: str | None = None
    ) -> ExecutionReport:
        self._ensure_open()
        identifier = self._id(execution_id)
        current = self._report(identifier)
        if current.snapshot.lifecycle is ExecutionStatus.FINISHED:
            raise AppExecutionError(
                "execution_terminal", "terminal executions cannot be cancelled"
            )
        handle = self._active_handles.get(identifier.root)
        try:
            if handle is not None:
                handle.cancel()
                # A local handle normally waits for the runtime's terminal
                # event, but the durable snapshot and trace are committed by
                # separate storage operations.  Give that final commit a
                # small settling window before returning a successful cancel
                # response.  Otherwise the next report request can observe
                # the still-active snapshot and incorrectly receive 409.
                deadline = time.monotonic() + _CANCEL_SETTLE_TIMEOUT_SECONDS
                while True:
                    settled = self._report(identifier)
                    if settled.snapshot.lifecycle is ExecutionStatus.FINISHED:
                        return settled
                    if time.monotonic() >= deadline:
                        raise AppExecutionError(
                            "cancellation_conflict",
                            "execution cancellation did not finish",
                        )
                    time.sleep(_CANCEL_SETTLE_POLL_SECONDS)
            else:
                self.store.request_cancel(identifier, reason=reason)
        except (StorageConflict, RuntimeError) as exc:
            raise AppExecutionError(
                "cancellation_conflict",
                "execution cancellation conflicted with its current state",
            ) from exc
        return self._report(identifier)

    def delete(self, execution_id: ExecutionId | str) -> ExecutionId:
        self._ensure_open()
        identifier = self._id(execution_id)
        self._report(identifier)
        try:
            self.store.delete_execution(identifier)
        except StorageConflict as exc:
            code = "execution_active" if "active" in str(exc) else "execution_conflict"
            message = (
                "active executions cannot be deleted"
                if code == "execution_active"
                else "execution could not be deleted"
            )
            raise AppExecutionError(code, message) from exc
        self._active_handles.pop(identifier.root, None)
        return identifier

    def aggregate(self, query: EvaluationQuery) -> EvaluationReport:
        self._ensure_open()
        try:
            return self.store.aggregate_evaluations(query)
        except (TypeError, ValueError) as exc:
            raise AppExecutionError(
                "invalid_evaluation_aggregate_query",
                "evaluation aggregate query is invalid",
            ) from exc

        except StorageError as exc:
            raise AppExecutionError(
                "evaluation_data_unavailable", "evaluation data is unavailable"
            ) from exc

    def list_runs(self) -> tuple[Mapping[str, object], ...]:
        """Return persisted pytest run manifests for safe API projection."""
        self._ensure_open()
        getter = getattr(self.store, "list_test_runs", None)
        if not callable(getter):
            return ()
        try:
            return tuple(getter())
        except StorageError as exc:
            raise AppExecutionError(
                "run_data_unavailable", "run data is unavailable"
            ) from exc

    def list_run_page(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        suite_id: int | None = None,
        project_id: str | None = None,
        q: str | None = None,
    ) -> tuple[tuple[Mapping[str, object], ...], int]:
        """Return one newest-first page of run manifests with their suites."""
        self._ensure_open()
        try:
            if q and q.strip():
                lister = self.store.list_test_run_page
                try:
                    parameters = inspect.signature(lister).parameters
                    supports_q = "q" in parameters or any(
                        item.kind is inspect.Parameter.VAR_KEYWORD
                        for item in parameters.values()
                    )
                except (TypeError, ValueError):
                    supports_q = True
                if supports_q:
                    return lister(
                        limit=limit,
                        offset=offset,
                        suite_id=suite_id,
                        project_id=project_id,
                        q=q,
                    )
                # An older injected store cannot search itself. Filter its
                # complete ordered result before applying the requested page.
                runs, _ = lister(
                    limit=None, offset=0, suite_id=suite_id, project_id=project_id
                )
                term = q.strip().casefold()
                exact_label = term.startswith("run #") and term[5:].isdigit()
                matches = tuple(
                    run
                    for run in runs
                    if (
                        isinstance(run.get("run_id"), str)
                        and term in run["run_id"].casefold()
                    )
                    or (
                        isinstance(run.get("run_label"), str)
                        and (
                            run["run_label"].casefold() == term
                            if exact_label
                            else term in run["run_label"].casefold()
                        )
                    )
                )
                end = None if limit is None else offset + limit
                return matches[offset:end], len(matches)
            return self.store.list_test_run_page(
                limit=limit, offset=offset, suite_id=suite_id, project_id=project_id
            )
        except StorageError as exc:
            raise AppExecutionError(
                "run_data_unavailable", "run data is unavailable"
            ) from exc

    def list_suites(self) -> tuple[Suite, ...]:
        """Return every registered suite."""
        self._ensure_open()
        try:
            return tuple(self.store.list_suites())
        except StorageError as exc:
            raise AppExecutionError(
                "run_data_unavailable", "run data is unavailable"
            ) from exc

    def feedback(self, run_id: str, *, baseline_run_id: str | None = None) -> Feedback:
        """Build read-only feedback for a recorded test run."""
        self._ensure_open()
        current = str(run_id)
        if not current:
            raise AppExecutionError("feedback_not_found", "feedback run was not found")

        def has_run(identifier: str) -> bool:
            get_manifest = getattr(self.store, "get_test_run", None)
            if callable(get_manifest) and get_manifest(identifier) is not None:
                return True
            try:
                page = self.store.list_executions(limit=1, offset=0, run_id=identifier)
            except TypeError:
                # Older injected stores may not expose run filtering.  They
                # cannot prove that a requested run exists.
                return False
            return bool(page.items)

        if not has_run(current):
            raise AppExecutionError("feedback_not_found", "feedback run was not found")
        if baseline_run_id is not None and not has_run(str(baseline_run_id)):
            raise AppExecutionError(
                "feedback_baseline_not_found", "feedback baseline run was not found"
            )
        try:
            return build_feedback(
                cast(ExecutionStore, self.store),
                current,
                baseline_run_id=baseline_run_id,
            )
        except (StorageError, TypeError, ValueError) as exc:
            raise AppExecutionError(
                "feedback_data_unavailable", "feedback data is unavailable"
            ) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failure: BaseException | None = None
        if self._owns_kit:
            try:
                self.kit.close()
            except BaseException as exc:  # pragma: no cover - defensive cleanup
                failure = exc
        if self._owns_store:
            close = getattr(self.store, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException as exc:  # pragma: no cover - defensive cleanup
                    if failure is None:
                        failure = exc
        self._active_handles.clear()
        if failure is not None:
            raise failure

    def __enter__(self) -> AppExecutionService:
        self._ensure_open()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


__all__ = [
    "AppExecutionError",
    "AppExecutionHandle",
    "AppExecutionKit",
    "AppExecutionService",
    "AppExecutionStore",
]
