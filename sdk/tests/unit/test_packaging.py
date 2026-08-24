"""Packaging and import-boundary checks for the distributable SDK."""

from __future__ import annotations

import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

import mcp_pal


def test_runtime_version_comes_from_distribution_metadata() -> None:
    assert mcp_pal.__version__ == importlib.metadata.version("mcp-pal")


def test_fastapi_metadata_uses_distribution_version() -> None:
    from mcp_pal.api import create_app

    assert create_app().openapi()["info"]["version"] == importlib.metadata.version("mcp-pal")


def test_runtime_schema_is_packaged() -> None:
    schemas = Path(mcp_pal.__file__).parent / "schemas"
    assert (schemas / "mcp-pal.harness.v1.schema.json").is_file()
    assert (schemas / "mcp-pal.event.v0.2.schema.json").is_file()


def test_import_has_no_filesystem_side_effects(tmp_path: Path) -> None:
    environment = os.environ.copy()
    source_root = Path(__file__).parents[2] / "src"
    environment["PYTHONPATH"] = str(source_root)

    result = subprocess.run(
        [sys.executable, "-c", "import mcp_pal"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
