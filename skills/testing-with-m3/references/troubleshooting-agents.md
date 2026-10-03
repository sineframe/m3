<!-- Generated from docs/site/troubleshooting/agents.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Agent readiness, approvals, and timeouts

## Agent test requires --harness

pytest stops during collection with
`agent test requires --harness or m3(agents=[...])` when a test requests the
`agent` fixture and nothing selects a harness for it. Select one in either
place:

- on the command line: `m3 test --harness KIND=MODEL`
- on the test: `pytest.mark.m3(agents=[...])`

A test can also skip the fixture and select its agent with
`kit.agents([...])`, as [the first agent test](guides-agents-first-test.md)
does. That test runs under plain pytest with no flags.

`-k` does not avoid the error, because collection fails before deselection.
Keep agent tests in their own file so that direct tests can run by path
without a harness. See [the pytest plugin reference](reference-pytest.md).

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
