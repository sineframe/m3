"""Integrity checks for the current regression evidence manifest."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_REPOSITORY_ROOT = Path(__file__).parents[3]
_MANIFEST = _REPOSITORY_ROOT / "sdk" / "tests" / "regression_manifest.json"
_VALID_STATUSES = {"passing", "live", "planned"}


def _node_exists(regression: str) -> bool:
    path_text, *nodes = regression.split("::")
    path = _REPOSITORY_ROOT / path_text
    if not path.is_file():
        return False
    source = path.read_text(encoding="utf-8")
    for node in nodes:
        if not re.search(rf"\b(?:async\s+def|def|class)\s+{re.escape(node)}\b", source):
            return False
    return True


def test_manifest_describes_current_regression_evidence() -> None:
    manifest: dict[str, Any] = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    entries = manifest["entries"]
    assert manifest["schema_version"] == 2
    assert isinstance(entries, list) and entries

    entry_findings = [entry["finding"] for entry in entries]
    entry_ids = [entry["id"] for entry in entries]
    assert len(entry_findings) == len(set(entry_findings))
    assert len(entry_ids) == len(set(entry_ids))

    for entry in entries:
        assert isinstance(entry["owner_milestones"], list)
        assert entry["owner_milestones"]
        assert entry["status"] in _VALID_STATUSES
        gate = entry["gate"]
        assert isinstance(gate["command"], str) and gate["command"].strip()
        assert isinstance(gate["expected"], str) and gate["expected"].strip()
        regression = entry["regression"]
        if entry["status"] == "planned":
            assert isinstance(regression, dict)
            assert isinstance(regression.get("path"), str)
            assert "::" in regression["path"]
            assert isinstance(regression.get("note"), str)
            assert regression["note"].strip()
            assert not _node_exists(regression["path"])
        else:
            regressions = [regression] if isinstance(regression, str) else regression
            assert isinstance(regressions, list) and regressions
            assert all(isinstance(item, str) and item.strip() for item in regressions)
            for item in regressions:
                assert _node_exists(item), item

    live_entries = [entry for entry in entries if entry["status"] == "live"]
    assert len(live_entries) == 1
    live_source = (_REPOSITORY_ROOT / "sdk/tests/e2e/test_live_opencode.py").read_text(
        encoding="utf-8"
    )
    assert "pytest.mark.xfail" not in live_source
