"""Integrity checks for the section 7 regression manifest."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any


_REPOSITORY_ROOT = Path(__file__).parents[3]
_PLAN = _REPOSITORY_ROOT / "plans" / "python-testing-sdk-v0.2-remediation.md"
_MANIFEST = _REPOSITORY_ROOT / "sdk" / "tests" / "regression_manifest.json"
_VALID_STATUSES = {"passing", "strict_xfail", "live", "planned"}


def _section_7_rows() -> dict[str, tuple[str, ...]]:
    lines = _PLAN.read_text(encoding="utf-8").splitlines()
    in_section = False
    rows: dict[str, tuple[str, ...]] = {}
    for line in lines:
        if line == "## 7. Finding-to-work mapping":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section or not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0] == "Confirmed finding":
            continue
        owners = tuple(part.strip() for part in cells[1].split("/"))
        rows[cells[0]] = owners
    return rows


def _node_exists(regression: str) -> None:
    path_text, *nodes = regression.split("::")
    path = _REPOSITORY_ROOT / path_text
    assert path.is_file(), regression
    source = path.read_text(encoding="utf-8")
    for node in nodes:
        assert re.search(
            rf"\b(?:async\s+def|def|class)\s+{re.escape(node)}\b", source
        ), regression


def _function_decorators(regression: str) -> tuple[str, ...]:
    path_text, *nodes = regression.split("::")
    assert len(nodes) == 1, regression
    path = _REPOSITORY_ROOT / path_text
    source = path.read_text(encoding="utf-8")
    functions = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    function = functions[nodes[0]]
    return tuple(
        segment
        for decorator in function.decorator_list
        if (segment := ast.get_source_segment(source, decorator)) is not None
    )


def test_manifest_matches_section_7_findings() -> None:
    manifest: dict[str, Any] = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    entries = manifest["entries"]
    assert manifest["schema_version"] == 1
    assert isinstance(entries, list) and entries

    findings = _section_7_rows()
    entry_findings = [entry["finding"] for entry in entries]
    entry_ids = [entry["id"] for entry in entries]
    assert len(entry_findings) == len(set(entry_findings))
    assert len(entry_ids) == len(set(entry_ids))
    assert set(entry_findings) == set(findings)

    for entry in entries:
        assert entry["owner_milestones"] == list(findings[entry["finding"]])
        assert entry["status"] in _VALID_STATUSES
        gate = entry["gate"]
        assert isinstance(gate["command"], str) and gate["command"].strip()
        assert isinstance(gate["expected"], str) and gate["expected"].strip()
        regression = entry["regression"]
        if entry["status"] == "planned":
            assert isinstance(regression, dict)
            assert regression["exists"] is False
            assert not (_REPOSITORY_ROOT / regression["path"].split("::", 1)[0]).exists()
        else:
            assert isinstance(regression, str)
            _node_exists(regression)

    strict_xfails = [entry for entry in entries if entry["status"] == "strict_xfail"]
    assert len(strict_xfails) == 1
    for entry in strict_xfails:
        decorators = _function_decorators(entry["regression"])
        xfail = tuple(item for item in decorators if item.startswith("pytest.mark.xfail"))
        assert len(xfail) == 1, entry["regression"]
        assert re.search(r"\bstrict\s*=\s*True\b", xfail[0]), entry["regression"]

    live_entries = [entry for entry in entries if entry["status"] == "live"]
    assert len(live_entries) == 1
    live_source = (_REPOSITORY_ROOT / "sdk/tests/e2e/test_live_opencode.py").read_text(
        encoding="utf-8"
    )
    assert "pytest.mark.xfail" not in live_source
