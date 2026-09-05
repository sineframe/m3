from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _windows_job() -> str:
    workflow = _workflow()
    job_start = workflow.index("  windows-installer:\n")
    job_end = workflow.index("\n  package:\n", job_start)
    return workflow[job_start:job_end]


def test_windows_installer_job_is_self_contained_and_pinned() -> None:
    job = _windows_job()

    assert "runs-on: windows-latest" in job
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in job
    assert "setup-uv" not in job
    assert "setup-python" not in job
    assert "MCPPAL_UI_TOKEN" not in job
    assert "mcppal-ui" not in job
    assert "pip install" not in job
    assert "cli/pyproject.toml" in job
    assert "\\r?$" in job
    assert "scripts/install.ps1.in" in job
    assert "@MCP_PAL_VERSION@" in job


def test_windows_installer_job_uses_native_powershell_parser_after_rendering() -> None:
    job = _windows_job()

    render_position = job.index("$template.Replace('@MCP_PAL_VERSION@', $version)")
    parser_position = job.index("[System.Management.Automation.Language.Parser]::ParseFile")

    assert render_position < parser_position
    assert "shell: pwsh" in job
    assert "[ref] $tokens" in job
    assert "[ref] $parseErrors" in job
    assert "$parseErrors.Count -gt 0" in job
    assert "throw \"PowerShell parser found syntax errors" in job
