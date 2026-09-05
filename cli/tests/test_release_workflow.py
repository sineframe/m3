from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release-cli.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_release_workflow_runs_the_exact_built_release_through_standalone_gate() -> None:
    workflow = _workflow()
    gate = """      - name: Run the isolated two-environment standalone gate
        run: |
          set -euo pipefail
          release_dir="$RUNNER_TEMP/mcp-pal-release"
          python scripts/check_cli_standalone.py \\
            --release-dir "$release_dir" \\
            --version '${{ steps.version.outputs.version }}'
"""

    assert gate in workflow
    assert workflow.count("python scripts/check_cli_standalone.py") == 1


def test_release_workflow_publishes_only_after_the_standalone_gate() -> None:
    workflow = _workflow()
    gate_position = workflow.index("- name: Run the isolated two-environment standalone gate")
    publish_position = workflow.index("- name: Publish the GitHub Release for a version tag")

    assert gate_position < publish_position
    publish_block = workflow[publish_position:]
    assert "if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')" in publish_block
    assert "gh release create" in publish_block


def test_release_workflow_builds_ui_without_running_ui_quality_suites() -> None:
    workflow = _workflow()

    assert "- name: Install and build the UI" in workflow
    assert "npm ci" in workflow
    assert "npm run build" in workflow
    assert "npm test" not in workflow
    assert "npm run typecheck" not in workflow
    assert "npm run lint" not in workflow
