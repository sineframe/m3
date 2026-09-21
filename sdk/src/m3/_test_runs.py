"""Internal persistence helpers for pytest run manifests.

The manifest is deliberately a plain JSON-shaped mapping.  It is an internal
bridge between the pytest plugin, storage backends, and the feedback builder;
it is not part of the public SDK model surface.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

REQUIRED_EVALUATION_STATUSES = frozenset({"failed", "error", "inconclusive", "not_run"})

_ACTIVE_TEST: ContextVar[dict[str, Any] | None] = ContextVar(
    "m3_active_pytest_test", default=None
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_record(
    run_id: str,
    *,
    project_root: str,
    selection: tuple[str, ...],
    capture: Mapping[str, Any],
    worker_id: str = "master",
    project_id: str | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": str(run_id),
        "status": "running",
        "project_root": str(project_root),
        "selection": list(selection),
        "collected_node_ids": [],
        "capture": dict(capture),
        "created_at": now_iso(),
        "finished_at": None,
        "exit_status": None,
        "worker_id": str(worker_id),
        "project_id": project_id,
        "project_name": project_name,
    }


def test_attempt(
    run_id: str,
    node_id: str,
    *,
    worker_id: str,
    suite_name: str | None = None,
    suite_id: str | None = None,
    description: str = "",
    project_id: str | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    # worker-qualified identity prevents xdist attempts from overwriting one
    # another while preserving the normal pytest node id for comparison.
    attempt_id = f"{run_id}:{worker_id}:{node_id}:{uuid4().hex}"
    return {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "run_id": str(run_id),
        "node_id": str(node_id),
        "description": str(description),
        "worker_id": str(worker_id),
        "suite_id": suite_id,
        "suite_name": suite_name,
        "project_id": project_id,
        "project_name": project_name,
        "phases": {},
        "outcome": "running",
        "duration_seconds": None,
        "execution_ids": [],
        "detached_evaluations": [],
        "diagnostics": {},
        "started_at": now_iso(),
        "finished_at": None,
    }


def activate_test(value: dict[str, Any]) -> Any:
    return _ACTIVE_TEST.set(value)


def reset_test(token: Any) -> None:
    _ACTIVE_TEST.reset(token)


def active_test() -> dict[str, Any] | None:
    return _ACTIVE_TEST.get()


def required_status_blocks(status: object) -> bool:
    """Return whether a required evaluation verdict blocks finalization."""

    return str(getattr(status, "value", status)) in REQUIRED_EVALUATION_STATUSES


def evaluation_lineage(
    record: Any,
) -> tuple[str, str | None, str, str | None, str | None]:
    """Return the stable identity shared by evaluation policy consumers."""

    execution_id = getattr(record, "execution_id", "")
    turn_id = getattr(record, "turn_id", None)
    return (
        str(getattr(execution_id, "root", execution_id)),
        str(getattr(turn_id, "root", turn_id)) if turn_id is not None else None,
        str(getattr(record, "name", "")),
        str(getattr(record, "subject_kind", "")),
        getattr(record, "subject_digest", None),
    )


def latest_evaluations(
    records: Iterable[Any],
) -> dict[tuple[str, str | None, str, str | None, str | None], Any]:
    """Reduce persisted records to the newest row for each evaluation lineage."""

    latest: dict[tuple[str, str | None, str, str | None, str | None], Any] = {}
    for record in records:
        lineage = evaluation_lineage(record)
        previous = latest.get(lineage)
        record_id = getattr(record, "evaluation_id", "")
        record_key = (
            getattr(record, "created_at", None),
            str(getattr(record_id, "root", record_id)),
        )
        if previous is None:
            latest[lineage] = record
            continue
        previous_id = getattr(previous, "evaluation_id", "")
        previous_key = (
            getattr(previous, "created_at", None),
            str(getattr(previous_id, "root", previous_id)),
        )
        if record_key >= previous_key:
            latest[lineage] = record
    return latest


def required_evaluation_lineages(
    records: Iterable[Any],
) -> set[tuple[str, str | None, str, str | None, str | None]]:
    """Return lineages that have been required by any persisted result."""

    return {
        evaluation_lineage(record)
        for record in records
        if bool(getattr(record, "required", False))
    }


def xfail_waives_required_evaluations(state: Mapping[str, Any]) -> bool:
    """Apply pytest's causal-linkage limitation to required evaluations.

    A valid expected failure can waive every required pair linked to the test,
    because pytest does not expose which assertion caused an evaluator result.
    Setup/teardown failures and persistence/collection failures remain
    independent evidence gaps and are never waived.  Strict XPASS is also not
    a waiver; pytest records its diagnostic as ``XPASS(strict)``.
    """

    phases = state.get("phases")
    if not isinstance(phases, Mapping):
        return False
    call = phases.get("call")
    if not isinstance(call, Mapping) or not bool(call.get("wasxfail")):
        return False
    if any(
        isinstance(value, Mapping) and value.get("outcome") == "failed"
        for phase, value in phases.items()
        if phase != "call"
    ):
        return False
    diagnostics = state.get("diagnostics")
    if isinstance(diagnostics, Mapping) and any(
        str(key).split(":", 1)[0] in {"setup", "teardown"}
        and str(key).endswith(":longrepr")
        for key in diagnostics
    ):
        return False
    if "call" not in phases:
        return False
    # ``passed`` with ``wasxfail`` is XPASS, including non-strict XPASS.  It
    # is evidence that the expected-failure contract was not met and cannot
    # waive a required evaluation.
    if call.get("outcome") not in {"failed", "skipped"}:
        return False
    if isinstance(diagnostics, Mapping) and any(
        "XPASS(strict)" in str(value) for value in diagnostics.values()
    ):
        return False
    return True


def associate_execution(execution_id: Any, *, run_id: Any = None) -> None:
    """Associate an execution created in the active test, when unambiguous."""
    current = _ACTIVE_TEST.get()
    if current is None:
        return
    expected = current.get("run_id")
    actual = getattr(run_id, "root", run_id)
    if actual is not None and expected is not None and str(actual) != str(expected):
        return
    key = str(getattr(execution_id, "root", execution_id))
    values = current.setdefault("execution_ids", [])
    if key not in values:
        values.append(key)


def record_detached_evaluation(result: Any) -> None:
    """Record a required evaluation that has no execution identity.

    The active pytest attempt is the durable, run-scoped owner for this
    evidence.  Keeping the record on that attempt preserves the SDK's
    execution-id-optional input contract without introducing process-global
    evaluator state.
    """

    current = _ACTIVE_TEST.get()
    if current is None or not bool(getattr(result, "required", False)):
        return
    values = current.setdefault("detached_evaluations", [])
    if not isinstance(values, list):
        return
    evaluation_id = getattr(result, "evaluation_id", "")
    status = getattr(result, "status", "")
    values.append(
        {
            "evaluation_id": str(getattr(evaluation_id, "root", evaluation_id)),
            "name": str(getattr(result, "name", "")),
            "status": str(getattr(status, "value", status)),
            "required": True,
            "message": getattr(result, "message", None),
            "score": getattr(result, "score", None),
            "rationale": getattr(result, "rationale", None),
            "metrics": dict(getattr(result, "metrics", {}) or {}),
            "details": dict(getattr(result, "details", {}) or {}),
        }
    )


__all__ = [
    "activate_test",
    "active_test",
    "associate_execution",
    "evaluation_lineage",
    "latest_evaluations",
    "now_iso",
    "record_detached_evaluation",
    "required_evaluation_lineages",
    "required_status_blocks",
    "reset_test",
    "run_record",
    "test_attempt",
    "xfail_waives_required_evaluations",
]
