"""The default harness registry imports each adapter only when its kind is
first resolved, so kits that only drive servers directly skip that cost."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_SRC = Path(__file__).parents[2].resolve() / "src"


def test_default_registry_defers_adapter_imports_until_resolved() -> None:
    script = (
        "import json, sys\n"
        "from m3.harness.contracts import default_adapters\n"
        "from m3.types import ClaudeCode\n"
        "adapter_modules = ('m3.harness.acp', 'm3.harness.claude', 'm3.harness.codex',\n"
        "                   'm3.harness.opencode', 'm3.harness.pi')\n"
        "registry = default_adapters()\n"
        "before = sorted(name for name in adapter_modules if name in sys.modules)\n"
        "registry._factories['claude_code'](ClaudeCode(model='m'))\n"
        "after = sorted(name for name in adapter_modules if name in sys.modules)\n"
        "print(json.dumps({'kinds': sorted(registry._factories), 'before': before,\n"
        "                  'after': after}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "PYTHONPATH": str(_SRC)},
        capture_output=True,
        text=True,
        check=True,
    )
    state = json.loads(result.stdout.strip().splitlines()[-1])

    assert state["kinds"] == ["acp", "claude_code", "codex", "opencode", "pi"]
    assert "m3.harness.acp" not in state["before"]
    assert "m3.harness.claude" in state["after"]
