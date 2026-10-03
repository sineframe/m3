<!-- Generated from docs/site/troubleshooting/agents.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Agent readiness, approvals, and timeouts

## Harness executable is unavailable

Use `m3 doctor --require harness:NAME` for a system runtime, or select a managed
runtime/version supported on the current OS and CPU.

## Tool call was denied

Approval is separate from MCP elicitation. The test's tool selection decides
which tools on its bound servers the agent may call: omitting `tools` allows
every tool the server advertises, and `tools=[...]` allows only the named ones.
M3 answers a harness's approval prompt for those calls from that selection, so
a simple test needs no permission setting.

`permission_policy` covers only harness-native permission prompts outside the
bound MCP servers, such as an ACP agent asking to run a command or edit files.
It denies them by default. Allow them only for a trusted agent and a scoped
workspace. If Codex lists tools but makes no call, check the `tools` selection
and the prompt.

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
