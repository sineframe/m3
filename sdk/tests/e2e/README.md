# SDK end-to-end tests

These tests use the public Python SDK in normal pytest modules. They do not use
scenario files. The deterministic suite starts real MCP subprocesses and, for
persistence checks, a separate SDK worker process:

```bash
uv run --project sdk --all-extras pytest -q sdk/tests/e2e/test_sdk_workflows.py
```

The keyless matrix suite covers server-by-harness, all-servers-per-harness,
server-by-tool, and server-by-tool-by-harness shapes with a real external ACP
process and real stdio MCP processes:

```bash
uv run --project sdk --all-extras pytest -q sdk/tests/e2e/test_sdk_matrix_workflows.py
```

Strict `xfail` cases document confirmed regressions. They intentionally become
suite failures (`XPASS`) when the underlying behavior is fixed, at which point
the marker should be removed.

## Codex MRTR native, managed, and example gate

The Codex MRTR suites test an unmodified Codex CLI 0.156.1 App Server. They use
a local MCP fixture and a local deterministic Responses API fixture, so they
make no paid model-provider calls. The native characterization suite proves
what Codex itself sends and surfaces; the managed suite proves M3 action and
SQLite round delivery; the example suite exercises the public Codex action
patterns in `sdk/docs/elicitation.md`; the action-scope suite covers non-accept
responses, two planned turns in one session, and required-plan completion. A
skipped test is not a passing gate.
Require the pinned binary in validation with `M3_REQUIRE_CODEX_MRTR=1`:

```bash
M3_REQUIRE_CODEX_MRTR=1 \
  uv run --project sdk --all-extras pytest -q \
  sdk/tests/e2e/test_real_codex_native_mrtr.py \
  sdk/tests/e2e/test_real_codex_managed_mrtr.py \
  sdk/examples/tests/test_modern_mrtr_codex.py \
  sdk/examples/tests/test_modern_mrtr_codex_action_scopes.py
```

CI installs `@openai/codex@0.156.1` and runs all four suites against the local
deterministic provider; all 40 tests pass in the current pinned gate. Coverage
includes accept/decline/cancel mappings for form and URL prompts, two planned
actions in one session, and unused required-plan failure. The example tests explicitly set
`permission_policy="allow"` for Codex MCP tool approval; this is independent
of the action-bound MRTR plan. These tests do not call a paid provider.

Set `M3_CODEX_EXECUTABLE` if the binary is not on `PATH`; set
`M3_CODEX_MRTR_VERSION` only when deliberately changing the pinned version.
The default expected version is `codex-cli 0.156.1`. Read the
[Codex limitations](../../docs/elicitation-api.md#codex-app-server-support-and-limitations)
and the [Pi-to-Codex scenario inventory](../mrtr-harness-parity.md) before
interpreting partial suite results.

The live OpenCode test is isolated from the deterministic suite because it may
use credentials, make network requests, and incur provider cost. Its default is
the free `opencode/big-pickle` model. It includes model-selected search,
server-by-tool matrix, and multi-server/multi-turn cases against real MCP
processes:

```bash
M3_RUN_LIVE_OPENCODE=1 \
  uv run --project sdk --all-extras \
  pytest -q sdk/tests/e2e/test_live_opencode.py
```

Set `M3_LIVE_OPENCODE_MODEL=opencode/big-pickle` to characterize another model.

The managed runtime smoke test downloads OpenCode 1.18.30 and 1.18.31 into a
temporary cache, makes one provider-backed MCP call with each, then repeats
1.18.30 to verify cache reuse and persisted version identity. It requires an
explicit opt-in and `OPENCODE_API_KEY`:

```bash
M3_RUN_LIVE_MANAGED_RUNTIME=1 \
  uv run --project sdk --all-extras \
  pytest -q sdk/tests/e2e/test_live_managed_runtime.py
```

The credential-free asset smoke checks download a pinned CLI, run `--version`,
and verify a warm cache hit without contacting the release host again. They do
not call a model provider. Select one recipe with `M3_LIVE_MANAGED_KIND`
(`claude`, `opencode`, `codex`, or `pi`):

```bash
M3_RUN_LIVE_MANAGED_ASSETS=1 M3_LIVE_MANAGED_KIND=opencode \
  uv run --project sdk --extra pytest \
  pytest -q sdk/tests/e2e/test_live_managed_asset_download.py
```

The [CI workflow](../../../.github/workflows/ci.yml) runs the complete seven-case
matrix on Linux, macOS, and Windows when dispatched manually. Release
verification runs the OpenCode/Linux, Claude/macOS, and Codex/Windows cases
before publishing. These checks make no provider calls.
The workflow supplies `M3_GITHUB_TOKEN` for release metadata so shared CI
runner IP addresses do not exhaust GitHub's anonymous API allowance.

Codex and Pi have separate opt-in live tests. Each command requires the
corresponding native executable, a matching model variable, and an explicit
credential route; credentials are passed through each selection's public
`credential_env` mapping into the isolated child environment. The tests
call the documented DeepWiki Streamable HTTP endpoint
(`https://mcp.deepwiki.com/mcp`) and invoke `read_wiki_structure` across two
turns, asserting both tool-call and projected trace evidence:

```bash
M3_RUN_LIVE_CODEX=1 M3_LIVE_CODEX_MODEL=gpt-5.6-sol \
  uv run --project sdk --all-extras \
  pytest -q sdk/tests/e2e/test_live_codex_pi.py -k codex

M3_RUN_LIVE_PI=1 M3_LIVE_PI_MODEL=gpt-5.6-sol \
  uv run --project sdk --all-extras \
  pytest -q sdk/tests/e2e/test_live_codex_pi.py -k pi
```

The Codex test requires an explicit `OPENAI_API_KEY` and uses
`M3_LIVE_CODEX_MODEL`.
The Pi test first accepts the fully explicit generic route
`M3_LIVE_PI_PROVIDER`, `M3_LIVE_PI_CREDENTIAL_ENV`, and
`M3_LIVE_PI_MODEL` together. This supports, for example:

```bash
M3_RUN_LIVE_PI=1 \
  M3_LIVE_PI_PROVIDER=opencode \
  M3_LIVE_PI_CREDENTIAL_ENV=OPENCODE_API_KEY \
  M3_LIVE_PI_MODEL=opencode/big-pickle \
  uv run --project sdk --all-extras \
  pytest -q sdk/tests/e2e/test_live_codex_pi.py -k pi
```

Without the generic route, Pi selects `OPENAI_API_KEY` with provider `openai`
and `M3_LIVE_PI_MODEL`, or `PI_CODING_AGENT_DIR` with provider
`openai-codex` and `M3_LIVE_PI_CODEX_MODEL` (the common Pi model variable
is accepted as a fallback). Use `M3_CODEX_EXECUTABLE` or
`M3_PI_EXECUTABLE` when the executable is not on `PATH`. The live flags
and an explicit credential route are mandatory; tests are skipped during
normal CI and no provider turn is made by collection or by a default test
run.
