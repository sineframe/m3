# Pin a managed harness runtime

Select a managed runtime when a test must use an explicit native harness version. M3 records the resolved runtime identity with the execution.

## Requirements

This live example uses Codex and provider access. Install M3 with pytest, sign in to Codex, and set these variables in your shell:

```bash
export M3_DOCS_CODEX_MODEL='<model available to your Codex login>'
export M3_DOCS_CODEX_VERSION='<explicit version to test>'
```

Replace both values before running the test. `M3_DOCS_CODEX_VERSION` must be a version string, not `latest`; this example checks a pinned version. M3 may need network access to acquire it.

## Run the pinned version

Save `shipping_server.py` from [the first agent test](/guides/agents/first-test) beside this complete test as `test_managed_runtime.py`:

```python
import os
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent


def test_managed_codex_version_is_recorded(tmp_path: Path) -> None:
    model = os.environ["M3_DOCS_CODEX_MODEL"]
    version = os.environ["M3_DOCS_CODEX_VERSION"]
    if version == "latest":
        raise ValueError("M3_DOCS_CODEX_VERSION must be an explicit version")
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    selection = {
        "harness": "codex",
        "models": [model],
        "runtime": "managed",
        "version": version,
    }

    with MCPTestKit(env={}, harness_cache_dir=tmp_path / "harness-cache") as kit:
        result = kit.agents([selection])[0].run(
            "Use shipping:shipping_quote once with weight_kg 2 and zone local.",
            server=server,
            tools=["shipping:shipping_quote"],
            timeout=180,
        )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    identity = result.snapshot.agent
    assert identity is not None
    assert identity.harness.runtime == "managed"
    assert identity.harness.resolved_version == version
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
    )
```

Run it from the project directory with `python -m pytest -q test_managed_runtime.py`. A passing test confirms the tool call and the resolved version recorded by M3. The requested selector alone does not prove which executable version ran.

If M3 cannot acquire the version or readiness fails, the run fails before the assertion. Do not report a system-installed executable as a managed-runtime pass. Next: see [harness compatibility](/reference/compatibility).
