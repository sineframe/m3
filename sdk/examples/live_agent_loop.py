"""Run one selected agent in a normal Python process.

Provider credentials are read from the process environment. This example does
not load dotenv implicitly; use ``uv run --env-file .env ...`` when needed.
"""

from __future__ import annotations

import argparse
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp_pal import MCPTestKit, expect
from mcp_pal.types import StdioServer


def selected_model(argv: list[str] | None = None) -> str:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="opencode/big-pickle")
    return parser.parse_args(argv).model


def event_progress(event: Any) -> str:
    """Format only stable event identity fields, never payload data."""

    line = (
        f"event sequence={event.sequence} kind={event.kind.value} "
        f"phase={event.lifecycle_phase.value}"
    )
    if event.kind.value == "diagnostic":
        payload = event.payload
        for key in ("stage", "operation", "elapsed_seconds", "timeout_seconds"):
            value = payload.get(key)
            if value is not None:
                line += f" {key}={value}"
    return line


def diagnostic_stage(events: list[Any], lifecycle: Any) -> str:
    """Classify a timeout from lifecycle phases without exposing provider data."""

    phases = {
        getattr(event.lifecycle_phase, "value", event.lifecycle_phase)
        for event in events
    }
    if "turn" in phases or "mcp_call" in phases:
        return "turn/provider response"
    if "startup" in phases or "initialization" in phases:
        return "harness startup/initialization"
    return str(getattr(lifecycle, "value", lifecycle))


def trace_summary(trace: Any, *, event_count: int) -> tuple[str, ...]:
    """Return safe terminal trace lines for a human running this example."""

    lines = [
        f"final trace outcome={trace.outcome.value} "
        f"completeness={trace.completeness} events={event_count}"
    ]
    for diagnostic in trace.diagnostics:
        lines.append(
            "final diagnostic "
            f"code={diagnostic.code} stage={diagnostic.stage} "
            f"operation={diagnostic.operation} "
            f"elapsed_seconds={diagnostic.elapsed_seconds} "
            f"timeout_seconds={diagnostic.timeout_seconds}"
        )
    return tuple(lines)


def run_with_diagnostics(
    agent: Any,
    message: str,
    *,
    server: Any,
    turn_timeout: float = 120.0,
    result_timeout: float = 135.0,
    emit: Callable[[str], None] = print,
) -> Any:
    """Run one agent turn with bounded waiting and safe event diagnostics.

    ``turn_timeout`` bounds the provider request. ``result_timeout`` only
    bounds waiting for the background execution to finish; it does not cancel
    the execution. On failure this helper cancels explicitly, waits briefly
    for finalization, and reports lifecycle/event metadata without payloads.
    """

    handle = agent.submit(
        message, server=server, timeout=turn_timeout, case_id="shipping-loop"
    )
    events: list[Any] = []

    def consume_events() -> None:
        try:
            for event in handle.events():
                events.append(event)
                emit(event_progress(event))
        except Exception as exc:  # pragma: no cover - defensive shutdown path
            emit(f"event stream ended with {type(exc).__name__}")

    watcher = threading.Thread(
        target=consume_events, name="mcp-pal-events", daemon=True
    )
    watcher.start()
    try:
        result = handle.result(timeout=result_timeout)
    except Exception as exc:
        snapshot = handle.snapshot()
        lifecycle = getattr(snapshot, "lifecycle", "unknown")
        emit(
            f"agent execution failed: {type(exc).__name__}; "
            f"stage={diagnostic_stage(events, lifecycle)}; "
            f"lifecycle={getattr(lifecycle, 'value', lifecycle)}"
        )
        try:
            handle.cancel()
        except Exception as cancel_error:  # pragma: no cover - defensive path
            emit(f"cancellation failed: {type(cancel_error).__name__}")
        try:
            finalized = handle.result(timeout=10)
            trace = finalized.trace_view
            emit(
                f"final trace outcome={trace.outcome.value} "
                f"completeness={trace.completeness} events={len(events)}"
            )
        except Exception as finalize_error:  # pragma: no cover - provider-dependent
            emit(f"final trace unavailable: {type(finalize_error).__name__}")
        raise
    finally:
        watcher.join(timeout=10)
        if watcher.is_alive():  # pragma: no cover - defensive shutdown path
            emit("event stream did not close after execution finalization")
    return result


def main(argv: list[str] | None = None) -> None:
    root = Path(__file__).parent
    server = StdioServer(
        name="example-mcp",
        command=sys.executable,
        args=(str(root / "servers" / "example_mcp_server.py"),),
        cwd=str(root),
    )
    agents = [{"harness": "opencode", "models": [selected_model(argv)]}]
    with MCPTestKit() as kit:
        for agent in kit.agents(agents, trials=1):
            result = run_with_diagnostics(
                agent, "Use shipping_quote for a local quote", server=server
            )
            trace = result.trace_view
            for line in trace_summary(trace, event_count=len(trace.timeline)):
                print(line, file=sys.stderr)
            if trace.outcome.value != "completed":
                raise RuntimeError(f"agent execution ended with {trace.outcome.value}")
            expect(result).to_have_tool_call(
                "shipping_quote", server=server.name, status="success"
            )


if __name__ == "__main__":
    main()
