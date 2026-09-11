"""Keep the checked sync/async examples executable and statically typed."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

EXAMPLE = Path(__file__).parents[1] / "typecheck" / "usage_examples.py"
INVALID_ADAPTER_EXAMPLE = (
    Path(__file__).parents[1] / "typecheck" / "invalid_session_adapter.py"
)
REPOSITORY_ROOT = Path(__file__).parents[3]


def test_usage_examples_compile() -> None:
    source = EXAMPLE.read_text(encoding="utf-8")
    compile(source, str(EXAMPLE), "exec")


def _run_mypy(example: Path) -> subprocess.CompletedProcess[str]:
    uv = shutil.which("uv")
    assert uv is not None, "uv is required to run the isolated typecheck environment"
    with tempfile.TemporaryDirectory(prefix="mcp-pal-mypy-") as cache_dir:
        return subprocess.run(
            [
                uv,
                "run",
                "--isolated",
                "--python",
                "3.10",
                "--locked",
                "--project",
                "sdk",
                "--extra",
                "pytest",
                "--group",
                "typecheck",
                "mypy",
                "--config-file",
                "sdk/pyproject.toml",
                "--strict",
                "--python-version",
                "3.10",
                "--cache-dir",
                cache_dir,
                str(example),
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )


def test_usage_examples_require_mypy_strict() -> None:
    result = _run_mypy(EXAMPLE)
    assert result.returncode == 0, result.stdout + result.stderr


def test_session_only_adapter_is_rejected_by_mypy() -> None:
    result = _run_mypy(INVALID_ADAPTER_EXAMPLE)
    diagnostics = result.stdout + result.stderr
    errors = [line for line in diagnostics.splitlines() if "error:" in line]
    assert result.returncode != 0
    assert len(errors) == 1, diagnostics
    assert "[arg-type]" in diagnostics, diagnostics
