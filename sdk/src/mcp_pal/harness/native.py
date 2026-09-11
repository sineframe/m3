"""Process-backed native harness primitives.

The native adapters in this package deliberately own their process, reader,
and temporary configuration lifetime.  They use argv-only subprocesses and
never use a shell.  Provider output is converted to bounded, value-free
errors before it crosses the adapter boundary.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..errors import CleanupError
from ..types import ErrorCode, ErrorInfo, SecretReference
from .contracts import (
    HarnessAdapterCapabilities,
    HarnessLaunch,
    HarnessSessionSnapshot,
    HarnessStartupError,
    HarnessTurnResult,
)

MAX_FRAME_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 256 * 1024
MAX_QUEUE_ITEMS = 128


def _executable(value: str | None, default: str) -> str:
    candidate = value or default
    if not candidate or "\x00" in candidate:
        raise ValueError("harness executable is invalid")
    return candidate


def _isolated_environment(
    root: Path, explicit: Mapping[str, str] | None
) -> dict[str, str]:
    """Build an isolated deterministic environment with explicit overrides."""

    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "XDG_STATE_HOME": str(root / "state"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONIOENCODING": "utf-8",
    }
    if explicit is not None:
        for key, value in explicit.items():
            if not isinstance(key, str) or not key or "\x00" in key or "\x00" in value:
                raise ValueError("harness environment is invalid")
            environment[key] = value
    for directory in ("home", "config", "data", "state", "cache"):
        (root / directory).mkdir(mode=0o700, exist_ok=True)
    return environment


def _resolve_runtime_value(
    value: Any,
    *,
    secrets: set[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Resolve one explicit credential reference at child-launch time.

    Harness launch values are immutable and may contain :class:`SecretReference`
    objects.  A live child needs the value, but the public launch/evidence must
    retain only the reference.  Environment references are the built-in
    provider; arbitrary providers fail closed until an adapter-specific
    resolver is supplied.
    """

    if isinstance(value, SecretReference):
        if value.source != "environment":
            raise HarnessStartupError("MCP credential resolution is unavailable")
        # An explicit value wins for this named reference; a missing value may
        # use the legacy ambient resolver.  Only referenced names are copied
        # into the child configuration/environment.
        resolved = (
            environment.get(value.name) if environment is not None else None
        ) or os.environ.get(value.name)
        if not resolved:
            raise HarnessStartupError("MCP credential is unavailable")
        if secrets is not None:
            secrets.add(resolved)
        return resolved
    if not isinstance(value, str) or "\x00" in value:
        raise HarnessStartupError("MCP configuration value is invalid")
    # Preserve the public profile convention for embedded environment refs
    # without copying the ambient environment wholesale.
    output = value
    start = 0
    while True:
        begin = output.find("${", start)
        if begin < 0:
            break
        end = output.find("}", begin + 2)
        if end < 0:
            break
        name = output[begin + 2 : end]
        if not name or any(
            character
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"
            for character in name
        ):
            raise HarnessStartupError("MCP configuration reference is invalid")
        resolved = (
            environment.get(name) if environment is not None else None
        ) or os.environ.get(name)
        if not resolved:
            raise HarnessStartupError("MCP credential is unavailable")
        if secrets is not None:
            secrets.update((resolved, "${" + name + "}"))
        output = output[:begin] + resolved + output[end + 1 :]
        start = begin + len(resolved)
    return output


def _resolved_config_values(
    values: Mapping[str, Any],
    *,
    secrets: set[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    return {
        str(key): _resolve_runtime_value(
            value, secrets=secrets, environment=environment
        )
        for key, value in values.items()
    }


def _server_configuration(
    launch: HarnessLaunch,
    *,
    resolve_credentials: bool = False,
    secrets: set[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Create the MCP config consumed by native clients.

    The file is temporary and removed immediately after the process has
    started. Values are supplied by the launch's selected references; each
    named reference may use the legacy ambient resolver when no override was
    supplied, but unrelated ambient values are never copied.
    """

    servers: dict[str, dict[str, Any]] = {}
    for config in launch.configurations:
        if not config.available:
            if config.required:
                raise HarnessStartupError("required MCP server is unavailable")
            continue
        if config.transport.value == "stdio":
            if config.command is None:
                raise HarnessStartupError("MCP server command is unavailable")
            servers[config.key] = {
                "command": config.command,
                "args": list(config.args),
                "env": (
                    _resolved_config_values(
                        config.environment, secrets=secrets, environment=environment
                    )
                    if resolve_credentials
                    else _redact_config_values(config.environment)
                ),
            }
            if config.cwd is not None:
                servers[config.key]["cwd"] = config.cwd
        else:
            if config.endpoint is None:
                raise HarnessStartupError("MCP server endpoint is unavailable")
            servers[config.key] = {
                "type": "sse" if config.transport.value == "sse" else "http",
                "url": (
                    _resolve_runtime_value(
                        config.endpoint, secrets=secrets, environment=environment
                    )
                    if resolve_credentials
                    else config.endpoint
                ),
                "headers": (
                    _resolved_config_values(
                        config.headers, secrets=secrets, environment=environment
                    )
                    if resolve_credentials
                    else _redact_config_values(config.headers)
                ),
            }
    return {"mcpServers": servers}


def _redact_config_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """Keep config files free of conventional credential-bearing values."""

    result: dict[str, Any] = {}
    for key, value in values.items():
        lowered = str(key).replace("-", "_").lower()
        sensitive = any(
            marker in lowered
            for marker in (
                "token",
                "secret",
                "password",
                "credential",
                "authorization",
                "api_key",
                "apikey",
                "cookie",
            )
        )
        result[str(key)] = "[REDACTED]" if sensitive else value
    return result


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("text", "result", "content", "message"):
            if key in value:
                found = _text(value[key])
                if found:
                    return found
    if isinstance(value, (list, tuple)):
        return "".join(_text(item) for item in value)
    return ""


async def read_bounded_line(
    stream: asyncio.StreamReader, *, maximum: int = MAX_FRAME_BYTES
) -> bytes | None:
    """Read one line without allowing an unterminated frame to grow unbounded."""
    try:
        line = await stream.readline()
    except (asyncio.LimitOverrunError, ValueError):
        raise HarnessStartupError("harness output exceeded safe frame size") from None
    if len(line) > maximum:
        raise HarnessStartupError("harness output exceeded safe frame size")
    return line or None


async def drain_bounded(stream: Any, *, maximum: int = MAX_STDERR_BYTES) -> bytes:
    """Drain a diagnostic stream to EOF while retaining a bounded prefix.

    Reading must continue after the retained diagnostic limit is reached.  A
    child (or one of its descendants) can otherwise block forever on a full
    inherited pipe, making a successful startup look like a hung harness and
    preventing process cleanup from completing.  The retained bytes are only
    diagnostic evidence; callers redact them before exposing them publicly.
    """

    if stream is None:
        return b""
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
        raise ValueError("diagnostic limit must be a positive integer")
    retained = bytearray()
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            return bytes(retained)
        if len(retained) < maximum:
            retained.extend(chunk[: maximum - len(retained)])


async def discard_bounded(
    stream: asyncio.StreamReader, *, maximum: int = MAX_STDERR_BYTES
) -> None:
    """Drain diagnostics to EOF without retaining them."""

    await drain_bounded(stream, maximum=maximum)


def probe_help(executable: str, args: tuple[str, ...]) -> str | None:
    """Read bounded capability help without credentials or model execution."""

    with tempfile.TemporaryDirectory(prefix="mcp-pal-probe-") as root:
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": root,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
        }
        try:
            result = subprocess.run(
                [executable, *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
                shell=False,
                cwd=root,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return (result.stdout + "\n" + result.stderr).replace("\x00", " ")[:32_768]


class ProcessOwner:
    """Own a process group and its non-blocking output readers."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.process: asyncio.subprocess.Process | None = None
        self.stderr_task: asyncio.Task[bytes] | None = None
        self.pgid: int | None = None
        self._closed = False

    async def spawn(
        self,
        argv: list[str],
        environment: Mapping[str, str],
        *,
        cwd: Path | str | None = None,
    ) -> None:
        try:
            self.process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # ``root`` is the adapter-control directory.  Native harnesses
                # must execute in the SDK workspace when one was selected;
                # keeping this separate prevents provider config/cache files
                # from becoming workspace artifacts.
                cwd=str(cwd if cwd is not None else self.root),
                env=dict(environment),
                start_new_session=True,
                limit=MAX_FRAME_BYTES + 1,
            )
        except (OSError, ValueError):
            raise HarnessStartupError("harness process could not start") from None
        assert self.process.stderr is not None
        self.stderr_task = asyncio.create_task(drain_bounded(self.process.stderr))
        if os.name == "posix" and self.process.pid is not None:
            # ``start_new_session=True`` makes the child its own group leader.
            # Keep this identity even if a very short-lived child is already
            # gone by the time ``getpgid`` runs; the termination helper still
            # verifies a live PID before issuing a group signal.
            self.pgid = self.process.pid
            try:
                candidate = os.getpgid(self.process.pid)
                if candidate == self.process.pid:
                    self.pgid = candidate
            except (ProcessLookupError, PermissionError, OSError):
                pass

    async def terminate(self) -> None:
        process = self.process
        if process is None:
            return
        if os.name == "posix":
            # Do not skip this merely because the direct child was already
            # reaped: descendants may still own stderr/stdout pipes.
            from .process_group import terminate_process_group

            await asyncio.to_thread(
                terminate_process_group,
                pid=process.pid,
                pgid=self.pgid,
                grace_seconds=0.25,
            )
        elif process.returncode is None:
            try:
                process.terminate()
            except (OSError, ProcessLookupError):
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            if os.name == "posix":
                from .process_group import terminate_process_group

                await asyncio.to_thread(
                    terminate_process_group,
                    pid=process.pid,
                    pgid=self.pgid,
                    grace_seconds=0.1,
                )
            elif process.returncode is None:
                try:
                    process.kill()
                except (OSError, ProcessLookupError):
                    pass
            # Group verification can fail closed (for example after a PID
            # race).  The Process object still identifies the exact owned
            # child, so finish with a direct kill rather than waiting forever.
            if process.returncode is None:
                try:
                    process.kill()
                except (OSError, ProcessLookupError):
                    pass
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                # Do not turn fail-closed ownership verification into an
                # unbounded cleanup wait. The exact child has received KILL;
                # a later event-loop child watcher can still reap it.
                return

    async def close(self) -> None:
        if self._closed:
            return
        failure = False
        try:
            await self.terminate()
            if self.stderr_task is not None:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self.stderr_task), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    # An unowned descendant can retain an inherited pipe even
                    # after safe group cleanup refuses to signal it.
                    self.stderr_task.cancel()
                    await asyncio.gather(self.stderr_task, return_exceptions=True)
            shutil.rmtree(self.root, ignore_errors=False)
        except (OSError, asyncio.CancelledError):
            failure = True
        if failure:
            raise CleanupError("harness cleanup failed") from None
        self._closed = True


class NativeSessionBase:
    """Shared immutable session projection for native adapters."""

    def __init__(
        self,
        owner: ProcessOwner,
        capabilities: HarnessAdapterCapabilities,
        session_id: str,
        *,
        server_configuration_count: int = 0,
        capture: Any = None,
    ) -> None:
        self.owner = owner
        self._capabilities = capabilities
        self._session_id = session_id
        self._turns = 0
        self._closed = False
        self._server_configuration_count = server_configuration_count
        self._capture = capture

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def capabilities(self) -> HarnessAdapterCapabilities:
        return self._capabilities

    def snapshot(self) -> HarnessSessionSnapshot:
        evidence: dict[str, str | int | float | bool | None] = {
            "adapter": self._capabilities.name,
            "process_owned": True,
            "transport_observed": "process",
            "content_observed": self._turns > 0,
            "tool_calls_observed": False,
            "mcp_traffic_observed": False,
            "usage_state": "unavailable",
        }
        if self._capture is not None:
            try:
                observed = self._capture.evidence()
                evidence.update(
                    {
                        str(key): value
                        for key, value in observed.items()
                        if isinstance(value, (str, int, float, bool)) or value is None
                    }
                )
            except Exception:
                evidence["capture_provenance"] = "unavailable"
        return HarnessSessionSnapshot(
            session_id=self._session_id,
            turns=self._turns,
            server_configuration_count=self._server_configuration_count,
            closed=self._closed,
            evidence=evidence,
        )

    async def cancel(self) -> None:
        await self.owner.terminate()

    async def close(self) -> None:
        if self._closed:
            return
        await self.owner.close()
        self._closed = True


def write_config(
    root: Path,
    launch: HarnessLaunch,
    *,
    environment: Mapping[str, str] | None = None,
    secrets: set[str] | None = None,
) -> Path:
    config = root / "mcp-config.json"
    try:
        # Resolve only while writing the 0600 child config; callers never get
        # the resolved object back and the file is removed by each adapter as
        # soon as startup has consumed it.
        payload = json.dumps(
            _server_configuration(
                launch,
                resolve_credentials=True,
                environment=environment,
                secrets=secrets,
            ),
            separators=(",", ":"),
        )
        descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
    except (OSError, TypeError, ValueError, HarnessStartupError):
        try:
            config.unlink(missing_ok=True)
        except OSError:
            pass
        raise HarnessStartupError("MCP configuration could not be prepared") from None
    return config


def workspace_for_launch(launch: HarnessLaunch, control_root: Path) -> Path:
    """Resolve the harness cwd without conflating it with control storage."""

    candidate = (
        control_root if launch.workspace_root is None else Path(launch.workspace_root)
    )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise HarnessStartupError("SDK workspace is unavailable") from None
    if not resolved.is_dir():
        raise HarnessStartupError("SDK workspace is unavailable")
    return resolved


def result_from_output(
    sequence: int, output: Mapping[str, Any], response_text: str = ""
) -> HarnessTurnResult:
    is_error = bool(output.get("is_error", output.get("isError", False)))
    return HarnessTurnResult(
        sequence=sequence,
        status="failed" if is_error else "completed",
        response=None,
        error=None
        if not is_error
        else ErrorInfo(code=ErrorCode.TRANSPORT_ERROR, message="harness turn failed"),
        evidence={"usage_observed": bool("usage" in output)},
        tool_calls=tuple(
            item for item in output.get("tool_calls", ()) if isinstance(item, Mapping)
        ),
    )


__all__ = [
    "MAX_FRAME_BYTES",
    "MAX_QUEUE_ITEMS",
    "MAX_STDERR_BYTES",
    "NativeSessionBase",
    "ProcessOwner",
    "_executable",
    "_isolated_environment",
    "_resolve_runtime_value",
    "_resolved_config_values",
    "_server_configuration",
    "_text",
    "discard_bounded",
    "drain_bounded",
    "probe_help",
    "read_bounded_line",
    "result_from_output",
    "workspace_for_launch",
    "write_config",
]
