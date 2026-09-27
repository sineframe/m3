from __future__ import annotations

from pathlib import Path

import pytest
from test_release_build import _synthetic_release, release


def _release_with_dependency(
    root: Path, wheel: str, dependency: str
) -> tuple[dict[str, str], Path]:
    if wheel == "sf_m3":
        return _synthetic_release(root, sdk_requires=(dependency,))
    if wheel == "sf_m3_app":
        return _synthetic_release(
            root, app_requires=("sf-m3[storage]==1.0", dependency)
        )
    return _synthetic_release(
        root,
        cli_requires=("sf-m3[storage]==1.0", "sf-m3-app==1.0", dependency),
    )


@pytest.mark.parametrize(
    ("wheel", "dependency"),
    [
        ("sf_m3", "PostgREST>=1; extra == 'storage'"),
        ("sf_m3", "GoTrue>=2; python_version < '3.13'"),
        ("sf_m3_app", "Supabase>=2"),
        ("sf_m3_app", "Supabase_Auth>=2; sys_platform == 'win32'"),
        ("sf_m3_app", "Firebase_Admin>=6; extra == 'legacy'"),
        ("sf_m3_cli", "postgrest>=1; extra == 'legacy'"),
    ],
)
def test_provider_dependencies_are_forbidden_from_every_release_wheel(
    tmp_path: Path, wheel: str, dependency: str
) -> None:
    expected, ui = _release_with_dependency(tmp_path, wheel, dependency)
    with pytest.raises(
        release.ReleaseBuildError, match=r"(Firebase|Supabase) dependency"
    ):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


def test_current_complete_release_wheels_pass_provider_dependency_guard(
    tmp_path: Path,
) -> None:
    expected, ui = _synthetic_release(
        tmp_path, sdk_requires=("sqlalchemy>=2; extra == 'storage'",)
    )
    assert release.verify_release(tmp_path, expected, ui_source_dist=ui) == {
        "sf_m3": next(tmp_path.glob("sf_m3-*.whl")),
        "sf_m3_app": next(tmp_path.glob("sf_m3_app-*.whl")),
        "sf_m3_cli": next(tmp_path.glob("sf_m3_cli-*.whl")),
    }
