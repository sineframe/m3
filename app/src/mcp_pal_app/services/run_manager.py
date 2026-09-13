"""FIFO execution, cancellation, and persistence orchestration for runs."""

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

from sqlalchemy.orm import Session

from mcp_pal.harness.acp import AcpHarnessRunner
from mcp_pal.harness.base import AcpRunSpec, HarnessResult, RunSpec
from mcp_pal.trace.acp import build_acp_trace
from mcp_pal.trace.claude import build_claude_trace, transport_for_server
from mcp_pal.trace.normalized import build_opencode_trace
from mcp_pal.trace.redaction import (
    RedactionConfig,
    redact_for_log,
    redact_for_persistence,
)
from mcp_pal_app.domain.events import derive_mcp_assertion
from mcp_pal_app.harness_claude_cli import ClaudeCodeRunner
from mcp_pal_app.harness_opencode_cli import OpenCodeRunner
from mcp_pal_app.persistence.models import (
    McpProfileRevision,
    Run,
    RunEvent,
    RunHarnessSnapshot,
    RunTrace,
    now,
)
from mcp_pal_app.settings import Settings


def _observed_acp_metadata(result: Any) -> dict[str, Any]:
    """Extract observed ACP session metadata from captured frames.

    The ACP runner already records raw protocol frames; keeping this parser in
    persistence orchestration avoids changing the runner contract or native
    harnesses while retaining requested-vs-observed provenance.
    """
    frames = getattr(result, "event_records", None) or ()
    observed: dict[str, Any] = {
        "session_id": getattr(result, "session_id", None),
        "agent_identity": None,
        "mode_id": None,
        "session_config": {},
        "modes": None,
        "config_options": None,
        "configured_transport": getattr(result, "configured_transport", None)
        or getattr(result, "transport", None),
        "instrumented_transport": getattr(result, "instrumented_transport", None)
        or getattr(result, "transport", None),
    }
    for frame in frames:
        payload = frame.get("payload") if isinstance(frame, dict) else None
        if not isinstance(payload, dict):
            continue
        value = (
            cast(dict[str, Any], payload.get("result"))
            if isinstance(payload.get("result"), dict)
            else {}
        )
        if isinstance(value.get("agentInfo"), dict):
            observed["agent_identity"] = value["agentInfo"]
        if value.get("sessionId") or value.get("session_id"):
            observed["session_id"] = value.get("sessionId") or value.get("session_id")
        if "modes" in value:
            observed["modes"] = value["modes"]
        if "configOptions" in value or "config_options" in value:
            observed["config_options"] = value.get("configOptions") or value.get(
                "config_options"
            )
        method = payload.get("method")
        params = (
            cast(dict[str, Any], payload.get("params"))
            if isinstance(payload.get("params"), dict)
            else {}
        )
        if method == "session/set_mode":
            observed["mode_id"] = params.get("modeId") or params.get("mode_id")
        elif method == "session/set_config_option":
            ident = params.get("configId") or params.get("config_id")
            if ident is not None:
                observed["session_config"][ident] = params.get("value")
    if observed["mode_id"] is None and isinstance(observed.get("modes"), dict):
        observed["mode_id"] = observed["modes"].get("currentModeId") or observed[
            "modes"
        ].get("current_mode_id")
    return observed


def _snapshot_verification(db: Session, run_id: str) -> dict[str, Any]:
    snapshot = db.get(RunHarnessSnapshot, run_id)
    return snapshot.verification if snapshot is not None else {}


class RunManager:
    def __init__(
        self, session_factory: Callable[[], Session], settings: Settings
    ) -> None:
        self.session_factory, self.settings = session_factory, settings
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.runners: dict[
            str, AcpHarnessRunner | ClaudeCodeRunner | OpenCodeRunner
        ] = {}
        self.done_events: dict[str, threading.Event] = {}
        self.lock = threading.Lock()

    def submit(self, run_id: str) -> None:
        self.executor.submit(self.execute, run_id)

    def runner_for(self, harness: str) -> ClaudeCodeRunner | OpenCodeRunner:
        if harness == "claude-code":
            return ClaudeCodeRunner(self.settings.claude_executable)
        if harness == "opencode":
            return OpenCodeRunner(
                self.settings.opencode_executable,
                self.settings.opencode_api_key,
                self.settings.opencode_provider_credentials(),
            )
        if harness == "acp":
            raise ValueError("ACP runner requires a revision manifest")
        raise ValueError(f"Unsupported harness: {harness}")

    def execute(self, run_id: str) -> None:
        db = self.session_factory()
        run = db.get(Run, run_id)
        if not run or run.status != "queued":
            db.close()
            return
        run.status, run.started_at = "running", now()
        db.commit()
        rev = cast(
            McpProfileRevision, db.get(McpProfileRevision, run.profile_revision_id)
        )
        cancel = asyncio.Event()
        snapshot = db.get(RunHarnessSnapshot, run.id) if run.harness == "acp" else None
        runner = cast(
            AcpHarnessRunner | ClaudeCodeRunner | OpenCodeRunner,
            (
                (AcpHarnessRunner(snapshot.manifest) if snapshot else None)
                if run.harness == "acp"
                else self.runner_for(run.harness)
            ),
        )
        done = threading.Event()
        with self.lock:
            self.runners[run_id], self.done_events[run_id] = runner, done
        config = RedactionConfig.from_environment()
        seq = db.query(RunEvent).filter_by(run_id=run_id).count()

        async def callback(raw: Any, event_type: str, payload: dict[str, Any]) -> None:
            nonlocal seq
            seq += 1
            safe_payload = redact_for_persistence(
                payload, config=config, path="$.event.payload"
            )
            safe_raw = redact_for_persistence(
                raw, config=config, path="$.event.raw_event"
            )
            db.add(
                RunEvent(
                    run_id=run_id,
                    sequence=seq,
                    event_type=event_type,
                    payload=safe_payload,
                    raw_event=safe_raw,
                )
            )
            db.commit()

        result: HarnessResult | None = None
        try:
            spec: AcpRunSpec | RunSpec
            if run.harness == "acp":
                if not snapshot:
                    raise ValueError("ACP harness snapshot not found")
                spec = AcpRunSpec(
                    run.prompt,
                    run.model,
                    rev.mcp_json,
                    run.enabled_server,
                    snapshot.manifest,
                    snapshot.tool_mode,
                    snapshot.agent_mode_id,
                    snapshot.session_config,
                    run.timeout_seconds,
                )
            else:
                spec = RunSpec(
                    run.prompt,
                    run.model,
                    rev.mcp_json,
                    run.enabled_server,
                    run.tool_mode,
                    run.timeout_seconds,
                    run.max_turns,
                    run.max_budget_usd,
                )
            if run.harness == "acp":
                result = asyncio.run(
                    cast(AcpHarnessRunner, runner).run(
                        cast(AcpRunSpec, spec), callback, cancel
                    )
                )
            else:
                result = asyncio.run(
                    cast(ClaudeCodeRunner | OpenCodeRunner, runner).run(
                        cast(RunSpec, spec), callback, cancel
                    )
                )
            safe_result = redact_for_persistence(
                result.final_text, config=config, path="$.run.final_output"
            )
            safe_stderr = redact_for_log(
                result.stderr, config=config, path="$.run.stderr"
            )
            safe_error = redact_for_log(result.error, config=config, path="$.run.error")
            (
                run.status,
                run.claude_result,
                run.exit_code,
                run.error_message,
                run.stderr,
            ) = result.status, safe_result, result.exit_code, safe_error, safe_stderr
            run.cost_usd, run.turns, run.session_id = (
                result.cost_usd,
                result.turns,
                result.session_id,
            )
            run.mcp_assertion = derive_mcp_assertion(result.events, run.enabled_server)
            run.finished_at = now()
            if run.harness == "acp" and (
                result.error or result.error_code or result.error_details
            ):
                snapshot = db.get(RunHarnessSnapshot, run.id)
                if snapshot:
                    safe_details = redact_for_persistence(
                        result.error_details or {},
                        config=config,
                        path="$.run.failure.diagnostics",
                    )
                    verification = dict(snapshot.verification or {})
                    verification["failure"] = {
                        "code": result.error_code or "acp_run_failed",
                        "phase": result.error_phase or "execution",
                        "message": safe_error,
                        "diagnostics": safe_details,
                    }
                    snapshot.verification = verification
            if run.harness == "acp":
                snapshot = db.get(RunHarnessSnapshot, run.id)
                if snapshot:
                    observed = _observed_acp_metadata(result)
                    safe_observed = redact_for_persistence(
                        observed, config=config, path="$.run.observed"
                    )
                    verification = dict(snapshot.verification or {})
                    verification["observed"] = safe_observed
                    snapshot.verification = verification
            if run.harness == "acp":
                # ACP assertions consume the normalized backend trace. This
                # keeps wire authority, inferred links, and configured versus
                # instrumented transport semantics in one place.
                acp_transport = (
                    getattr(result, "configured_transport", None)
                    or getattr(result, "transport", None)
                    or "unknown"
                )
                instrumented_transport = (
                    getattr(result, "instrumented_transport", None) or acp_transport
                )
                trace = build_acp_trace(
                    acp_frames=result.event_records or (),
                    mcp_frames=result.protocol_events or (),
                    selected_server=run.enabled_server,
                    configured_transport=acp_transport,
                    instrumented_transport=instrumented_transport,
                    status=result.status,
                    session_id=result.session_id,
                    result_metadata=_snapshot_verification(db, run.id),
                )
                calls = trace.get("mcp_calls") or []
                # The ACP report describes the model's claimed calls, while
                # the instrumented MCP stream is the authority.  Every
                # normalized model-reported call must have a completed,
                # correlated wire request/response; an ACP-only call is a
                # failure even when another call has valid wire evidence.
                reported_calls = [
                    c for c in calls if c.get("provenance", {}).get("model")
                ]
                wire_calls = [c for c in calls if c.get("provenance", {}).get("wire")]
                unproven_reported = [
                    c for c in reported_calls if not c.get("provenance", {}).get("wire")
                ]
                run.mcp_assertion = (
                    "passed"
                    if wire_calls
                    and not unproven_reported
                    and all(
                        c.get("status") == "completed"
                        and c.get("wire_request") is not None
                        and c.get("wire_response") is not None
                        for c in wire_calls
                    )
                    else "failed"
                )
            if run.harness == "claude-code":
                transport = transport_for_server(
                    rev.mcp_json.get("mcpServers", {}).get(run.enabled_server) or {}
                )
                trace = build_claude_trace(
                    events=result.event_records or result.events,
                    protocol_events=result.protocol_events,
                    transport=transport,
                    status=result.status,
                    cost_usd=result.cost_usd,
                    session_id=result.session_id,
                    selected_server=run.enabled_server,
                )
                safe_trace = redact_for_persistence(
                    trace, config=config, path="$.trace"
                )
                db.merge(
                    RunTrace(
                        run_id=run.id,
                        harness=run.harness,
                        schema_version=str(safe_trace.get("schema", "claude.v2")),
                        capture_status=str(
                            safe_trace.get("capture_status", "complete")
                        ),
                        trace=safe_trace,
                    )
                )
            elif run.harness == "opencode":
                transport = transport_for_server(
                    rev.mcp_json.get("mcpServers", {}).get(run.enabled_server) or {}
                )
                trace = build_opencode_trace(
                    events=result.event_records or result.events,
                    protocol_events=result.protocol_events,
                    selected_server=run.enabled_server,
                    transport=transport,
                    status=result.status,
                    session_id=result.session_id,
                )
                safe_trace = redact_for_persistence(
                    trace, config=config, path="$.trace"
                )
                db.merge(
                    RunTrace(
                        run_id=run.id,
                        harness=run.harness,
                        schema_version=str(safe_trace.get("schema", "opencode.v2")),
                        capture_status=str(
                            safe_trace.get("capture_status", "complete")
                        ),
                        trace=safe_trace,
                    )
                )
            elif run.harness == "acp":
                trace = build_acp_trace(
                    acp_frames=result.event_records or (),
                    mcp_frames=result.protocol_events or (),
                    selected_server=run.enabled_server,
                    configured_transport=getattr(result, "configured_transport", None)
                    or result.transport,
                    instrumented_transport=getattr(
                        result, "instrumented_transport", None
                    )
                    or result.transport,
                    status=result.status,
                    session_id=result.session_id,
                    result_metadata=_snapshot_verification(db, run.id),
                )
                safe_trace = redact_for_persistence(
                    trace, config=config, path="$.trace"
                )
                db.merge(
                    RunTrace(
                        run_id=run.id,
                        harness="acp",
                        schema_version=trace["schema"],
                        capture_status=trace["capture_status"],
                        trace=safe_trace,
                    )
                )
            db.commit()
        except Exception as e:
            exception_message = redact_for_log(
                str(e), config=config, path="$.run.error"
            )
            run.status, run.error_message, run.finished_at = (
                "failed",
                exception_message,
                now(),
            )
            if run.harness == "acp" and result is not None:
                snapshot = db.get(RunHarnessSnapshot, run.id)
                if snapshot and (
                    result.error or result.error_code or result.error_details
                ):
                    safe_error = redact_for_log(
                        result.error, config=config, path="$.run.failure.message"
                    )
                    safe_details = redact_for_persistence(
                        result.error_details or {},
                        config=config,
                        path="$.run.failure.diagnostics",
                    )
                    verification = dict(snapshot.verification or {})
                    verification["failure"] = {
                        "code": result.error_code or "acp_run_failed",
                        "phase": result.error_phase or "execution",
                        "message": safe_error or str(e),
                        "diagnostics": safe_details,
                    }
                    snapshot.verification = verification
            # Preserve a useful failed trace even when process setup failed.
            if result is not None and run.harness == "claude-code":
                transport = transport_for_server(
                    rev.mcp_json.get("mcpServers", {}).get(run.enabled_server) or {}
                )
                trace = build_claude_trace(
                    events=result.event_records or result.events,
                    protocol_events=result.protocol_events,
                    transport=transport,
                    status="failed",
                    cost_usd=result.cost_usd,
                    session_id=result.session_id,
                    selected_server=run.enabled_server,
                )
                safe_trace = redact_for_persistence(
                    trace, config=config, path="$.trace"
                )
                db.merge(
                    RunTrace(
                        run_id=run.id,
                        harness=run.harness,
                        schema_version=str(safe_trace.get("schema", "claude.v2")),
                        capture_status="partial",
                        trace=safe_trace,
                    )
                )
            elif result is not None and run.harness == "opencode":
                transport = transport_for_server(
                    rev.mcp_json.get("mcpServers", {}).get(run.enabled_server) or {}
                )
                trace = build_opencode_trace(
                    events=result.event_records or result.events,
                    protocol_events=result.protocol_events,
                    selected_server=run.enabled_server,
                    transport=transport,
                    status="failed",
                    session_id=result.session_id,
                )
                safe_trace = redact_for_persistence(
                    trace, config=config, path="$.trace"
                )
                db.merge(
                    RunTrace(
                        run_id=run.id,
                        harness=run.harness,
                        schema_version=str(safe_trace.get("schema", "opencode.v2")),
                        capture_status="partial",
                        trace=safe_trace,
                    )
                )
            elif result is not None and run.harness == "acp":
                trace = build_acp_trace(
                    acp_frames=result.event_records or (),
                    mcp_frames=result.protocol_events or (),
                    selected_server=run.enabled_server,
                    configured_transport=getattr(result, "configured_transport", None)
                    or result.transport,
                    instrumented_transport=getattr(
                        result, "instrumented_transport", None
                    )
                    or result.transport,
                    status="failed",
                    session_id=result.session_id,
                    result_metadata=_snapshot_verification(db, run.id),
                )
                safe_trace = redact_for_persistence(
                    trace, config=config, path="$.trace"
                )
                db.merge(
                    RunTrace(
                        run_id=run.id,
                        harness="acp",
                        schema_version=trace["schema"],
                        capture_status="partial",
                        trace=safe_trace,
                    )
                )
            db.commit()
        finally:
            with self.lock:
                self.runners.pop(run_id, None)
                event = self.done_events.pop(run_id, None)
                if event:
                    event.set()
            db.close()

    def cancel(self, run_id: str) -> None:
        with self.lock:
            r, done = self.runners.get(run_id), self.done_events.get(run_id)
            r and r.request_cancel()
        if done:
            done.wait(timeout=3)
        db = self.session_factory()
        run = db.get(Run, run_id)
        if run and run.status == "queued":
            run.status, run.finished_at = "cancelled", now()
            db.commit()
        db.close()

    def shutdown(self) -> None:
        with self.lock:
            for runner in self.runners.values():
                if runner is not None:
                    runner.request_cancel()
        self.executor.shutdown(wait=True, cancel_futures=True)


__all__ = ["RunManager"]
