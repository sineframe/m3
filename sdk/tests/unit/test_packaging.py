"""Packaging and import-boundary checks for the distributable SDK."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import m3


def test_runtime_version_comes_from_distribution_metadata() -> None:
    assert m3.__version__ == importlib.metadata.version("sf-m3")


def test_runtime_schema_is_packaged() -> None:
    schemas = Path(m3.__file__).parent / "schemas"
    assert (schemas / "m3.harness.v1.schema.json").is_file()
    assert (schemas / "m3.event.v0.2.schema.json").is_file()


def test_acp_fixture_agent_is_private_and_bridge_package_is_gone() -> None:
    assert importlib.util.find_spec("m3.fixtures.acp_agent") is not None
    assert importlib.util.find_spec("m3.bridge") is None


def test_import_has_no_filesystem_side_effects(tmp_path: Path) -> None:
    environment = os.environ.copy()
    source_root = Path(__file__).parents[3] / "sdk" / "src"
    environment["PYTHONPATH"] = str(source_root)

    result = subprocess.run(
        [sys.executable, "-c", "import m3"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
