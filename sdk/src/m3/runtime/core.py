"""Acquisition and lifecycle management for pinned native harness runtimes.

The module deliberately uses only the standard library.  Network access is
performed in worker threads so the public API remains asynchronous while being
easy to exercise with a local HTTP server.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, cast

from .recipes import default_manifest_url

VERSION_RE = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9a-z]+(?:\.[0-9a-z]+)*)?$"
)
SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")
KINDS = {"claude", "claude_code", "opencode", "codex", "pi"}
CAPS = {
    # download bytes, extracted bytes, entry count
    "claude": (512 * 1024**2, 512 * 1024**2, 1),
    "claude_code": (512 * 1024**2, 512 * 1024**2, 1),
    "opencode": (256 * 1024**2, 512 * 1024**2, 64),
    "codex": (384 * 1024**2, 768 * 1024**2, 64),
    # Measured official pi-darwin-arm64 v0.86.1 bundle: 230 tar members.
    "pi": (256 * 1024**2, 512 * 1024**2, 512),
}


class RuntimeErrorBase(RuntimeError):
    """Base error for acquisition failures."""


class RuntimeValidationError(RuntimeErrorBase, ValueError):
    pass


@contextmanager
def _entry_lock(destination: Path, *, timeout: float = 300) -> Iterator[None]:
    """Serialize lease creation and pruning with an install of this entry."""
    lock = destination.with_name(destination.name + ".lock")
    deadline = time.monotonic() + timeout
    owner_inode: tuple[int, int] | None = None
    while True:
        try:
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(f"{os.getpid()}\n")
                stream.flush()
                os.fsync(stream.fileno())
            info = lock.lstat()
            owner_inode = (info.st_dev, info.st_ino)
            break
        except FileExistsError:
            try:
                info = lock.lstat()
                stale = False
                if lock.is_symlink():
                    stale = True
                elif info.st_size <= 32:
                    try:
                        owner = int(lock.read_text(encoding="ascii").strip())
                    except (OSError, ValueError):
                        owner = 0
                    if owner > 0:
                        try:
                            os.kill(owner, 0)
                        except ProcessLookupError:
                            stale = True
                        except PermissionError:
                            pass
                    elif time.time() - info.st_mtime > 3600:
                        stale = True
                elif time.time() - info.st_mtime > 3600:
                    stale = True
                if stale:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise RuntimeValidationError("runtime cache entry is busy") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        # Never remove a lock that was created by a later owner after an
        # unexpected unlink or replacement.
        try:
            info = lock.lstat()
            if owner_inode == (info.st_dev, info.st_ino):
                lock.unlink()
        except FileNotFoundError:
            pass


@contextlib.asynccontextmanager
async def _async_entry_lock(destination: Path) -> AsyncIterator[None]:
    """Hold the file lock without blocking the event loop while waiting."""
    lock = _entry_lock(destination)
    acquire = asyncio.create_task(asyncio.to_thread(lock.__enter__))
    try:
        await asyncio.shield(acquire)
    except asyncio.CancelledError:

        def release_late(done: asyncio.Future[None]) -> None:
            if done.cancelled() or done.exception() is not None:
                return
            release = asyncio.create_task(
                asyncio.to_thread(lock.__exit__, None, None, None)
            )
            release.add_done_callback(
                lambda task: None if task.cancelled() else task.exception()
            )

        acquire.add_done_callback(release_late)
        raise
    try:
        yield
    finally:
        release = asyncio.create_task(
            asyncio.to_thread(lock.__exit__, None, None, None)
        )
        await asyncio.shield(release)


def _cache_path_has_symlink(path: Path, root: Path) -> bool:
    """Return whether an existing component between root and path is a link."""
    try:
        path.relative_to(root)
    except ValueError:
        return True
    current = path
    while True:
        if current.is_symlink():
            return True
        if current == root:
            return False
        current = current.parent


def _platform_env(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _is_windows_system_path(
    path: str | Path, env: Mapping[str, str] | None = None
) -> bool:
    candidate = PureWindowsPath(str(path))
    blocked: set[PureWindowsPath] = set()
    if candidate.drive:
        blocked.add(PureWindowsPath(candidate.drive + "\\Windows"))
    values = _platform_env(env)
    for name in ("SYSTEMROOT", "WINDIR"):
        value = values.get(name)
        if value:
            blocked.add(PureWindowsPath(value))
    return any(candidate == item or item in candidate.parents for item in blocked)


def resolve_cache_root(
    project_root: str | Path,
    explicit: str | Path | None = None,
    cli_override: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve and validate an external cache root.

    ``explicit`` has highest precedence, followed by the CLI override and
    ``M3_HARNESS_CACHE_DIR``. A cache nested in the project, PATH, or common system
    directories is rejected to prevent accidental repository or system writes.
    """
    project = Path(project_root).expanduser().resolve()
    raw = explicit or cli_override or _platform_env(env).get("M3_HARNESS_CACHE_DIR")
    if raw:
        requested_root = Path(raw).expanduser()
        if requested_root.is_symlink():
            raise RuntimeValidationError("runtime cache path contains a symlink")
        root = requested_root.resolve()
    elif sys.platform == "win32":
        root = (
            Path(
                _platform_env(env).get(
                    "LOCALAPPDATA", Path.home() / "AppData" / "Local"
                )
            )
            / "m3"
            / "harnesses"
        )
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches" / "m3" / "harnesses"
    else:
        root = (
            Path(_platform_env(env).get("XDG_CACHE_HOME", Path.home() / ".cache"))
            / "m3"
            / "harnesses"
        )
    root = root.resolve()
    if sys.platform == "win32" and _is_windows_system_path(root, env):
        raise RuntimeValidationError(
            "runtime cache must not be inside a system directory"
        )
    try:
        root.relative_to(project)
    except ValueError:
        pass
    else:
        raise RuntimeValidationError("runtime cache must be outside the project")
    path_entries = [
        Path(p).expanduser().resolve()
        for p in _platform_env(env).get("PATH", "").split(os.pathsep)
        if p
    ]
    if any(root == p or p in root.parents for p in path_entries):
        raise RuntimeValidationError("runtime cache must not be inside PATH")
    blocked = [
        Path("/usr"),
        Path("/bin"),
        Path("/sbin"),
        Path("/System"),
    ]
    if any(root == p or p in root.parents for p in blocked):
        raise RuntimeValidationError(
            "runtime cache must not be inside a system directory"
        )
    return root


def detect_target(
    kind: str | None = None, *, env: Mapping[str, str] | None = None
) -> str:
    """Return a stable target label, including OpenCode ABI distinctions."""
    e = {} if env is None else env
    machine = (e.get("M3_RUNTIME_ARCH") or platform.machine()).lower()
    machine = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(
        machine, machine
    )
    system = sys.platform
    if system == "darwin":
        system_name = "darwin"
        if machine == "arm64" and e.get("M3_ROSETTA", "").lower() in {
            "1",
            "true",
            "yes",
        }:
            machine = "x64-rosetta"
    elif system.startswith("win"):
        system_name = "windows"
    else:
        system_name = "linux"
    bits = "64" if sys.maxsize > 2**32 else "32"
    if kind in {"opencode", "open-code"} and system_name == "linux":
        libc = (e.get("M3_RUNTIME_LIBC") or "").lower()
        if not libc:
            try:
                libc = platform.libc_ver()[0].lower() or "glibc"
            except Exception:
                libc = "glibc"
        avx = e.get("M3_RUNTIME_AVX2", "").lower() in {"1", "true", "yes"}
        return f"linux-{machine}-{libc}-{'avx2' if avx else 'baseline'}"
    return f"{system_name}-{machine}-{bits}"


def _safe_component(value: str, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
    ):
        raise RuntimeValidationError(f"invalid {name}")
    return value


def _safe_relative(value: str, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 256
        or "\\" in value
        or ":" in value
        or value.startswith("/")
        or any(ord(character) < 32 for character in value)
    ):
        raise RuntimeValidationError(f"invalid {name}")
    parts = value.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise RuntimeValidationError(f"invalid {name}")
    for part in parts:
        _safe_component(part, name)
    return value


def _version(value: Any) -> str:
    normalized = str(value)
    if len(normalized) > 64:
        raise RuntimeValidationError("invalid runtime version")
    if normalized == "latest":
        return normalized
    if not VERSION_RE.match(normalized):
        raise RuntimeValidationError("invalid runtime version")
    return normalized.lstrip("v")


def _release_version(kind: str, tag: str) -> str:
    """Canonicalize a vendor release tag before applying version validation."""
    normalized = tag.strip()
    if kind == "codex":
        normalized = normalized.removeprefix("rust-")
    normalized = normalized.removeprefix("v")
    return _version(normalized)


def _url(value: Any) -> str:
    parsed = urllib.parse.urlparse(str(value))
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        (parsed.scheme != "https" and not (parsed.scheme == "http" and local))
        or not parsed.netloc
        or "@" in parsed.netloc
    ):
        raise RuntimeValidationError("runtime URL must use HTTPS without credentials")
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, "")
    )


def _url_no_query(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, "", "", "")
    )


@dataclass(frozen=True)
class RuntimeLease:
    executable: Path
    provenance: Mapping[str, Any]
    _lease_path: Path | None = None

    async def release(self) -> None:
        if self._lease_path:
            try:
                self._lease_path.unlink()
            except FileNotFoundError:
                pass

    async def close(self) -> None:
        await self.release()


class ProgressEmitter:
    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None

    def emit(self, event: str, **fields: Any) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"event": event, "time": time.time(), **fields}
        line = json.dumps(payload, sort_keys=True)
        if len(line) > 2047:
            payload["requested_selector"] = str(payload.get("requested_selector", ""))[
                :128
            ]
            payload.pop("url", None)
            line = json.dumps(payload, sort_keys=True)
            if len(line) > 2047:
                payload = {
                    key: payload[key]
                    for key in ("event", "time", "phase", "kind", "target")
                    if key in payload
                }
                line = json.dumps(payload, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as f:
            with contextlib.suppress(ImportError):
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(line + "\n")
            f.flush()
            with contextlib.suppress(ImportError):
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class RuntimeManager:
    def __init__(
        self,
        cache_root: str | Path | None = None,
        project_root: str | Path = ".",
        invocation_dir: str | Path | None = None,
        progress_file: str | Path | None = None,
    ):
        self.project_root = Path(project_root).expanduser().resolve()
        self.cache_root = resolve_cache_root(self.project_root, cache_root)
        self.invocation_dir = (
            Path(invocation_dir).expanduser().resolve() if invocation_dir else None
        )
        self.progress = ProgressEmitter(progress_file)
        self._leases: list[RuntimeLease] = []
        self._manifest_cache: dict[str, Mapping[str, Any]] = {}

    async def acquire(
        self, kind: str, selector: str | Mapping[str, Any]
    ) -> RuntimeLease:
        kind = {"open-code": "opencode", "claude_code": "claude"}.get(
            kind.lower(), kind.lower()
        )
        if kind not in KINDS:
            raise RuntimeValidationError("unsupported runtime kind")
        spec = {"version": selector} if isinstance(selector, str) else dict(selector)
        version = _version(spec.get("version") or spec.get("tag"))
        target_env = spec.get("env")
        if target_env is not None and not isinstance(target_env, Mapping):
            raise RuntimeValidationError("runtime target environment is invalid")
        target = detect_target(kind, env=target_env)
        requested_selector = version
        if version != "latest" and isinstance(selector, str):
            cached = await asyncio.to_thread(
                self._cached_version, kind, version, target
            )
            if cached is not None:
                self.progress.emit(
                    "cache_hit",
                    phase="ready",
                    kind=kind,
                    target=target,
                    requested_selector=requested_selector,
                    resolved_version=version,
                )
                return await asyncio.to_thread(self._lease, cached)
        url = spec.get("url") or spec.get("asset_url") or spec.get("download_url")
        manifest_url = spec.get("manifest_url")
        if not url and not manifest_url and version == "latest":
            manifest_url = default_manifest_url(kind, version)
        if not url and not manifest_url and version != "latest":
            manifest_url = default_manifest_url(kind, version)
        if not url and manifest_url:
            self.progress.emit(
                "acquire_start",
                phase="resolve",
                kind=kind,
                target=target,
                requested_selector=requested_selector,
                resolved_version=None if version == "latest" else version,
            )
            manifest_url = _url(manifest_url)
            manifest = await self._resolve_manifest(kind, manifest_url, version, target)
            item = self._select_manifest_asset(kind, manifest, target, version)
            resolved_manifest_version = (
                item.get("version") if isinstance(item, Mapping) else None
            )
            if not resolved_manifest_version:
                resolved_manifest_version = manifest.get("version")
            spec = {**spec, **item}
            url = spec.get("url") or spec.get("asset_url")
            if version == "latest":
                version = _version(resolved_manifest_version)
        if not url:
            raise RuntimeValidationError(
                "selector must provide a download URL or manifest_url"
            )
        url = _url(url)
        digest = str(spec.get("sha256") or "").lower()
        if not digest:
            raise RuntimeValidationError("runtime metadata lacks an independent sha256")
        if not SHA_RE.match(digest):
            raise RuntimeValidationError("invalid sha256")
        executable_name = str(
            spec.get("executable")
            or {
                "claude": "claude",
                "opencode": "opencode",
                "codex": "codex",
                "pi": "pi",
            }.get(kind, kind)
        )
        executable_name = _safe_relative(executable_name, "executable")
        destination = (
            self.cache_root
            / kind
            / _safe_component(version, "version")
            / _safe_component(target, "target")
            / ("sha256-" + digest)
        )
        if _cache_path_has_symlink(destination, self.cache_root):
            raise RuntimeValidationError("runtime cache path contains a symlink")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if _cache_path_has_symlink(destination, self.cache_root):
            raise RuntimeValidationError("runtime cache path contains a symlink")
        staged_asset: Path | None = None
        downloaded_size = 0
        try:
            async with _async_entry_lock(destination):
                if not self._receipt_valid(destination):
                    self.progress.emit(
                        "acquire_start",
                        phase="download",
                        requested_selector=requested_selector,
                        resolved_version=version,
                        kind=kind,
                        target=target,
                        url=_url_no_query(url),
                        bytes=0,
                    )
                    download = asyncio.create_task(
                        asyncio.to_thread(
                            self._download_staged,
                            url,
                            kind,
                            lambda current, total: self.progress.emit(
                                "download_progress",
                                phase="download",
                                requested_selector=requested_selector,
                                resolved_version=version,
                                kind=kind,
                                target=target,
                                bytes=current,
                                total_bytes=total,
                            ),
                        )
                    )
                    try:
                        staged_asset, downloaded_size, actual = await asyncio.shield(
                            download
                        )
                    except asyncio.CancelledError:
                        # Let the worker finish so its temporary file is known
                        # to the cleanup path before releasing the entry lock.
                        staged_asset, downloaded_size, actual = await download
                        raise
                    self.progress.emit(
                        "acquire_start",
                        phase="verify",
                        kind=kind,
                        target=target,
                        requested_selector=requested_selector,
                        resolved_version=version,
                    )
                    if digest != actual:
                        raise RuntimeValidationError("runtime sha256 mismatch")
                    if destination.exists() and any(destination.glob("lease-*")):
                        raise RuntimeValidationError(
                            "runtime cache entry is in use and invalid"
                        )
                    staging_destination = destination.with_name(
                        f".staging-{os.getpid()}-{time.time_ns()}"
                    )
                    staging_destination.mkdir(mode=0o700)
                    self.progress.emit(
                        "acquire_start",
                        phase="extract",
                        requested_selector=requested_selector,
                        resolved_version=version,
                        kind=kind,
                        target=target,
                        bytes=downloaded_size,
                    )
                    if staged_asset is None:
                        raise RuntimeValidationError("runtime asset staging failed")
                    await asyncio.to_thread(
                        self._install,
                        staged_asset,
                        staging_destination,
                        executable_name,
                        kind,
                    )
                    smoke = staging_destination / executable_name
                    if (
                        not smoke.is_file()
                        or smoke.is_symlink()
                        or staging_destination not in smoke.resolve().parents
                    ):
                        raise RuntimeValidationError(
                            "runtime archive contains no expected executable"
                        )
                    try:
                        with tempfile.TemporaryDirectory(
                            prefix=".smoke-", dir=self.cache_root
                        ) as smoke_home_value:
                            smoke_home = Path(smoke_home_value)
                            smoke_home.chmod(0o700)
                            result = await asyncio.to_thread(
                                subprocess.run,
                                [str(smoke), "--version"],
                                capture_output=True,
                                timeout=10,
                                check=True,
                                text=True,
                                cwd=smoke_home,
                                env=self._smoke_environment(smoke_home),
                            )
                        output = (result.stdout + result.stderr).lower()
                        if not self._output_reports_version(output, version):
                            raise RuntimeValidationError(
                                "runtime executable reported an unexpected version"
                            )
                    except (OSError, subprocess.SubprocessError) as exc:
                        raise RuntimeValidationError(
                            "runtime executable failed --version smoke check"
                        ) from exc
                    provenance = {
                        "kind": kind,
                        "version": version,
                        "target": target,
                        "url": _url_no_query(url),
                        "sha256": digest,
                        "source": spec.get("source", "selector"),
                        "executable": executable_name,
                        "asset_name": spec.get("asset_name"),
                        "verification_method": spec.get(
                            "verification_method", "github_asset_digest"
                        ),
                        "immutable_release": spec.get("immutable_release"),
                    }
                    pending_receipt = staging_destination / "receipt.pending"
                    pending_receipt.write_text(
                        json.dumps(
                            {
                                "provenance": provenance,
                                "files": self._tree_hashes(staging_destination),
                            },
                            sort_keys=True,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    os.replace(pending_receipt, staging_destination / "receipt.json")
                    if destination.exists():
                        if destination.is_symlink():
                            raise RuntimeValidationError(
                                "runtime cache path contains a symlink"
                            )
                        shutil.rmtree(destination)
                    # Windows scanners may briefly hold the smoke-tested binary.
                    for attempt in range(10):
                        try:
                            os.replace(staging_destination, destination)
                            break
                        except PermissionError:
                            if attempt == 9:
                                raise
                            time.sleep(0.1 * (attempt + 1))
        finally:
            if "staging_destination" in locals() and staging_destination.exists():
                shutil.rmtree(staging_destination, ignore_errors=True)
            if staged_asset is not None:
                staged_asset.unlink(missing_ok=True)
        lease = await asyncio.to_thread(self._lease, destination, executable_name)
        self.progress.emit(
            "acquire_complete",
            phase="ready",
            requested_selector=requested_selector,
            resolved_version=version,
            kind=kind,
            target=target,
            sha256=digest,
            bytes=0,
        )
        return lease

    @staticmethod
    def _smoke_environment(home: Path) -> dict[str, str]:
        environment = {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CACHE_HOME": str(home / "cache"),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_DATA_HOME": str(home / "data"),
            "TMPDIR": str(home),
            "TMP": str(home),
            "TEMP": str(home),
            "PATH": os.defpath,
            "CI": "1",
            "NO_COLOR": "1",
        }
        if os.name == "nt":
            for name in ("SYSTEMROOT", "WINDIR"):
                value = os.environ.get(name)
                if value:
                    environment[name] = value
        return environment

    @staticmethod
    def _output_reports_version(output: str, version: str) -> bool:
        pattern = rf"(?:^|[^0-9A-Za-z._])v?{re.escape(version)}(?![0-9A-Za-z._-])"
        return re.search(pattern, output, re.IGNORECASE) is not None

    def _cached_version(self, kind: str, version: str, target: str) -> Path | None:
        parent = self.cache_root / kind / version / target
        if _cache_path_has_symlink(parent, self.cache_root) or not parent.is_dir():
            return None
        candidates = [
            path
            for path in parent.glob("sha256-*")
            if not _cache_path_has_symlink(path, self.cache_root)
            and self._receipt_valid(path)
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _lease(
        self, destination: Path, executable_name: str | None = None
    ) -> RuntimeLease:
        with _entry_lock(destination):
            if not self._receipt_valid(destination):
                raise RuntimeValidationError("runtime cache entry failed verification")
            receipt = json.loads(
                (destination / "receipt.json").read_text(encoding="utf-8")
            )
            executable = destination / _safe_relative(
                executable_name or receipt["provenance"].get("executable", ""),
                "executable",
            )
            if (
                not executable.is_file()
                or executable.is_symlink()
                or destination not in executable.resolve().parents
            ):
                raise RuntimeValidationError(
                    "runtime archive contains no expected executable"
                )
            lease_path = destination / f"lease-{os.getpid()}-{time.time_ns()}"
            descriptor = os.open(
                lease_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            try:
                os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
            finally:
                os.close(descriptor)
        lease = RuntimeLease(executable, receipt["provenance"], lease_path)
        self._leases.append(lease)
        return lease

    async def close(self) -> None:
        await asyncio.gather(
            *(x.release() for x in self._leases), return_exceptions=True
        )
        self._leases.clear()

    async def release(self, lease: RuntimeLease) -> None:
        await lease.release()
        if lease in self._leases:
            self._leases.remove(lease)

    async def _fetch_json(self, url: str) -> Mapping[str, Any]:
        raw = await asyncio.to_thread(self._download, url, "manifest")
        try:
            value = json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise RuntimeValidationError("invalid runtime manifest") from e
        if not isinstance(value, dict):
            raise RuntimeValidationError("invalid runtime manifest")
        return value

    async def _resolve_manifest(
        self, kind: str, url: str, version: str, target: str
    ) -> Mapping[str, Any]:
        """Pin latest metadata once per invocation, including across xdist workers."""
        key = hashlib.sha256(f"{kind}\0{url}\0{version}\0{target}".encode()).hexdigest()
        if key in self._manifest_cache:
            return self._manifest_cache[key]
        if version != "latest" or self.invocation_dir is None:
            manifest = await self._fetch_claude_or_json(kind, url, version)
            self._manifest_cache[key] = manifest
            return manifest
        directory = self.invocation_dir
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        record = directory / f"{key}.json"
        lock = directory / f"{key}.lock"
        if record.is_file():
            manifest = self._read_pin(record)
            self._manifest_cache[key] = manifest
            return manifest
        owner = False
        owner_inode: tuple[int, int] | None = None
        try:
            deadline = time.monotonic() + 30
            while True:
                if record.is_file():
                    manifest = self._read_pin(record)
                    self._manifest_cache[key] = manifest
                    return manifest
                try:
                    descriptor = os.open(
                        lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                    )
                    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                        stream.write(f"{os.getpid()}\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    info = lock.lstat()
                    owner_inode = (info.st_dev, info.st_ino)
                    owner = True
                    break
                except FileExistsError:
                    if self._pin_lock_is_stale(lock):
                        try:
                            stale_info = lock.lstat()
                            stale_inode = (stale_info.st_dev, stale_info.st_ino)
                            if self._pin_lock_is_stale(lock):
                                current = lock.lstat()
                                if stale_inode == (current.st_dev, current.st_ino):
                                    lock.unlink()
                                    continue
                        except FileNotFoundError:
                            continue
                    if time.monotonic() >= deadline:
                        raise RuntimeValidationError(
                            "runtime manifest resolution lock timed out"
                        ) from None
                    await asyncio.sleep(0.05)
            manifest = await self._fetch_claude_or_json(kind, url, version)
            selected = self._select_manifest_asset(kind, manifest, target, version)
            selected_url = (
                selected.get("url")
                or selected.get("asset_url")
                or selected.get("download_url")
            )
            if (
                isinstance(selected_url, str)
                and urllib.parse.urlparse(selected_url).query
            ):
                raise RuntimeValidationError("signed runtime URLs cannot be pinned")
            manifest = self._sanitize_pin(manifest)
            encoded = json.dumps(manifest, sort_keys=True)
            if len(encoded) > 1024 * 1024:
                raise RuntimeValidationError("runtime metadata exceeds pin size limit")
            if "?" in encoded and any(
                token in encoded.lower() for token in ("signature", "token=", "x-amz-")
            ):
                raise RuntimeValidationError("signed runtime URLs cannot be pinned")
            temporary = directory / f"{key}.{os.getpid()}.tmp"
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, record)
            self._manifest_cache[key] = manifest
            return cast(Mapping[str, Any], manifest)

        finally:
            if owner and owner_inode is not None:
                try:
                    info = lock.lstat()
                    if owner_inode == (info.st_dev, info.st_ino):
                        lock.unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
    def _sanitize_pin(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: RuntimeManager._sanitize_pin(item) for key, item in value.items()
            }
        if isinstance(value, list):
            return [RuntimeManager._sanitize_pin(item) for item in value]
        if isinstance(value, str) and (
            value.startswith("http://") or value.startswith("https://")
        ):
            return _url_no_query(value)
        return value

    @staticmethod
    def _pin_lock_is_stale(path: Path) -> bool:
        try:
            info = path.lstat()
            if path.is_symlink():
                return True
            if info.st_size > 32:
                return time.time() - info.st_mtime > 3600
            try:
                owner = int(path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                # Avoid racing the brief interval between exclusive creation
                # and writing the owner PID.
                return time.time() - info.st_mtime > 1
            if owner <= 0:
                return time.time() - info.st_mtime > 1
            try:
                os.kill(owner, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            except OSError:
                return True
            return False
        except FileNotFoundError:
            return False

    @staticmethod
    def _read_pin(path: Path) -> Mapping[str, Any]:
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            raise RuntimeValidationError("runtime invocation pin is invalid")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeValidationError("runtime invocation pin is invalid") from exc
        if not isinstance(value, dict):
            raise RuntimeValidationError("runtime invocation pin is invalid")
        return value

    async def _fetch_claude_or_json(
        self, kind: str, url: str, version: str
    ) -> Mapping[str, Any]:
        if (
            kind != "claude"
            or version != "latest"
            or urllib.parse.urlparse(url).hostname != "downloads.claude.ai"
        ):
            return await self._fetch_json(url)
        raw = await asyncio.to_thread(self._download, url, "manifest")
        try:
            resolved = _version(raw.decode("utf-8").strip())
        except (UnicodeError, RuntimeValidationError):
            raise RuntimeValidationError("invalid Claude release channel") from None
        manifest_url = default_manifest_url("claude", resolved)
        assert manifest_url is not None
        manifest = await self._fetch_json(manifest_url)
        if manifest.get("version") != resolved:
            raise RuntimeValidationError("Claude release metadata version mismatch")
        return manifest

    @staticmethod
    def _select_manifest_asset(
        kind: str, manifest: Mapping[str, Any], target: str, requested: str
    ) -> Mapping[str, Any]:
        """Turn a GitHub release response into the selector shape."""
        if kind == "claude" and "platforms" in manifest:
            version = _version(manifest.get("version"))
            if requested != "latest" and version != requested:
                raise RuntimeValidationError("Claude release metadata version mismatch")
            platform_name = target.split("-64", 1)[0].replace("windows", "win32")
            platform_name = platform_name.replace(
                "linux-x64-glibc-baseline", "linux-x64"
            )
            platforms = manifest.get("platforms")
            item = (
                platforms.get(platform_name) if isinstance(platforms, Mapping) else None
            )
            if not isinstance(item, Mapping):
                raise RuntimeValidationError(
                    f"Claude release lacks target {platform_name}"
                )
            digest = item.get("checksum")
            if not isinstance(digest, str) or not SHA_RE.fullmatch(digest):
                raise RuntimeValidationError("Claude release metadata lacks checksum")
            base = "https://downloads.claude.ai/claude-code-releases"
            return {
                "version": version,
                "url": f"{base}/{version}/{platform_name}/claude",
                "sha256": digest,
                "executable": "claude.exe"
                if platform_name.startswith("win32")
                else "claude",
                "source": "claude-manifest",
                "asset_name": platform_name,
                "verification_method": "claude_manifest_checksum",
                "immutable_release": None,
            }
        if isinstance(manifest.get("assets"), list):
            candidates = [
                a
                for a in manifest["assets"]
                if isinstance(a, Mapping)
                and a.get("browser_download_url")
                and str(a.get("name", ""))
                .lower()
                .endswith((".tar.gz", ".tgz", ".zip", ".tar.xz"))
                and not re.search(
                    r"(?:\.sha256|\.sha512|\.sig|\.asc|checksums?)$",
                    str(a.get("name", "")),
                    re.I,
                )
            ]
            patterns = RuntimeManager._asset_patterns(kind, target)
            matches: list[Mapping[str, Any]] = []
            # Patterns are ordered by preference so current assets can take
            # priority without making historical releases ambiguous.
            for pattern in patterns:
                matches = [
                    a for a in candidates if re.fullmatch(pattern, str(a["name"]), re.I)
                ]
                if matches:
                    break
            if len(matches) != 1:
                state = "no" if not matches else "ambiguous"
                raise RuntimeValidationError(
                    f"{state} {kind} release asset matches target {target}"
                )
            asset = matches[0]
            digest = str(asset.get("digest", "")).removeprefix("sha256:")
            name = str(asset.get("name", "")).lower()
            if kind == "pi":
                executable = "pi.exe" if target.startswith("windows-") else "pi/pi"
            elif kind == "opencode":
                executable = (
                    "opencode.exe" if target.startswith("windows-") else "opencode"
                )
            else:
                executable = Path(name).name.rsplit(".tar", 1)[0].rsplit(".zip", 1)[0]
            return {
                "version": _release_version(
                    kind, str(manifest.get("tag_name", requested))
                ),
                "url": asset["browser_download_url"],
                "sha256": digest,
                "executable": executable,
                "asset_name": asset["name"],
                "verification_method": "github_asset_digest",
                "immutable_release": (
                    bool(manifest["immutable"]) if "immutable" in manifest else None
                ),
            }
        return manifest.get(target) or manifest.get(requested) or manifest

    @staticmethod
    def _asset_patterns(kind: str, target: str) -> tuple[str, ...]:
        parts = target.split("-")
        system, arch = parts[0], parts[1]
        if kind == "opencode":
            if system == "linux":
                libc = parts[2] if len(parts) > 2 else "glibc"
                variant = parts[3] if len(parts) > 3 else "baseline"
                if arch == "arm64":
                    asset = "opencode-linux-arm64"
                    if libc == "musl":
                        asset += "-musl"
                else:
                    asset = "opencode-linux-x64"
                    if variant == "baseline":
                        asset += "-baseline"
                    if libc == "musl":
                        asset += "-musl"
                return (rf"{asset}\.tar\.gz",)
            platform = "windows" if system == "windows" else system
            return (rf"opencode-{platform}-{arch}\.(?:tar\.gz|tgz|zip)",)
        if kind == "codex":
            codex_arch = "aarch64" if arch == "arm64" else "x86_64"
            platform = {
                "darwin": "apple-darwin",
                "linux": "unknown-linux-musl",
                "windows": "pc-windows-msvc",
            }.get(system, system)
            if system == "windows":
                return (rf"codex-{codex_arch}-{platform}\.exe\.zip",)
            preferred = rf"codex-{codex_arch}-{platform}\.(?:tar\.gz|tgz|zip)"
            if system == "linux":
                legacy = rf"codex-{codex_arch}-unknown-linux-gnu\.(?:tar\.gz|tgz|zip)"
                return (preferred, legacy)
            return (preferred,)
        if kind == "pi":
            return (rf"pi-{system}-{arch}\.(?:tar\.gz|tgz|zip)",)
        raise RuntimeValidationError(f"unsupported release target for {kind}: {target}")

    @staticmethod
    def _open_download(url: str) -> Any:
        """Open a validated download while stripping credentials on redirects."""
        headers = {"User-Agent": "m3-runtime/1"}
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme == "https" and parsed.hostname == "api.github.com":
            token = os.environ.get("M3_GITHUB_TOKEN", "").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)

        class SafeRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(
                self,
                request: Any,
                fp: Any,
                code: int,
                msg: str,
                headers: Any,
                newurl: str,
            ) -> Any:
                _url(newurl)
                redirected = super().redirect_request(
                    request, fp, code, msg, headers, newurl
                )
                if redirected is not None:
                    redirected.remove_header("Authorization")
                    redirected.remove_header("Proxy-Authorization")
                return redirected

        try:
            return urllib.request.build_opener(SafeRedirect()).open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            raise RuntimeValidationError(
                f"runtime download failed (HTTP {exc.code})"
            ) from None
        except (OSError, ValueError):
            raise RuntimeValidationError("runtime download failed") from None

    @staticmethod
    def _download(
        url: str,
        kind: str,
        progress: Callable[[int, int | None], None] | None = None,
    ) -> bytes:
        """Read small metadata through the same bounded download path as assets."""
        staged, _, _ = RuntimeManager._download_staged(url, kind, progress)
        try:
            return staged.read_bytes()
        except OSError:
            raise RuntimeValidationError("runtime download failed") from None
        finally:
            staged.unlink(missing_ok=True)

    @staticmethod
    def _download_staged(
        url: str,
        kind: str,
        progress: Callable[[int, int | None], None] | None = None,
    ) -> tuple[Path, int, str]:
        """Stream an asset into a private file while hashing it."""
        cap = (
            8 * 1024**2
            if kind == "manifest"
            else CAPS.get(kind, (512 * 1024**2, 512 * 1024**2, 1024))[0]
        )
        descriptor, staged_name = tempfile.mkstemp(prefix="m3-runtime-")
        os.close(descriptor)
        staged = Path(staged_name)
        try:
            with (
                RuntimeManager._open_download(url) as response,
                staged.open("wb") as output,
            ):
                _url(response.geturl())
                length = int(response.headers.get("Content-Length") or 0)
                if length > cap:
                    raise RuntimeValidationError("runtime download exceeds size cap")
                digest = hashlib.sha256()
                total = 0
                while True:
                    chunk = response.read(min(1024 * 1024, cap - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > cap:
                        raise RuntimeValidationError(
                            "runtime download exceeds size cap"
                        )
                    output.write(chunk)
                    digest.update(chunk)
                    if progress is not None:
                        progress(total, length or None)
                return staged, total, digest.hexdigest()
        except RuntimeValidationError:
            staged.unlink(missing_ok=True)
            raise
        except (OSError, ValueError):
            staged.unlink(missing_ok=True)
            raise RuntimeValidationError("runtime download failed") from None

    @staticmethod
    def _install(data: Path, destination: Path, executable: str, kind: str) -> None:
        with tempfile.TemporaryDirectory(dir=destination.parent) as td:
            source = Path(td) / "archive"
            shutil.copyfile(data, source)
            staging = Path(td) / "out"
            staging.mkdir()
            try:
                source_size = data.stat().st_size
                expanded_cap = min(CAPS[kind][1], max(source_size * 100, source_size))
                if zipfile.is_zipfile(source):
                    with zipfile.ZipFile(source) as z:
                        RuntimeManager._extract_zip(
                            z, staging, CAPS[kind][2], expanded_cap
                        )
                elif tarfile.is_tarfile(source):
                    with tarfile.open(source) as t:
                        RuntimeManager._extract_tar(
                            t, staging, CAPS[kind][2], expanded_cap
                        )
                else:
                    raw_executable = staging / executable
                    raw_executable.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(data, raw_executable)
                    raw_executable.chmod(0o755)
            except (tarfile.TarError, zipfile.BadZipFile) as e:
                raise RuntimeValidationError("invalid runtime archive") from e
            for p in staging.rglob("*"):
                rel = p.relative_to(staging)
                target = destination / rel
                if p.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                elif p.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, target)
            target_exec = destination / executable
            if target_exec.exists():
                target_exec.chmod(target_exec.stat().st_mode | stat.S_IXUSR)

    @staticmethod
    def _extract_zip(
        z: zipfile.ZipFile, out: Path, max_entries: int, max_bytes: int
    ) -> None:
        infos = z.infolist()
        if len(infos) > max_entries:
            raise RuntimeValidationError("archive contains too many entries")
        total = 0
        seen: set[Path] = set()
        for info in infos:
            total += info.file_size
            if total > max_bytes:
                raise RuntimeValidationError("archive expands beyond size cap")
            name = Path(info.filename)
            if name.is_absolute() or ".." in name.parts:
                raise RuntimeValidationError("archive path traversal")
            if name in seen:
                raise RuntimeValidationError("archive contains duplicate paths")
            seen.add(name)
            mode = (info.external_attr >> 16) & 0o170000
            if mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise RuntimeValidationError("archive contains an unsafe member")
            target = out / name
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, target.open("xb") as dst:
                    RuntimeManager._copy_bounded(src, dst, info.file_size)

    @staticmethod
    def _extract_tar(
        t: tarfile.TarFile, out: Path, max_entries: int, max_bytes: int
    ) -> None:
        members = t.getmembers()
        if len(members) > max_entries:
            raise RuntimeValidationError("archive contains too many entries")
        total = sum(max(0, m.size) for m in members if m.isfile())
        if total > max_bytes:
            raise RuntimeValidationError("archive expands beyond size cap")
        seen: set[Path] = set()
        for member in members:
            name = Path(member.name)
            if (
                name.is_absolute()
                or ".." in name.parts
                or not (member.isdir() or member.isfile())
            ):
                raise RuntimeValidationError("unsafe archive member")
            if name in seen:
                raise RuntimeValidationError("archive contains duplicate paths")
            seen.add(name)
            target = out / name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                src = t.extractfile(member)
                if src:
                    with target.open("xb") as dst:
                        RuntimeManager._copy_bounded(src, dst, member.size)

    @staticmethod
    def _copy_bounded(source: Any, destination: Any, expected_bytes: int) -> None:
        copied = 0
        while True:
            chunk = source.read(min(1024 * 1024, expected_bytes - copied + 1))
            if not chunk:
                break
            copied += len(chunk)
            if copied > expected_bytes:
                raise RuntimeValidationError("archive member exceeds declared size")
            destination.write(chunk)
        if copied != expected_bytes:
            raise RuntimeValidationError("archive member is truncated")

    @staticmethod
    def _tree_hashes(root: Path) -> dict[str, str]:
        out = {}
        for p in sorted(root.rglob("*")):
            if p.is_symlink():
                raise RuntimeValidationError("runtime cache contains a symlink")
            if (
                p.is_file()
                and p.name not in {"receipt.json", "receipt.pending"}
                and not p.name.startswith("lease-")
            ):
                h = hashlib.sha256()
                with p.open("rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(chunk)
                out[str(p.relative_to(root))] = h.hexdigest()
        return out

    @classmethod
    def _receipt_valid(cls, root: Path) -> bool:
        receipt = root / "receipt.json"
        if not receipt.is_file():
            return False
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return False
            provenance = payload.get("provenance")
            if not isinstance(provenance, dict) or not SHA_RE.match(
                str(provenance.get("sha256", ""))
            ):
                return False
            if root.name != "sha256-" + str(provenance["sha256"]).lower():
                return False
            if (
                provenance.get("kind"),
                provenance.get("version"),
                provenance.get("target"),
            ) != (
                root.parent.parent.parent.name,
                root.parent.parent.name,
                root.parent.name,
            ):
                return False
            files = payload.get("files")
            if not isinstance(files, dict):
                return False
            return files == cls._tree_hashes(root)
        except (OSError, ValueError, TypeError):
            return False


def list_cache(cache_root: str | Path) -> list[Mapping[str, Any]]:
    root = Path(cache_root).expanduser()
    if root.is_symlink():
        return []
    root = root.resolve()
    result: list[Mapping[str, Any]] = []
    for entry in root.glob("*/*/*/sha256-*"):
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            receipt = entry / "receipt.json"
            relative = entry.relative_to(root)
            current = root
            if any((current := current / part).is_symlink() for part in relative.parts):
                continue
            if receipt.is_symlink():
                continue
            parsed = (
                json.loads(receipt.read_text(encoding="utf-8"))
                if receipt.is_file()
                else {}
            )
            item = dict(parsed) if isinstance(parsed, Mapping) else {}
            item["path"] = str(receipt.parent)
            item["status"] = (
                "ready" if RuntimeManager._receipt_valid(entry) else "corrupt"
            )
            result.append(item)
        except (OSError, TypeError, ValueError):
            if not entry.is_symlink() and entry.is_dir():
                result.append({"path": str(entry), "status": "corrupt"})
    return result


def prune_cache(
    cache_root: str | Path, *, max_age: float | None = None, dry_run: bool = False
) -> list[Path]:
    root = Path(cache_root).expanduser().resolve()
    now = time.time()
    removed: list[Path] = []
    candidates = {Path(str(item["path"])) for item in list_cache(root)}
    for lock in root.glob("*/*/*/sha256-*.lock"):
        if (
            lock.is_symlink()
            or not lock.is_file()
            or re.fullmatch(r"sha256-[0-9a-fA-F]{64}\.lock", lock.name) is None
        ):
            continue
        candidates.add(lock.with_name(lock.name.removesuffix(".lock")))
    for path in sorted(candidates):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            continue
        with _entry_lock(path, timeout=10):
            if not path.exists():
                continue
            if path.is_symlink() or not path.is_dir():
                continue
            active_lease = False
            for lease in path.glob("lease-*"):
                if _lease_is_active(lease):
                    active_lease = True
                else:
                    lease.unlink(missing_ok=True)
            if active_lease:
                continue
            if max_age is not None and now - path.stat().st_mtime < max_age:
                continue
            removed.append(path)
            if not dry_run:
                shutil.rmtree(path)
    return removed


def _lease_is_active(path: Path) -> bool:
    """Check the owning PID recorded in a lease marker."""
    if path.is_symlink():
        return False
    try:
        pid = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        # Older m3 releases wrote empty lease files but included the PID in
        # the filename. Preserve those leases while their owner is alive.
        legacy = re.fullmatch(r"lease-([0-9]+)-[0-9]+", path.name)
        if legacy is None:
            return True
        pid = int(legacy.group(1))
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
