from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_release_build import (
    _synthetic_jwt_parts,
    _synthetic_jwt_raw,
    _synthetic_release,
    release,
)

_WHEELS = ["sf_m3", "sf_m3_app", "sf_m3_cli"]
_CUSTOM_TOKEN_AUDIENCE = "https://identitytoolkit.googleapis.com/google.identity.identitytoolkit.v1.IdentityToolkit"
_PRIVATE_KEY = (
    "-----BEGIN PRIVATE KEY-----\nSYNTHETIC-NOT-A-REAL-KEY\n-----END PRIVATE KEY-----"
)


def _release_with_asset(
    root: Path, wheel: str, name: str, contents: bytes
) -> tuple[dict[str, str], Path]:
    argument = {
        "sf_m3": "sdk_entries",
        "sf_m3_app": "app_entries",
        "sf_m3_cli": "cli_entries",
    }[wheel]
    return _synthetic_release(root, **{argument: ((name, contents),)})


@pytest.mark.parametrize("wheel", _WHEELS)
@pytest.mark.parametrize("location", ["contents", "filename"])
@pytest.mark.parametrize(
    "claims",
    [
        {
            "iss": "https://securetoken.google.com/synthetic-project",
            "aud": "synthetic-project",
            "sub": "synthetic-user",
            "exp": 1,
        },
        {
            "iss": "https://session.firebase.google.com/synthetic-project",
            "sub": "synthetic-user",
        },
        {
            "iss": "synthetic@synthetic-project.iam.gserviceaccount.com",
            "aud": _CUSTOM_TOKEN_AUDIENCE,
            "uid": "synthetic-user",
        },
    ],
    ids=["id-token", "session-cookie", "custom-token"],
)
def test_release_rejects_firebase_jwts_in_every_wheel(
    tmp_path: Path, wheel: str, location: str, claims: dict[str, object]
) -> None:
    token = _synthetic_jwt_parts({"alg": "RS256", "typ": "JWT"}, claims)
    name, contents = (
        (f"arbitrary/{token.decode()}.bin", b"benign")
        if location == "filename"
        else ("arbitrary/token.bin", b"\x00\xff" + token)
    )
    expected, ui = _release_with_asset(tmp_path, wheel, name, contents)
    with pytest.raises(release.ReleaseBuildError, match="provider credential") as exc:
        release.verify_release(tmp_path, expected, ui_source_dist=ui)
    assert token.decode() not in str(exc.value)


@pytest.mark.parametrize("wheel", _WHEELS)
@pytest.mark.parametrize("extension", ["json", "env", "bin"])
@pytest.mark.parametrize("key_type", ["PRIVATE KEY", "RSA PRIVATE KEY"])
def test_release_rejects_service_account_private_keys_in_every_wheel(
    tmp_path: Path, wheel: str, extension: str, key_type: str
) -> None:
    contents = json.dumps(
        {
            "type": "service_account",
            "project_id": "synthetic-project",
            "client_email": "synthetic@synthetic-project.iam.gserviceaccount.com",
            "private_key": _PRIVATE_KEY.replace("PRIVATE KEY", key_type),
        }
    ).encode()
    expected, ui = _release_with_asset(
        tmp_path, wheel, f"arbitrary/config.{extension}", contents
    )
    with pytest.raises(release.ReleaseBuildError, match="provider credential") as exc:
        release.verify_release(tmp_path, expected, ui_source_dist=ui)
    assert "SYNTHETIC-NOT-A-REAL-KEY" not in str(exc.value)
    assert "synthetic-project" not in str(exc.value)


@pytest.mark.parametrize("wheel", _WHEELS)
@pytest.mark.parametrize(
    ("name", "provider"),
    [
        ("arbitrary/firebase/config", "Firebase"),
        ("arbitrary/supabase/config", "Supabase"),
    ],
)
def test_release_rejects_provider_asset_names_in_every_wheel(
    tmp_path: Path, wheel: str, name: str, provider: str
) -> None:
    expected, ui = _release_with_asset(tmp_path, wheel, name, b"{}")
    with pytest.raises(release.ReleaseBuildError, match=f"{provider} auth assets"):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


@pytest.mark.parametrize("wheel", _WHEELS)
@pytest.mark.parametrize("location", ["contents", "filename"])
def test_release_rejects_firebase_api_keys_without_configuration_markers(
    tmp_path: Path, wheel: str, location: str
) -> None:
    key = b"AIza" + b"X" * 35
    name, contents = (
        (f"arbitrary/{key.decode()}.bin", b"benign")
        if location == "filename"
        else ("arbitrary/config.env", key)
    )
    expected, ui = _release_with_asset(tmp_path, wheel, name, contents)
    with pytest.raises(release.ReleaseBuildError, match="provider credential") as exc:
        release.verify_release(tmp_path, expected, ui_source_dist=ui)
    assert key.decode() not in str(exc.value)


@pytest.mark.parametrize("wheel", _WHEELS)
@pytest.mark.parametrize(
    ("name", "contents", "provider"),
    [
        (
            "arbitrary/config.env",
            b"SUPABASE_URL=https://project.supabase.co",
            "Supabase",
        ),
        (
            "arbitrary/page.html",
            b"<script>firebase.initializeApp({authDomain:'custom.example.test'})</script>",
            "Firebase",
        ),
        (
            "arbitrary/asset.map",
            b"import { createClient } from '@supabase/supabase-js'",
            "Supabase",
        ),
        (
            "arbitrary/data.bin",
            b"\x00\xffFIREBASE_PROJECT_ID=synthetic-project",
            "Firebase",
        ),
        ("arbitrary/settings", b"https://project.supabase.in", "Supabase"),
        ("NOTICE", b"import firebase_admin", "Firebase"),
    ],
)
def test_release_rejects_provider_markers_without_path_or_suffix_exemptions(
    tmp_path: Path, wheel: str, name: str, contents: bytes, provider: str
) -> None:
    if name == "NOTICE":
        name = f"{wheel}-1.0.dist-info/NOTICE"
    expected, ui = _release_with_asset(tmp_path, wheel, name, contents)
    with pytest.raises(
        release.ReleaseBuildError, match=f"{provider} auth code or config"
    ):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


@pytest.mark.parametrize("wheel", _WHEELS)
def test_release_rejects_duplicate_firebase_jwt_claims(
    tmp_path: Path, wheel: str
) -> None:
    token = _synthetic_jwt_raw(
        b'{"alg":"RS256"}',
        b'{"iss":"https://securetoken.google.com/synthetic-project","iss":"https://unrelated.example.test"}',
    )
    expected, ui = _release_with_asset(tmp_path, wheel, "arbitrary/token.bin", token)
    with pytest.raises(release.ReleaseBuildError, match="provider credential"):
        release.verify_release(tmp_path, expected, ui_source_dist=ui)


@pytest.mark.parametrize("wheel", _WHEELS)
def test_release_allows_unrelated_configuration_and_public_keys(
    tmp_path: Path, wheel: str
) -> None:
    unrelated = _synthetic_jwt_parts(
        {"alg": "RS256"},
        {
            "iss": "https://accounts.google.com",
            "aud": "synthetic-client",
            "sub": "synthetic-user",
        },
    )
    contents = (
        b"M3_CONTROL_PLANE_URL=https://control.example.test\n"
        b"-----BEGIN PUBLIC KEY-----\nSYNTHETIC-PUBLIC-KEY\n-----END PUBLIC KEY-----\n"
        b"ordinary.dotted.string\n" + unrelated
    )
    expected, ui = _release_with_asset(
        tmp_path, wheel, "arbitrary/config.env", contents
    )
    assert release.verify_release(tmp_path, expected, ui_source_dist=ui)
