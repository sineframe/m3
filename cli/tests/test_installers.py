from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "render_cli_installers.py"
SPEC = importlib.util.spec_from_file_location("render_cli_installers", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


def _executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _write_release_wheels(directory: Path, version: str, *, valid: bool = True) -> None:
    assets = [
        f"sf_m3_cli-{version}-py3-none-any.whl",
        f"sf_m3-{version}-py3-none-any.whl",
        f"sf_m3_app-{version}-py3-none-any.whl",
    ]
    records = []
    for index, name in enumerate(assets):
        payload = f"wheel-{index}".encode()
        (directory / name).write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        records.append(f"{digest if valid else '0' * 64}  {name}")
    (directory / "SHA256SUMS").write_text("\n".join(records) + "\n", encoding="utf-8")


def test_renderer_emits_one_versioned_posix_installer(tmp_path: Path) -> None:
    shell = renderer.render_installer("0.2.0a13", tmp_path)
    assert shell.name == "install.sh"
    assert shell.stat().st_mode & 0o111
    contents = shell.read_text(encoding="utf-8")
    assert "@M3_VERSION@" not in contents
    assert "0.2.0a13" in contents
    assert "sf_m3-${VERSION}-py3-none-any.whl" in contents
    assert "sf_m3_app-${VERSION}-py3-none-any.whl" in contents
    assert "sf_m3_cli-${VERSION}-py3-none-any.whl" in contents
    assert "gh auth" not in contents


def test_renderer_rejects_unsafe_version(tmp_path: Path) -> None:
    with pytest.raises(renderer.InstallerRenderError):
        renderer.render_installer("0.2.0; touch /tmp/pwned", tmp_path)


def test_rendered_shell_syntax(tmp_path: Path) -> None:
    shell = renderer.render_installer("1.2.3", tmp_path)
    subprocess.run(["sh", "-n", str(shell)], check=True)


def _run_installer(tmp_path: Path, *, valid: bool) -> subprocess.CompletedProcess[str]:
    version = "1.2.3"
    release, fake_bin, uv_bin, uv_root = (
        tmp_path / n for n in ("release", "bin", "uv-bin", "uv-tools")
    )
    for directory in (release, fake_bin, uv_bin, uv_root / "sf-m3-cli" / "bin"):
        directory.mkdir(parents=True)
    _write_release_wheels(release, version, valid=valid)
    _executable(
        fake_bin / "curl",
        """#!/bin/sh
set -eu
out=
previous=
for arg do if [ "$previous" = output ]; then out=$arg; fi; if [ "$arg" = --output ]; then previous=output; else previous=; fi; done
url=$*
name=${url##*/}
cp "$M3_TEST_RELEASE/$name" "$out"
""",
    )
    _executable(
        fake_bin / "uv",
        """#!/bin/sh
set -eu
case "$1 $2 ${3:-}" in
  'tool install '*) exit 0 ;;
  'tool dir --bin') printf '%s\\n' "$M3_TEST_UV_BIN" ;;
  'tool dir ') printf '%s\\n' "$M3_TEST_UV_ROOT" ;;
  *) exit 2 ;;
esac
""",
    )
    _executable(uv_bin / "m3", "#!/bin/sh\nexit 0\n")
    _executable(uv_root / "sf-m3-cli" / "bin" / "python", "#!/bin/sh\nexit 0\n")
    shell = renderer.render_installer(version, tmp_path / "rendered")
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "M3_RELEASE_BASE_URL": "https://example.invalid/release",
            "M3_TEST_RELEASE": str(release),
            "M3_TEST_UV_BIN": str(uv_bin),
            "M3_TEST_UV_ROOT": str(uv_root),
        }
    )
    return subprocess.run(
        ["sh", str(shell)], cwd=tmp_path, env=env, capture_output=True, text=True
    )


def test_public_posix_installer_verifies_and_installs_release(tmp_path: Path) -> None:
    result = _run_installer(tmp_path, valid=True)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "m3 installed with uv" in result.stdout


def test_public_posix_installer_rejects_bad_checksum(tmp_path: Path) -> None:
    result = _run_installer(tmp_path, valid=False)
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr


def _bootstrap(
    tmp_path: Path, releases: list[dict[str, object]], *args: str
) -> tuple[subprocess.CompletedProcess[str], str | None]:
    tmp_path.mkdir(parents=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    release_file = tmp_path / "releases.json"
    release_file.write_text(json.dumps(releases), encoding="utf-8")
    asset_root = tmp_path / "assets"
    for release in releases:
        tag = str(release["tag_name"])
        if not any(asset.get("name") == "install.sh" for asset in release["assets"]):
            continue
        directory = asset_root / tag
        directory.mkdir(parents=True)
        installer = f'echo {tag} > "$M3_TEST_SELECTED"\n'
        (directory / "install.sh").write_text(installer, encoding="utf-8")
        checksums = "not executed by the bootstrap test\n"
        (directory / "SHA256SUMS").write_text(checksums, encoding="utf-8")
        version = tag[1:]
        manifest = {
            "version": version,
            "assets": {
                "install.sh": hashlib.sha256(installer.encode()).hexdigest(),
                "SHA256SUMS": hashlib.sha256(checksums.encode()).hexdigest(),
            },
        }
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    selected = tmp_path / "selected"
    _executable(
        fake_bin / "curl",
        """#!/bin/sh
set -eu
out=
previous=
for arg do
  if [ "$previous" = output ]; then out=$arg; previous=; continue; fi
  if [ "$arg" = --output ]; then previous=output; fi
done
url=$*
case "$url" in
  *api.github.com*) cp "$M3_TEST_RELEASES" "$out" ;;
  */releases/download/*/manifest.json)
    tag=${url#*/releases/download/}; tag=${tag%%/*}
    cp "$M3_TEST_ASSETS/$tag/manifest.json" "$out"
    ;;
  */releases/download/*/SHA256SUMS)
    tag=${url#*/releases/download/}; tag=${tag%%/*}
    cp "$M3_TEST_ASSETS/$tag/SHA256SUMS" "$out"
    ;;
  */releases/download/*/install.sh)
    tag=${url#*/releases/download/}; tag=${tag%%/*}
    cp "$M3_TEST_ASSETS/$tag/install.sh" "$out"
    ;;
  *) exit 4 ;;
esac
""",
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "M3_TEST_RELEASES": str(release_file),
            "M3_TEST_ASSETS": str(asset_root),
            "M3_TEST_SELECTED": str(selected),
            "TMPDIR": str(tmp_path),
        }
    )
    result = subprocess.run(
        ["sh", str(ROOT / "scripts" / "install-latest.sh"), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    return result, selected.read_text().strip() if selected.exists() else None


def _release(
    tag: str, *, prerelease: bool = False, draft: bool = False, installer: bool = True
) -> dict[str, object]:
    assets = [{"name": "install.sh"}] if installer else []
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft, "assets": assets}


def test_bootstrap_selects_highest_final_and_explicit_prerelease(
    tmp_path: Path,
) -> None:
    releases = [
        _release("v0.9.0"),
        _release("v1.0.0a12", prerelease=True),
        _release("v1.0.0b2", prerelease=True),
        _release("v1.0.0"),
    ]
    result, selected = _bootstrap(tmp_path / "stable", releases)
    assert result.returncode == 0, result.stderr
    assert selected == "v1.0.0"
    result, selected = _bootstrap(tmp_path / "pre", releases, "--prerelease")
    assert result.returncode == 0, result.stderr
    assert selected == "v1.0.0b2"


def test_bootstrap_alpha_fallback_and_exact_tag(tmp_path: Path) -> None:
    releases = [
        _release("v0.4.0a2", prerelease=True),
        _release("v0.4.0a12", prerelease=True),
        _release("v2.0.0", draft=True),
    ]
    result, selected = _bootstrap(tmp_path / "fallback", releases)
    assert result.returncode == 0, result.stderr
    assert selected == "v0.4.0a12"
    result, selected = _bootstrap(tmp_path / "exact", releases, "--tag", "v0.4.0a2")
    assert result.returncode == 0, result.stderr
    assert selected == "v0.4.0a2"


def test_bootstrap_fails_when_release_has_no_installer(tmp_path: Path) -> None:
    result, selected = _bootstrap(
        tmp_path / "missing", [_release("v1.0.0", installer=False)]
    )
    assert result.returncode != 0
    assert "no published M3 release with install.sh" in result.stderr
    assert selected is None
