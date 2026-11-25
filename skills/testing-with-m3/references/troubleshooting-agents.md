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

## Managed runtime download fails with HTTP 403

`could not acquire managed Codex ...: runtime download failed (HTTP 403)` means
GitHub refused the release lookup. M3 finds managed Codex, OpenCode and Pi
releases through `api.github.com`, and GitHub limits unauthenticated requests
per IP address. Shared CI runners reach that limit often.

Set `M3_GITHUB_TOKEN` to a GitHub token. M3 sends it only to `api.github.com`
and removes it on redirects. In GitHub Actions, pass the workflow token:

```yaml
env:
  M3_GITHUB_TOKEN: ${{ github.token }}
```

## Doctor reports no credential

`m3 doctor --require harness:claude` and `--require harness:codex` exit with
status 1 and print `harness: unavailable (no credential for Claude Code)` or
`(no credential for Codex)` when the executable runs but M3 has no credential
to give it. The lines below name each source doctor looked at, and the `Next:`
line names the ways to provide one:

- Claude Code: set `ANTHROPIC_API_KEY`, or map a token from `claude setup-token`.
- Codex: set `OPENAI_API_KEY`, or sign in with `codex login` so that
  `auth.json` exists in `CODEX_HOME` (default `~/.codex`).

Doctor reads the same sources as `m3 test`: the process environment and the
project root `.env`, or the file passed with `--env-file`. If your tests map a
credential from another variable, pass the same `--credential-env
[KIND:]TARGET=SOURCE` to doctor; it then checks that each `SOURCE` is set to a
non-empty value instead of the default variable. A mapping written only in test
code as `credential_env={...}` is invisible to doctor, so repeat it on the
doctor command line.

Doctor checks that a source is present. It does not send the key, token, or
login to the provider, so a revoked or mistyped credential still passes. For
OpenCode and Pi the variable depends on the model prefix, which doctor does not
know, so it lists the variables and reports `credentials: not checked` without
failing. Doctor never prints a credential value.

## Codex has no login or rejects the credentials

Codex needs either an API key or a ChatGPT subscription login. Startup fails
with `Codex has no login` when it has neither: map a key with
`--credential-env codex:OPENAI_API_KEY=SOURCE`, or sign in to Codex on the host
and run without a mapped key. When a turn fails with `Codex turn failed: model
provider rejected the credentials (HTTP 401)`, the mapped key or the copied
login was refused by the model provider. See
[credentials](reference-credentials.md#native-harnesses).

A failed agent turn ends the execution with the harness's own reason in
`result.error.message` and `result.error.details["cause"]`. Codex failures add
`reason` and `http_status` to the details. `session lost during turn` means the
harness gave no reason. A turn that timed out, was cancelled, or was
interrupted ends the execution with that same outcome rather than `failed`.

## Claude Code turn failed

A Claude Code test that fails on its first turn, within a few seconds, usually
has no credential. The turn error, `turn.error`, is
`ErrorInfo(code=TRANSPORT_ERROR, message='turn failed', ...)` for every failed
turn. The execution error, `session.result.error`, carries the reason: when M3
passed no credential to Claude Code, its message is `Claude Code turn failed:
no credential was passed to Claude Code; set ANTHROPIC_API_KEY or map
CLAUDE_CODE_OAUTH_TOKEN with credential_env`. M3 starts Claude Code with a
temporary home directory and does not copy the host's Claude Code login, so a
signed-in machine is not enough. Provide one of:

- `ANTHROPIC_API_KEY` set to a non-empty value in the environment that runs the
  test, with no `credential_env` in the selection.
- A token from `claude setup-token`, exported under a name you choose and
  mapped with `credential_env={"CLAUDE_CODE_OAUTH_TOKEN": "MY_CLAUDE_TOKEN"}`,
  or `--credential-env claude_code:CLAUDE_CODE_OAUTH_TOKEN=MY_CLAUDE_TOKEN` for
  agents selected on the command line.

An explicit `credential_env` is the complete set of credentials the agent
receives. `m3 doctor --require harness:claude` checks for the same two sources
before you run the test; see [Doctor reports no credential](#doctor-reports-no-credential).
A message of `Claude Code turn failed` without the credential sentence means a
credential was passed and the turn failed for another reason, such as a key the
provider rejected.

`UnsupportedFeature: Claude Code does not support exact tool restrictions`
means the test passed `tools=[...]`. Remove it and assert the call with
`expect(result).to_have_tool_call(...)`; see
[the Claude Code first test](guides-agents-first-test.md#run-the-test-with-claude-code).

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

A timed-out trace keeps the messages, tool calls, and usage that Codex, Pi,
Claude Code, and ACP agents reported before the deadline, with the
`capture_incomplete` limitation. An agent that was still working shows that
activity; one that stalled shows none. OpenCode returns a turn in one response,
so its timed-out turns have no messages or tool calls.

An explicit tool request that times out while waiting for a harness response
may be an unanswered Codex MCP approval request. `agent.run(..., timeout=...)`
covers startup, turns, and cleanup; `handle.result(timeout=...)` limits only
waiting. Diagnose with trace diagnostics: stage, operation, elapsed time, and
configured seconds.
