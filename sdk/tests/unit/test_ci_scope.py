from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


_MODULE_PATH = Path(__file__).parents[3] / "scripts" / "ci_scope.py"
_SPEC = importlib.util.spec_from_file_location("ci_scope", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules["ci_scope"] = _MODULE
_SPEC.loader.exec_module(_MODULE)
Scope = _MODULE.Scope
scope_for_paths = _MODULE.scope_for_paths


def test_scope_selection_keeps_dependents_for_sdk_changes() -> None:
    assert scope_for_paths(("sdk/src/mcp_pal/direct_client.py",)) == Scope.all()
    assert scope_for_paths(("sdk/tests/unit/test_direct_client.py",)) == Scope.all()


def test_scope_selection_maps_app_and_cli_changes() -> None:
    assert scope_for_paths(("app/src/mcp_pal_app/main.py",)) == Scope(
        sdk=False, app=True, cli=True
    )
    assert scope_for_paths(("cli/src/mcp_pal_cli/main.py",)) == Scope(
        sdk=False, app=False, cli=True
    )


def test_scope_selection_is_conservative_for_root_and_unknown_changes() -> None:
    assert scope_for_paths(("uv.lock",)) == Scope.all()
    assert scope_for_paths((".github/workflows/ci.yml",)) == Scope.all()
    assert scope_for_paths(("new-root-config.ini",)) == Scope.all()


def test_documentation_only_changes_do_not_run_compatibility_suites() -> None:
    assert not scope_for_paths(("README.md", "docs/build.md", "plans/ci.md")).any


def test_scope_selection_keeps_deleted_code_in_scope() -> None:
    assert scope_for_paths(("app/src/mcp_pal_app/removed.py",)) == Scope(
        sdk=False, app=True, cli=True
    )


def test_changed_paths_disables_rename_collapse(monkeypatch) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="sdk/src/mcp_pal/old.py\ndocs/new.md\n")

    monkeypatch.setattr(_MODULE.subprocess, "run", fake_run)
    assert _MODULE.changed_paths("base", "head") == (
        "sdk/src/mcp_pal/old.py",
        "docs/new.md",
    )
    command, kwargs = calls[0]
    assert "--no-renames" in command
    assert "--diff-filter=ACMRTUXBD" in command
    assert kwargs["check"] is True
    assert scope_for_paths(_MODULE.changed_paths("base", "head")) == Scope.all()


def test_scope_command_is_conservative_for_invalid_ranges() -> None:
    result = subprocess.run(
        [sys.executable, str(_MODULE_PATH), "--base", "missing", "--head", "also-missing"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert dict(line.split("=", 1) for line in result.stdout.splitlines()) == {
        "sdk": "true",
        "app": "true",
        "cli": "true",
        "any": "true",
    }
