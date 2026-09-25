---
name: testing-with-m3
description: Use when setting up M3 or writing, running, debugging, evaluating, or comparing Python tests of MCP servers and the agents that use them
---

# Testing with M3

M3 runs ordinary Python and pytest tests against an MCP server. A direct test
proves server behavior; an agent test proves what a selected harness did with
the server. Choose the boundary from the claim, then assert observed results.
A collected test, a completed execution, and a passing evaluation are different
facts.

Read only the reference needed for the task:

- [CLI runner](references/cli-runner.md) for installing the standalone `m3`
  command, choosing the project Python, credentials, or CLI options.
- [Test patterns](references/test-patterns.md) before writing a test; use its
  section for the claim under test.
- [Feedback and iteration](references/feedback-iteration.md) for saved runs,
  trace inspection, evaluation totals, a baseline comparison, or opening
  existing run history without executing tests.

## Begin in the target project

1. Read the project's documented behavior, server launch command or MCP URL,
   existing fixtures and pytest conventions. Find independent expected results
   before writing assertions. Do not copy the current output into an expected
   value merely to make a test pass.
2. Use the standalone `m3` CLI as the default runner. Check its availability,
   the project Python, and the installed M3 version. If the CLI is missing,
   install the intended release using [CLI runner](references/cli-runner.md).
   The CLI and SDK install separately.
3. In the target project, run
   `m3 init --project-name NAME --suite mcp-behavior`, then `m3 setup` and
   `m3 doctor`. Run `init` even when the project is already fully initialized:
   it preserves existing files and creates a missing `.env.example`. If it
   reports partial initialization, repair the named files and retry. Supply
   both names to avoid interactive prompts. `setup`
   installs the matching SDK into the selected project Python without editing
   the dependency manifest or lockfile. Replace the skipped starter with a
   real test and remove its skip.
4. Write a deterministic direct test that actually calls a server tool, then
   run it with `m3 test -- tests/PATH.py`. A normal pytest assertion without an
   M3 client or agent operation produces no execution. Give every test a short
   behavior-focused function docstring; M3 saves it as the test description
   shown in the UI. Use the [stdio example](references/test-patterns.md#stdio-local-command)
   or the project's equivalent server fixture. Add agent/provider tests when
   the claim involves tool selection or an agent's output.
5. Inspect the printed feedback path and require a passing pytest case **and**
   at least one linked execution for the direct test. If no execution was
   recorded, fix the test and rerun it before claiming M3 coverage. Follow
   [feedback and iteration](references/feedback-iteration.md#one-run-find-the-verdict-and-evidence)
   for the exact JSON checks.

## Choose the test and the evidence

| Claim | Test boundary | Minimum evidence |
|---|---|---|
| The server advertises a tool, description, input/output schema, resource, or prompt | `MCPTestKit.direct()` | Discovered object and its expected fields; use `list_all_tools()` for paged catalogs |
| A tool handles valid, boundary, or invalid arguments | Direct client or `ToolMatrix` | Expected structured result, typed tool error, or expected exception |
| Calls share state or depend on earlier results | One direct-client context | Result of each call and final state |
| An agent selects the right tool | Marked pytest `agent` or `kit.agents(...)` | Finalized wire-observed call, server, arguments, result, count, and relevant absent calls |
| An agent handles a continuing conversation | `agent.session(...)` | Turn-scoped calls and the finalized `session.result` |
| An answer meets a quality rule | Explicit `kit.evaluate(...)` or `judge_response(...)` | An evaluator result that fails the test when the rule is required |
| A change helps across cases or harnesses | Stable cases, selections, and trials plus a saved baseline | Matched test/evaluation changes, observed interface change, coverage, and limitations |

For a simple marked test, declare server cases with
`@pytest.mark.m3(servers=[{"type": "http", "url": URL, "trust": "public"}])`
or choose them with CLI `--server http --url URL --trust public`. Request
`server` in the test function and pass it to `agent.run(..., server=server)`
or `kit.direct(server)`. Each entry runs separately with each selected agent
and trial; CLI server groups replace the marker list completely. Use
`StdioServer` or `HTTPServer` directly for advanced settings. An HTTP URL is
one MCP protocol endpoint, not a REST route. A server definition does not
start a deployed service. For async tests, use `AsyncMCPTestKit` with
`async with` and `await`.

For feature and regression tests, cover the behavior that can fail: advertised
catalog and schema, representative valid and boundary cases, expected domain
errors, invalid schemas, and any resource, prompt, state, or transport behavior
the project exposes. Isolate persistent external state between cases. Keep
known-call contract checks separate from agent selection checks.

A tool-choice test needs realistic safe alternatives. Do not name the desired
tool in a prompt intended to test discovery or description quality. Assert
wire-observed use with `expect(result).to_have_tool_call(...)`, and check
arguments, count, status, and unwanted calls where the claim needs them. A
policy that exposes only one tool proves use, not choice. Omitted `tools`
advertises the bound server's tools; `tools=[]` denies them.
For a trusted test server under the native Codex harness, explicitly pass
`permission_policy="allow"` to `agent.run` or `agent.session`. The default
permission policy denies MCP tool approvals. A nonlocal HTTP agent server
defaults to `untrusted`; declare `trust="public"` for a public endpoint or
`trusted_private` for a private endpoint you own. Literal loopback addresses
and `localhost` get loopback-only private trust; a mixed DNS result containing
any non-loopback address is rejected.
Keep the server's tool policy and test workspace scoped to the intended
operations.

## Run and score

`m3 test` is the primary runner. It runs pytest in the project Python, saves
executions and pytest outcomes in SQLite, and prints a run ID plus the feedback
path. Put CLI options before `--` and pytest selectors after it:

```sh
m3 test --suite mcp-behavior -- tests/test_m3_starter.py
m3 test --harness codex=gpt-5.6-sol --trials 2 -- tests/test_agent.py
```

For reproducible native harness versions, add `--runtime=managed` and use
`--harness KIND@VERSION=MODEL`; repeat `--harness` for each version. An
unversioned managed selection resolves `latest` once per run. Without
managed mode, M3 uses the locally installed harness. In Python, set
`runtime="managed"` and `version="..."` on the harness spec. Both test kit
constructors accept `harness_cache_dir=...`; CLI runs may use
`--harness-cache-dir PATH` or `M3_HARNESS_CACHE_DIR`. Agent startup waits
for download or cache verification. Reports show the resolved version.

A marked test requesting `agent` needs `--harness KIND=MODEL` or marker
`agents=[...]`. A marker alone does not select an agent. When the user
specifically needs SDK-only pytest, use the project's approved SDK
installation workflow. Direct pytest history is in memory unless the kit uses
`SQLiteExecutionStore` or the M3 pytest plugin receives `--results-db`.
Keep direct-only and agent tests in separate files when running without a
harness: `-k` filters after collection and does not avoid an agent fixture
selection error in the same collected file.

Treat an evaluator as a test gate only when it uses `required=True` or asserts
`status == EvaluationStatus.PASSED`. `required=False` records non-passing
decisions without failing pytest. Keep deterministic evaluators and LLM judges
separate in aggregates. Use ordinary pytest parameters for logical cases and
`--trials N` for independent agent attempts; never rerun only failures and
report the best attempt. Pass rate is passed evaluations divided by expected
evaluations, so error, inconclusive, not-run, and terminal missing required
evidence lower it. Report status and missing/pending counts with the rate.
Small trial counts show observations, not reliable improvement estimates.

## Elicitation

Start with the complete [Elicitation guide](../../sdk/docs/elicitation.md#one-prompt-two-elicitation-rounds-one-tool-call).
Its runnable test sends one prompt, answers either an address form or its
alternative, then answers a URL request on a later retry. It asserts one
successful logical tool call after `agent.run` returns. Copy the maintained
[composed tests](../../sdk/examples/tests/test_modern_mrtr_pi_composed.py),
which also run the optional-address and two-addresses-in-one-round variants.

To write a new test, first build a deterministic server fixture that emits
keyed `InputRequiredResult` requests and validates the next call's
`requestState` and `inputResponses`. Bind each form or URL leaf to a response.
Use `one_of` for alternatives in one round, `round_of` for multiple keys in
one round, `optional` for a round that may be skipped, and `sequence` for
successive rounds. Attach the complete plan to `client.call_tool`,
`agent.run`, or the exact `session.send` that can elicit. Assert the final
operation result or `expect(result).to_have_tool_call(...)`; inspect attempts
and elicitation entries when order matters. The tool assertion runs after the
action because one logical call owns all retries.

The maintained [server](../../sdk/examples/servers/modern_mrtr_server.py),
[direct test](../../sdk/examples/tests/test_modern_mrtr_direct.py), and
[session test](../../sdk/examples/tests/test_modern_mrtr_pi_session.py) show
those action boundaries. Pi 0.85.1 is the verified Pi baseline. Codex support
uses the unmodified App Server and local deterministic provider fixtures; its
full M3 conformance gate is pending. Do not treat native Codex characterization
or a skipped binary test as proof that M3 action integration passed. The
[Codex limitations section](../../sdk/docs/elicitation-api.md#codex-app-server-support-and-limitations)
is canonical, and the [Pi-to-Codex parity inventory](../../sdk/tests/mrtr-harness-parity.md)
lists each existing Pi scenario, its Codex counterpart, and remaining gaps.
For exact helper signatures, prompt/resource actions, manual and managed
input, URL details, and trace fields, use the
[MRTR API reference](../../sdk/docs/elicitation-api.md).

Run the pinned Codex MRTR gate only with Codex CLI 0.156.1 installed; both
suites use local deterministic MCP and Responses API fixtures and make no
paid model-provider calls:

```bash
M3_REQUIRE_CODEX_MRTR=1 \
  uv run --project sdk --all-extras pytest -q \
  sdk/tests/e2e/test_real_codex_native_mrtr.py \
  sdk/tests/e2e/test_real_codex_managed_mrtr.py
```

## Credentials and troubleshooting

| Needed for | Source |
|---|---|
| Installing the CLI and SDK | `uv tool install sf-m3-cli` for the CLI; `m3 setup` for the project SDK |
| Selected model provider | Its named environment variable or a supported native harness login |
| `LLMJudge` | `M3_JUDGE_API_KEY` by default; separate from the agent key |
| Authenticated MCP endpoint | `SecretReference` in `HTTPServer.headers` for agent access, or a direct-client bearer reference |

Local direct tests and the deterministic ACP fixture need no model key.
`m3 init` creates `.env.example` with blank credential names, not working
credentials. For agent or judge tests, copy it to `.env` if needed, fill only
the keys for the selected provider or judge from an authorized source, and
run `m3 test --env-file .env ...`; the CLI never auto-loads `.env`. A supported
native harness login can also authenticate without a key. Keep `.env`
ignored, use names rather than values in flags, and never print or place
secrets in tests or reports. CLI-run tests receive credentials through the
selected child environment; configuring the app's `Settings` separately is
unnecessary for this workflow.

When a run fails, check collection and environment first, then server startup
or connection, harness/provider setup, operation assertions, evaluator status,
and finally trace completeness. An MCP tool failure is a result with
`is_error=True`; transport, protocol, timeout, and local schema failures are
exceptions. `validate_schemas=True` opts into local JSON Schema checks.
Read `client.final_trace` or `session.result.trace_view` only after closure.
`TraceView` observations have states: an unavailable or hidden value is not
observed evidence.
If Codex lists tools but makes no call, inspect the prompt and permission
policy separately. A request for a real-world quote can reasonably prompt for
carrier and postal details; name the bound test service without naming its
target tool when testing discovery. An explicit tool request that times out
while waiting for a harness response may indicate an unanswered Codex MCP
approval request. A trace with no `tools/call` never proves tool choice.

For debugging, the CLI already writes `.m3/reports/<run-id>/feedback.json` and
referenced trace files. Read selected JSON fields, not a full dump in the
terminal or shared logs; arguments and results can contain application data.
See [feedback and iteration](references/feedback-iteration.md) for commands.
A `tests[].outcome` is a pytest case outcome, `tests[].verdict` refines it,
`executions[].outcome=completed` is lifecycle only, and
`evaluation_stats` describes explicit saved evaluations.
