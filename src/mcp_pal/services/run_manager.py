"""FIFO execution, cancellation, and persistence orchestration for runs."""
import asyncio, threading
from concurrent.futures import ThreadPoolExecutor
from ..harness.base import RunSpec
from ..harness.claude_cli import ClaudeCodeRunner
from ..harness.opencode_cli import OpenCodeRunner
from ..domain.events import derive_mcp_assertion
from ..persistence.models import McpProfileRevision, Run, RunEvent, RunTrace, now
from ..trace.claude import build_claude_trace, transport_for_server
from ..trace.normalized import build_opencode_trace
from ..trace.redaction import redact

class RunManager:
    def __init__(self, session_factory, settings):
        self.session_factory, self.settings = session_factory, settings; self.executor = ThreadPoolExecutor(max_workers=1); self.runners={}; self.done_events={}; self.lock=threading.Lock()
    def submit(self, run_id): self.executor.submit(self.execute, run_id)
    def runner_for(self, harness):
        if harness == "claude-code": return ClaudeCodeRunner(self.settings.claude_executable)
        if harness == "opencode": return OpenCodeRunner(self.settings.opencode_executable,self.settings.opencode_api_key,self.settings.opencode_provider_credentials())
        raise ValueError(f"Unsupported harness: {harness}")
    def execute(self, run_id):
        db=self.session_factory(); run=db.get(Run,run_id)
        if not run or run.status != "queued": db.close(); return
        run.status,run.started_at="running",now(); db.commit(); rev=db.get(McpProfileRevision,run.profile_revision_id); cancel=asyncio.Event(); runner=self.runner_for(run.harness); done=threading.Event()
        with self.lock: self.runners[run_id],self.done_events[run_id]=runner,done
        seq=db.query(RunEvent).filter_by(run_id=run_id).count()
        async def callback(raw,event_type,payload):
            nonlocal seq
            seq+=1
            safe_payload, _ = redact(payload)
            safe_raw, _ = redact(raw)
            db.add(RunEvent(run_id=run_id,sequence=seq,event_type=event_type,payload=safe_payload,raw_event=safe_raw)); db.commit()
        result = None
        try:
            result=asyncio.run(runner.run(RunSpec(run.prompt,run.model,rev.mcp_json,run.enabled_server,run.tool_mode,run.timeout_seconds,run.max_turns,run.max_budget_usd),callback,cancel))
            safe_result, _ = redact(result.final_text)
            safe_stderr, _ = redact(result.stderr)
            run.status,run.claude_result,run.exit_code,run.error_message,run.stderr=result.status,safe_result,result.exit_code,result.error,safe_stderr; run.cost_usd,run.turns,run.session_id=result.cost_usd,result.turns,result.session_id; run.mcp_assertion=derive_mcp_assertion(result.events,run.enabled_server); run.finished_at=now()
            if run.harness == "claude-code":
                transport = transport_for_server((rev.mcp_json.get("mcpServers", {}).get(run.enabled_server) or {}))
                trace = build_claude_trace(events=result.event_records or result.events, protocol_events=result.protocol_events, transport=transport, status=result.status, cost_usd=result.cost_usd, session_id=result.session_id, selected_server=run.enabled_server)
                safe_trace, _ = redact(trace)
                db.merge(RunTrace(run_id=run.id, harness=run.harness, schema_version=str(safe_trace.get("schema", "claude.v1")), capture_status=str(safe_trace.get("capture_status", "complete")), trace=safe_trace))
            elif run.harness == "opencode":
                transport = transport_for_server((rev.mcp_json.get("mcpServers", {}).get(run.enabled_server) or {}))
                trace = build_opencode_trace(events=result.event_records or result.events, selected_server=run.enabled_server, transport=transport, status=result.status, session_id=result.session_id)
                safe_trace, _ = redact(trace)
                db.merge(RunTrace(run_id=run.id, harness=run.harness, schema_version=str(safe_trace.get("schema", "opencode.v1")), capture_status=str(safe_trace.get("capture_status", "complete")), trace=safe_trace))
            db.commit()
        except Exception as e:
            run.status,run.error_message,run.finished_at="failed",str(e),now()
            # Preserve a useful failed trace even when process setup failed.
            if result is not None and run.harness == "claude-code":
                transport = transport_for_server((rev.mcp_json.get("mcpServers", {}).get(run.enabled_server) or {}))
                trace = build_claude_trace(events=result.event_records or result.events, protocol_events=result.protocol_events, transport=transport, status="failed", cost_usd=result.cost_usd, session_id=result.session_id, selected_server=run.enabled_server)
                safe_trace, _ = redact(trace)
                db.merge(RunTrace(run_id=run.id, harness=run.harness, schema_version=str(safe_trace.get("schema", "claude.v1")), capture_status="partial", trace=safe_trace))
            elif result is not None and run.harness == "opencode":
                transport = transport_for_server((rev.mcp_json.get("mcpServers", {}).get(run.enabled_server) or {}))
                trace = build_opencode_trace(events=result.event_records or result.events, selected_server=run.enabled_server, transport=transport, status="failed", session_id=result.session_id)
                safe_trace, _ = redact(trace)
                db.merge(RunTrace(run_id=run.id, harness=run.harness, schema_version="opencode.v1", capture_status="partial", trace=safe_trace))
            db.commit()
        finally:
            with self.lock: self.runners.pop(run_id,None); event=self.done_events.pop(run_id,None); event and event.set()
            db.close()
    def cancel(self,run_id):
        with self.lock: r,done=self.runners.get(run_id),self.done_events.get(run_id); r and r.request_cancel()
        if done: done.wait(timeout=3)
        db=self.session_factory(); run=db.get(Run,run_id)
        if run and run.status=="queued": run.status,run.finished_at="cancelled",now(); db.commit()
        db.close()
    def shutdown(self):
        with self.lock:
            for runner in self.runners.values(): runner.request_cancel()
        self.executor.shutdown(wait=True,cancel_futures=True)

__all__ = ["RunManager"]
