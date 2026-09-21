"""Runtime and model provenance is present in public execution payloads."""

from m3.observability import TraceView
from m3.types import (
    AgentIdentity,
    ExecutionReport,
    ExecutionState,
    HarnessIdentity,
    ModelIdentity,
)
from m3_app.api.report_payloads import build_execution_envelope, build_report_envelope


def test_execution_and_report_payloads_include_agent_identity() -> None:
    agent = AgentIdentity(
        harness=HarnessIdentity(
            kind="opencode",
            runtime="managed",
            requested_selector="latest",
            resolved_version="1.18.30",
            target="darwin-arm64",
            digest="a" * 64,
        ),
        model=ModelIdentity(
            requested_id="openai/gpt-5", provider="openai", observed_id="gpt-5"
        ),
    )
    snapshot = ExecutionState(execution_id="runtime-api", agent=agent)
    report = ExecutionReport(snapshot=snapshot, agent=agent)
    trace = TraceView(trace_id="runtime-trace", execution_id="runtime-api", agent=agent)

    detail = build_execution_envelope(snapshot, None, "example")
    payload = build_report_envelope("runtime-api", None, report, trace, ())

    for value in (
        detail["snapshot"]["agent"],
        payload["report"]["agent"],
        payload["trace"]["agent"],
    ):
        assert value["harness"]["resolved_version"] == "1.18.30"
        assert value["harness"]["digest"] == "a" * 64
        assert value["model"]["requested_id"] == "openai/gpt-5"
        assert value["model"]["observed_id"] == "gpt-5"
