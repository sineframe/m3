from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "render_cli_installers.py"
SPEC = importlib.util.spec_from_file_location("render_cli_installers", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def test_renderer_writes_versioned_executable_installers(tmp_path: Path) -> None:
    shell, powershell = renderer.render_installers("0.2.0a2", tmp_path)
    assert shell.name == "install.sh"
    assert powershell.name == "install.ps1"
    assert shell.stat().st_mode & 0o111
    for path in (shell, powershell):
        contents = path.read_text(encoding="utf-8")
        assert "@MCP_PAL_VERSION@" not in contents
        assert "0.2.0a2" in contents
        assert "mcp_pal-${VERSION}-py3-none-any.whl" in contents or "mcp_pal-$Version-py3-none-any.whl" in contents
        assert "mcp_pal_app-${VERSION}-py3-none-any.whl" in contents or "mcp_pal_app-$Version-py3-none-any.whl" in contents
        assert "mcp_pal_cli-${VERSION}-py3-none-any.whl" in contents or "mcp_pal_cli-$Version-py3-none-any.whl" in contents


def test_renderer_rejects_unsafe_version(tmp_path: Path) -> None:
    with pytest.raises(renderer.InstallerRenderError):
        renderer.render_installers("0.2.0; touch /tmp/pwned", tmp_path)


def test_renderer_requires_one_placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    template = ROOT / "scripts" / "install.sh.in"
    monkeypatch.setattr(renderer, "PLACEHOLDER", "not-in-template")
    with pytest.raises(renderer.InstallerRenderError, match="exactly one"):
        renderer.render_template(template, "0.2.0a2")


def test_rendered_posix_installer_has_valid_shell_syntax(tmp_path: Path) -> None:
    shell, _ = renderer.render_installers("0.2.0a2", tmp_path)
    result = subprocess.run(["sh", "-n", str(shell)], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout)


def test_installers_use_isolated_paths_and_local_wheels() -> None:
    shell = (ROOT / "scripts" / "install.sh.in").read_text(encoding="utf-8")
    powershell = (ROOT / "scripts" / "install.ps1.in").read_text(encoding="utf-8")
    for contents in (shell, powershell):
        assert "MCP_PAL_RELEASE_BASE_URL" in contents
        assert "SHA256SUMS" in contents
        assert "--with" in contents or "Get-FileHash" in contents
        assert "pip --user" not in contents
        assert "sudo" not in contents
    assert "uv tool install --force" in shell
    assert "tool install --force" in powershell
    assert "uv tool run" not in shell
    assert "tool run" not in powershell
    assert "elif command -v mcp-pal" not in shell
    assert "ExistingCommand" not in powershell
    assert "MCP_PAL_INSTALL_ROOT" in shell
    assert "MCP_PAL_INSTALL_ROOT" in powershell
    assert "MCP_PAL_BIN_DIR" in shell
    assert "MCP_PAL_BIN_DIR" in powershell
    assert "-m pip install" in shell
    assert "-m pip install" in powershell
    assert ".mcp-pal-install" in shell
    assert ".mcp-pal-install" in powershell
    for contents in (shell, powershell):
        assert "[1/4] Downloading release assets" in contents
        assert "[2/4] Verifying checksums" in contents
        assert "[3/4] Installing into isolated tool storage" in contents
        assert "[4/4] Verifying command and bundled UI" in contents
        assert "Next:" in contents and "mcp-pal setup" in contents
        assert "Command:" in contents
    assert "STAGE=" not in shell
    assert "$Stage" not in powershell
    assert 'created_link=1' in shell
    assert 'if [ "$created_link" -eq 1 ]' in shell
    assert shell.index('"$COMMAND_PATH" --help') < shell.index('transaction_done=1')
    assert "mcp-pal-bin" in powershell
    assert "if (-not (Test-Path -LiteralPath $Marker))" in powershell
    assert "-and -not (Test-Path (Join-Path $InstallRoot 'Scripts/python.exe'))" not in powershell
    assert "$CreatedCommand = $true" in powershell
    assert "$CreatedCommand -and" in powershell
    assert powershell.index('& $CommandPath --help') < powershell.index('Remove-Item -Recurse -Force -LiteralPath $Backup')


def test_installers_support_authenticated_private_release_downloads() -> None:
    shell = (ROOT / "scripts" / "install.sh.in").read_text(encoding="utf-8")
    powershell = (ROOT / "scripts" / "install.ps1.in").read_text(encoding="utf-8")

    assert 'RELEASE_TAG="v${VERSION}"' in shell
    assert 'gh auth status --hostname github.com' in shell
    assert 'gh release download "$RELEASE_TAG"' in shell
    assert '--repo "$REPOSITORY"' in shell
    assert '--pattern "$artifact"' in shell
    assert '--output "$destination"' in shell
    assert 'download "$artifact"' in shell

    assert '$ReleaseTag = "v$Version"' in powershell
    assert 'auth status --hostname github.com' in powershell
    assert 'release download $ReleaseTag --repo $Repository --pattern $Name --output $Destination' in powershell
    assert 'Download $Name (Join-Path $TempDir $Name)' in powershell
    # `exit` would terminate the caller when this script is invoked with `&`.
    assert 'exit 0' not in powershell


def test_posix_installer_fetches_exact_private_assets_into_isolated_uv_tool(
    tmp_path: Path,
) -> None:
    version = "0.2.0a2"
    release = tmp_path / "release"
    fake_bin = tmp_path / "bin"
    uv_root = tmp_path / "uv-tools"
    uv_bin = tmp_path / "uv-bin"
    log = tmp_path / "calls.log"
    for directory in (release, fake_bin, uv_bin, uv_root / "mcp-pal-cli" / "bin"):
        directory.mkdir(parents=True)

    assets = (
        f"mcp_pal_cli-{version}-py3-none-any.whl",
        f"mcp_pal-{version}-py3-none-any.whl",
        f"mcp_pal_app-{version}-py3-none-any.whl",
    )
    checksums: list[str] = []
    for index, name in enumerate(assets):
        payload = f"wheel-{index}".encode()
        (release / name).write_bytes(payload)
        checksums.append(f"{hashlib.sha256(payload).hexdigest()}  {name}")
    (release / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")

    _write_executable(
        fake_bin / "gh",
        """#!/bin/sh
set -eu
printf 'gh %s\\n' "$*" >> "$MCP_PAL_TEST_LOG"
if [ "$1 $2" = 'auth status' ]; then exit 0; fi
[ "$1 $2" = 'release download' ] || exit 20
tag=$3
shift 3
pattern=
output=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo) [ "$2" = 'rishhavv/mcp-pal' ] || exit 21; shift 2 ;;
    --pattern) pattern=$2; shift 2 ;;
    --output) output=$2; shift 2 ;;
    *) exit 22 ;;
  esac
done
[ "$tag" = 'v0.2.0a2' ] && [ -n "$pattern" ] && [ -n "$output" ] || exit 23
cp "$MCP_PAL_TEST_RELEASE/$pattern" "$output"
""",
    )
    _write_executable(
        fake_bin / "uv",
        """#!/bin/sh
set -eu
printf 'uv %s\\n' "$*" >> "$MCP_PAL_TEST_LOG"
if [ "$1 $2" = 'tool install' ]; then exit 0; fi
if [ "$1 $2 ${3:-}" = 'tool dir --bin' ]; then printf '%s\\n' "$MCP_PAL_TEST_UV_BIN"; exit 0; fi
if [ "$1 $2" = 'tool dir' ]; then printf '%s\\n' "$MCP_PAL_TEST_UV_ROOT"; exit 0; fi
exit 30
""",
    )
    for executable in (uv_bin / "mcp-pal", uv_root / "mcp-pal-cli" / "bin" / "python"):
        _write_executable(executable, "#!/bin/sh\nexit 0\n")

    shell, _ = renderer.render_installers(version, tmp_path / "rendered")
    environment = os.environ.copy()
    environment.pop("MCP_PAL_RELEASE_BASE_URL", None)
    environment.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
            "MCP_PAL_TEST_LOG": str(log),
            "MCP_PAL_TEST_RELEASE": str(release),
            "MCP_PAL_TEST_UV_BIN": str(uv_bin),
            "MCP_PAL_TEST_UV_ROOT": str(uv_root),
        }
    )
    result = subprocess.run(
        ["sh", str(shell)],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    calls = log.read_text(encoding="utf-8").splitlines()
    downloads = [line for line in calls if line.startswith("gh release download ")]
    assert len(downloads) == 4
    for name in (*assets, "SHA256SUMS"):
        assert any(f"--pattern {name} --output " in line for line in downloads)
    assert any(
        line.startswith("uv tool install --force ")
        and line.count(" --with ") == 2
        for line in calls
    )


def test_private_install_docs_use_exact_authenticated_assets() -> None:
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "[CLI guide](cli/README.md)" in root_readme

    contents = (ROOT / "cli" / "README.md").read_text(encoding="utf-8")
    assert "gh auth login" in contents
    assert (
        "gh release download vX.Y.Z --repo rishhavv/mcp-pal "
        "--pattern install.sh --output install.sh"
    ) in contents
    assert "--pattern install.sh --output install.sh" in contents
    assert "sh install.sh\nrm install.sh" in contents
    assert "--pattern install.ps1 --output install.ps1" in contents
    assert ".\\install.ps1\nRemove-Item install.ps1" in contents
    assert 'mcp-pal[pytest,storage] @ ./.mcp-pal-download/$SDK_WHEEL' in contents
    assert "only downloads files" in contents
    assert "does not create or modify a" in contents


def test_release_workflow_publishes_only_tag_runs() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-cli.yml").read_text(encoding="utf-8")
    assert 'tags:\n      - "v*"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')" in workflow
    assert "gh release create" in workflow
    assert "--verify-tag" in workflow
    assert "--generate-notes" in workflow
    assert "upload-artifact" not in workflow
    assert "pypi" not in workflow.lower()
    assert "actions/setup-node@395ad3262231945c25e8478fd5baf05154b1d79f" in workflow
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in workflow
    assert "repository: rishhavv/mcppal-ui" in workflow
    assert "ref: ${{ steps.ui-ref.outputs.sha }}" in workflow
    assert "--print-version" in workflow
    assert "--expected-version" in workflow
