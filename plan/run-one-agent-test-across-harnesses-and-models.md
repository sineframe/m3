# Run one agent test across harnesses and models

## 1. User-facing contract

### Pytest: the test contains no harness or model selection

```python
import pytest
from mcp_pal import expect

@pytest.mark.mcp_pal
def test_shipping_tool(agent, shipping_server):
    result = agent.run(
        "Get a local shipping quote for a 2 kg parcel.",
        server=shipping_server,
    )
    expect(result).to_have_tool_call(
        "shipping_quote",
        server=shipping_server.name,
        status="success",
    )
```

The bare `mcp_pal` marker is the visible opt-in. It selects no harness or model by itself; the CLI supplies those values:

```bash
mcp-pal test \
  --harness opencode=opencode/big-pickle \
  --harness codex=gpt-model \
  --trials 2 \
  -- tests/test_shipping.py
```

That command collects **four pytest items and four agent executions**: two harness/model selections × two trials. `--trials 2` repeats **every combination** independently; it does not retry failures. A test without an `agent` argument still collects once, even when these CLI flags are present.

A test can instead keep default selections in code:

```python
import pytest

pytestmark = pytest.mark.mcp_pal(
    agents=[
        {"harness": "opencode", "models": ["opencode/big-pickle"]},
        {"harness": "codex", "models": ["gpt-model"]},
    ],
    trials=2,
)

def test_shipping_tool(agent, shipping_server):
    ...
```

The marker can be on a function or module. It does not multiply tests that do not request `agent`. CLI `--harness` replaces the marker’s selection; CLI `--trials` replaces its trial count. Direct pytest requires the plugin:

```bash
python -m pytest -p mcp_pal.pytest_plugin tests/test_shipping.py
```

If an `agent` test has neither a CLI selection nor a marked selection, collection fails with an error showing both forms.

### Plain Python and notebooks: the same selection mechanism

```python
from mcp_pal import MCPTestKit

agents = [
    {"harness": "opencode", "models": ["provider/a", "provider/b"]},
    {"harness": "codex", "models": ["gpt-model"]},
]

with MCPTestKit() as kit:
    for agent in kit.agents(agents):
        result = agent.run(
            "Find the shipping tool",
            server=shipping_server,
            case_id="shipping-tool",
        )
        tools = [call.tool.value for call in result.trace_view.tool_calls]
        print(agent.harness, agent.model, agent.trial, tools)
```

`kit.agents(agents, trials=2)` yields two selections per harness/model. It performs no MCP or harness I/O until `run`, `submit`, or `session` is called. The base SDK remains usable without pytest installed. `AsyncMCPTestKit.agents(...)` supplies equivalent selections whose `run` is awaited.

### ToolMatrix composition

Keep deterministic `case.run()` unchanged. For agent tool selection, accept `ServerCase` directly as `agent.run(..., server=...)`, retaining its server alias:

```python
@pytest.mark.mcp_pal
@matrix.parametrize()
def test_agent_chooses_tool(case, agent):
    result = agent.run(case.tool.prompt, server=case.server)
    expect(result).to_have_tool_call(
        case.tool.name,
        server=case.server.name,
        status="success",
    )
```

If the matrix has three tool cases and the CLI supplies two harness/model choices with two trials, this collects **12 agent test items**. `case.tool.prompt` must be supplied for this pattern; an explicit prompt in the test is also valid. The matrix’s declared tools do not silently narrow what the agent sees: tool-choice tests need realistic alternatives.

## 2. Credentials: exact setup and behavior

A model selection is separate from authentication. Users put provider credentials in the process environment or in a file passed explicitly to the CLI. The normal documentation command will be:

```bash
mcp-pal test --env-file .env \
  --harness opencode=opencode/big-pickle \
  --harness codex=gpt-model \
  -- tests/test_shipping.py
```

The ignored `.env` file contains the user’s actual `OPENCODE_API_KEY` and `OPENAI_API_KEY`. Alternatively, users export those variables before running the command. **No CLI option accepts a secret value.** Plain scripts and notebooks receive credentials through their process environment, for example by running the script with an explicitly loaded environment file.

Implement these rules:

1. Add `mcp-pal test --env-file PATH`. Read it with `dotenv_values(..., interpolate=False)` and pass a merged environment **only to the pytest child process**. Ambient values win over file values. Do not mutate the CLI process environment, load `.env` implicitly, pass values in argv, or give file-only provider keys to the UI server process. `doctor --env-file` currently reads only `MCP_PAL_*` configuration; the new **test** option must also load provider variables.
2. Add repeatable optional `--credential-env TARGET=SOURCE` as the normal form; **no harness kind is required**. Both sides are environment-variable **names**. `--credential-env VENDOR_API_KEY=MY_VENDOR_KEY` supplies that target to selected native harnesses. Support `--credential-env KIND:TARGET=SOURCE` only when a mapping should apply to one harness kind. Add the same target-to-source mapping in an agent dictionary as `"credential_env": {"VENDOR_API_KEY": "MY_VENDOR_KEY"}`. Validate names against Python-style environment identifiers. Reject duplicate target definitions at the same precedence level.
3. Convert every selected `credential_env` entry to the existing `SecretReference(source="environment", name=SOURCE)` in the internal harness value. Resolve its value only at harness launch. Precedence is known default mapping, marked dictionary, global CLI mapping, then kind-scoped CLI mapping. A missing explicitly selected source fails before harness launch with an error naming the variable, not its value.
4. For bare native CLI selections, create default references for known routes: Claude Code → `ANTHROPIC_API_KEY`; Codex → `OPENAI_API_KEY`; OpenCode or Pi with model prefix `opencode/`, `openai/`, or `anthropic/` → the corresponding `OPENCODE_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`. Pi’s existing `openai-codex` route may reference `PI_CODING_AGENT_DIR`. Do not guess credentials for an unrecognized provider prefix; the user supplies `credential_env` or an ACP manifest. ACP retains authentication defined by its manifest.
5. Distinguish **provider credentials** from **MCP server credentials** in every guide. For an authenticated HTTP MCP server, the user places a `SecretReference` in `HTTPServer.headers` or uses the existing direct-client bearer-token option. Harness `credential_env` does not authenticate an MCP endpoint.
6. Test with a sentinel credential value that the value never appears in `pytest_command`, saved execution specifications, SQLite report JSON, feedback JSON, normal errors, or CLI diagnostics. Variable names may appear.

## 3. SDK implementation

### Selection module and input validation

Create `sdk/src/mcp_pal/_agent_selection.py`. It must import no pytest module. Give it four responsibilities: validate dictionaries, expand them, construct the existing harness value, and construct the existing execution specification. Use one internal immutable selection record; do **not** export a new configuration class.

Accepted agent entries:

- Native/ACP: `{"harness": KIND, "models": [MODEL, ...], ...}`.
- Advanced saved profile: `{"harness_profile": {"profile_id": ID, "revision": {"mode": "latest"}}}` or the existing pinned revision shape. It yields one selection because the saved profile determines its model. It requires a configured persistent store, as the current profile path does.
- Optional native fields: `name`, `executable`, `provider`, `dialect`, `credential_env`, and existing `credential_references` for compatibility. `provider` defaults to the prefix before `/` for OpenCode and Pi when present.
- Optional ACP fields: `manifest`, `agent_mode_id`, `session_config`, and `name`. ACP requires a runnable manifest; a CLI model string alone cannot create one.

Validate nonempty strings and models, positive integer trials excluding booleans, known harness names, allowed keys per harness, valid credential variable names, and duplicate `(kind, name, model)` selections. Permit two configurations of the same kind/model only when their explicit names differ. Preserve input order; for each entry, yield models in list order and trials `1..N`.

A selection exposes `harness` (kind), `model` (the requested model; `None` for an unresolved saved profile), `name`, and one-based `trial`. Its synchronous `run` calls `MCPTestKit.run(spec)`, `submit` calls `MCPTestKit.submit(spec)`, and `session` calls `MCPTestKit.agent_session(spec, ...)`. The async selection uses the corresponding async kit methods. Selection objects hold the kit but never close it. These are internal selection instances, not a new exported configuration type.

### Exact run/session behavior

Use these public method signatures on each selected agent:

```python
agent.run(message, *, server=None, servers=None, tools=None, **execution_options)
agent.submit(message, *, server=None, servers=None, tools=None, **execution_options)
agent.session(*, server=None, servers=None, tools=None, **execution_options)
```

`run` waits and returns the existing `ExecutionResult`; it is the default shown in ordinary tests and quick starts. `submit` returns the existing `ExecutionHandle` immediately, allowing `snapshot()`, events, `result(timeout=...)`, and `cancel()`; its async counterpart uses awaitable handle operations. Keep `submit` as an advanced control path for background execution and cancellation parity, not as a second ordinary way to write tests. Both methods share one specification-building path. The async `run` is awaited.

Use these inputs on `run` and `submit`:

- Positional `message`: nonempty `str` or existing `UserMessage`; convert strings to the existing text-message form.
- Exactly one of `server` or `servers`. Each item may be an existing server value, `ServerBinding`, a plain binding mapping for an alias/profile, or `ServerCase` from ToolMatrix. A `ServerCase` contributes its underlying server and its case name as alias.
- The literal default `tools=None`, an explicit list of qualified `server:tool` strings, or an existing `tool_policy`; reject an explicit `tools` list plus `tool_policy`. Omitting `tools` is identical to `tools=None` and does not narrow advertised MCP tools. Validate an explicit qualifier against bound aliases and reject duplicates. `tools=[]` means deny all MCP tools; it must not mean the default. Apply the same default and distinction to `session`.
- `timeout` maps to `AgentSpec.timeout_seconds`; `case_id` overrides a pytest-provided logical case ID.
- Allow every remaining existing agent execution option by its current field name: `run_id`, `protocol`, `goal`, `evaluations`, `artifact_policy`, `declared_artifacts`, `workspace`, `permission_policy`, `elicitation_policy`, `sampling_policy`, `filesystem_policy`, `terminal_policy`, and `metadata`. Pass these to the current validator; do not invent a second validation system.

`session` accepts the same binding, policy, identity, and execution options but no initial message. It builds `AgentSpec(message=None)` and passes `adapter`, `runtime_servers`, and `interaction_handlers` to the existing session method. This preserves multi-turn send, cancellation, fork, turn results, and trace access. `submit` returns the existing cancellable execution handle. Reject `InProcessServer` for these serializable agent specifications with the current actionable error.

For the default tool access, construct the existing full policy internally for OpenCode, Codex, Pi, and ACP so bound servers’ advertised MCP tools are available; retain separate permission and workspace controls. Claude Code requires its existing native `mcp_only` policy scoped to its single bound server. An explicit `tools` list creates the existing restrictive policy where exact enforcement is supported. For Claude Code, reject an exact `tools` restriction with `UnsupportedFeature` and tell the caller to leave the server’s safe choices available and assert the selected tool in the trace. Its existing multi-server restriction remains. These policy-class details stay out of user guides.

### Capability parity before implementation

Use this mapping as the migration checklist. Add a new-interface test for each row **before** removing its public example:

| Existing capability | New author-facing expression |
|---|---|
| `AgentSpec` + `kit.run` / `submit` | `agent.run` / advanced `agent.submit` with handle lifecycle |
| `AgentSpec` + `agent_session` | `agent.session` |
| Harnesses and multiple models | List of dictionaries in `kit.agents(...)`, marker, or CLI |
| `HarnessMatrix.each_server` | Ordinary pytest server parameter or Python loop × selected `agent` |
| `HarnessMatrix.each_tool` | `ToolMatrix.parametrize()` + `agent`, or ordinary tool parameter |
| `HarnessMatrix.all_servers` | `agent.run(..., servers=[...])` or `agent.session(servers=[...])` |
| Matrix trials | `--trials`, marker `trials`, or `kit.agents(..., trials=N)` |
| Named logical cases/evaluations | `case_id=...`, existing `kit.evaluate`, and saved aggregate queries |
| ACP/custom executable | Agent dictionary with `manifest` |
| Saved server/harness profile | Plain binding/profile mapping converted to existing profile refs |
| Existing policy, workspace, artifact, interaction options | Same option names forwarded by `agent.run`/`session` |

## 4. CLI and pytest implementation

In `cli/src/mcp_pal_cli/main.py`, add `test` parser options for `--harness`, `--trials`, `--credential-env`, and `--env-file`. Parse `KIND=MODEL[,MODEL...]` at the first `=`; trim and reject empty model segments, unknown kinds, and duplicate choices. Repeated flags append choices in order. Accept both global `TARGET=SOURCE` and optional kind-scoped `KIND:TARGET=SOURCE` credential mappings. Everything after `--` still passes unchanged to pytest.

Thread the parsed values through both `supervisor.run_test` and `run_test_with_runs`, then `_run_pytest_process` and `pytest_command`. `pytest_command` adds repeatable internal `--mcp-pal-harness`, `--mcp-pal-credential-env`, and optional `--mcp-pal-trials` before the user’s pytest arguments. `_run_pytest_process` supplies the merged child environment. Start **one** pytest subprocess for the whole selection; never loop over harnesses in the CLI.

In `sdk/src/mcp_pal/pytest_plugin.py`:

1. Register the marker and internal selection options in `pytest_addoption`/`pytest_configure` **before** the current early return when there is no results database. Direct pytest must work without SQLite.
2. Add function-scoped `mcp_pal_kit` that constructs and closes `MCPTestKit`. Projects may override it to supply an adapter registry or store. Add function-scoped `agent` using `request.param` and that kit.
3. Add `pytest_generate_tests`. Act only when `"agent"` is in `metafunc.fixturenames`; require its closest `mcp_pal` marker, including the valid bare `@pytest.mark.mcp_pal` form. Read the marker's optional defaults. If CLI harnesses exist, replace the marked agent list with CLI choices. For each CLI kind, copy advanced settings from the **single** marked entry of that kind if present, replace its models, then apply CLI credential targets. More than one matching marked entry is an explicit ambiguity error. If no CLI harnesses exist, use the marked list. Fail clearly if neither source supplies a selection. CLI trials override marked trials; otherwise default to one. Expand and call `metafunc.parametrize("agent", selections, indirect=True, ids=...)`. A marker without an `agent` fixture does not multiply the test.
4. IDs include kind, model, optional configuration name, and trial. They contain no credential values. Pytest’s ordinary parameterization supplies the cross-product with server fixtures and ToolMatrix cases. Construct no server, process, store execution, or network connection in `pytest_generate_tests`.
5. At fixture runtime, compute the logical case ID from the unparameterized test node ID plus sorted `request.node.callspec.indices` **excluding `agent`**. Hash that payload and truncate its readable prefix to fit the existing 256-character `case_id` limit. Thus one ToolMatrix case shares a case ID across harnesses and trials, while another tool case has a different ID. An explicit `case_id` passed to `agent.run` wins.
6. For legacy `HarnessMatrix.parametrize()` items only: when a CLI harness filter exists, inspect the parameter value for `HarnessMatrixCase`, retain items matching its actual harness kind and model, and report other items through `pytest_deselected`. Do this before the manifest records collected IDs. Never rewrite a legacy case or apply the new trials to it. With no CLI filter, preserve current behavior.

## 5. Persistence, API v2, and exports

Build the current `AgentSpec` internally, so SQLite storage and `/api/v2` execution schema remain unchanged. For selected pytest executions, set:

- `spec.case_id` to the logical ID above;
- `harness_config` to a stable `kind:model` label, including configuration name when supplied;
- the existing internal trial, matrix, and cell metadata keys currently read by `aggregations.py` and `feedback.py`.

The matrix ID identifies the underlying test, the cell ID identifies the ordinary pytest/ToolMatrix parameter combination **without** harness or trial, and the trial number is one-based. Reserve these metadata keys so user metadata cannot overwrite report identity. For a plain Python loop, preserve an explicit `case_id` across selections/trials and record the same configuration/trial labels.

Verify that the existing report UI can read every selected execution through `GET /api/v2/executions/{id}/report`, the same saved evaluations through `POST /api/v2/evaluations/aggregate`, and the same run comparison through `GET /api/v2/feedback/{run_id}`. No endpoint, envelope, field removal, database migration, or UI contract change is planned. Adjust SDK projection code only if its current label lookup fails for the new cases. The report must show distinct execution IDs and harness/model labels for each trial while retaining one logical case ID.

Reduce the **supported wildcard surface** as follows:

- `mcp_pal.__all__` becomes exactly `__version__`, `MCPTestKit`, `StdioServer`, `HTTPServer`, `SSEServer`, `InProcessServer`, `ExecutionResult`, `ExecutionOutcome`, `TurnOutcome`, `expect`, `check`, `MCPError`.
- `mcp_pal.matrix.__all__` becomes exactly `ToolCase`, `ServerCase`, `ToolMatrix`, `ToolMatrixCase`.
- Remove `AgentSpec`, `ExecutionSpec`, `HarnessSpec`, `HarnessValue`, `HarnessProfileRef`, `ACPAgent`, `ClaudeCode`, `OpenCode`, `Codex`, `Pi`, and `NativeToolPolicy` from the supported `mcp_pal.types` export manifest. Keep direct-result, server, trace, evaluation, and `SecretReference` types there for focused advanced use.

Keep **explicit old imports** working during this migration. Update `_exports.py`, `types.py`, `matrix.py`, `__init__.py`, and the public-surface tests together. Those tests currently assume every module attribute must be in `__all__`; replace that assertion with an explicit compatibility allowlist rather than deleting old attributes. Preserve the legacy types’ `__module__ = "mcp_pal.types"` assignment even after removing them from `types.__all__`, and test their pickle round trips. Wildcard imports intentionally expose the smaller list.

## 6. Documentation rewrite: concrete content

Update executable examples at the same time as their linked prose. The user guides and the testing skill must show **only the simple authoring path**. Do not put `AgentSpec`, `HarnessMatrix`, concrete harness classes, native policy classes, fixture-generation mechanics, or internal metadata keys in user-facing examples. The generated OpenAPI schema may retain its existing wire model names.

1. **Root README:** Replace “Test the behavior that matters” with the bare-marked `agent` test from section 1. Immediately beneath it, add “Set credentials” with `OPENCODE_API_KEY` and `OPENAI_API_KEY` in exported environment variables or an explicitly loaded `.env`, then show a two-harness CLI command and explain the exact item count. Replace “Bring your own harness” with an ACP agent dictionary containing a manifest. In the SDK section, show the `kit.agents(...)` Python loop and printed tool names.
2. **SDK quick start:** Keep the direct MCP discovery/call walkthrough. Add a second walkthrough: define a real server fixture; write one `@pytest.mark.mcp_pal` agent test; run it through `mcp-pal test --harness ...`; optionally use `pytest.mark.mcp_pal(agents=[...])` with direct `pytest -p mcp_pal.pytest_plugin`. State the provider key names and show `--env-file .env`. Explain that the fixture supplies the selected agent and the test supplies the server and assertion. Show `agent.session(server=...)` for two turns.
3. **SDK examples:** Replace the current native harness example and ACP example with complete agent dictionaries. Replace the large server/harness matrix section with three executable examples: ordinary server parametrization + a marked `agent` test; `ToolMatrix` + a marked `agent` test; and `kit.agents(...)` in a normal Python file. Keep `ToolMatrix.case.run()` as the direct-call example. Put `submit` only in an advanced background-execution example showing handle status and result or cancellation. State needed provider key names alongside each runnable example. Update the corresponding files under `sdk/examples/`, including the live OpenCode example used by the UI gate.
4. **Concepts:** Rewrite “Test matrices” to explain three separate user choices: direct deterministic `ToolMatrix`, a selected `agent` supplied by CLI/marker/list, and ordinary pytest or Python loops for servers/tools. Explain that `--trials 2` creates two independent executions for every combination. Explain omitted `tools` and `tools=[]` accurately without exposing internal policy classes. Link to the credential setup in the quick start.
5. **Evaluations:** Rewrite the math example to use ordinary math-case parametrization plus a marked `agent`; run it with `--trials 2` and a documented provider-key setup. Pass each math case’s stable ID as `case_id` in Python-loop examples; pytest gets one automatically. Keep one explicit `kit.evaluate(...)` call per completed execution, then show `EvaluationQuery` grouped by `metadata.harness_config` and filtered to the run. State that a plain Python `assert` is a pytest outcome, while an explicit evaluation or recorded matcher is what aggregate queries score.
6. **HTTP guide:** Keep direct bearer authentication with `SecretReference`. Replace the agent HTTP and matrix examples with `agent.run(..., server=HTTPServer(...))` and a marked ToolMatrix-crossed test. Show the MCP endpoint’s token reference in `HTTPServer.headers`; show provider keys separately in the CLI environment and `--env-file` example. Retain explicit `TrustLevel.PUBLIC` for the public endpoint.
7. **CLI README:** Add a flag table with `--harness`, `--trials`, `--env-file`, and `--credential-env`; a complete two-harness command; the marker/CLI precedence; the formula for trial counts; a known-provider key table; and an unknown-provider example using normal `--credential-env VENDOR_API_KEY=MY_VENDOR_KEY` plus optional scoped form. State that only variable names go in test code/flags and that `.env` is read only when requested. Clarify that `mcp-pal doctor --env-file` does not itself provide credentials to a later test command.
8. **API v2 guide:** Keep the HTTP contract and JSON examples. Replace prose saying SDK authors create an `AgentSpec` with the test/CLI and Python-loop flows, including where provider keys are set for execution. Explain that those flows write the same wire execution records and that the UI reads the three unchanged routes above. Do not describe pytest internals or internal metadata keys.
9. **Local skill:** In `skills/testing-with-mcp-pal/SKILL.md`, change the boundary table to `agent` for tool choice and `kit.agents(...)` for scripts. Update its workflow to inspect which provider key **names** are required, run a narrow CLI selection with exported variables or `--env-file`, and report live results. Fix its “no hidden fixture” guidance to say no server is generated automatically; the plugin supplies only `agent`. Replace the old harness-spec code in `references/test-patterns.md` with the bare-marked fixture example. Add exact CLI, `.env`, known key names, global/scoped custom-provider mappings, and missing-key guidance to `references/cli-runner.md`. The skill must never ask for or print a secret value in a test, flag, log, or report.

## 7. Tests and mandatory final gate

Create focused tests in SDK unit/integration/e2e, CLI tests, and app API integration tests. Acceptance cases:

- **Selection and API:** native/ACP/profile entries; several models; named duplicate configurations; invalid kinds/keys/models/trials; sync and async `run` returning results; immediate `submit` returning handles with snapshot/result/cancel; multi-turn session; multi-server session; aliases and saved server profiles; advanced execution options; literal `tools=None` signature defaults, omitted tools, `tools=[]`, explicit tools, and Claude Code limitations.
- **Credentials:** each default provider mapping, a renamed source variable, explicit CLI override, unknown provider, missing source, `.env` loading and ambient precedence, and a sentinel secret absent from command arguments, SQLite, report, feedback, and errors.
- **Collection:** bare-marked `agent` test with CLI selection; marker defaults without CLI; function marker over module marker; CLI replacement; missing-marker and missing-selection errors; ordinary test collected once; marked ToolMatrix × agent × trials with exact IDs/counts; no server or harness I/O under `--collect-only`; matching xdist collection IDs.
- **Execution identity:** for three tool cases × two harness/model selections × two trials, assert 12 agent executions, three logical case IDs, 12 distinct execution IDs, correct harness/model/trial labels, and per-trial matcher/evaluation records.
- **Compatibility:** old explicit imports and pickle paths; legacy matrix tests unchanged without CLI flags; kind/model filtering without rewriting under CLI flags; direct `ToolMatrixCase.run()` unchanged.
- **API/UI contract:** use a real CLI-created SQLite run in app integration tests, read every execution report, aggregate its recorded checks by configuration, fetch feedback and a baseline comparison, and assert the existing JSON envelope/field shapes consumed by the UI.

Run the deterministic SDK, CLI, and app suites, plus the updated executable documentation examples. Test a notebook-style script in an environment with base `mcp-pal` installed **without pytest**.

**The final gate also runs live providers; skipped tests do not satisfy it.** Add one bare-marked live test that uses a real local MCP server and the `agent` fixture. Update `scripts/live_ui_gate.py` to run that test through production CLI/SDK/app wheels with one real OpenCode and one real Codex selection, require both tool calls, then inspect their distinct SQLite records, API v2 reports, feedback, bundled UI, and redaction. Run one real OpenCode `kit.agents(...)` Python script as the non-pytest live path. The gate checks for required executables and credentials up front and fails clearly if absent. Existing Claude Code, Pi, and migrated ACP live tests remain available for their documented routes; run any touched route as part of the final gate.

## 8. Locked decisions and defaults

- `@pytest.mark.mcp_pal` with no arguments is the standard CLI-driven test opt-in. The `agent` fixture alone does not opt in.
- `run` is the ordinary blocking result path. `submit` is the advanced nonblocking handle path retained for status, later waiting, events, and cancellation; both share one specification builder.
- `tools=None` is the literal default on `run`, `submit`, and `session`. Omission and `None` are equivalent; `tools=[]` denies MCP tools.
- `--credential-env TARGET=SOURCE` is the normal global form. `KIND:TARGET=SOURCE` is optional scope. Provider credentials come from the environment or explicit `--env-file`; MCP server authentication is separate.
- No new public configuration type is introduced. Existing explicit imports remain compatible while wildcard exports and author-facing documentation become smaller.
- `/api/v2` routes and payload shapes remain stable; live OpenCode and Codex checks are mandatory before completion.
