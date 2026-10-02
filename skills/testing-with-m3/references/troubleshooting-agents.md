<!-- Generated from docs/site/troubleshooting/agents.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Agent readiness, approvals, and timeouts

## Harness executable is unavailable

Use `m3 doctor --require harness:NAME` for a system runtime, or select a managed
runtime/version supported on the current OS and CPU.

## Tool call was denied

Approval is separate from MCP elicitation. Set the permission policy required
by the harness only for a trusted server and scoped workspace.

The default permission policy denies MCP tool approvals. For a trusted test
server, pass `permission_policy="allow"` to `agent.run(...)` or
`agent.session(...)`. If Codex lists tools but makes no call, inspect the
prompt and permission policy separately.

## Execution timed out

Use `--execution-timeout SECONDS` to set an intentional deadline. Inspect the
trace and harness diagnostics before increasing it; a server waiting for input
or approval will not be fixed by an arbitrary larger value.

An explicit tool request that times out while waiting for a harness response
may be an unanswered Codex MCP approval request. `agent.run(..., timeout=...)`
covers startup, turns, and cleanup; `handle.result(timeout=...)` limits only
waiting. Diagnose with trace diagnostics: stage, operation, elapsed time, and
configured seconds.
