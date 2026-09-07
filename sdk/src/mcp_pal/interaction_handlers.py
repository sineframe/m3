"""Typed, policy-gated interaction handlers for agent sessions.

The default for every interaction is deny.  Handler results deliberately
separate safe audit receipts from active-process values such as sampling
content, filesystem bytes, and terminal output.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import shutil
import signal
import stat
import tempfile
from types import MappingProxyType
from typing import Any, Literal, Protocol, TypeAlias
from uuid import uuid4

from .types import (
    ElicitationPolicy,
    FilesystemPolicy,
    PermissionPolicy,
    SamplingPolicy,
    TerminalPolicy,
)


Decision = Literal["allow", "deny", "error"]
FilesystemOperation = Literal["read", "write", "list", "delete"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class InteractionReceipt:
    """Safe decision evidence; request values and handler errors are excluded."""

    request_id: str
    kind: str
    decision: Decision
    reason: str
    timestamp: datetime = field(default_factory=_now)


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    operation: str
    resource: str = ""
    destructive: bool = False


@dataclass(frozen=True, slots=True)
class PermissionResult:
    allowed: bool
    receipt: InteractionReceipt
    confirmation_required: bool = False


@dataclass(frozen=True, slots=True)
class ElicitationRequest:
    prompt: str
    schema: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema", MappingProxyType(dict(self.schema)))


@dataclass(frozen=True, slots=True)
class ElicitationResult:
    accepted: bool
    value: Any = None
    receipt: InteractionReceipt = field(
        default_factory=lambda: InteractionReceipt("none", "elicitation", "deny", "default_deny")
    )


@dataclass(frozen=True, slots=True)
class SamplingRequest:
    prompt: str
    model: str | None = None
    metadata: Mapping[str, str | int | float | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class SamplingResult:
    accepted: bool
    content: str | None
    receipt: InteractionReceipt


@dataclass(frozen=True, slots=True)
class FilesystemRequest:
    operation: FilesystemOperation
    path: str
    data: bytes | None = None
    max_bytes: int = 1 << 20


@dataclass(frozen=True, slots=True)
class FilesystemResult:
    allowed: bool
    data: bytes | tuple[str, ...] | None
    receipt: InteractionReceipt


@dataclass(frozen=True, slots=True)
class TerminalRequest:
    argv: tuple[str, ...]
    cwd: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    max_output_bytes: int = 1 << 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))


@dataclass(frozen=True, slots=True)
class TerminalResult:
    allowed: bool
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    truncated: bool
    receipt: InteractionReceipt


class PermissionHandler(Protocol):
    async def __call__(self, request: PermissionRequest) -> PermissionResult | bool: ...


class ElicitationHandler(Protocol):
    async def __call__(self, request: ElicitationRequest) -> ElicitationResult | Any: ...


class SamplingHandler(Protocol):
    async def __call__(self, request: SamplingRequest) -> SamplingResult | str: ...


class FilesystemHandler(Protocol):
    async def __call__(self, request: FilesystemRequest) -> FilesystemResult: ...


class TerminalHandler(Protocol):
    async def __call__(self, request: TerminalRequest) -> TerminalResult: ...


PermissionCallback: TypeAlias = Callable[[PermissionRequest], PermissionResult | bool | Awaitable[PermissionResult | bool]]
ElicitationCallback: TypeAlias = Callable[[ElicitationRequest], ElicitationResult | Any | Awaitable[ElicitationResult | Any]]
SamplingCallback: TypeAlias = Callable[[SamplingRequest], SamplingResult | str | Awaitable[SamplingResult | str]]


def _receipt(kind: str, decision: Decision, reason: str) -> InteractionReceipt:
    return InteractionReceipt(f"interaction-{uuid4().hex}", kind, decision, reason)


def _deny(kind: str, reason: str = "default_deny") -> InteractionReceipt:
    return _receipt(kind, "deny", reason)


async def _resolve(value: Any) -> Any:
    return await value if asyncio.iscoroutine(value) or isinstance(value, Awaitable) else value


@dataclass(frozen=True, slots=True)
class InteractionHandlers:
    """Optional callbacks; absent callbacks are always default-deny."""

    permission: PermissionCallback | None = None
    elicitation: ElicitationCallback | None = None
    sampling: SamplingCallback | None = None
    filesystem: FilesystemHandler | None = None
    terminal: TerminalHandler | None = None


class Interactions:
    """Apply immutable policies around explicit interaction callbacks."""

    def __init__(
        self,
        *,
        permission_policy: PermissionPolicy | None = None,
        elicitation_policy: ElicitationPolicy | None = None,
        sampling_policy: SamplingPolicy | None = None,
        filesystem_policy: FilesystemPolicy | None = None,
        terminal_policy: TerminalPolicy | None = None,
        handlers: InteractionHandlers | None = None,
    ) -> None:
        self.permission_policy = permission_policy or PermissionPolicy()
        self.elicitation_policy = elicitation_policy or ElicitationPolicy()
        self.sampling_policy = sampling_policy or SamplingPolicy()
        self.filesystem_policy = filesystem_policy or FilesystemPolicy()
        self.terminal_policy = terminal_policy or TerminalPolicy()
        self.handlers = handlers or InteractionHandlers()
        self._receipts: list[InteractionReceipt] = []
        self._receipt_lock = asyncio.Lock()

    def __setattr__(self, name: str, value: Any) -> None:
        # Policies are part of the immutable session contract.  Exposing a
        # mutable controller must not provide a way to weaken them after the
        # harness has been opened.
        if name in {
            "permission_policy",
            "elicitation_policy",
            "sampling_policy",
            "filesystem_policy",
            "terminal_policy",
            "handlers",
        } and name in self.__dict__:
            raise AttributeError(f"interaction {name} is immutable")
        object.__setattr__(self, name, value)

    async def _record(self, receipt: InteractionReceipt) -> InteractionReceipt:
        async with self._receipt_lock:
            self._receipts.append(receipt)
        return receipt

    def receipts(self) -> tuple[InteractionReceipt, ...]:
        return tuple(self._receipts)

    async def permission(self, request: PermissionRequest) -> PermissionResult:
        if self.permission_policy.mode == "deny":
            return PermissionResult(False, await self._record(_deny("permission")))
        if self.permission_policy.mode == "allow":
            return PermissionResult(True, await self._record(_receipt("permission", "allow", "policy_allow")))
        callback = self.handlers.permission
        if callback is None:
            return PermissionResult(False, await self._record(_deny("permission", "confirmation_unavailable")), True)
        try:
            value = await _resolve(callback(request))
            if isinstance(value, PermissionResult):
                allowed = bool(value.allowed)
                receipt = await self._record(
                    _receipt("permission", "allow" if allowed else "deny", "handler_decision")
                )
                return PermissionResult(
                    value.allowed,
                    receipt,
                    value.confirmation_required or request.destructive,
                )
            allowed = bool(value)
            return PermissionResult(
                allowed,
                await self._record(_receipt("permission", "allow" if allowed else "deny", "handler_decision")),
                request.destructive,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return PermissionResult(False, await self._record(_deny("permission", "handler_error")))

    async def elicitate(self, request: ElicitationRequest) -> ElicitationResult:
        if self.elicitation_policy.mode == "deny" or self.handlers.elicitation is None:
            return ElicitationResult(False, receipt=await self._record(_deny("elicitation")))
        try:
            value = await _resolve(self.handlers.elicitation(request))
            if isinstance(value, ElicitationResult):
                return ElicitationResult(
                    value.accepted,
                    value.value,
                    await self._record(_receipt("elicitation", "allow" if value.accepted else "deny", "handler_decision")),
                )
            return ElicitationResult(True, value, await self._record(_receipt("elicitation", "allow", "handler_decision")))
        except asyncio.CancelledError:
            raise
        except Exception:
            return ElicitationResult(False, receipt=await self._record(_deny("elicitation", "handler_error")))

    async def elicit(self, request: ElicitationRequest) -> ElicitationResult:
        """Alias using the protocol's conventional verb."""

        return await self.elicitate(request)

    async def sample(self, request: SamplingRequest) -> SamplingResult:
        if self.sampling_policy.mode == "deny" or self.handlers.sampling is None:
            return SamplingResult(False, None, await self._record(_deny("sampling")))
        try:
            value = await _resolve(self.handlers.sampling(request))
            if isinstance(value, SamplingResult):
                return SamplingResult(
                    value.accepted,
                    value.content,
                    await self._record(_receipt("sampling", "allow" if value.accepted else "deny", "handler_decision")),
                )
            return SamplingResult(True, str(value), await self._record(_receipt("sampling", "allow", "handler_decision")))
        except asyncio.CancelledError:
            raise
        except Exception:
            return SamplingResult(False, None, await self._record(_deny("sampling", "handler_error")))

    async def filesystem(self, request: FilesystemRequest) -> FilesystemResult:
        allowed_mode = self.filesystem_policy.mode
        if allowed_mode == "deny" or self.handlers.filesystem is None:
            return FilesystemResult(False, None, await self._record(_deny("filesystem")))
        if allowed_mode == "read_only" and request.operation in {"write", "delete"}:
            return FilesystemResult(False, None, await self._record(_deny("filesystem", "read_only")))
        try:
            result = await _resolve(self.handlers.filesystem(request))
            if not isinstance(result, FilesystemResult):
                raise TypeError("filesystem handler returned an invalid result")
            return FilesystemResult(
                result.allowed,
                result.data,
                await self._record(_receipt("filesystem", "allow" if result.allowed else "deny", "handler_decision")),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return FilesystemResult(False, None, await self._record(_deny("filesystem", "handler_error")))

    async def terminal(self, request: TerminalRequest) -> TerminalResult:
        if self.terminal_policy.mode == "deny" or self.handlers.terminal is None:
            return TerminalResult(False, None, b"", b"", False, False, await self._record(_deny("terminal")))
        try:
            result = await _resolve(self.handlers.terminal(request))
            if not isinstance(result, TerminalResult):
                raise TypeError("terminal handler returned an invalid result")
            return TerminalResult(
                result.allowed,
                result.returncode,
                result.stdout,
                result.stderr,
                result.timed_out,
                result.truncated,
                await self._record(_receipt("terminal", "allow" if result.allowed else "deny", "handler_decision")),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return TerminalResult(False, None, b"", b"", False, False, await self._record(_deny("terminal", "handler_error")))


class WorkspaceFiles:
    """Bounded filesystem handler rooted inside one owned workspace."""

    def __init__(self, root: str | Path, *, mode: Literal["read_only", "read_write"] = "read_only", max_bytes: int = 1 << 20) -> None:
        self._root = Path(root).resolve()
        if not self._root.is_dir():
            raise ValueError("filesystem root must be an existing directory")
        if max_bytes <= 0:
            raise ValueError("filesystem max_bytes must be positive")
        self._mode = mode
        self._max_bytes = max_bytes

    def _path(self, value: str) -> Path:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError("filesystem path is invalid")
        # Normalize ``..`` lexically, but never resolve symlinks here.  Every
        # component is lstat'ed below so a path cannot pass a containment
        # check by traversing a link into another tree.
        raw = Path(value) if Path(value).is_absolute() else self._root / value
        candidate = Path(os.path.abspath(os.fspath(raw)))
        if candidate != self._root and self._root not in candidate.parents:
            raise PermissionError("filesystem path is outside workspace")
        relative = candidate.relative_to(self._root)
        current = self._root
        for component in relative.parts:
            current /= component
            try:
                info = os.lstat(current)
            except FileNotFoundError:
                # A missing final component is valid for an atomic write;
                # missing parents are rejected by the operation itself.
                if current != candidate:
                    raise PermissionError("filesystem parent is unavailable") from None
                break
            if stat.S_ISLNK(info.st_mode):
                raise PermissionError("filesystem symlinks are not allowed")
            if current != candidate and not stat.S_ISDIR(info.st_mode):
                raise PermissionError("filesystem parent is unavailable")
        return candidate

    @staticmethod
    def _regular_file(path: Path) -> bool:
        try:
            info = os.lstat(path)
        except OSError:
            return False
        return stat.S_ISREG(info.st_mode) and info.st_nlink == 1

    @staticmethod
    def _directory(path: Path) -> bool:
        try:
            return stat.S_ISDIR(os.lstat(path).st_mode)
        except OSError:
            return False

    @staticmethod
    def _read_bounded(path: Path, limit: int) -> tuple[bytes, bool]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(os.fspath(path), flags)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PermissionError("filesystem file is not a private regular file")
            chunks: list[bytes] = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            return data[:limit], len(data) > limit
        finally:
            os.close(descriptor)

    async def __call__(self, request: FilesystemRequest) -> FilesystemResult:
        if not isinstance(request.max_bytes, int) or isinstance(request.max_bytes, bool):
            return FilesystemResult(False, None, _deny("filesystem", "size_limit"))
        try:
            path = self._path(request.path)
        except (OSError, ValueError, PermissionError):
            return FilesystemResult(False, None, _deny("filesystem", "path_outside_workspace"))
        if request.max_bytes <= 0 or request.max_bytes > self._max_bytes:
            return FilesystemResult(False, None, _deny("filesystem", "size_limit"))
        if request.operation in {"write", "delete"} and self._mode != "read_write":
            return FilesystemResult(False, None, _deny("filesystem", "read_only"))
        if request.operation == "read":
            try:
                if not self._regular_file(path):
                    return FilesystemResult(False, None, _deny("filesystem", "file_type_denied"))
                data, truncated = await asyncio.to_thread(self._read_bounded, path, request.max_bytes)
            except (OSError, PermissionError):
                return FilesystemResult(False, None, _deny("filesystem", "read_failed"))
            if truncated:
                return FilesystemResult(False, None, _deny("filesystem", "size_limit"))
            return FilesystemResult(True, data, _receipt("filesystem", "allow", "read"))
        if request.operation == "list":
            try:
                if not self._directory(path):
                    return FilesystemResult(False, None, _deny("filesystem", "directory_required"))
                entries_list: list[str] = []
                total = 0
                with os.scandir(path) as scandir_entries:
                    for item in scandir_entries:
                        total += len(item.name.encode("utf-8"))
                        if total > request.max_bytes:
                            return FilesystemResult(False, None, _deny("filesystem", "size_limit"))
                        entries_list.append(item.name)
                entries = tuple(sorted(entries_list))
            except (OSError, UnicodeError):
                return FilesystemResult(False, None, _deny("filesystem", "list_failed"))
            return FilesystemResult(True, entries, _receipt("filesystem", "allow", "list"))
        if request.operation == "write":
            data = request.data if request.data is not None else b""
            if not isinstance(data, bytes):
                return FilesystemResult(False, None, _deny("filesystem", "data_invalid"))
            if len(data) > request.max_bytes:
                return FilesystemResult(False, None, _deny("filesystem", "size_limit"))
            parent = path.parent
            if not self._directory(parent):
                return FilesystemResult(False, None, _deny("filesystem", "path_unavailable"))
            if path.exists() and not self._regular_file(path):
                return FilesystemResult(False, None, _deny("filesystem", "file_type_denied"))
            temporary: str | None = None
            try:
                def atomic_write() -> None:
                    nonlocal temporary
                    descriptor, temporary = tempfile.mkstemp(prefix=".mcp-pal-", dir=os.fspath(parent))
                    try:
                        with os.fdopen(descriptor, "wb") as stream:
                            stream.write(data)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, os.fspath(path))
                        temporary = None
                    finally:
                        if temporary is not None:
                            try:
                                os.unlink(temporary)
                            except OSError:
                                pass

                await asyncio.to_thread(atomic_write)
            except (OSError, TypeError, ValueError):
                return FilesystemResult(False, None, _deny("filesystem", "write_failed"))
            return FilesystemResult(True, None, _receipt("filesystem", "allow", "write"))
        if request.operation == "delete":
            try:
                if not self._regular_file(path):
                    return FilesystemResult(False, None, _deny("filesystem", "file_type_denied"))
                await asyncio.to_thread(path.unlink)
            except OSError:
                return FilesystemResult(False, None, _deny("filesystem", "delete_failed"))
            return FilesystemResult(True, None, _receipt("filesystem", "allow", "delete"))
        return FilesystemResult(False, None, _deny("filesystem", "unsupported_operation"))


class AllowedCommands:
    """Safe argv-only terminal handler with cwd, timeout, and output bounds."""

    def __init__(
        self,
        *,
        allowed_executables: Sequence[str],
        root: str | Path,
        environment: Mapping[str, str] | None = None,
        allowed_environment: Sequence[str] = (),
    ) -> None:
        allowed_paths: dict[str, str] = {}
        allowed_names: dict[str, str] = {}
        allowed_aliases: dict[str, str] = {}
        for value in allowed_executables:
            if not value or "\x00" in value:
                continue
            candidate = Path(value)
            resolved = (
                Path(os.path.realpath(os.fspath(candidate)))
                if candidate.is_absolute()
                else Path(os.path.realpath(shutil.which(os.fspath(candidate)) or ""))
            )
            if not resolved or not resolved.is_absolute():
                continue
            try:
                info = os.lstat(resolved)
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            allowed_paths[os.fspath(resolved)] = os.fspath(resolved)
            if not candidate.is_absolute():
                allowed_names[os.fspath(candidate)] = os.fspath(resolved)
            else:
                allowed_aliases[os.path.abspath(os.fspath(candidate))] = os.fspath(resolved)
        if not allowed_paths:
            raise ValueError("terminal executable allowlist is empty")
        self._allowed_paths = frozenset(allowed_paths)
        self._allowed_names = allowed_names
        self._allowed_aliases = allowed_aliases
        self._root = Path(root).resolve()
        if not self._root.is_dir():
            raise ValueError("terminal root must be an existing directory")
        self._environment = dict(environment or {})
        self._allowed_environment = frozenset(allowed_environment).union(self._environment)
        if any(
            not isinstance(key, str)
            or not key
            or "=" in key
            or "\x00" in key
            or "\x00" in value
            for key, value in self._environment.items()
        ):
            raise ValueError("terminal environment is invalid")

    def _cwd(self, value: str | None) -> str:
        raw = self._root if value is None else Path(value)
        path = Path(os.path.abspath(os.fspath(raw)))
        if path != self._root and self._root not in path.parents:
            raise PermissionError("terminal cwd is outside workspace")
        current = self._root
        for component in path.relative_to(self._root).parts:
            current /= component
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode):
                raise PermissionError("terminal cwd symlinks are not allowed")
        if not self._root.is_dir() or not path.is_dir():
            raise ValueError("terminal cwd is unavailable")
        return str(path)

    def _executable(self, value: str) -> str | None:
        if not value or "\x00" in value:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            resolved = self._allowed_names.get(os.fspath(candidate))
            if resolved is None:
                return None
            try:
                info = os.lstat(resolved)
            except OSError:
                return None
            return resolved if stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) else None
        alias = self._allowed_aliases.get(os.path.abspath(os.fspath(candidate)))
        resolved = alias or os.path.realpath(os.fspath(candidate))
        if resolved not in self._allowed_paths:
            return None
        try:
            info = os.lstat(resolved)
        except OSError:
            return None
        if not stat.S_ISREG(info.st_mode):
            return None
        if alias is None:
            try:
                requested_info = os.lstat(candidate)
            except OSError:
                return None
            if stat.S_ISLNK(requested_info.st_mode):
                return None
        return resolved

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        try:
            if process.returncode is None:
                if hasattr(os, "killpg") and process.pid is not None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        process.kill()
                else:
                    process.kill()
        except ProcessLookupError:
            pass
        await process.wait()

    async def __call__(self, request: TerminalRequest) -> TerminalResult:
        if not isinstance(request.argv, (tuple, list)) or not request.argv or any(
            not isinstance(value, str) or not value or "\x00" in value for value in request.argv
        ):
            return TerminalResult(False, None, b"", b"", False, False, _deny("terminal", "argv_invalid"))
        executable = self._executable(request.argv[0])
        if executable is None:
            return TerminalResult(False, None, b"", b"", False, False, _deny("terminal", "executable_not_allowed"))
        if not isinstance(request.timeout_seconds, (int, float)) or isinstance(request.timeout_seconds, bool):
            return TerminalResult(False, None, b"", b"", False, False, _deny("terminal", "limits_invalid"))
        if not isinstance(request.max_output_bytes, int) or isinstance(request.max_output_bytes, bool):
            return TerminalResult(False, None, b"", b"", False, False, _deny("terminal", "limits_invalid"))
        if not math.isfinite(request.timeout_seconds) or request.timeout_seconds <= 0 or request.max_output_bytes <= 0:
            return TerminalResult(False, None, b"", b"", False, False, _deny("terminal", "limits_invalid"))
        process: asyncio.subprocess.Process | None = None
        try:
            cwd = self._cwd(request.cwd)
            if any(key not in self._allowed_environment for key in request.environment):
                return TerminalResult(False, None, b"", b"", False, False, _deny("terminal", "environment_not_allowed"))
            environment = dict(self._environment)
            environment.update(request.environment)
            if any("\x00" in key or "\x00" in value for key, value in environment.items()):
                raise ValueError("terminal environment is invalid")
            process = await asyncio.create_subprocess_exec(
                executable,
                *request.argv[1:],
                cwd=cwd,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            if process.stdout is None or process.stderr is None:
                raise RuntimeError("terminal output streams are unavailable")
            async def read(stream: asyncio.StreamReader) -> tuple[bytes, bool]:
                data = await stream.read(request.max_output_bytes + 1)
                if len(data) > request.max_output_bytes:
                    try:
                        await self._terminate(process)
                    except ProcessLookupError:
                        pass
                return data[: request.max_output_bytes], len(data) > request.max_output_bytes
            try:
                (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.wait_for(
                    asyncio.gather(read(process.stdout), read(process.stderr)), request.timeout_seconds
                )
                await process.wait()
                return TerminalResult(True, process.returncode, stdout, stderr, False, stdout_truncated or stderr_truncated, _receipt("terminal", "allow", "completed"))
            except asyncio.TimeoutError:
                await self._terminate(process)
                return TerminalResult(True, process.returncode, b"", b"", True, False, _receipt("terminal", "allow", "timed_out"))
        except asyncio.CancelledError:
            if process is not None:
                await self._terminate(process)
            raise
        except Exception:
            return TerminalResult(False, None, b"", b"", False, False, _deny("terminal", "handler_error"))


__all__ = [
    "AllowedCommands", "ElicitationCallback", "ElicitationHandler", "ElicitationRequest", "ElicitationResult",
    "FilesystemHandler", "FilesystemOperation", "FilesystemRequest", "FilesystemResult", "Interactions", "InteractionHandlers",
    "InteractionReceipt", "PermissionCallback", "PermissionHandler", "PermissionRequest", "PermissionResult", "SamplingCallback", "SamplingHandler",
    "SamplingRequest", "SamplingResult", "TerminalHandler", "TerminalRequest", "TerminalResult", "WorkspaceFiles",
]
