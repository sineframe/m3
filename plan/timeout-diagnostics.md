# Timeout diagnostics for agent executions

## Goal and current behavior

A timed-out agent execution must identify the operation in progress, retain that evidence in a partial trace, and expose it through the SDK, API, CLI feedback bundle, and UI. The current 300-second limit belongs to `scripts/live_ui_gate.py`; it kills a child process and cannot reliably finalize a trace. The SDK's current `agent.run(..., timeout=...)` bounds the turn but not startup. `ExecutionHandle.result(timeout=...)` only bounds the caller's wait.

## Timeouts and precedence

- Make `agent.run(..., timeout=SECONDS)` and `agent.submit(..., timeout=SECONDS)` apply one execution deadline across workspace/server startup, harness startup, the turn, and bounded cleanup. Give the simple agent API a finite 180-second default; permit an explicit positive override and document how to disable it for long-running work. Preserve `handle.result(timeout=...)` as a wait-only timeout.
- Add `mcp-pal test --execution-timeout SECONDS` as the default for each selected harness/model/trial execution. An explicit per-call SDK timeout wins. Each case gets its own deadline; it is not a shared pytest-process budget. Pass the CLI option through the supervisor and pytest plugin into selected agents, including agent selections used inside ToolMatrix tests.
- Keep the live gate's separate hard-process limit at 300 seconds by default, and add `scripts/live_ui_gate.py --process-timeout SECONDS`. Its value must exceed the execution deadline plus cleanup allowance in the diagnostic live run. Do not imply this gate-only option is an `mcp-pal test` option.
- Validate positive finite seconds and report invalid values without including credentials or command-line secrets.

## Diagnostic event and trace contract

- Emit committed, redacted `diagnostic` events on entry to and exit from workspace/MCP-server startup, harness startup/session creation, turn submission, waiting for the harness response, and cleanup. For OpenCode, identify the response wait as `opencode.session_message` without recording the URL, request, prompt, tool arguments, provider response, or credentials.
- On deadline expiry emit `code=operation_timeout`, the active `stage` and `operation`, elapsed and configured seconds, and a short safe message. Preserve event ordering and the last started stage. Describe the observed wait, not an unproven provider root cause.
- Finalize the execution as timed out. Preserve the committed events in a partial trace when capture is incomplete, with an explicit limitation. Project structured diagnostic fields into optional fields of the typed trace diagnostic entry, in addition to its current code and message. Update the schema/version and compatibility tests for this additive trace change.
- Bound cancellation and cleanup separately so a normal deadline can produce a terminal record before a longer external process limit. Document that an uninterruptible process kill cannot guarantee trace finalization.

Example final event:

```json
{
  "kind": "diagnostic",
  "lifecycle_phase": "turn",
  "payload": {
    "code": "operation_timeout",
    "stage": "waiting_for_harness_response",
    "operation": "opencode.session_message",
    "elapsed_seconds": 5.0,
    "timeout_seconds": 5.0,
    "message": "Timed out waiting for the OpenCode session message response"
  }
}
```

## SDK, API, CLI, and UI access

- SDK: keep blocking `agent.run(...)` simple and return a timed-out result with trace. `agent.submit(...)` exposes live committed events via `handle.events()`; document a safe example that prints stage, operation, elapsed time, and execution ID. `kit.get_trace(execution_id)` remains available after finalization when a store is configured.
- API after completion: `/api/v2/executions/{id}/report` already returns `report.events` and `trace`; the new fields appear in both. This is an additive API response change. Keep the existing report envelope and terminal-only report behavior unchanged.
- UI: no UI implementation is included in this SDK/API change. Existing report readers remain compatible with the additive diagnostic fields and can display timeout stage, duration, and trace limitation in a later UI change.
- CLI: `mcp-pal test` already writes per-execution JSON in `executions/<id>.json` and typed trace JSON in `traces/<id>.json` under `.mcp-pal/reports/<run-id>/`. Verify the diagnostic fields in both and print a concise timeout line with execution ID, stage, elapsed seconds, and feedback path. Do not describe a nonexistent general `mcp-pal test --json` stdout mode.

## Documentation

Update the SDK quick start, plain-Python example, pytest/CLI guide, troubleshooting guide, and local testing skill. Show `timeout=` and `--execution-timeout`, the wait-only semantics of `handle.result(timeout=...)`, the separate live-gate `--process-timeout`, a safe live-event example, feedback JSON paths and fields, and existing credential setup. Do not put implementation internals in user-facing quick starts.

## Verification and investigation sequence

1. Provider-free tests deliberately stall each stage: server startup, harness startup, harness response, and cleanup. Assert deadline behavior, timed-out outcome, event order, partial-trace limitation, diagnostic stage/operation/timing, redaction, API visibility, CLI summary, and exported JSON. Also verify that `handle.result(timeout=...)` alone does not cancel an execution.
2. Add a **deliberate short-timeout end-to-end test** using a deterministic local fake harness/server whose chosen operation is known to wait longer than the configured deadline. Run it once with SDK `timeout=SMALL_SECONDS` and once through `mcp-pal test --execution-timeout SMALL_SECONDS`, with no per-call timeout in the latter. Assert that each run times out for the intended stage, writes a terminal execution and **partial trace**, and that the trace contains the stage-start event followed by `operation_timeout` with the same operation, configured deadline, and a clear capture limitation. Check the API report and the CLI feedback `executions/` and `traces/` files, not just in-memory results. Use a synchronization barrier in the fake so the test proves the intended stage was entered before the deadline; avoid timing-only sleeps as its sole assertion.
3. Verify precedence and boundaries: explicit SDK timeout over CLI default, distinct deadlines for multiple harness/model/trial cases, positive-value validation, startup deadlines, and bounded cleanup. Keep tests provider-free and deterministic.
4. After the deliberate timeout test proves the full diagnostic path, run the authorized free OpenCode live case with an execution deadline shorter than the configurable gate process limit. Retain event/log and feedback artifacts. Read the last stage-start event, the timeout event, and the partial trace to determine whether intermittent OpenCode failures occur in startup, session creation, HTTP response wait, MCP/tool activity, or cleanup. Repeat only enough to characterize intermittency; report each observed stage and distinguish observation from inferred cause. Do not run paid Codex without authorization.

Acceptance requires a deliberately triggered timeout to produce a readable partial trace in the SDK, terminal API report, and CLI feedback files; SDK handles may expose live progress events; and the OpenCode investigation must identify the observed stalled stage or state clearly why evidence remains insufficient.
