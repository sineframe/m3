---
name: testing-with-mcp-pal
description: Use when installing or setting up the MCP Pal CLI, or when writing, reviewing, running, or debugging Python tests for MCP servers, MCP tool calls, coding-agent tool selection, traces, or harnesses with the MCP Pal SDK
---

# Testing with MCP Pal

## Overview

Choose the test boundary from the claim: direct tests prove MCP server behavior;
harness tests prove how an agent uses that server. Assert captured calls, not an
agent's prose.

**REQUIRED REFERENCE:** Read [references/test-patterns.md](references/test-patterns.md)
before implementing a test.

When the task involves installing or setting up the standalone CLI, or intends
to use the CLI and the `mcp-pal` command is missing, read
[references/cli-runner.md](references/cli-runner.md). The project SDK and CLI
are separate installations; never assume `uv add "mcp-pal[pytest]"` provides
the CLI or UI.

## Choose the Boundary

| Claim | Use |
|---|---|
| Server advertises the right schema or returns the right value | `MCPTestKit.direct()` |
| Agent selects and calls the right tool | Mark a test with `@pytest.mark.mcp_pal`, request `agent`, then assert captured calls |
| Known calls should work across tools or servers | `ToolMatrix` |
| Prompts should work across harnesses or servers | Marked `agent` plus CLI selections, or `kit.agents([...])` in Python |
| Surrounding test is async | `AsyncMCPTestKit` |

## Choose the transport

| Target | Definition |
|---|---|
| Deployed MCP endpoint | `HTTPServer` |
| Local command you own | `StdioServer` |
| Existing legacy HTTP+SSE endpoint | `SSEServer` |

For HTTP, the URL is one MCP protocol endpoint, not a REST route. Keep
credentials out of URLs and query parameters: static non-secret headers may be
declared on `HTTPServer`, while direct-client bearer authentication
uses a `SecretReference` with `kit.direct(..., bearer_token=...)`.
This is a direct-client option; agent tests receive the selected `agent` fixture.
A public endpoint exposed to an agent requires `TrustLevel.PUBLIC`; private or
localhost endpoints you own require
`TrustLevel.TRUSTED_PRIVATE`. These labels describe ownership and exposure,
not validation bypasses. `MCPTestKit` owns connection/client cleanup and trace
finalization but does not start or stop a deployed service.

## Workflow

1. Inspect the target project's server command, existing fixtures, tool schema,
   expected output, and installed MCP Pal version. Never invent contract values.
   For an uncovered API, inspect the installed public object or signature rather
   than guessing or relying on a plan.
2. Reuse its fixture and pytest conventions.
3. Write the smallest test that proves the requested claim.
4. For an agent test, identify its model provider's required credential
   variable **name**. Common routes use `OPENCODE_API_KEY`, `OPENAI_API_KEY`,
   or `ANTHROPIC_API_KEY`. Export the variable or use `mcp-pal test --env-file
   .env`; the file is never loaded implicitly. A custom source uses
   `--credential-env TARGET=SOURCE`, optionally
   `KIND:TARGET=SOURCE` for one harness. Keep MCP server authentication in
   `HTTPServer.headers` or a direct-client bearer reference. Never request,
   print, log, or put a secret value into a test, command argument, or report.
5. Choose the runner. Prefer `mcp-pal test -- <pytest arguments>` when the
   separately installed CLI is available and `mcp-pal doctor` reports that the
   project is ready. It runs the same pytest tests while recording MCP Pal
   executions to SQLite. Use direct pytest when the CLI is unavailable or the
   project already has a specific test command; direct SDK/pytest use is
   in-memory unless the test passes `SQLiteExecutionStore` or pytest installs
   the MCP Pal plugin with `--mcp-pal-results-db`. Do not install the optional
   CLI merely to run one test unless CLI setup is part of the task. Add `--ui`
   only when the user wants the local viewer; it keeps the command open until
   interrupted.
6. Run the narrow test and report nondeterministic external/provider tests
   separately from deterministic contract tests. Follow the target project's
   isolation convention; this repository isolates its external example by
   placing it under `examples/nondeterministic/`.

## Invariants

- `MCPTestKit` owns runtime cleanup. Keep direct calls inside the direct-client
  context.
- Assert operation results directly. Read `client.final_trace` only after the
  client closes.
- `session.send()` returns a `TurnResult`. After the session closes, assert
  against `session.result`, optionally scoped with `turn=turn`.
- For local debugging, `view.model_dump(mode="json")` exposes the complete
  JSON-compatible public trace view. Use typed fields for assertions and
  do not print whole traces to shared logs; messages, arguments, and results may
  contain sensitive application data.
- For cost budgets, use finalized `trace_view.summary.usage`, require an
  observed cost, and check currency when the provider emits it. See
  [trace metadata](references/test-patterns.md#inspect-trace-metadata).
- Agent execution `timeout=` is a full startup, turn, and cleanup deadline;
  `handle.result(timeout=...)` is wait-only. For diagnosis, consume
  `handle.events()` and log only `sequence`, `kind`, and `lifecycle_phase`.
  Timeout diagnostics expose safe `stage`, `operation`, elapsed seconds, and
  configured seconds in the finalized trace. The pytest/CLI path writes the
  same fields to `.mcp-pal/reports/<run-id>/traces/` and prints the feedback
  path. Supply provider credentials with `--env-file` or ambient variables;
  credential values are excluded from summaries and feedback. Do not log event
  payloads.
- A tool-selection test must inspect finalized wire evidence. The default
  matcher evidence is `"wire"`; request `"reported"` only when comparing sources.
- If policy exposes only the expected tool, the test proves tool use, not tool
  choice. Preserve realistic safe alternatives when selection is the claim.
- Tool failures are results with `is_error=True`; transport and local schema
  failures are exceptions. Schema checking requires `validate_schemas=True`.
- Use environment variables or explicit `--env-file` for provider credentials;
  use `SecretReference` for MCP endpoint credentials. Never embed or log values.
  Keep nondeterministic external/provider tests separate from deterministic
  tests because they may change or incur provider usage.
- The plugin supplies `agent` for a marked test. It never invents a server;
  define one explicitly with `StdioServer`, `HTTPServer`, `SSEServer`, or an
  existing project fixture.
- With the MCP Pal pytest plugin active, SQLite retains internal run records,
  pytest item outcomes, phase diagnostics, and exact execution associations;
  MCP Pal matcher checks are retained as execution evaluations. This is in
  addition to execution specs/snapshots, events/traces,
  sessions/turns, artifacts/evidence, and explicit evaluations. A plain
  `print()` or log line remains diagnostic text and is never a score. Never
  infer a pass from lifecycle `completed`; use an explicit evaluator or saved
  pytest/check result.

## Evaluations and saved history

Evaluators are explicit runtime callbacks. Register them on the kit and invoke
`kit.evaluate(...)`; `ExecutionSpec.evaluations` is retained as portable
metadata and is intentionally not executed across worker-process boundaries.
Use a stable, versioned evaluator name such as `project.answer-quality.v1`.

`EvaluationDecision` can carry a normalized `score`, rationale, metrics, and
model/rubric source details. Built-ins include
`mcp_pal.execution.completed.v1`, `mcp_pal.tool_call.succeeded.v1`, and
`mcp_pal.output.has_text.v1`. When using `store=SQLiteExecutionStore(...)` or
`--mcp-pal-results-db`, evaluations attached to an execution are saved and can
be queried after reopening SQLite; in-memory SDK storage remains ephemeral.

Use `--trials N`, marker `trials=N`, or `kit.agents(..., trials=N)` for
independent measured attempts. Invoke the evaluator once per execution or
turn, then aggregate its records. Ordinary pytest parameters or a Python loop
vary logical prompts. A marked test's ordinary parameters retain stable case
identity across harnesses and trials. The bound agent exposes advertised MCP
tools when `tools` is omitted; `tools=[]` denies MCP tools. When evaluating a
final answer after several turns, use `agent.session(...)` and inspect the
finalized `session.result.trace_view`.

Call `store.aggregate_evaluations(EvaluationQuery(...))` for pass rates,
status counts, run/time trends, case labels, and execution/tool health. Filter
to one evaluator or group by `evaluator`; deterministic and user-supplied LLM
evaluators must not be mixed into one pass rate. MCP Pal has no built-in LLM
judge: SDK users may write callbacks that call an LLM, while API v2 only groups
their saved results and provenance. Summaries are calculated on request.

When improving tool descriptions, run a representative suite through
`mcp-pal test`. The command prints a run ID and writes
`.mcp-pal/reports/<run-id>/feedback.json`. After a focused server edit, run the
same selection with `--baseline RUN_ID`; inspect interface changes, matched
checks, execution references, and regressions before making another edit. The
JSON bundle is the agent-facing format. Keep prompts, policies, expectations,
and harness/model settings fixed while testing a description change, and treat
missing or incomplete evidence as a limitation rather than an improvement.

## Common Mistakes

| Mistake | Correction |
|---|---|
| Checking final prose to infer tool use | Assert `to_have_tool_call()` |
| Reading a trace while its client/session is open | Close it first |
| Writing a custom trace serializer | Use `view.model_dump(mode="json")` |
| Parsing text when structured output exists | Assert `structured_content` |
| Restricting selection to one possible tool | Include safe competing tools |
| Missing model provider credential | Export the expected variable or pass `--env-file`; use `--credential-env TARGET=SOURCE` for custom names |
| Assuming the SDK installs the `mcp-pal` command | Install the standalone CLI separately |
| Passing pytest flags directly to `mcp-pal test` | Put them after `--` |
| Assuming direct pytest writes run history | Pass `store=SQLiteExecutionStore(...)` or load the plugin with `--mcp-pal-results-db` |
| Treating a completed persisted execution as a passed test | Record or inspect an explicit test/evaluation verdict |
| Retrying a failed nondeterministic case until it passes | Keep every attempt as a scored trial |
| Assuming `tools=[]` exposes all tools | Omit `tools` or pass `tools=None` |
