# SDK end-to-end tests

These tests use the public Python SDK in normal pytest modules. They do not use
scenario files. The deterministic suite starts real MCP subprocesses and, for
persistence checks, a separate SDK worker process:

```bash
uv run --project sdk --all-extras pytest -q sdk/tests/e2e/test_sdk_workflows.py
```

Strict `xfail` cases document confirmed regressions. They intentionally become
suite failures (`XPASS`) when the underlying behavior is fixed, at which point
the marker should be removed.

The live OpenCode test is isolated from the deterministic suite because it may
use credentials, make network requests, and incur provider cost. Its default is
the free `opencode/big-pickle` model:

```bash
MCP_PAL_RUN_LIVE_OPENCODE=1 \
  uv run --project sdk --all-extras \
  pytest -q sdk/tests/e2e/test_live_opencode.py
```

Set `MCP_PAL_LIVE_OPENCODE_MODEL=provider/model` to characterize another model.
