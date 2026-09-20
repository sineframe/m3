"""Shared public v2 report responses for the app and explicit CLI uploads."""

from __future__ import annotations

from typing import Any

from m3_app.api.wire import neutralize_response


def _id_value(value: Any) -> str:
    return str(getattr(value, "root", value))


def build_execution_envelope(
    snapshot: Any, spec: Any, project_name: str | None, *, public: bool = True
) -> dict[str, Any]:
    value = {
        "version": "v2",
        "execution_id": _id_value(snapshot.execution_id),
        "snapshot": snapshot.model_dump(mode="json", by_alias=True),
        "spec": spec.model_dump(mode="json", by_alias=True)
        if spec is not None
        else None,
        "project_name": project_name,
    }
    return (
        neutralize_response("/api/v2/executions/{execution_id}", value)
        if public
        else value
    )


def build_report_envelope(
    execution_id: Any,
    spec: Any,
    report: Any,
    trace: Any,
    test_results: Any,
    *,
    public: bool = True,
) -> dict[str, Any]:
    value = {
        "version": "v2",
        "execution_id": _id_value(execution_id),
        "spec": spec.model_dump(mode="json", by_alias=True)
        if spec is not None
        else None,
        "report": report.model_dump(mode="json", by_alias=True),
        "trace": trace.model_dump(mode="json", by_alias=True),
        "test_results": list(test_results),
    }
    return (
        neutralize_response("/api/v2/executions/{execution_id}/report", value)
        if public
        else value
    )


__all__ = ["build_execution_envelope", "build_report_envelope"]
