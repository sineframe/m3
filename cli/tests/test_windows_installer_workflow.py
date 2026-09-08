from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _quality_job() -> str:
    workflow = _workflow()
    job_start = workflow.index("  quality:\n")
    job_end = workflow.index("\n  cli-standalone:\n", job_start)
    return workflow[job_start:job_end]


def test_installer_parser_runs_in_consolidated_quality_job() -> None:
    job = _quality_job()

    assert "runs-on: ubuntu-latest" in job
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in job
    assert "MCPPAL_UI_TOKEN" not in job
    assert "mcppal-ui" not in job
    assert "pip install" not in job
    assert "cli/pyproject.toml" in job
    assert "\\r?$" in job
    assert "scripts/install.ps1.in" in job
    assert "@MCP_PAL_VERSION@" in job


def test_installer_parser_uses_native_powershell_parser_after_rendering() -> None:
    job = _quality_job()

    render_position = job.index("$template.Replace('@MCP_PAL_VERSION@', $version)")
    parser_position = job.index("[System.Management.Automation.Language.Parser]::ParseFile")

    assert render_position < parser_position
    assert "shell: pwsh" in job
    assert "[ref] $tokens" in job
    assert "[ref] $parseErrors" in job
    assert "$parseErrors.Count -gt 0" in job
    assert "throw \"PowerShell parser found syntax errors" in job


def test_ci_concurrency_separates_scheduled_and_push_runs() -> None:
    workflow = _workflow()
    assert (
        "group: ci-${{ github.workflow }}-${{ github.event_name }}-${{ github.ref }}"
        in workflow
    )
