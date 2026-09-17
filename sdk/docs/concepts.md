# Concepts

## Server definition, kit, and client

A server definition such as `HTTPServer` or `StdioServer` describes
how to reach an MCP server. It does not connect when it is constructed. Use
`HTTPServer` for a deployed MCP endpoint and `StdioServer` for a
local subprocess. The shared
[`example_server` fixture](../examples/tests/conftest.py) shows a stdio binding
to a Python subprocess.

`MCPTestKit` is the outer runtime and cleanup boundary. `kit.direct(server)`
creates a direct client for one MCP connection. Entering the client starts the
transport and performs MCP initialization; leaving it closes the connection
and its owned subprocess. Keep calls that depend on server session state inside
the same client context. The lifecycle behavior is executable in
[`test_tracing_and_lifecycle.py`](../examples/tests/test_tracing_and_lifecycle.py).

## Sync and async APIs

Use `MCPTestKit` in synchronous tests and `AsyncMCPTestKit` in async tests. The
direct operations have matching shapes, but calls on the async client are
awaited. Compare the synchronous
[`test_quick_start.py`](../examples/tests/test_quick_start.py) with
[`test_async_usage.py`](../examples/tests/test_async_usage.py).

Choose the API that matches the surrounding application or test. There is no
need to create an event loop inside a synchronous test or move an async test
through a synchronous wrapper.

## Direct protocol testing

The examples exercise MCP operations directly: initialize, discover tools,
call tools, list and read resources, and list and render prompts. This gives
deterministic protocol-level assertions without involving an LLM or agent
harness. Resource and prompt coverage lives in
[`test_resources_and_prompts.py`](../examples/tests/test_resources_and_prompts.py).

For HTTP direct, agent, and matrix patterns, see the
[Streamable HTTP guide](http.md) and its external endpoint example
[`test_streamable_http.py`](../examples/nondeterministic/test_streamable_http.py).
For an agent-driven local workflow, see the deterministic ACP harness example
[`test_harness_trace_view.py`](../examples/tests/test_harness_trace_view.py).
Harness assertions verify typed finalized `TraceView` tool calls rather than
trusting model prose.

## Test matrices

`ToolMatrix` directly invokes known tools with known arguments. It does not
prompt a harness or test which tool an agent selects. Put each tool under the
`ServerCase` that owns it; this gives deterministic MCP contract coverage
across servers and tools. A ToolMatrix case answers practical questions such
as:

- Are the accepted arguments correct?
- Does the tool return the expected structured output?
- Does a normal MCP tool error arrive as a typed tool result?
- Does schema validation accept and reject the right inputs and outputs?
- Are trace, timing, and persistence records captured as expected?
- Does the same contract hold across multiple servers or server versions and
  configurations?

Each case runs through the normal SDK execution boundary and returns the usual
`ExecutionResult`.

`HarnessMatrix` sends prompts to Claude Code, OpenCode, Codex, Pi, or ACP and tests which
tool the harness chooses and how it uses that tool. Choose the shape that
matches the question:

- `each_server` creates a server × harness case and lets the harness choose
  from that selected server's listed tools.
- `each_tool` creates a server-owned-tool × harness case and restricts the
  harness to that selected tool.
- `all_servers` creates one all-servers × harness case, useful for workflows
  that move between servers.
- `trials=3` repeats each cell as three independent pytest items and SDK
  executions.

Use `@matrix.parametrize()` for ordinary pytest collection, stable case IDs,
marks, fixtures, and `pytest -k`; use `.cases()` at any other boundary. Matrix
construction and expansion perform no MCP, harness, subprocess, network, or
persistence work. Work begins only when a case helper such as `run()` or
`session()` is called. Sync and async cases use the matching kit helpers.

The SDK derives restrictive tool policies from each case. OpenCode, Codex, Pi, and ACP
receive qualified `server:tool` allowlists; Claude Code uses its native
server-scoped MCP policy, so exact tool restriction is not portable. Claude
Code is therefore not supported for `all_servers` with multiple servers.

Every cell has stable matrix metadata such as its case ID, mode, servers,
harness, tool, and trial. Normal one-turn and multi-turn execution traces can
be persisted through the existing SQLite execution store; a multi-turn matrix
session remains one execution containing all turns. The MCP Pal pytest plugin
persists pytest outcomes in internal run records and matcher checks as
execution evaluations; ordinary direct SDK use does not. Matrix summary rows
are not persisted. Use
`store.aggregate_evaluations(...)` to calculate matrix and run summaries from
saved evaluations.

## Tool errors and exceptions

An MCP server can successfully answer `tools/call` while reporting that the
tool itself failed. That is a `ToolCallResult` with `is_error=True`, not a
Python exception. Assert its returned content as shown in
[`test_assert_an_expected_tool_error`](../examples/tests/test_errors_and_contracts.py).

Transport failures, protocol failures, timeouts, and local validation failures
are exceptions. Keeping these two paths distinct lets a test say whether the
server was unreachable or the requested operation produced an expected domain
error.

## Structured output and schema validation

`ToolCallResult.structured_content` exposes the MCP tool's structured result
without parsing its text representation. Tools may also advertise input and
output JSON Schemas. Pass `validate_schemas=True` to `kit.direct(...)` when a
test should enforce those contracts locally. Valid data-driven cases and an
invalid-input assertion are executable in
[`test_errors_and_contracts.py`](../examples/tests/test_errors_and_contracts.py).

Schema validation is opt-in. A validation mismatch raises
`ModelValidationError`; it is not represented as `is_error=True` because the
SDK rejected a contract mismatch rather than receiving a tool-error result.

## Chained, stateful workflows

A chained test keeps one direct client open and feeds structured output from
one tool into the arguments of the next. Because all calls share one MCP
connection and server process, the workflow may also verify state written by
an earlier call.

The complete
[`test_chained_workflow.py`](../examples/tests/test_chained_workflow.py)
normalizes a customer, passes that identifier into order creation, then passes
the returned order identifier into retrieval and asserts the stored record.
This is a multi-step protocol workflow, not a conversational agent session:
the test explicitly controls every call and transition.

## Finalized traces

The SDK records a `TraceResult` and derives the public immutable
`TraceView` from it. `TraceResult.events` is useful for storage and auditing;
application and test assertions should use `trace.view()` (or
`result.trace_view`). Projection is finalized-only: attempting to project an
open trace raises `TraceNotFinalized`. The finalized view has one ordered
`timeline` plus indexes such as `messages`, `reasoning`, `tool_calls`,
`protocol`, `interactions`, `processes`, and `raw_messages`. Use `for_turn`,
`for_session`, `for_server`, and `between` to make filtered views.

Every observed value is an `Observation`: `OBSERVED` has a value,
`NOT_EMITTED` means the source did not provide the field, `UNAVAILABLE` means
capture or correlation failed, and `UNSUPPORTED` means the harness cannot
expose it. `PROVIDER_HIDDEN` and `ENCRYPTED` preserve provider-hidden and
encrypted reasoning without inventing plaintext; reasons such as
`MALFORMED_SOURCE` and states such as `TRUNCATED` or `REDACTED` preserve other
limitations. A finalized-only projection can raise `TraceNotFinalized` while
an unavailable persisted trace can raise `TraceUnavailable`; an open trace is
not assumed to have a usable view.
Inspect both `state` and (when present) `reason`; never treat `value=None` as
the only availability signal.

Tool entries retain `wire` and `reported` evidence separately. A resolved
entry uses wire authority when the two correlate, while `conflicts` records
disagreements instead of hiding them. Messages and reasoning are typed
entries; encrypted or provider-hidden reasoning is represented by its state,
not guessed plaintext. Runtime metadata is discriminated by `runtime.kind`:
`direct`, `opencode`, `claude_code`, `codex`, `pi`, or `acp`, and each variant exposes only
fields that source can truthfully provide.

Raw provider/MCP/process evidence is bounded and redacted before persistence.
When a `raw_messages` entry has an `evidence_ref`, read it through the kit or
store `read_raw_evidence` API; preview state distinguishes observed, redacted,
and truncated content. Sync and async kits project the same typed shape.
Harness-specific fields may legitimately be `NOT_EMITTED` or `UNSUPPORTED`
(for example ACP usage), and failed, timed-out, or cancelled traces retain
partial evidence and limitations without invented provider, usage, reasoning,
HTTP, or process facts. See
[`test_typed_trace_view.py`](../examples/tests/test_typed_trace_view.py).

When a harness reports cost, a test can check a budget on the finalized trace:

```python
usage = session.result.trace_view.summary.usage.value
assert usage is not None, "harness did not report usage"
assert usage.cost.value is not None, "harness did not report cost"
assert usage.cost.value < 100.0
```

Cost and currency are provider-reported observations; some harnesses omit
either. `summary.usage` reflects the latest usage entry, so check the source's
reporting semantics before treating it as a total across turns. The runnable
single-turn example is
[`test_live_opencode.py`](../examples/tests/test_live_opencode.py).

An agent `session.send(...)` returns a terminal `TurnResult` for that turn.
After the session closes, use `session.result` for finalized assertions and
`session.result.trace_view` for the immutable view. `TurnResult` (and its
`turn_id`), `TurnState`, `TurnId`, and string IDs are accepted by
`TraceView.for_turn(...)` and matcher `turn=` selectors; a turn result does not
have its own `trace_view`. Assertions against an open execution remain subject
to finalized-only errors.

## Optional and CLI-managed persistence

SDK persistence is selected at the toolkit boundary:

- With no `store`, `MCPTestKit` and `AsyncMCPTestKit` retain execution data in
  memory only. This is the default for direct SDK and pytest use.
- Passing `SQLiteExecutionStore(path)` as `store=` makes those executions
  saved and reopenable by execution ID.
- `mcp-pal test` always supplies SQLite storage for otherwise unconfigured
  kits. It invokes pytest with the SDK plugin and
  `--mcp-pal-results-db PATH`; the CLI default path is
  `.mcp-pal/executions.sqlite` in the project.
- Direct pytest users may opt into the same behavior explicitly with
  `-p mcp_pal.pytest_plugin --mcp-pal-results-db PATH`.

`SQLiteExecutionStore` and the pytest database flag require the optional
`mcp-pal[storage]` dependency; `mcp-pal[pytest,storage]` installs both direct
pytest support and SQLite storage.

The pytest flag installs a default store factory. An explicit `store=` passed
to a kit still takes precedence, so a test can choose an isolated database or
another execution store.

The SQLite execution store currently persists:

- immutable execution specifications, snapshots, and binding revisions;
- recorded execution events and finalized complete or partial traces;
- agent sessions and turns;
- redacted artifacts, blob metadata, and raw-evidence references;
- worker leases, commands, and cancellation state used by persistent runs.

It does not currently persist pytest collection/session details, pytest item
pass/fail/skip outcomes, ordinary Python assertion results, or aggregate
matrix/trial summary rows. Use `store.aggregate_evaluations(...)` for rates.
Evaluations created through `kit.evaluate()` are
saved when the kit explicitly receives `store=SQLiteExecutionStore(path)`
or pytest is run with `--mcp-pal-results-db PATH`; otherwise they remain in
memory.

Execution lifecycle, MCP activity, and evaluation verdicts are different
facts. A persisted `completed` execution therefore must not be counted as a
passed test or evaluation unless an explicit verdict has also been recorded.

## Determinism and isolation

The example server is local, contains no network or model dependency, and
returns deterministic values. Each stdio client starts a fresh subprocess, so
state is retained across chained calls on one connection but does not leak to
the next connection. The isolation and process-cleanup assertions are in
[`test_tracing_and_lifecycle.py`](../examples/tests/test_tracing_and_lifecycle.py).

Use the same pattern in a project: control fixtures and avoid shared external
state when testing protocol behavior. Select explicit SQLite storage when
opening saved data again is part of the test, or use `mcp-pal test` when CLI-managed run
history and the local viewer are wanted.
