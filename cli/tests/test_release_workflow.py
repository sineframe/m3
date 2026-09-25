from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release-cli.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_release_builds_and_gates_exact_tagged_assets_before_publication() -> None:
    workflow = _workflow()
    for required in (
        "name: Build assets",
        'python scripts/check_cli_standalone.py --release-dir "$out" --version "$VERSION" --ui-dir .release-ui',
        "name: Upload immutable release artifact",
        "name: Publish to PyPI",
        "name: Publish wheels with PyPI Trusted Publishing",
    ):
        assert required in workflow
    assert workflow.index("name: Build assets") < workflow.index(
        "name: Publish to PyPI"
    )
    assert workflow.index(
        "name: Verify original bytes and stage draft release"
    ) < workflow.index("name: Publish wheels with PyPI Trusted Publishing")


def test_release_builds_production_ui_and_skips_ui_quality_suites() -> None:
    workflow = _workflow()

    assert "npm ci" in workflow
    assert "npm run build" in workflow
    assert "npm test" not in workflow
    assert "npm run typecheck" not in workflow
    assert "npm run lint" not in workflow
    assert "--ui-dir .release-ui" in workflow


def test_release_uses_pep440_tag_version_and_does_not_commit_preparation() -> None:
    workflow = _workflow()

    assert "from packaging.version import Version" in workflow
    assert (
        'uv run --no-project --with packaging python scripts/prepare_release.py "$VERSION"'
        in workflow
    )
    assert "git commit" not in workflow
    assert "git push" not in workflow


def test_tag_publication_waits_for_prepared_source_tests_and_managed_assets() -> None:
    workflow = _workflow()

    for required in (
        "release-source-checks:",
        "release-tests:",
        "name: Tests (Python 3.10)",
        'python-version: "3.10"',
        "release-managed-assets:",
        'uv run --no-project --with packaging python scripts/prepare_release.py "$version"',
        'pytest -q -n 2 --dist worksteal -m "not live and not process_lifecycle" sdk/tests',
        'pytest -q -m "not live and process_lifecycle" sdk/tests',
        "{ os: ubuntu-latest, kind: opencode }",
        "{ os: macos-latest, kind: claude }",
        "{ os: windows-latest, kind: codex }",
        "needs: [build, release-source-checks, release-tests, release-managed-assets]",
        "needs.release-source-checks.result == 'success'",
        "needs.release-tests.result == 'success'",
        "needs.release-managed-assets.result == 'success'",
    ):
        assert required in workflow

    assert 'M3_RUN_LIVE_MANAGED_ASSETS: "1"' in workflow
    assert "M3_LIVE_MANAGED_KIND: ${{ matrix.kind }}" in workflow
    assert "matrix.python-version" not in workflow
    assert all(f'python-version: "3.{minor}"' not in workflow for minor in (11, 12, 13))


def test_release_recovery_bypasses_skipped_preflight_without_running_build() -> None:
    workflow = _workflow()

    publish = workflow.split("\n  publish:\n", maxsplit=1)[1].split(
        "\n  verify-pypi:\n", maxsplit=1
    )[0]
    assert "if: github.event_name == 'push' || inputs.operation == 'build'" in workflow
    assert "inputs.operation == 'recover_tag'" in publish
    assert "needs.build.result == 'success'" in publish
    assert "needs.release-tests.result == 'success'" in publish
    assert "needs.release-managed-assets.result == 'success'" in publish


def test_draft_recovery_verifies_original_asset_manifest_and_tag_commit() -> None:
    workflow = _workflow()

    assert "Download existing draft assets for recovery" in workflow
    assert 'manifest.get("source_commit") != commit' in workflow
    assert "draft release asset set is incomplete or unexpected" in workflow
    assert 'raise SystemExit(f"asset hash mismatch: {name}")' in workflow


def test_public_smoke_follows_pypi_install_checks_and_release_promotion() -> None:
    workflow = _workflow()

    assert "name: PyPI installs (" in workflow
    smoke = ROOT.joinpath("scripts/smoke_pypi_install.py").read_text()
    for method in ("# uv add:", "# uv pip:", "# uvx:", "# pip:"):
        assert method in smoke
    assert '"tool",\n                "install"' in smoke
    assert "name: Publish GitHub release" in workflow
    assert "name: Public installer (" in workflow
    assert workflow.index(
        'gh release edit "$TAG" --repo sineframe/m3 --draft=false'
    ) < workflow.index("name: Public installer (")
