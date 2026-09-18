"""Internal persistence helpers for pytest run manifests.

The manifest is deliberately a plain JSON-shaped mapping.  It is an internal
bridge between the pytest plugin, storage backends, and the feedback builder;
it is not part of the public SDK model surface.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

_ACTIVE_TEST: ContextVar[dict[str, Any] | None] = ContextVar(
    "mcp_pal_active_pytest_test", default=None
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
    }


def test_attempt(
    run_id: str,
    node_id: str,
    *,
    worker_id: str,
    suite_name: str | None = None,
    suite_id: str | None = None,
) -> dict[str, Any]:
    # worker-qualified identity prevents xdist attempts from overwriting one
    # another while preserving the normal pytest node id for comparison.
    attempt_id = f"{run_id}:{worker_id}:{node_id}:{uuid4().hex}"
    return {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "run_id": str(run_id),
        "node_id": str(node_id),
        "worker_id": str(worker_id),
        "suite_id": suite_id,
        "suite_name": suite_name,
        "phases": {},
        "outcome": "running",
        "duration_seconds": None,
        "execution_ids": [],
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


__all__ = [
    "activate_test",
    "active_test",
    "associate_execution",
    "now_iso",
    "reset_test",
    "run_record",
    "test_attempt",
]
