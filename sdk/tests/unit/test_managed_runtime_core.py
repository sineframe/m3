import asyncio
import hashlib
import io
import json
import multiprocessing
import os
import re
import shlex
import sys
import tarfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from m3.runtime import (
    RuntimeManager,
    RuntimeValidationError,
    default_manifest_url,
    detect_target,
    list_cache,
    prune_cache,
    resolve_cache_root,
)
from m3.runtime.core import (
    RuntimeManager as _CoreManager,
)
from m3.runtime.core import (
    _entry_lock,
    _is_windows_system_path,
)


def test_resolve_cache_root_rejects_project_child(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(ValueError):
        resolve_cache_root(project, project / ".cache")


def test_windows_system_directory_guard_is_drive_aware() -> None:
    assert _is_windows_system_path(r"C:\Windows\m3-cache", {})
    assert _is_windows_system_path(
        r"D:\CustomWindows\m3-cache", {"SYSTEMROOT": r"D:\CustomWindows"}
    )
    assert not _is_windows_system_path(r"C:\Users\test\m3-cache", {})


def test_opencode_target_includes_abi_and_avx() -> None:
    target = detect_target(
        "opencode",
        env={
            "M3_RUNTIME_ARCH": "x86_64",
            "M3_RUNTIME_LIBC": "musl",
            "M3_RUNTIME_AVX2": "1",
        },
    )
    if sys.platform.startswith("linux"):
        assert target.startswith("linux-x64-musl-avx2")


FAKE_BINARY = b"#!/bin/sh\necho 1.2.3\n"


def _archive(name: str = "claude", payload: bytes = FAKE_BINARY) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(name, payload)
    return out.getvalue()


class _Server(BaseHTTPRequestHandler):
    body = _archive()
    requests = 0
    manifest_log = None

    def do_GET(self):
        type(self).requests += 1
        if self.path.startswith("/manifest"):
            if type(self).manifest_log:
                Path(type(self).manifest_log).open("a").write("lookup\n")
            body = json.dumps(
                {
                    "version": "1.2.3",
                    "url": f"http://127.0.0.1:{self.server.server_port}/asset.zip",
                    "sha256": hashlib.sha256(self.body).hexdigest(),
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *_args):
        pass


@pytest.fixture()
def archive_server():
    _Server.body = _archive()
    _Server.requests = 0
    _Server.manifest_log = None
    server = HTTPServer(("127.0.0.1", 0), _Server)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/asset.zip"
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.asyncio
async def test_acquire_receipt_cache_hit_progress_and_prune(
    tmp_path: Path, archive_server: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "cache"
    events = tmp_path / "events.jsonl"
    digest = hashlib.sha256(_Server.body).hexdigest()
    manager = RuntimeManager(cache, project, tmp_path / "invoke", events)
    first = await manager.acquire(
        "claude", {"version": "1.2.3", "url": archive_server, "sha256": digest}
    )
    await first.release()
    second = await manager.acquire(
        "claude", {"version": "1.2.3", "url": archive_server, "sha256": digest}
    )
    assert second.executable.name == "claude"
    assert _Server.requests == 1
    assert (
        list_cache(cache)[0]["files"]["claude"]
        == hashlib.sha256(FAKE_BINARY).hexdigest()
    )
    lines = [json.loads(line) for line in events.read_text().splitlines()]
    assert all("?" not in str(line.get("url", "")) for line in lines)
    await second.release()
    assert prune_cache(cache, max_age=-1)


@pytest.mark.asyncio
async def test_hostile_selector_url_and_version_rejected(tmp_path: Path) -> None:
    manager = RuntimeManager(tmp_path.parent / "cache-hostile", tmp_path)
    with pytest.raises(RuntimeValidationError):
        await manager.acquire("claude", {"version": "../x", "url": "https://x/y"})
    with pytest.raises(RuntimeValidationError):
        await manager.acquire(
            "claude",
            {
                "version": "1.0.0",
                "url": "https://user:pass@example.com/x",
                "sha256": "a" * 64,
            },
        )
    with pytest.raises(RuntimeValidationError):
        await manager.acquire("unknown", "1.0.0")


def test_cache_path_safety(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(RuntimeValidationError):
        resolve_cache_root(project, project / "cache")
    with pytest.raises(RuntimeValidationError):
        resolve_cache_root(project, "/usr/local/cache")
    override = tmp_path / "external"
    assert (
        resolve_cache_root(project, env={"M3_HARNESS_CACHE_DIR": str(override)})
        == override.resolve()
    )


@pytest.mark.asyncio
async def test_acquire_requires_independent_digest(
    tmp_path: Path, archive_server: str
) -> None:
    manager = RuntimeManager(tmp_path.parent / "cache-digest", tmp_path)
    with pytest.raises(RuntimeValidationError, match="independent sha256"):
        await manager.acquire("claude", {"version": "1.0.0", "url": archive_server})


def test_versions_are_canonical_semver() -> None:
    from m3.runtime.core import _version

    with pytest.raises(RuntimeValidationError):
        _version("1.0")


@pytest.mark.parametrize(
    ("kind", "tag", "expected"),
    [
        ("codex", "rust-v0.155.1", "0.155.1"),
        ("opencode", "v1.18.30", "1.18.30"),
        ("pi", "v0.86.1", "0.86.1"),
    ],
)
def test_release_tags_are_canonical(kind: str, tag: str, expected: str) -> None:
    from m3.runtime.core import _release_version

    assert _release_version(kind, tag) == expected


def test_staged_download_enforces_manifest_ceiling(archive_server: str) -> None:
    original = _Server.body
    try:
        _Server.body = b"x" * (8 * 1024 * 1024 + 1)
        with pytest.raises(RuntimeValidationError, match="size cap"):
            _CoreManager._download_staged(archive_server, "manifest")
    finally:
        _Server.body = original


@pytest.mark.parametrize("denials", [1, 9])
@pytest.mark.asyncio
async def test_publish_retries_transient_permission_error(
    tmp_path: Path,
    archive_server: str,
    monkeypatch: pytest.MonkeyPatch,
    denials: int,
) -> None:
    original_replace = os.replace
    attempts = 0

    def replace(source: str | Path, destination: str | Path) -> None:
        nonlocal attempts
        if Path(source).name.startswith(".staging-"):
            attempts += 1
            if attempts <= denials:
                raise PermissionError("temporary file lock")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr("m3.runtime.core.time.sleep", lambda _seconds: None)
    manager = RuntimeManager(tmp_path / "cache", tmp_path / "project")
    lease = await manager.acquire(
        "claude",
        {
            "version": "1.2.3",
            "url": archive_server,
            "sha256": hashlib.sha256(_Server.body).hexdigest(),
        },
    )
    assert attempts == denials + 1
    assert lease.executable.is_file()
    await lease.release()


def test_pi_archive_entry_boundary(tmp_path: Path) -> None:
    for count, expected in ((512, None), (513, RuntimeValidationError)):
        archive = tmp_path / f"pi-{count}.tar"
        with tarfile.open(archive, "w") as tar:
            for index in range(count):
                info = tarfile.TarInfo(f"file-{index}")
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
        output = tmp_path / f"out-{count}"
        output.mkdir()
        with (
            archive.open("rb") as stream,
            tarfile.open(fileobj=stream, mode="r:") as tar,
        ):
            if expected:
                with pytest.raises(expected):
                    _CoreManager._extract_tar(tar, output, 512, 1024 * 1024)
            else:
                _CoreManager._extract_tar(tar, output, 512, 1024 * 1024)


def test_corrupt_receipt_and_symlinked_entry_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    entry = root / "claude" / "1.0" / "target" / ("sha256-" + "a" * 64)
    entry.mkdir(parents=True)
    (entry / "receipt.json").write_text("{}")
    assert not _CoreManager._receipt_valid(entry)
    outside = tmp_path / "outside"
    outside.mkdir()
    import shutil

    shutil.rmtree(root / "claude")
    os.symlink(outside, root / "claude")
    manager = _CoreManager(root, tmp_path / "project")
    assert manager._cached_version("claude", "1.0", "target") is None


def test_prune_waits_for_concurrent_install_lock(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    destination = root / "claude" / "1.2.3" / "target" / ("sha256-" + "a" * 64)
    destination.parent.mkdir(parents=True)
    locked = threading.Event()
    publish = threading.Event()

    def installer() -> None:
        with _entry_lock(destination):
            locked.set()
            assert publish.wait(5)
            destination.mkdir()

    owner = threading.Thread(target=installer)
    owner.start()
    assert locked.wait(5)
    assert list_cache(root) == []
    timer = threading.Timer(0.1, publish.set)
    timer.start()
    try:
        removed = prune_cache(root)
    finally:
        timer.cancel()
        publish.set()
        owner.join(5)

    assert removed == [destination]
    assert not destination.exists()
    assert not owner.is_alive()


@pytest.mark.asyncio
async def test_acquire_rejects_symlinked_cache_component_before_writing(
    tmp_path: Path, archive_server: str
) -> None:
    cache = tmp_path / "cache"
    outside = tmp_path / "outside"
    outside.mkdir()
    cache.mkdir()
    os.symlink(outside, cache / "claude")
    manager = RuntimeManager(cache, tmp_path / "project")
    digest = hashlib.sha256(_Server.body).hexdigest()
    with pytest.raises(RuntimeValidationError, match="symlink"):
        await manager.acquire(
            "claude", {"version": "1.2.3", "url": archive_server, "sha256": digest}
        )
    assert not any(outside.iterdir())


def test_release_asset_selection_excludes_checksums() -> None:
    target = "darwin-arm64-64"
    asset_name = "opencode-darwin-arm64.tar.gz"
    selected = _CoreManager._select_manifest_asset(
        "opencode",
        {
            "tag_name": "v1.2.3",
            "assets": [
                {
                    "name": asset_name + ".sha256",
                    "browser_download_url": "http://bad/check",
                },
                {
                    "name": asset_name,
                    "browser_download_url": "http://good/archive",
                    "digest": "sha256:" + "a" * 64,
                },
            ],
        },
        target,
        "latest",
    )
    assert selected["url"] == "http://good/archive"
    assert selected["sha256"] == "a" * 64


@pytest.mark.parametrize(
    "kind,target,asset_suffix,expected",
    [
        (
            "codex",
            "darwin-arm64-64",
            "codex-aarch64-apple-darwin.tar.gz",
            "codex-aarch64-apple-darwin",
        ),
        ("pi", "darwin-arm64-64", "pi-darwin-arm64.tar.gz", "pi/pi"),
        ("opencode", "windows-x64-64", "opencode-windows-x64.zip", "opencode.exe"),
        (
            "codex",
            "windows-x64-64",
            "codex-x86_64-pc-windows-msvc.exe.zip",
            "codex-x86_64-pc-windows-msvc.exe",
        ),
        ("pi", "windows-x64-64", "pi-windows-x64.zip", "pi.exe"),
    ],
)
def test_captured_release_asset_executable_mapping(
    kind: str, target: str, asset_suffix: str, expected: str
) -> None:
    tag = "v0.86.1" if kind == "pi" else "v1.2.3"
    selected = _CoreManager._select_manifest_asset(
        kind,
        {
            "tag_name": tag,
            "assets": [
                {
                    "name": asset_suffix,
                    "browser_download_url": "http://good/archive",
                    "digest": "sha256:" + "b" * 64,
                },
                {
                    "name": asset_suffix + ".sha256",
                    "browser_download_url": "http://bad/check",
                },
            ],
        },
        target,
        "latest",
    )
    assert selected["executable"] == expected


def test_official_pi_release_metadata_endpoint() -> None:
    assert (
        default_manifest_url("pi", "0.86.1")
        == "https://api.github.com/repos/earendil-works/pi/releases/tags/v0.86.1"
    )


@pytest.mark.parametrize(
    ("kind", "target", "name"),
    [
        ("opencode", "darwin-arm64-64", "opencode-darwin-arm64.tar.gz"),
        (
            "opencode",
            "linux-x64-glibc-baseline",
            "opencode-linux-x64-baseline.tar.gz",
        ),
        ("opencode", "linux-x64-musl-avx2", "opencode-linux-x64-musl.tar.gz"),
        ("opencode", "linux-arm64-glibc-baseline", "opencode-linux-arm64.tar.gz"),
        ("opencode", "linux-arm64-musl-baseline", "opencode-linux-arm64-musl.tar.gz"),
        ("opencode", "windows-x64-64", "opencode-windows-x64.zip"),
        ("codex", "linux-x64-64", "codex-x86_64-unknown-linux-musl.tar.gz"),
        ("codex", "linux-arm64-64", "codex-aarch64-unknown-linux-musl.tar.gz"),
        ("codex", "linux-x64-64", "codex-x86_64-unknown-linux-gnu.tar.gz"),
        ("codex", "windows-x64-64", "codex-x86_64-pc-windows-msvc.exe.zip"),
        ("pi", "darwin-arm64-64", "pi-darwin-arm64.tar.gz"),
        ("pi", "windows-x64-64", "pi-windows-x64.zip"),
    ],
)
def test_deterministic_vendor_target_patterns(
    kind: str, target: str, name: str
) -> None:
    patterns = _CoreManager._asset_patterns(kind, target)
    assert any(re.fullmatch(pattern, name, re.I) for pattern in patterns)


def test_codex_prefers_cli_over_package_asset() -> None:
    selected = _CoreManager._select_manifest_asset(
        "codex",
        {
            "tag_name": "rust-v0.155.1",
            "assets": [
                {
                    "name": "codex-package-aarch64-apple-darwin.tar.gz",
                    "browser_download_url": "http://bad/package",
                },
                {
                    "name": "codex-aarch64-apple-darwin.tar.gz",
                    "browser_download_url": "http://good/cli",
                    "digest": "sha256:" + "c" * 64,
                },
            ],
        },
        "darwin-arm64-64",
        "0.155.1",
    )
    assert selected["url"] == "http://good/cli"
    assert selected["version"] == "0.155.1"
    assert selected["immutable_release"] is None


def test_codex_prefers_current_linux_musl_asset_with_gnu_fallback() -> None:
    def selected_name(names: list[str]) -> str:
        selected = _CoreManager._select_manifest_asset(
            "codex",
            {
                "tag_name": "rust-v0.155.1",
                "assets": [
                    {
                        "name": name,
                        "browser_download_url": f"https://example.test/{name}",
                        "digest": "sha256:" + "c" * 64,
                    }
                    for name in names
                ],
            },
            "linux-x64-64",
            "0.155.1",
        )
        return str(selected["asset_name"])

    musl = "codex-x86_64-unknown-linux-musl.tar.gz"
    gnu = "codex-x86_64-unknown-linux-gnu.tar.gz"
    assert selected_name([gnu, musl]) == musl
    assert selected_name([gnu]) == gnu


def test_codex_windows_selects_zip_when_release_has_multiple_formats() -> None:
    stem = "codex-x86_64-pc-windows-msvc.exe"
    selected = _CoreManager._select_manifest_asset(
        "codex",
        {
            "tag_name": "rust-v0.155.1",
            "assets": [
                {
                    "name": stem + extension,
                    "browser_download_url": "https://example.com/" + extension,
                    "digest": "sha256:" + "c" * 64,
                }
                for extension in (".tar.gz", ".zip", ".zst")
            ],
        },
        "windows-x64-64",
        "0.155.1",
    )
    assert selected["asset_name"] == stem + ".zip"


@pytest.mark.asyncio
async def test_concurrent_publish_removes_unused_staged_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = tmp_path / "staged-asset"
    staged.write_bytes(b"asset")
    digest = hashlib.sha256(staged.read_bytes()).hexdigest()
    manager = RuntimeManager(tmp_path / "cache", tmp_path / "project")
    manager.cache_root.mkdir(parents=True)
    checks = 0

    def receipt_is_ready(_root: Path) -> bool:
        nonlocal checks
        checks += 1
        # The cache entry appears between the initial check and the download.
        return checks >= 2

    manager._receipt_valid = receipt_is_ready  # type: ignore[method-assign]
    monkeypatch.setattr(
        _CoreManager,
        "_download_staged",
        staticmethod(lambda *_args, **_kwargs: (staged, 5, digest)),
    )

    def install(_source: Path, destination: Path, *_args: object) -> None:
        executable = destination / "claude"
        executable.write_bytes(FAKE_BINARY)
        executable.chmod(0o700)

    monkeypatch.setattr(_CoreManager, "_install", staticmethod(install))
    sentinel = object()
    monkeypatch.setattr(manager, "_lease", lambda *_args: sentinel)

    result = await manager.acquire(
        "claude",
        {
            "version": "1.2.3",
            "url": "http://127.0.0.1/asset",
            "sha256": digest,
        },
    )
    assert result is sentinel
    assert not staged.exists()


@pytest.mark.asyncio
async def test_concurrent_acquire_downloads_once(
    tmp_path: Path, archive_server: str
) -> None:
    cache = tmp_path / "cache"
    project = tmp_path / "project"
    project.mkdir()
    digest = hashlib.sha256(_Server.body).hexdigest()
    manager = RuntimeManager(cache, project)
    selector = {"version": "1.2.3", "url": archive_server, "sha256": digest}

    first, second = await asyncio.gather(
        manager.acquire("claude", selector), manager.acquire("claude", selector)
    )

    assert first.executable == second.executable
    assert _Server.requests == 1
    await first.release()
    await second.release()


@pytest.mark.asyncio
async def test_entry_lock_cancellation_does_not_leak(tmp_path: Path) -> None:
    from m3.runtime.core import _async_entry_lock

    destination = tmp_path / "entry"
    destination.parent.mkdir(parents=True, exist_ok=True)
    holder = _async_entry_lock(destination)
    await holder.__aenter__()
    waiter = asyncio.create_task(_async_entry_lock(destination).__aenter__())
    await asyncio.sleep(0.05)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiter, timeout=1)
    await holder.__aexit__(None, None, None)
    await asyncio.sleep(0.05)
    async with _async_entry_lock(destination):
        pass


@pytest.mark.asyncio
async def test_zip_traversal_and_entry_limits(
    tmp_path: Path, archive_server: str
) -> None:
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../escape", b"x")
    _Server.body = bad.getvalue()
    manager = RuntimeManager(tmp_path.parent / "cache-archive", tmp_path)
    with pytest.raises(RuntimeValidationError, match="traversal"):
        await manager.acquire(
            "claude",
            {
                "version": "1.0.0",
                "url": archive_server,
                "sha256": hashlib.sha256(_Server.body).hexdigest(),
            },
        )


@pytest.mark.asyncio
async def test_lease_prevents_prune_until_release_and_detects_tamper(
    tmp_path: Path, archive_server: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "cache"
    manager = RuntimeManager(cache, project)
    lease = await manager.acquire(
        "claude",
        {
            "version": "1.2.3",
            "url": archive_server,
            "sha256": hashlib.sha256(_Server.body).hexdigest(),
        },
    )
    assert prune_cache(cache, max_age=-1) == []
    lease.executable.write_bytes(b"tampered")
    await lease.release()
    # A second acquisition verifies the receipt and repairs the modified tree.
    repaired = await manager.acquire(
        "claude",
        {
            "version": "1.2.3",
            "url": archive_server,
            "sha256": hashlib.sha256(_Server.body).hexdigest(),
        },
    )
    assert repaired.executable.read_bytes() == FAKE_BINARY
    await repaired.release()
    assert prune_cache(cache, max_age=-1)


@pytest.mark.asyncio
async def test_missing_expected_executable_is_never_published(
    tmp_path: Path, archive_server: str
) -> None:
    _Server.body = _archive("wrong-name")
    _Server.requests = 0
    cache = tmp_path / "cache"
    manager = RuntimeManager(cache, tmp_path / "project")
    selector = {
        "version": "1.2.3",
        "url": archive_server,
        "sha256": hashlib.sha256(_Server.body).hexdigest(),
    }

    for _ in range(2):
        with pytest.raises(RuntimeValidationError, match="no expected executable"):
            await manager.acquire("claude", selector)

    assert _Server.requests == 2
    assert not list(cache.glob("**/receipt.json"))


@pytest.mark.asyncio
async def test_version_smoke_check_uses_isolated_environment_and_cwd(
    tmp_path: Path,
    archive_server: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = tmp_path / "smoke-context"
    script = (
        "#!/bin/sh\n"
        f'printf \'%s\\n%s\\n%s\\n\' "${{AUDIT_SECRET-unset}}" "$HOME" "$PWD" > {shlex.quote(str(capture))}\n'
        "echo 1.2.3\n"
    ).encode()
    _Server.body = _archive("claude", script)
    monkeypatch.setenv("AUDIT_SECRET", "ambient-canary")
    monkeypatch.setenv("HOME", str(tmp_path / "real-home"))
    manager = RuntimeManager(tmp_path / "cache", tmp_path / "project")

    lease = await manager.acquire(
        "claude",
        {
            "version": "1.2.3",
            "url": archive_server,
            "sha256": hashlib.sha256(_Server.body).hexdigest(),
        },
    )
    await lease.release()

    secret, home, cwd = capture.read_text(encoding="utf-8").splitlines()
    assert secret == "unset"
    assert Path(home).resolve() == Path(cwd).resolve()
    assert home != str(tmp_path / "real-home")
    assert cwd != str(tmp_path / "project")


@pytest.mark.asyncio
async def test_version_smoke_check_requires_exact_version(
    tmp_path: Path, archive_server: str
) -> None:
    _Server.body = _archive("claude", b"#!/bin/sh\necho 1.2.30\n")
    manager = RuntimeManager(tmp_path / "cache", tmp_path / "project")

    with pytest.raises(RuntimeValidationError, match="unexpected version"):
        await manager.acquire(
            "claude",
            {
                "version": "1.2.3",
                "url": archive_server,
                "sha256": hashlib.sha256(_Server.body).hexdigest(),
            },
        )
    assert not list((tmp_path / "cache").glob("**/receipt.json"))


@pytest.mark.parametrize(
    ("output", "expected"),
    (
        ("codex-cli 1.2.3", True),
        ("v1.2.3", True),
        ("1.2.30", False),
        ("11.2.3", False),
        ("1.2.3-beta", False),
        ("1.2.3_bad", False),
    ),
)
def test_reported_version_match_uses_complete_token(
    output: str, expected: bool
) -> None:
    assert _CoreManager._output_reports_version(output, "1.2.3") is expected


@pytest.mark.asyncio
async def test_latest_manifest_resolves_concrete_version(
    tmp_path: Path, archive_server: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    manager = RuntimeManager(tmp_path / "cache", project)
    manifest = archive_server.replace("/asset.zip", "/manifest.json")
    lease = await manager.acquire(
        "claude", {"version": "latest", "manifest_url": manifest}
    )
    assert lease.provenance["version"] == "1.2.3"
    assert lease.provenance["url"].endswith("/asset.zip")
    await lease.release()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "target", "asset_name"),
    (
        ("opencode", "darwin-arm64-64", "opencode-darwin-arm64.tar.gz"),
        ("codex", "linux-x64-64", "codex-x86_64-unknown-linux-musl.tar.gz"),
        ("pi", "darwin-arm64-64", "pi-darwin-arm64.tar.gz"),
    ),
)
async def test_latest_pin_allows_unrelated_queried_github_urls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    target: str,
    asset_name: str,
) -> None:
    manager = RuntimeManager(
        tmp_path / "cache", tmp_path / "project", tmp_path / "invocation"
    )
    manifest = {
        "tag_name": "v1.2.3" if kind != "codex" else "rust-v1.2.3",
        "upload_url": "https://uploads.github.test/releases/1/assets{?name,label}",
        "author": {"avatar_url": "https://avatars.github.test/u/1?v=4"},
        "assets": [
            {
                "name": asset_name,
                "browser_download_url": f"https://example.test/{asset_name}",
                "digest": "sha256:" + "a" * 64,
            }
        ],
    }

    async def fetch(*_args: object) -> dict[str, object]:
        return manifest

    monkeypatch.setattr(manager, "_fetch_claude_or_json", fetch)
    pinned = await manager._resolve_manifest(
        kind, "https://api.github.test/releases/latest", "latest", target
    )
    assert pinned["assets"][0]["browser_download_url"].endswith(asset_name)  # type: ignore[index,union-attr]
    assert "?" not in json.dumps(pinned)


@pytest.mark.asyncio
async def test_latest_pin_rejects_queried_selected_download_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = RuntimeManager(
        tmp_path / "cache", tmp_path / "project", tmp_path / "invocation"
    )

    async def fetch(*_args: object) -> dict[str, object]:
        return {
            "tag_name": "v1.2.3",
            "assets": [
                {
                    "name": "opencode-darwin-arm64.tar.gz",
                    "browser_download_url": "https://example.test/asset?signature=secret",
                    "digest": "sha256:" + "a" * 64,
                }
            ],
        }

    monkeypatch.setattr(manager, "_fetch_claude_or_json", fetch)
    with pytest.raises(RuntimeValidationError, match="cannot be pinned"):
        await manager._resolve_manifest(
            "opencode",
            "https://api.github.test/releases/latest",
            "latest",
            "darwin-arm64-64",
        )


@pytest.mark.asyncio
async def test_opencode_plain_latest_smoke(
    tmp_path: Path, archive_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import m3.runtime.core as core

    _Server.body = _archive("opencode")
    monkeypatch.setattr(
        core,
        "default_manifest_url",
        lambda *_args: archive_server.replace("/asset.zip", "/manifest.json"),
    )
    project = tmp_path / "project"
    project.mkdir()
    manager = RuntimeManager(tmp_path / "cache", project)
    lease = await manager.acquire("opencode", "latest")
    assert lease.executable.name == "opencode"
    await lease.release()


def _latest_worker(cache: str, project: str, manifest: str, invocation: str) -> None:
    async def run() -> None:
        manager = RuntimeManager(cache, project, invocation)
        lease = await manager.acquire(
            "claude", {"version": "latest", "manifest_url": manifest}
        )
        await lease.release()

    asyncio.run(run())


@pytest.mark.asyncio
async def test_latest_manifest_resolution_is_shared_between_workers(
    tmp_path: Path, archive_server: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "cache"
    log = tmp_path / "manifest-lookups"
    _Server.manifest_log = str(log)
    manifest = archive_server.replace("/asset.zip", "/manifest.json")
    invocation = tmp_path / "shared-invocation"
    processes = [
        multiprocessing.Process(
            target=_latest_worker,
            args=(str(cache), str(project), manifest, str(invocation)),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
    assert all(process.exitcode == 0 for process in processes)
    assert log.read_text().splitlines() == ["lookup"]


@pytest.mark.asyncio
async def test_invocation_pins_are_isolated(
    tmp_path: Path, archive_server: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _Server.manifest_log = str(tmp_path / "manifest-lookups")
    manifest = archive_server.replace("/asset.zip", "/manifest.json")
    manager_a = RuntimeManager(tmp_path / "cache", project, tmp_path / "invoke-a")
    manager_b = RuntimeManager(tmp_path / "cache", project, tmp_path / "invoke-b")
    await manager_a.acquire("claude", {"version": "latest", "manifest_url": manifest})
    await manager_b.acquire("claude", {"version": "latest", "manifest_url": manifest})
    pins_a = list((tmp_path / "invoke-a").glob("*.json"))
    pins_b = list((tmp_path / "invoke-b").glob("*.json"))
    assert pins_a and pins_b
    assert all(not pin.is_symlink() for pin in pins_a + pins_b)
    assert pins_a[0].read_text() == pins_b[0].read_text()
    await manager_a.close()
    await manager_b.close()


@pytest.mark.asyncio
async def test_latest_manifest_recovers_dead_owner_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invocation = tmp_path / "invocation"
    invocation.mkdir()
    manager = RuntimeManager(tmp_path / "cache", tmp_path / "project", invocation)
    url = "https://example.test/releases/latest"
    target = "darwin-arm64-64"
    key = hashlib.sha256(f"opencode\0{url}\0latest\0{target}".encode()).hexdigest()
    (invocation / f"{key}.lock").write_text("99999999\n", encoding="ascii")

    async def fetch(*_args: object) -> dict[str, object]:
        return {
            "tag_name": "v1.2.3",
            "assets": [
                {
                    "name": "opencode-darwin-arm64.tar.gz",
                    "browser_download_url": "https://example.test/asset.tar.gz",
                    "digest": "sha256:" + "a" * 64,
                }
            ],
        }

    monkeypatch.setattr(manager, "_fetch_claude_or_json", fetch)
    manifest = await manager._resolve_manifest("opencode", url, "latest", target)
    assert manifest["tag_name"] == "v1.2.3"
    assert not (invocation / f"{key}.lock").exists()
