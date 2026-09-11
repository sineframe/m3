from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "check_cli_standalone.py"
_SPEC = importlib.util.spec_from_file_location("check_cli_standalone", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_GATE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_GATE)


def _wheel(path: Path, *, ui: bool = True) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        if ui:
            archive.writestr("mcp_pal_cli/ui/index.html", "<!doctype html>")
            archive.writestr("mcp_pal_cli/ui/assets/app.js", "")


def test_wheel_paths_requires_the_exact_three_release_wheels(tmp_path: Path) -> None:
    version = "1.2.3"
    names = (
        "mcp_pal_cli",
        "mcp_pal",
        "mcp_pal_app",
    )
    for name in names:
        (tmp_path / f"{name}-{version}-py3-none-any.whl").touch()
    assert [path.name for path in _GATE.wheel_paths(tmp_path, version)] == [
        f"{name}-{version}-py3-none-any.whl" for name in names
    ]
    (tmp_path / "unexpected-1.0-py3-none-any.whl").touch()
    with pytest.raises(_GATE.StandaloneGateError, match="exactly"):
        _GATE.wheel_paths(tmp_path, version)


def test_assert_bundled_ui_checks_index_and_assets(tmp_path: Path) -> None:
    wheel = tmp_path / "cli.whl"
    _wheel(wheel)
    _GATE.assert_bundled_ui(wheel)

    missing = tmp_path / "missing.whl"
    _wheel(missing, ui=False)
    with pytest.raises(_GATE.StandaloneGateError, match="bundled production UI"):
        _GATE.assert_bundled_ui(missing)


def test_clean_environment_removes_secrets_and_environment_links() -> None:
    source = {
        "PATH": "/bin",
        "OPENAI_API_KEY": "secret",
        "SERVICE_TOKEN": "token",
        "DB_PASSWORD": "password",
        "MCP_SECRET_VALUE": "secret",
        "VIRTUAL_ENV": "/project/.venv",
        "CONDA_PREFIX": "/project/conda",
        "PYTHONPATH": "/project/src",
        "UV_PROJECT_ENVIRONMENT": "/project/.venv",
        "SAFE_VALUE": "kept",
    }
    assert _GATE._clean_environment(source) == {"PATH": "/bin", "SAFE_VALUE": "kept"}


def test_redact_diagnostics_hides_secret_like_values() -> None:
    output = "OPENAI_API_KEY=secret-value token: another-secret SAFE=visible"
    redacted = _GATE._redact_diagnostics(
        output,
        {"OPENAI_API_KEY": "secret-value", "SAFE": "visible"},
    )
    assert "secret-value" not in redacted
    assert "another-secret" not in redacted
    assert "SAFE=visible" in redacted


def test_parse_ui_links_uses_the_complete_encoded_run_suffix() -> None:
    output = "\n".join(
        (
            "MCP-Pal UI: http://127.0.0.1:8123/history",
            "Run: http://127.0.0.1:8123/playground/run/run%20id%2Fpart",
        )
    )
    assert _GATE._parse_ui_links(
        output, "http://127.0.0.1:8123", "run id/part"
    ).endswith("run%20id%2Fpart")
    with pytest.raises(_GATE.StandaloneGateError, match="history link"):
        _GATE._parse_ui_links(
            output.replace("8123/history", "8124/history"),
            "http://127.0.0.1:8123",
            "run id/part",
        )


def test_standalone_gate_exercises_public_setup_command() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")
    assert '"setup", "--project-root"' in source
    assert "MCP_PAL_RELEASE_BASE_URL" in source
    assert '"uv", "pip", "install"' not in source
