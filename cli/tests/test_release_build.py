from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "build_cli_release.py"
_SPEC = importlib.util.spec_from_file_location("build_cli_release", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
release = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = release
_SPEC.loader.exec_module(release)


def _valid_ui(root: Path) -> Path:
    ui = root / "ui"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (ui / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    return ui


@pytest.mark.parametrize("missing", ["index", "assets", "empty-assets"])
def test_validate_ui_dist_rejects_invalid_bundle(tmp_path: Path, missing: str) -> None:
    ui = tmp_path / "ui"
    ui.mkdir()
    if missing != "index":
        (ui / "index.html").write_text("html", encoding="utf-8")
    if missing == "assets":
        pass
    elif missing == "empty-assets":
        (ui / "assets").mkdir()
    else:
        (ui / "assets").mkdir()
        (ui / "assets" / "app.js").write_text("js", encoding="utf-8")
    with pytest.raises(release.ReleaseBuildError):
        release.validate_ui_dist(ui, tmp_path / "out")


def test_validate_ui_dist_rejects_bundle_inside_output(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ui = out / "ui"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("html", encoding="utf-8")
    (ui / "assets" / "app.js").write_text("js", encoding="utf-8")
    with pytest.raises(release.ReleaseBuildError, match="must not overlap"):
        release.validate_ui_dist(ui, out)


def test_build_release_rejects_nonempty_output_before_build(tmp_path: Path) -> None:
    ui = _valid_ui(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "old.whl").write_text("old", encoding="utf-8")
    with pytest.raises(release.ReleaseBuildError, match="must be empty"):
        release.build_release(ui, out)


def test_build_release_rejects_tag_version_mismatch(tmp_path: Path) -> None:
    ui = _valid_ui(tmp_path)
    with pytest.raises(
        release.ReleaseBuildError, match="does not match expected version"
    ):
        release.build_release(ui, tmp_path / "out", expected_version="9.9.9")


def test_project_versions_are_common() -> None:
    versions = release.project_versions()
    assert set(versions) == {"mcp_pal", "mcp_pal_app", "mcp_pal_cli"}
    assert len(set(versions.values())) == 1


def test_classify_wheels_does_not_use_prefix_matching(tmp_path: Path) -> None:
    app = tmp_path / "mcp_pal_app-1.0-py3-none-any.whl"
    sdk = tmp_path / "mcp_pal-1.0-py3-none-any.whl"
    result = release.classify_wheels(
        [app, sdk],
        {"mcp_pal": "1.0", "mcp_pal_app": "1.0"},
    )
    assert result == {"mcp_pal": sdk, "mcp_pal_app": app}


def _wheel(
    output: Path,
    filename_dist: str,
    metadata_name: str,
    version: str,
    *,
    requires: tuple[str, ...] = (),
    entry_point: str | None = None,
    ui: bool = False,
) -> Path:
    wheel = output / f"{filename_dist}-{version}-py3-none-any.whl"
    info = f"{filename_dist}-{version}.dist-info"
    metadata = [
        "Metadata-Version: 2.3",
        f"Name: {metadata_name}",
        f"Version: {version}",
    ]
    metadata.extend(f"Requires-Dist: {value}" for value in requires)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{info}/METADATA", "\n".join(metadata) + "\n")
        if entry_point is not None:
            archive.writestr(
                f"{info}/entry_points.txt", "[console_scripts]\n" + entry_point + "\n"
            )
        if ui:
            archive.writestr("mcp_pal_cli/ui/index.html", "html")
            archive.writestr("mcp_pal_cli/ui/assets/app.js", "js")
    return wheel


def _synthetic_release(
    root: Path,
    *,
    cli_requires: tuple[str, ...] | None = None,
    cli_entry_point: str | None = "mcp-pal = mcp_pal_cli.main:main",
    cli_ui: bool = True,
    app_requires: tuple[str, ...] = (),
) -> tuple[dict[str, str], Path]:
    version = "1.0"
    _wheel(root, "mcp_pal", "mcp-pal", version)
    _wheel(root, "mcp_pal_app", "mcp-pal-app", version, requires=app_requires)
    _wheel(
        root,
        "mcp_pal_cli",
        "mcp-pal-cli",
        version,
        requires=cli_requires
        or (f"mcp-pal[storage]=={version}", f"mcp-pal-app=={version}"),
        entry_point=cli_entry_point,
        ui=cli_ui,
    )
    return {
        "mcp_pal": version,
        "mcp_pal_app": version,
        "mcp_pal_cli": version,
    }, _valid_ui(root)


def test_verify_release_checks_metadata_entry_point_dependencies_and_ui(
    tmp_path: Path,
) -> None:
    version = "1.0"
    _wheel(tmp_path, "mcp_pal", "mcp-pal", version)
    _wheel(
        tmp_path,
        "mcp_pal_app",
        "mcp-pal-app",
        version,
        requires=(
            'requests>=2; extra == "legacy-ui"',
            'streamlit>=1; extra == "legacy-ui"',
        ),
    )
    _wheel(
        tmp_path,
        "mcp_pal_cli",
        "mcp-pal-cli",
        version,
        requires=(f"mcp-pal[storage]=={version}", f"mcp-pal-app=={version}"),
        entry_point="mcp-pal = mcp_pal_cli.main:main",
        ui=True,
    )
    ui = _valid_ui(tmp_path)
    result = release.verify_release(
        tmp_path,
        {"mcp_pal": version, "mcp_pal_app": version, "mcp_pal_cli": version},
        ui_source_dist=ui,
    )
    assert set(result) == {"mcp_pal", "mcp_pal_app", "mcp_pal_cli"}


def test_verify_release_rejects_cli_without_packaged_ui(tmp_path: Path) -> None:
    expected, ui = _synthetic_release(tmp_path, cli_ui=False)
    with pytest.raises(release.ReleaseBuildError, match=r"ui/index\.html"):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


@pytest.mark.parametrize(
    ("requires", "entry_point", "message"),
    [
        (("mcp-pal[storage]==1.0",), "mcp-pal = mcp_pal_cli.main:main", "dependencies"),
        (
            ("mcp-pal[storage]==1.0", "mcp-pal-app==1.0"),
            None,
            "entry point",
        ),
    ],
)
def test_verify_release_rejects_incomplete_cli_contract(
    tmp_path: Path,
    requires: tuple[str, ...],
    entry_point: str | None,
    message: str,
) -> None:
    expected, ui = _synthetic_release(
        tmp_path,
        cli_requires=requires,
        cli_entry_point=entry_point,
    )
    with pytest.raises(release.ReleaseBuildError, match=message):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


@pytest.mark.parametrize("target", ["app", "cli"])
@pytest.mark.parametrize("dependency", ["requests>=2", "streamlit>=1"])
def test_verify_release_rejects_forbidden_mandatory_dependencies(
    tmp_path: Path, target: str, dependency: str
) -> None:
    expected, ui = _synthetic_release(
        tmp_path,
        app_requires=(dependency,) if target == "app" else (),
        cli_requires=(
            "mcp-pal[storage]==1.0",
            "mcp-pal-app==1.0",
            dependency,
        ),
    )
    with pytest.raises(
        release.ReleaseBuildError, match="forbidden mandatory dependency"
    ):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


def test_ui_ref_is_the_pinned_commit() -> None:
    assert (release.ROOT / "cli" / "UI_REF").read_text(encoding="utf-8") == (
        "ad8a4d2c93e7cfa48f9ff8c18cb57d531640a503\n"
    )
