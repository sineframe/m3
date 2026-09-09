"""Collection-boundary coverage for matrix pytest integration."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.process_lifecycle


_SDK_ROOT = Path(__file__).parents[2]
_SRC_ROOT = _SDK_ROOT / "src"


def _pytest_env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(_SRC_ROOT) if not existing else f"{_SRC_ROOT}{os.pathsep}{existing}"
    return env


def test_matrix_collection_has_exact_ids_custom_argname_marks_fixtures_and_k_filter(
    tmp_path: Path,
) -> None:
    sentinel = tmp_path / "server-ran"
    server_script = tmp_path / "sentinel_server.py"
    server_script.write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    test_file = tmp_path / "test_matrix_collection.py"
    test_file.write_text(
        f"""
import sys

import pytest

from mcp_pal.matrix import (
    HarnessCase,
    HarnessMatrix,
    HarnessMatrixCase,
    ServerCase,
    ToolCase,
    ToolMatrix,
    ToolMatrixCase,
)
from mcp_pal.types import ACPAgent, StdioServer

sentinel_server = StdioServer(
    name="catalog",
    command=sys.executable,
    args=({str(server_script)!r},),
)
catalog = ServerCase(
    name="catalog",
    server=sentinel_server,
    tools=(ToolCase(name="search"), ToolCase(name="get")),
)
warehouse = ServerCase(
    name="warehouse",
    server=StdioServer(
        name="warehouse",
        command=sys.executable,
        args=({str(server_script)!r},),
    ),
    tools=(ToolCase(name="stock"),),
)
harnesses = (
    HarnessCase(name="alpha", harness=ACPAgent(model="alpha")),
    HarnessCase(name="beta", harness=ACPAgent(model="beta")),
)
tool_matrix = ToolMatrix(servers=(catalog, warehouse))
harness_matrix = HarnessMatrix.each_server(
    servers=(catalog, warehouse), harnesses=harnesses, trials=2
)

def _forbidden(*args, **kwargs):
    raise AssertionError("matrix execution helper invoked during collection")

ToolMatrixCase.run = _forbidden
ToolMatrixCase.run_async = _forbidden
HarnessMatrixCase.run = _forbidden
HarnessMatrixCase.run_async = _forbidden
HarnessMatrixCase.session = _forbidden
HarnessMatrixCase.async_session = _forbidden

@pytest.fixture
def ordinary_fixture(tmp_path):
    marker = tmp_path / "fixture-used"
    marker.write_text("used")
    return marker

@tool_matrix.parametrize("tool_case")
def test_tool(tool_case, ordinary_fixture):
    assert ordinary_fixture.exists()
    assert tool_case.id in {{"catalog/search", "catalog/get", "warehouse/stock"}}

@pytest.mark.usefixtures("ordinary_fixture")
@harness_matrix.parametrize()
def test_harness(case):
    assert case.id
""",
        encoding="utf-8",
    )

    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", test_file.name],
        cwd=tmp_path,
        env=_pytest_env(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    expected_ids = (
        "catalog/search",
        "catalog/get",
        "warehouse/stock",
        "catalog/alpha/trial-1",
        "catalog/alpha/trial-2",
        "catalog/beta/trial-1",
        "catalog/beta/trial-2",
        "warehouse/alpha/trial-1",
        "warehouse/alpha/trial-2",
        "warehouse/beta/trial-1",
        "warehouse/beta/trial-2",
    )
    expected_nodes = tuple(
        f"{test_file.name}::"
        f"{'test_tool' if case_id.count('/') == 1 else 'test_harness'}[{case_id}]"
        for case_id in expected_ids
    )
    collected_nodes = tuple(
        line.strip()
        for line in collected.stdout.splitlines()
        if line.strip().startswith(f"{test_file.name}::")
    )
    assert collected_nodes == expected_nodes
    assert not sentinel.exists()

    filtered = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-k", "catalog and beta", test_file.name],
        cwd=tmp_path,
        env=_pytest_env(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert filtered.returncode == 0, filtered.stdout + filtered.stderr
    assert "2 passed" in filtered.stdout
    assert "9 deselected" in filtered.stdout
    assert not sentinel.exists()


def test_matrix_import_and_cases_work_when_pytest_is_unavailable(tmp_path: Path) -> None:
    smoke = tmp_path / "matrix_import_smoke.py"
    smoke.write_text(
        """
import builtins

original_import = builtins.__import__

def block_pytest(name, *args, **kwargs):
    if name == "pytest" or name.startswith("pytest."):
        raise ModuleNotFoundError("No module named 'pytest'", name="pytest")
    return original_import(name, *args, **kwargs)

builtins.__import__ = block_pytest
import mcp_pal
from mcp_pal.matrix import ServerCase, ToolCase, ToolMatrix
from mcp_pal.types import StdioServer

matrix = ToolMatrix(servers=(ServerCase(
    name="catalog",
    server=StdioServer(name="catalog", command="not-started"),
    tools=(ToolCase(name="search"),),
),))
assert [case.id for case in matrix.cases()] == ["catalog/search"]
print("ok")
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(smoke)],
        cwd=tmp_path,
        env=_pytest_env(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ok"
