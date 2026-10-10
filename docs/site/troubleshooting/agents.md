---
title: "Agent readiness, approvals, and timeouts"
description: "Use m3 doctor --require harness:NAME for a system runtime, or select a managed runtime/version supported on the current OS and CPU."
---

# Agent readiness, approvals, and timeouts

## Agent test requires --harness

pytest stops during collection with
`agent test requires --harness or m3(agents=[...])` when a test requests the
`agent` fixture and nothing selects a harness for it. Select one in either
place:

- on the command line: `m3 test --harness KIND=MODEL`
- on the test: `pytest.mark.m3(agents=[...])`

A test can also skip the fixture and select its agent with
`kit.agents([...])`, as [the first agent test](../guides/agents/first-test.md)
does. That test runs under plain pytest with no flags.

`-k` does not avoid the error, because collection fails before deselection.
Keep agent tests in their own file so that direct tests can run by path
without a harness. See [the pytest plugin reference](../reference/pytest.md).

## Harness executable is unavailable

Use `m3 doctor --require harness:NAME` for a system runtime, or select a managed
runtime/version supported on the current OS and CPU.

## Codex has no login or rejects the credentials

Codex needs either an API key or a ChatGPT subscription login. Startup fails
with `Codex has no login` when it has neither: map a key with
`--credential-env codex:OPENAI_API_KEY=SOURCE`, or sign in to Codex on the host
and run without a mapped key. When a turn fails with `Codex turn failed: model
provider rejected the credentials (HTTP 401)`, the mapped key or the copied
login was refused by the model provider. See
[credentials](../reference/credentials.md#native-harnesses).

A failed agent turn ends the execution with the harness's own reason in
`result.error.message` and `result.error.details["cause"]`. Codex failures add
`reason` and `http_status` to the details. `session lost during turn` means the
harness gave no reason.

## Tool call was denied

Approval is separate from MCP elicitation. The test's tool selection decides
which tools on its bound servers the agent may call: omitting `tools` allows
every tool the server advertises, `tools=[...]` allows only the named ones, and
`tools=[]` allows none.

Codex asks for approval before each MCP tool call. M3 answers it from that tool
selection, so a Codex test needs no permission setting; a tool outside the
selection is declined even if `permission_policy="allow"` is set. If Codex
lists tools but makes no call, check the `tools` selection and the prompt.

Codex also asks before running a command outside its sandbox, editing files
outside the workspace, or widening sandbox permissions (for example network
access). M3 answers these from `permission_policy`, so they are declined by
default and the model carries on without them. The trace records each one as a
`permission.request`/`permission.response` pair. Pass `permission_policy="allow"`
only for a trusted agent and a scoped workspace.

ACP agents are different: every permission request they send, including one
for an MCP tool call, goes to `permission_policy`, which denies by default.
For an ACP agent that asks before calling tools, pass
`permission_policy="allow"` to `agent.run(...)` or `agent.session(...)`. Allow
it only for a trusted agent and a scoped workspace, because the same setting
also approves the agent's requests to run commands or edit files.

## Execution timed out

Each agent execution gets 180 seconds unless you change it. `--execution-timeout
SECONDS` changes the deadline for every selected agent, but only where the test
doesn't pass its own `timeout=`. A `timeout=` in `agent.run()`, `agent.submit()`,
or `agent.session()` always wins, so raising the flag does nothing for those
calls. Change the value in the test instead.

Inspect the trace and harness diagnostics before increasing either value; a
server waiting for input or approval will not be fixed by an arbitrary larger
value.

An explicit tool request that times out while waiting for a harness response
may be an unanswered Codex MCP approval request. `agent.run(..., timeout=...)`
covers startup, turns, and cleanup; `handle.result(timeout=...)` limits only
waiting. Diagnose with trace diagnostics: stage, operation, elapsed time, and
configured seconds.
