"""Contained execution workspaces and safe output collection.

This module deliberately owns only directories created by it.  A source tree
used for a copy/read-only/worktree is never removed, and symlinks are not
followed while copying or collecting artifacts.  The public artifact store
redacts bytes before they are persisted.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from glob import has_magic
from pathlib import Path

from .storage import ArtifactStore, InMemoryArtifactStore
from .types import (
    ArtifactPolicy,
    ArtifactRef,
    ExecutionId,
    ExecutionOutcome,
    WorkspaceKind,
    WorkspacePolicy,
)

_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "node_modules",
        ".mcp-pal",
        ".mcp_pal",
        "mcp-pal-artifacts",
        "artifacts",
    }
)
_EXCLUDED_NAMES = frozenset(
    {"credentials", "credentials.json", "credentials.yaml", "credentials.yml"}
)
_EXCLUDED_COMPONENTS = frozenset(
    value.casefold() for value in (_EXCLUDED_DIRS | _EXCLUDED_NAMES)
)
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class WorkspaceEntry:
    """One deterministic file-level workspace change."""

    path: str
    status: str
    size_bytes: int = 0
    sha256: str | None = None


@dataclass(frozen=True)
class WorkspaceDiff:
    entries: tuple[WorkspaceEntry, ...] = ()
    limitations: tuple[str, ...] = ()

    @property
    def added(self) -> tuple[WorkspaceEntry, ...]:
        return tuple(item for item in self.entries if item.status == "added")

    @property
    def modified(self) -> tuple[WorkspaceEntry, ...]:
        return tuple(item for item in self.entries if item.status == "modified")

    @property
    def deleted(self) -> tuple[WorkspaceEntry, ...]:
        return tuple(item for item in self.entries if item.status == "deleted")

    def as_payload(self) -> dict[str, object]:
        return {
            "entries": tuple(
                {
                    "path": item.path,
                    "status": item.status,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in self.entries
            ),
            "limitations": self.limitations,
        }


@dataclass(frozen=True)
class WorkspaceCapture:
    diff: WorkspaceDiff
    artifacts: tuple[ArtifactRef, ...] = ()
    limitations: tuple[str, ...] = ()


class WorkspaceError(RuntimeError):
    """A workspace could not be safely created, captured, or cleaned."""


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_source(value: str) -> Path:
    source = Path(value).expanduser()
    try:
        resolved = source.resolve(strict=True)
    except OSError as exc:
        raise WorkspaceError("workspace source is unavailable") from exc
    if not resolved.is_dir():
        raise WorkspaceError("workspace source must be a directory")
    return resolved


class WorkspaceManager:
    """Create, capture, and clean one isolated workspace.

    ``declared_artifacts`` are relative paths inside the resulting workspace.
    Files changed outside that declaration remain in :class:`WorkspaceDiff`
    and are never promoted to artifacts.
    """

    def __init__(
        self,
        policy: WorkspacePolicy,
        execution_id: ExecutionId | str,
        *,
        artifact_policy: ArtifactPolicy = ArtifactPolicy.FAILED,
        declared_artifacts: Iterable[str] = (),
        artifact_store: ArtifactStore | None = None,
        include_excluded: bool = False,
        acknowledge_risky_inclusion: bool = False,
    ) -> None:
        if include_excluded and not acknowledge_risky_inclusion:
            raise WorkspaceError(
                "including excluded workspace paths requires explicit acknowledgement"
            )
        self.policy = policy
        self.execution_id = execution_id
        self.artifact_policy = artifact_policy
        self.declared_artifacts = tuple(
            self._validate_relative_path(item) for item in declared_artifacts
        )
        self._store = (
            artifact_store if artifact_store is not None else InMemoryArtifactStore()
        )
        self._include_excluded = include_excluded
        self._root: Path | None = None
        self._owned_root = False
        self._baseline: dict[str, tuple[int, str]] = {}
        self._lock = threading.RLock()
        self._captured: WorkspaceCapture | None = None
        self._cleaned = False
        self._cleanup_error: WorkspaceError | None = None

    @property
    def root(self) -> Path:
        with self._lock:
            if self._root is None:
                raise WorkspaceError("workspace has not been created")
            return self._root

    @property
    def captured(self) -> WorkspaceCapture | None:
        with self._lock:
            return self._captured

    @property
    def cleanup_error(self) -> WorkspaceError | None:
        with self._lock:
            return self._cleanup_error

    @staticmethod
    def _validate_relative_path(value: str) -> str:
        if not value or "\x00" in value or Path(value).is_absolute():
            raise WorkspaceError("workspace paths must be non-empty and relative")
        path = Path(value)
        if any(part in {"", ".", ".."} for part in path.parts) or "\\" in value:
            raise WorkspaceError("workspace path is not safely relative")
        return path.as_posix()

    def _excluded(self, name: str, *, is_dir: bool) -> bool:
        if self._include_excluded:
            return False
        del is_dir
        folded = name.casefold()
        if folded in _EXCLUDED_COMPONENTS:
            return True
        return folded.startswith(".env") or "credential" in folded

    def _has_excluded_component(self, relative: str) -> bool:
        return any(self._excluded(part, is_dir=True) for part in Path(relative).parts)

    def create(self) -> Path:
        with self._lock:
            if self._root is not None:
                return self._root
            kind = self.policy.kind
            if kind is WorkspaceKind.TEMPORARY:
                root = Path(tempfile.mkdtemp(prefix="mcp-pal-workspace-")).resolve()
                self._owned_root = True
            elif self.policy.source is None:
                raise WorkspaceError("workspace source is required")
            elif kind is WorkspaceKind.IN_PLACE:
                root = _safe_source(self.policy.source)
            else:
                source = _safe_source(self.policy.source)
                root = Path(tempfile.mkdtemp(prefix="mcp-pal-workspace-")).resolve()
                self._owned_root = True
                if kind is WorkspaceKind.GIT_WORKTREE:
                    self._create_worktree(source, root)
                else:
                    self._copy_tree(source, root)
                    if kind is WorkspaceKind.READ_ONLY:
                        self._make_read_only(root)
            self._root = root
            self._baseline = self._snapshot(root)
            return root

    def _create_worktree(self, source: Path, target: Path) -> None:
        try:
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(target), "HEAD"],
                cwd=str(source),
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            shutil.rmtree(target, ignore_errors=True)
            self._owned_root = False
            raise WorkspaceError("git worktree could not be created") from exc

    def _copy_tree(self, source: Path, target: Path) -> None:
        try:
            root_stat = source.lstat()
        except OSError as exc:
            raise WorkspaceError("workspace source could not be read safely") from exc
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            raise WorkspaceError("workspace source changed to a non-directory")
        try:
            with os.scandir(source) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            raise WorkspaceError(
                "workspace source could not be scanned safely"
            ) from exc
        for entry in entries:
            try:
                entry_is_dir = entry.is_dir(follow_symlinks=False)
            except OSError as exc:
                raise WorkspaceError(
                    "workspace source changed while being copied"
                ) from exc
            if self._excluded(entry.name, is_dir=entry_is_dir):
                continue
            destination = target / entry.name
            if entry.is_symlink():
                # Following a source link can exfiltrate arbitrary host files.
                continue
            if entry.is_dir(follow_symlinks=False):
                destination.mkdir()
                self._copy_tree(Path(entry.path), destination)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            self._copy_file_no_follow(Path(entry.path), destination)

    @staticmethod
    def _copy_file_no_follow(source: Path, destination: Path) -> None:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            fd = os.open(source, flags)
        except OSError as exc:
            raise WorkspaceError(
                "workspace source file could not be read safely"
            ) from exc
        try:
            source_stat = os.fstat(fd)
            if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
                return
            with os.fdopen(fd, "rb") as src:
                fd = -1
                with destination.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
        except OSError as exc:
            raise WorkspaceError("workspace source file could not be copied") from exc
        finally:
            if fd >= 0:
                os.close(fd)

    @staticmethod
    def _make_read_only(root: Path) -> None:
        for directory, dirs, files in os.walk(root, topdown=False, followlinks=False):
            for name in files:
                path = Path(directory) / name
                if not path.is_symlink():
                    path.chmod(0o444)
            for name in dirs:
                path = Path(directory) / name
                if not path.is_symlink():
                    path.chmod(0o555)
        root.chmod(0o555)

    def _snapshot(self, root: Path) -> dict[str, tuple[int, str]]:
        result: dict[str, tuple[int, str]] = {}
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as iterator:
                    entries = sorted(iterator, key=lambda item: item.name, reverse=True)
            except OSError:
                continue
            for entry in entries:
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if self._excluded(entry.name, is_dir=is_dir):
                    continue
                relative = Path(entry.path).relative_to(root).as_posix()
                if is_dir:
                    stack.append(Path(entry.path))
                    continue
                try:
                    if entry.is_symlink():
                        digest = hashlib.sha256(
                            os.readlink(entry.path).encode()
                        ).hexdigest()
                        result[relative] = (0, digest)
                    elif entry.is_file(follow_symlinks=False):
                        result[relative] = self._file_fingerprint(Path(entry.path))
                except OSError:
                    continue
        return result

    @staticmethod
    def _file_fingerprint(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise WorkspaceError(
                "workspace file could not be fingerprinted safely"
            ) from exc
        try:
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
                raise WorkspaceError("workspace file is not a safe regular file")
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    digest.update(chunk)
        except OSError as exc:
            raise WorkspaceError(
                "workspace file could not be fingerprinted safely"
            ) from exc
        finally:
            if fd >= 0:
                os.close(fd)
        return size, digest.hexdigest()

    def capture(self, outcome: ExecutionOutcome) -> WorkspaceCapture:
        with self._lock:
            if self._captured is not None:
                return self._captured
            root = self.root
            current = self._snapshot(root)
            entries: list[WorkspaceEntry] = []
            for path in sorted(set(self._baseline) | set(current)):
                before = self._baseline.get(path)
                after = current.get(path)
                if before is None and after is not None:
                    entries.append(WorkspaceEntry(path, "added", after[0], after[1]))
                elif before is not None and after is None:
                    entries.append(
                        WorkspaceEntry(path, "deleted", before[0], before[1])
                    )
                elif before != after and after is not None:
                    entries.append(WorkspaceEntry(path, "modified", after[0], after[1]))
            diff = WorkspaceDiff(tuple(entries))
            artifacts: list[ArtifactRef] = []
            limitations: list[str] = []
            should_collect = self.artifact_policy is ArtifactPolicy.ALWAYS or (
                self.artifact_policy is ArtifactPolicy.FAILED
                and outcome is not ExecutionOutcome.COMPLETED
            )
            if should_collect:
                for declared in self.declared_artifacts:
                    try:
                        artifacts.extend(self._collect_declared(declared))
                    except WorkspaceError:
                        limitations.append(f"artifact_unavailable:{declared}")
            self._captured = WorkspaceCapture(
                diff, tuple(artifacts), tuple(limitations)
            )
            return self._captured

    def _collect_declared(self, relative: str) -> list[ArtifactRef]:
        root = self.root.resolve()
        if self._has_excluded_component(relative):
            raise WorkspaceError("declared artifact is excluded by workspace policy")
        if has_magic(relative):
            matches = sorted(root.glob(relative))
            if not matches:
                raise WorkspaceError("declared artifact is unavailable")
            result: list[ArtifactRef] = []
            for match in matches:
                match_relative = match.relative_to(root).as_posix()
                if self._has_excluded_component(match_relative):
                    continue
                result.extend(self._collect_path(match, root))
            if not result:
                raise WorkspaceError("declared artifact is unavailable")
            return result
        return self._collect_path(root / relative, root)

    def _collect_path(self, candidate: Path, root: Path) -> list[ArtifactRef]:
        current = root
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError("declared artifact escaped workspace") from exc
        for part in relative.parts:
            current = current / part
            try:
                if current.is_symlink():
                    raise WorkspaceError("declared artifact contains a symlink")
            except OSError as exc:
                raise WorkspaceError("declared artifact is unavailable") from exc
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise WorkspaceError("declared artifact is unavailable") from exc
        if not _within(resolved, root) or candidate.is_symlink():
            raise WorkspaceError("declared artifact escaped workspace")
        if resolved.is_dir():
            result: list[ArtifactRef] = []
            stack = [resolved]
            while stack:
                directory = stack.pop()
                try:
                    entries = sorted(
                        os.scandir(directory), key=lambda item: item.name, reverse=True
                    )
                except OSError as exc:
                    raise WorkspaceError("declared artifact is unavailable") from exc
                for entry in entries:
                    entry_path = Path(entry.path)
                    entry_relative = entry_path.relative_to(root).as_posix()
                    if self._has_excluded_component(entry_relative):
                        continue
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry_path)
                        elif entry.is_file(follow_symlinks=False):
                            result.extend(self._collect_file(entry_path, root))
                    except OSError as exc:
                        raise WorkspaceError(
                            "declared artifact is unavailable"
                        ) from exc
            return result
        if not resolved.is_file():
            raise WorkspaceError("declared artifact is not a regular file")
        return self._collect_file(resolved, root)

    def _collect_file(self, path: Path, root: Path) -> list[ArtifactRef]:
        if not _within(path.resolve(), root) or path.is_symlink():
            raise WorkspaceError("declared artifact escaped workspace")
        try:
            expected = path.lstat()
            if not stat.S_ISREG(expected.st_mode) or expected.st_nlink != 1:
                raise WorkspaceError("declared artifact is not a safe regular file")
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            fd = os.open(path, flags)
            opened = os.fstat(fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_dev != expected.st_dev
                or opened.st_ino != expected.st_ino
            ):
                raise WorkspaceError("declared artifact changed while being read")
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                content = handle.read(_MAX_ARTIFACT_BYTES + 1)
        except OSError as exc:
            raise WorkspaceError("declared artifact could not be read safely") from exc
        finally:
            if "fd" in locals() and fd >= 0:
                os.close(fd)
        if len(content) > _MAX_ARTIFACT_BYTES:
            raise WorkspaceError("declared artifact exceeds safe size limit")
        name = path.relative_to(root).as_posix()
        return [
            self._store.put(
                self.execution_id, name, content, media_type="application/octet-stream"
            )
        ]

    def cleanup(self) -> None:
        with self._lock:
            if self._cleaned:
                return
            root = self._root
            if root is None:
                self._cleaned = True
                return
            if not self._owned_root:
                self._cleaned = True
                return
            try:
                if self.policy.kind is WorkspaceKind.READ_ONLY:
                    for directory, dirs, files in os.walk(
                        root, topdown=False, followlinks=False
                    ):
                        for name in files:
                            path = Path(directory) / name
                            if not path.is_symlink():
                                path.chmod(0o600)
                        for name in dirs:
                            path = Path(directory) / name
                            if not path.is_symlink():
                                path.chmod(0o700)
                    root.chmod(0o700)
                if self.policy.kind is WorkspaceKind.GIT_WORKTREE:
                    source = (
                        Path(self.policy.source).expanduser().resolve()
                        if self.policy.source
                        else None
                    )
                    if source is None:
                        raise WorkspaceError("git worktree source is unavailable")
                    subprocess.run(
                        ["git", "worktree", "remove", "--force", str(root)],
                        cwd=str(source),
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                else:
                    shutil.rmtree(root)
                if root.exists():
                    raise WorkspaceError(
                        "workspace cleanup did not remove the owned root"
                    )
            except (OSError, subprocess.SubprocessError, WorkspaceError):
                self._cleanup_error = WorkspaceError("workspace cleanup failed")
                raise self._cleanup_error from None
            self._cleaned = True


__all__ = [
    "WorkspaceCapture",
    "WorkspaceDiff",
    "WorkspaceEntry",
    "WorkspaceError",
    "WorkspaceManager",
]
