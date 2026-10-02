<!-- Generated from docs/site/reference/python/m3/core.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Core execution API

## `MCPTestKit`

The synchronous runtime and cleanup boundary. Construct it with optional
environment, store, evaluator, runtime, and harness-cache configuration. Enter
it before opening direct clients or agent sessions and close it after all work.

- `direct(server)` opens a synchronous initialized MCP client.
- `agents(selections, trials=N)` expands explicit agent selections for scripts.
- `register_evaluator(name, callback)` registers executable evaluation code.
- `evaluate(subject, name, …)` persists one explicit evaluation.
- `judge_response(…)` evaluates response text with an `LLMJudge`.

`KitClosed` is raised when work starts after closure. Cleanup failures are
reported rather than silently discarded.

## Direct clients

`DirectClient` is returned by `kit.direct(server)`. It supports initialization,
tool discovery and calls, resources, resource templates, prompts, and ping.
Pagination helpers such as `list_all_tools()` collect all pages. Operation
results are typed; normal MCP tool errors remain `ToolCallResult` values with
`is_error=True`, while transport and protocol failures raise exceptions.

`final_trace` is available after the client closes. Calls that depend on one
server session must stay in the same client context.

`client.initialization` contains initialization evidence.
`client.transport_evidence` remains available with state `"closed"` after
exit. Read transport entries with `trace.view().transports`.

## Agents and sessions

An agent selection can run one action with `run(...)`, start a continuing
`session(...)`, or use managed-input methods on supported paths. A session's
`send(...)` returns a `TurnResult`; its finalized `result` is available after
the session closes.

Tool availability, permission policy, workspace policy, timeout, and
elicitation plan belong to the action that uses them. Consult the applicable
harness guide because integrations expose different evidence and interactions.

Omitting `tools` advertises the bound server's tools; `tools=[]` denies them.
The default permission policy denies MCP tool approvals. For a trusted test
server under native Codex, pass `permission_policy="allow"` to `agent.run(...)`
or `agent.session(...)`.

## Async API

`AsyncMCPTestKit`, `AsyncDirectClient`, `AsyncAgentSession`, and
`AsyncExecutionHandle` mirror the synchronous lifecycle. Await their operations
and use `async with`; do not wrap them in a second event loop.

## Probes and configuration

`load_config` returns validated `Config` data with `ConfigOrigin` and
`ConfigSource`. `Probes` and `AsyncProbes` run explicit readiness checks and
return `ProbeReport`/`ProbeResult` evidence. Probes do not make an unsupported
runtime combination supported.
