"""Keep the checked sync/async examples executable and statically typed."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

EXAMPLE = Path(__file__).parents[1] / "typecheck" / "usage_examples.py"


def test_usage_examples_compile() -> None:
    source = EXAMPLE.read_text(encoding="utf-8")
    compile(source, str(EXAMPLE), "exec")


def test_usage_examples_require_mypy_strict() -> None:
    mypy = shutil.which("mypy")
    assert mypy is not None, "mypy is required; run through the explicit typecheck dependency group"
    with tempfile.TemporaryDirectory(prefix="mcp-pal-mypy-") as cache_dir:
        result = subprocess.run(
            [mypy, "--strict", "--python-version", "3.10", "--cache-dir", cache_dir, str(EXAMPLE)],
            cwd=Path(__file__).parents[2],
            check=False,
            capture_output=True,
            text=True,
        )
    assert result.returncode == 0, result.stdout + result.stderr
