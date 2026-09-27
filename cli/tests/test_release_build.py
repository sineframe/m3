from __future__ import annotations

import base64
import importlib.util
import json
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
    assert set(versions) == {"sf_m3", "sf_m3_app", "sf_m3_cli"}
    assert len(set(versions.values())) == 1


def test_classify_wheels_does_not_use_prefix_matching(tmp_path: Path) -> None:
    app = tmp_path / "sf_m3_app-1.0-py3-none-any.whl"
    sdk = tmp_path / "sf_m3-1.0-py3-none-any.whl"
    result = release.classify_wheels(
        [app, sdk],
        {"sf_m3": "1.0", "sf_m3_app": "1.0"},
    )
    assert result == {"sf_m3": sdk, "sf_m3_app": app}


def _wheel(
    output: Path,
    filename_dist: str,
    metadata_name: str,
    version: str,
    *,
    requires: tuple[str, ...] = (),
    provides_extras: tuple[str, ...] = (),
    entry_point: str | None = None,
    ui: bool = False,
    app_ui: bool = False,
    ui_content: str = "js",
    ui_assets: tuple[tuple[str, str], ...] = (),
    archive_entries: tuple[tuple[str, bytes | str], ...] = (),
) -> Path:
    wheel = output / f"{filename_dist}-{version}-py3-none-any.whl"
    info = f"{filename_dist}-{version}.dist-info"
    metadata = [
        "Metadata-Version: 2.3",
        f"Name: {metadata_name}",
        f"Version: {version}",
    ]
    metadata.extend(f"Requires-Dist: {value}" for value in requires)
    metadata.extend(f"Provides-Extra: {value}" for value in provides_extras)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{info}/METADATA", "\n".join(metadata) + "\n")
        if entry_point is not None:
            archive.writestr(
                f"{info}/entry_points.txt", "[console_scripts]\n" + entry_point + "\n"
            )
        if ui:
            archive.writestr("m3_cli/ui/index.html", "html")
            archive.writestr("m3_cli/ui/assets/app.js", ui_content)
            for name, content in ui_assets:
                archive.writestr(f"m3_cli/ui/assets/{name}", content)
        if app_ui:
            archive.writestr("m3_app/ui/__init__.py", "")
        for name, content in archive_entries:
            archive.writestr(name, content)
    return wheel


def _synthetic_release(
    root: Path,
    *,
    sdk_requires: tuple[str, ...] = (),
    cli_requires: tuple[str, ...] | None = None,
    cli_entry_point: str | None = "m3 = m3_cli.main:main",
    cli_ui: bool = True,
    app_requires: tuple[str, ...] = (),
    app_provides_extras: tuple[str, ...] = (),
    app_ui: bool = False,
    cli_ui_content: str = "js",
    cli_ui_assets: tuple[tuple[str, str], ...] = (),
    sdk_entries: tuple[tuple[str, bytes | str], ...] = (),
    app_entries: tuple[tuple[str, bytes | str], ...] = (),
    cli_entries: tuple[tuple[str, bytes | str], ...] = (),
) -> tuple[dict[str, str], Path]:
    version = "1.0"
    _wheel(
        root,
        "sf_m3",
        "sf-m3",
        version,
        requires=sdk_requires,
        provides_extras=("storage",),
        archive_entries=sdk_entries,
    )
    _wheel(
        root,
        "sf_m3_app",
        "sf-m3-app",
        version,
        requires=app_requires or (f"sf-m3[storage]=={version}",),
        provides_extras=app_provides_extras,
        app_ui=app_ui,
        archive_entries=app_entries,
    )
    _wheel(
        root,
        "sf_m3_cli",
        "sf-m3-cli",
        version,
        requires=cli_requires
        or (f"sf-m3[storage]=={version}", f"sf-m3-app=={version}"),
        entry_point=cli_entry_point,
        ui=cli_ui,
        ui_content=cli_ui_content,
        ui_assets=cli_ui_assets,
        archive_entries=cli_entries,
    )
    return {
        "sf_m3": version,
        "sf_m3_app": version,
        "sf_m3_cli": version,
    }, _valid_ui(root)


def test_verify_release_checks_metadata_entry_point_dependencies_and_ui(
    tmp_path: Path,
) -> None:
    version = "1.0"
    _wheel(tmp_path, "sf_m3", "sf-m3", version)
    _wheel(
        tmp_path,
        "sf_m3_app",
        "sf-m3-app",
        version,
        requires=(f"sf-m3[storage]=={version}",),
    )
    _wheel(
        tmp_path,
        "sf_m3_cli",
        "sf-m3-cli",
        version,
        requires=(f"sf-m3[storage]=={version}", f"sf-m3-app=={version}"),
        entry_point="m3 = m3_cli.main:main",
        ui=True,
    )
    ui = _valid_ui(tmp_path)
    result = release.verify_release(
        tmp_path,
        {"sf_m3": version, "sf_m3_app": version, "sf_m3_cli": version},
        ui_source_dist=ui,
    )
    assert set(result) == {"sf_m3", "sf_m3_app", "sf_m3_cli"}


def test_verify_release_rejects_cli_without_packaged_ui(tmp_path: Path) -> None:
    expected, ui = _synthetic_release(tmp_path, cli_ui=False)
    with pytest.raises(release.ReleaseBuildError, match=r"ui/index\.html"):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


def test_verify_release_rejects_firebase_assets_in_cli_wheel(tmp_path: Path) -> None:
    version = "1.0"
    _wheel(tmp_path, "sf_m3", "sf-m3", version)
    _wheel(
        tmp_path,
        "sf_m3_app",
        "sf-m3-app",
        version,
        requires=(f"sf-m3[storage]=={version}",),
    )
    cli = _wheel(
        tmp_path,
        "sf_m3_cli",
        "sf-m3-cli",
        version,
        requires=(f"sf-m3[storage]=={version}", f"sf-m3-app=={version}"),
        entry_point="m3 = m3_cli.main:main",
        ui=True,
    )
    with zipfile.ZipFile(cli, "a") as archive:
        archive.writestr("m3_cli/firebase/google-services.json", "{}")
    ui = _valid_ui(tmp_path)
    expected = {"sf_m3": version, "sf_m3_app": version, "sf_m3_cli": version}
    with pytest.raises(release.ReleaseBuildError, match="Firebase auth assets"):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


def test_verify_release_rejects_firebase_auth_marker_in_generic_app_bundle(
    tmp_path: Path,
) -> None:
    expected, ui = _synthetic_release(
        tmp_path, cli_ui_content='import { getAuth } from "firebase/auth";'
    )
    with pytest.raises(release.ReleaseBuildError, match="Firebase auth code or config"):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


def test_verify_release_rejects_firebase_cli_dependency(tmp_path: Path) -> None:
    version = "1.0"
    _wheel(tmp_path, "sf_m3", "sf-m3", version)
    _wheel(
        tmp_path,
        "sf_m3_app",
        "sf-m3-app",
        version,
        requires=(f"sf-m3[storage]=={version}",),
    )
    _wheel(
        tmp_path,
        "sf_m3_cli",
        "sf-m3-cli",
        version,
        requires=(
            f"sf-m3[storage]=={version}",
            f"sf-m3-app=={version}",
            "firebase-admin>=6",
        ),
        entry_point="m3 = m3_cli.main:main",
        ui=True,
    )
    ui = _valid_ui(tmp_path)
    expected = {"sf_m3": version, "sf_m3_app": version, "sf_m3_cli": version}
    with pytest.raises(release.ReleaseBuildError, match="Firebase dependency"):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


def test_verify_release_rejects_supabase_config_asset(tmp_path: Path) -> None:
    expected, ui = _synthetic_release(tmp_path)
    cli = tmp_path / "sf_m3_cli-1.0-py3-none-any.whl"
    with zipfile.ZipFile(cli, "a") as archive:
        archive.writestr("m3_cli/supabase/config.toml", "[auth]\nenabled = true\n")
    with pytest.raises(release.ReleaseBuildError, match="Supabase auth assets"):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


def test_verify_release_rejects_supabase_import_in_cli_ui(tmp_path: Path) -> None:
    expected, ui = _synthetic_release(
        tmp_path, cli_ui_content='import { createClient } from "@supabase/supabase-js";'
    )
    with pytest.raises(release.ReleaseBuildError, match="Supabase auth code or config"):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


@pytest.mark.parametrize("key_prefix", ["sb_secret_", "sb_publishable_"])
def test_verify_release_rejects_supabase_key_prefix_in_cli_ui(
    tmp_path: Path, key_prefix: str
) -> None:
    expected, ui = _synthetic_release(
        tmp_path,
        cli_ui_content=f'const key = "{key_prefix}SYNTHETIC_FIXTURE";',
    )
    with pytest.raises(
        release.ReleaseBuildError, match="release wheel contains a Supabase credential"
    ):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


@pytest.mark.parametrize("extension", ["mjs", "cjs"])
@pytest.mark.parametrize(
    ("provider_code", "message"),
    [
        ('import { getAuth } from "firebase/auth";', "Firebase auth code or config"),
        (
            'import { createClient } from "@supabase/supabase-js";',
            "Supabase auth code or config",
        ),
    ],
)
def test_verify_release_rejects_provider_code_in_module_assets(
    tmp_path: Path, extension: str, provider_code: str, message: str
) -> None:
    expected, ui = _synthetic_release(
        tmp_path,
        cli_ui_assets=((f"provider.{extension}", provider_code),),
    )
    with pytest.raises(release.ReleaseBuildError, match=message):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


@pytest.mark.parametrize("extension", ["mjs", "cjs"])
def test_verify_release_allows_provider_independent_module_assets(
    tmp_path: Path, extension: str
) -> None:
    expected, ui = _synthetic_release(
        tmp_path,
        cli_ui_assets=((f"provider.{extension}", "export const ready = true;"),),
    )
    assert release.verify_release(tmp_path, expected, ui_source_dist=ui)


def _synthetic_jwt(claims: dict[str, object]) -> bytes:
    return _synthetic_jwt_parts({"alg": "HS256", "typ": "JWT"}, claims)


def _synthetic_jwt_parts(header: object, claims: object) -> bytes:
    return _synthetic_jwt_raw(
        json.dumps(header, separators=(",", ":")).encode(),
        json.dumps(claims, separators=(",", ":")).encode(),
    )


def _synthetic_jwt_raw(header: bytes, claims: bytes) -> bytes:
    return (
        base64.urlsafe_b64encode(header).rstrip(b"=")
        + b"."
        + base64.urlsafe_b64encode(claims).rstrip(b"=")
        + b".synthetic-signature"
    )


@pytest.mark.parametrize(
    ("wheel_name", "archive_name", "key_prefix"),
    [
        ("sf_m3_cli", "m3_cli/.env", "sb_secret_"),
        ("sf_m3_cli", "m3_cli/ui/assets/app.js.map", "sb_publishable_"),
        ("sf_m3", "arbitrary/root/credential.bin", "sb_secret_"),
        ("sf_m3", "sf_m3-1.0.dist-info/NOTICE", "sb_publishable_"),
        ("sf_m3_app", "arbitrary/root/settings.env", "sb_publishable_"),
    ],
)
def test_verify_release_rejects_key_prefix_in_any_wheel_entry(
    tmp_path: Path, wheel_name: str, archive_name: str, key_prefix: str
) -> None:
    entry = ((archive_name, f"credential={key_prefix}SYNTHETIC_FIXTURE"),)
    kwargs = {
        "sf_m3": {"sdk_entries": entry},
        "sf_m3_app": {"app_entries": entry},
        "sf_m3_cli": {"cli_entries": entry},
    }[wheel_name]
    expected, ui = _synthetic_release(tmp_path, **kwargs)
    if archive_name.endswith(".map"):
        # A source map exists in the supplied production source, so the failure
        # must come from credential detection rather than source-map validation.
        (ui / "assets" / "app.js.map").write_text("{}", encoding="utf-8")
    with pytest.raises(
        release.ReleaseBuildError, match="release wheel contains a Supabase credential"
    ) as exc:
        release.verify_release(tmp_path, expected, ui_source_dist=ui)
    assert key_prefix not in str(exc.value)


def test_verify_release_rejects_key_prefix_in_archive_filename(tmp_path: Path) -> None:
    expected, ui = _synthetic_release(
        tmp_path,
        sdk_entries=(("arbitrary/sb_secret_SYNTHETIC_FIXTURE.bin", b"benign"),),
    )
    with pytest.raises(
        release.ReleaseBuildError, match="release wheel contains a Supabase credential"
    ):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


@pytest.mark.parametrize(
    ("claims", "wheel_name"),
    [
        (
            {"iss": "supabase", "ref": "synthetic-project", "role": "service_role"},
            "sf_m3_cli",
        ),
        ({"iss": "supabase", "ref": "synthetic-project", "role": "anon"}, "sf_m3_app"),
        ({"iss": "https://auth.example.test/auth/v1"}, "sf_m3"),
        ({"role": "service_role"}, "sf_m3_app"),
        ({"role": "anon", "ref": "synthetic-project"}, "sf_m3_cli"),
        (
            {
                "iss": "https://auth.example.test/auth/v1",
                "role": "authenticated",
                "aud": "authenticated",
                "session_id": "synthetic-session",
            },
            "sf_m3",
        ),
    ],
    ids=[
        "legacy-service-role",
        "legacy-anon",
        "issuer-only",
        "service-role-only",
        "anon-role-and-ref",
        "browser-access-token",
    ],
)
def test_verify_release_rejects_supabase_jwt_credentials_in_any_wheel(
    tmp_path: Path, claims: dict[str, object], wheel_name: str
) -> None:
    token = _synthetic_jwt(claims)
    entries = (("arbitrary/root/credential.bin", token),)
    kwargs = {
        "sf_m3": {"sdk_entries": entries},
        "sf_m3_app": {"app_entries": entries},
        "sf_m3_cli": {"cli_ui_content": "const auth = " + repr(token.decode()) + ";"},
    }[wheel_name]
    expected, ui = _synthetic_release(tmp_path, **kwargs)
    with pytest.raises(
        release.ReleaseBuildError, match="release wheel contains a Supabase credential"
    ) as exc:
        release.verify_release(tmp_path, expected, ui_source_dist=ui)
    assert token.decode() not in str(exc.value)


@pytest.mark.parametrize("wheel_name", ["sf_m3", "sf_m3_app", "sf_m3_cli"])
def test_verify_release_scans_arbitrary_binary_entries_in_all_wheels(
    tmp_path: Path, wheel_name: str
) -> None:
    token = _synthetic_jwt(
        {
            "iss": "supabase",
            "ref": "synthetic-project",
            "role": "service_role",
        }
    )
    entries = (("arbitrary/root/opaque.bin", b"\x00binary-prefix\xff" + token),)
    kwargs = (
        {"sdk_entries": entries}
        if wheel_name == "sf_m3"
        else (
            {"app_entries": entries}
            if wheel_name == "sf_m3_app"
            else {"cli_entries": entries}
        )
    )
    expected, ui = _synthetic_release(tmp_path, **kwargs)
    with pytest.raises(
        release.ReleaseBuildError, match="release wheel contains a Supabase credential"
    ):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


def test_verify_release_allows_unrelated_or_malformed_jwts_and_benign_binary(
    tmp_path: Path,
) -> None:
    unrelated = _synthetic_jwt(
        {"iss": "https://issuer.example.test", "sub": "synthetic", "role": "user"}
    )
    unrelated_ref = _synthetic_jwt(
        {
            "iss": "https://issuer.example.test",
            "sub": "synthetic",
            "ref": "not-a-project",
        }
    )
    malformed = b"eyJhbGciOiJIUzI1NiJ9.not-json.synthetic-signature"
    entries = (
        ("arbitrary/dotted.txt", b"ordinary.dotted.string"),
        ("arbitrary/malformed.bin", malformed),
        ("arbitrary/benign.bin", b"\x00\xffbinary-data." + unrelated),
        ("arbitrary/unrelated-ref.bin", unrelated_ref),
    )
    expected, ui = _synthetic_release(tmp_path, cli_entries=entries)
    assert release.verify_release(tmp_path, expected, ui_source_dist=ui)


def test_verify_release_detects_whitespace_jwt_header_and_new_algorithm(
    tmp_path: Path,
) -> None:
    token = _synthetic_jwt_raw(
        b'{ "ALG" : "UNLISTED-ALGORITHM", "typ": "custom" }',
        b'{"role":"service_role","exp":1}',
    )
    expected, ui = _synthetic_release(
        tmp_path,
        cli_entries=(("arbitrary/token.bin", token),),
    )
    with pytest.raises(
        release.ReleaseBuildError, match="release wheel contains a Supabase credential"
    ):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


@pytest.mark.parametrize("location", ["filename", "contents"])
def test_verify_release_detects_duplicate_jwt_claims(
    tmp_path: Path, location: str
) -> None:
    token = _synthetic_jwt_raw(
        b'{"alg":"HS256"}', b'{"role":"service_role","role":"user"}'
    )
    entry = (
        (f"arbitrary/{token.decode()}.bin", b"benign")
        if location == "filename"
        else ("arbitrary/token.bin", token)
    )
    expected, ui = _synthetic_release(tmp_path, app_entries=(entry,))
    with pytest.raises(
        release.ReleaseBuildError, match="release wheel contains a Supabase credential"
    ) as exc:
        release.verify_release(tmp_path, expected, ui_source_dist=ui)
    assert token.decode() not in str(exc.value)


def test_verify_release_scans_duplicate_archive_members(tmp_path: Path) -> None:
    expected, ui = _synthetic_release(tmp_path)
    cli = tmp_path / "sf_m3_cli-1.0-py3-none-any.whl"
    with zipfile.ZipFile(cli, "a") as archive:
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr(
                "arbitrary/duplicate.bin", b"sb_secret_DUPLICATE_SYNTHETIC"
            )
            archive.writestr("arbitrary/duplicate.bin", b"benign duplicate entry")
    with pytest.raises(
        release.ReleaseBuildError, match="release wheel contains a Supabase credential"
    ):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


@pytest.mark.parametrize("kind", ["oversized", "oversized-header", "deeply-nested"])
def test_verify_release_fails_closed_for_uninspectable_jwt_claims(
    tmp_path: Path, kind: str
) -> None:
    if kind == "oversized":
        token = _synthetic_jwt_parts(
            {"alg": "new-alg"},
            {"padding": "x" * release._MAX_JWT_JSON_BYTES},
        )
    elif kind == "oversized-header":
        token = _synthetic_jwt_parts(
            {"padding": "x" * release._MAX_JWT_HEADER_BYTES, "alg": "custom"},
            {"role": "service_role"},
        )
    else:
        # pytest can raise the interpreter's recursion limit. Construct raw
        # JSON above the actual limit without recursing in the test itself.
        depth = sys.getrecursionlimit() + 50
        token = _synthetic_jwt_raw(
            b'{"alg":"new-alg"}',
            b'{"padding":'
            + b"[" * depth
            + b"0"
            + b"]" * depth
            + b',"role":"service_role"}',
        )
    expected, ui = _synthetic_release(
        tmp_path, sdk_entries=(("arbitrary/token.bin", token),)
    )
    with pytest.raises(
        release.ReleaseBuildError, match="release wheel contains a Supabase credential"
    ):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


@pytest.mark.parametrize("dependency", ["supabase>=2", "supabase-auth>=2", "gotrue>=2"])
def test_verify_release_rejects_supabase_cli_dependencies(
    tmp_path: Path, dependency: str
) -> None:
    expected, ui = _synthetic_release(
        tmp_path,
        cli_requires=(
            "sf-m3[storage]==1.0",
            "sf-m3-app==1.0",
            dependency,
        ),
    )
    with pytest.raises(release.ReleaseBuildError, match="Supabase dependency"):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


@pytest.mark.parametrize(
    ("requires", "entry_point", "message"),
    [
        (("sf-m3[storage]==1.0",), "m3 = m3_cli.main:main", "dependencies"),
        (
            ("sf-m3[storage]==1.0", "sf-m3-app==1.0"),
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


@pytest.mark.parametrize(
    "dependency",
    [
        "requests>=2",
        "streamlit>=1",
        'requests>=2; extra == "other"',
        'streamlit>=1; extra == "other"',
    ],
)
def test_verify_release_rejects_forbidden_app_dependencies(
    tmp_path: Path, dependency: str
) -> None:
    expected, ui = _synthetic_release(
        tmp_path,
        app_requires=(dependency,),
    )
    with pytest.raises(release.ReleaseBuildError, match="forbidden dependency"):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


@pytest.mark.parametrize("extra", ["legacy-ui", "Legacy_UI"])
def test_verify_release_rejects_removed_app_extra(tmp_path: Path, extra: str) -> None:
    expected, ui = _synthetic_release(
        tmp_path,
        app_provides_extras=(extra,),
    )
    with pytest.raises(release.ReleaseBuildError, match="removed legacy-ui extra"):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


def test_verify_release_rejects_removed_app_ui_package(tmp_path: Path) -> None:
    expected, ui = _synthetic_release(tmp_path, app_ui=True)
    with pytest.raises(release.ReleaseBuildError, match="removed UI package"):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


@pytest.mark.parametrize("dependency", ["requests>=2", "streamlit>=1"])
def test_verify_release_rejects_forbidden_cli_dependencies(
    tmp_path: Path, dependency: str
) -> None:
    expected, ui = _synthetic_release(
        tmp_path,
        cli_requires=(
            "sf-m3[storage]==1.0",
            "sf-m3-app==1.0",
            dependency,
        ),
    )
    with pytest.raises(
        release.ReleaseBuildError, match="forbidden mandatory dependency"
    ):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)
