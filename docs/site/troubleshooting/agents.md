---
title: "Agent readiness, approvals, and timeouts"
description: "Use m3 doctor --require harness:NAME for a system runtime, or select a managed runtime/version supported on the current OS and CPU."
---

# Agent readiness, approvals, and timeouts

## Harness executable is unavailable

Use `m3 doctor --require harness:NAME` for a system runtime, or select a managed
runtime/version supported on the current OS and CPU.

## Tool call was denied

Approval is separate from MCP elicitation. The test's tool selection decides
which tools on its bound servers the agent may call: omitting `tools` allows
every tool the server advertises, `tools=[...]` allows only the named ones, and
`tools=[]` allows none.

Codex asks for approval before each MCP tool call. M3 answers it from that tool
selection, so a Codex test needs no permission setting; a tool outside the
selection is declined even if `permission_policy="allow"` is set. If Codex
lists tools but makes no call, check the `tools` selection and the prompt.

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
