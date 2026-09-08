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
| Agent selects and calls the right tool | `MCPTestKit.agent_session()` + `expect(...).to_have_tool_call()` |
| Known calls should work across tools or servers | `ToolMatrix` |
| Prompts should work across harnesses or servers | `HarnessMatrix` |
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
This is a direct-client option, not an `AgentSpec` option.
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
4. Choose the runner. Prefer `mcp-pal test -- <pytest arguments>` when the
   separately installed CLI is available and `mcp-pal doctor` reports that the
   project is ready. It runs the same pytest tests while recording MCP Pal
   executions to SQLite. Use direct pytest when the CLI is unavailable or the
   project already has a specific test command; direct SDK/pytest use is
   in-memory unless the test passes `SQLiteExecutionStore` or pytest installs
   the MCP Pal plugin with `--mcp-pal-results-db`. Do not install the optional
   CLI merely to run one test unless CLI setup is part of the task. Add `--ui`
   only when the user wants the local viewer; it keeps the command open until
   interrupted.
5. Run the narrow test and report nondeterministic external/provider tests
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
- A tool-selection test must inspect finalized wire evidence. The default
  matcher evidence is `"wire"`; request `"reported"` only when comparing sources.
- If policy exposes only the expected tool, the test proves tool use, not tool
  choice. Preserve realistic safe alternatives when selection is the claim.
- Tool failures are results with `is_error=True`; transport and local schema
  failures are exceptions. Schema checking requires `validate_schemas=True`.
- Reference credentials with `SecretReference`; never embed or log secrets.
  Keep nondeterministic external/provider tests separate from deterministic
  tests because they may change or incur provider usage.
- There is no hidden `mcp_test` fixture or scenario format. Define the server
  explicitly with `StdioServer`, `HTTPServer`, `SSEServer`, or an
  existing project fixture.
- Persistence of an execution is not persistence of a test verdict. SQLite
  retains execution specs/snapshots, recorded events/traces, sessions/turns,
  artifacts/evidence, and explicit evaluations attached to an execution. It does
  not retain pytest item outcomes or saved summary rows. Never infer a pass from
  lifecycle `completed`; use an explicit evaluator result.

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

Matrix cases expose stable matrix/cell/trial metadata. Use the same evaluator
name across trials; aggregate statistics are derived later from raw records.
For nondeterministic harness quality, use `HarnessMatrix(..., trials=N)` as
independent measured attempts, not retry-until-success. Invoke the evaluator
once per execution or turn, then aggregate its records. When logical prompts
have different expectations, build a matrix per logical case so its trials
share a stable case identity.

Use `FullToolPolicy(acknowledge_risk=True)` only when unrestricted tool access
is part of the claim and the bound servers/tools are safe. Otherwise use an
explicit `RestrictiveToolPolicy`; its empty allowlist denies every tool. If an
evaluator reads the final assistant answer, prefer `case.session()` and the
completed `TurnResult`, then inspect tool usage on the finalized
`session.result.trace_view`.

Call `store.aggregate_evaluations(EvaluationQuery(...))` for pass rates,
status counts, run/time trends, case labels, and execution/tool health. Filter
to one evaluator or group by `evaluator`; deterministic and user-supplied LLM
evaluators must not be mixed into one pass rate. MCP Pal has no built-in LLM
judge: SDK users may write callbacks that call an LLM, while API v2 only groups
their saved results and provenance. Summaries are calculated on request.

## Common Mistakes

| Mistake | Correction |
|---|---|
| Checking final prose to infer tool use | Assert `to_have_tool_call()` |
| Reading a trace while its client/session is open | Close it first |
| Writing a custom trace serializer | Use `view.model_dump(mode="json")` |
| Parsing text when structured output exists | Assert `structured_content` |
| Restricting selection to one possible tool | Include safe competing tools |
| Assuming a parent CLI login is inherited | Use environment `SecretReference`s |
| Assuming the SDK installs the `mcp-pal` command | Install the standalone CLI separately |
| Passing pytest flags directly to `mcp-pal test` | Put them after `--` |
| Assuming direct pytest writes run history | Pass `store=SQLiteExecutionStore(...)` or load the plugin with `--mcp-pal-results-db` |
| Treating a completed persisted execution as a passed test | Record or inspect an explicit test/evaluation verdict |
| Retrying a failed nondeterministic case until it passes | Keep every attempt as a scored matrix trial |
| Assuming an empty restrictive allowlist exposes all tools | Use an explicit safe allowlist or an acknowledged full policy |
