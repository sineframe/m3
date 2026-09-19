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
