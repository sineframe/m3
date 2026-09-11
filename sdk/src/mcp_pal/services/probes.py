"""Small, dependency-light capability and readiness probes.

The probe service deliberately knows nothing about the application, kit,
configuration resolver, persistence, or pytest.  Callers request the exact
components they want checked and receive typed, redacted evidence.  In
particular, a failed harness probe does not cause another harness to be
selected.
"""

from __future__ import annotations

import asyncio as _asyncio
import importlib.util
import json
import math
import os
import re
import shutil
import signal
import subprocess
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import Field

from ..types import Capability, CapabilityStatus, FrozenModel, Readiness, TransportKind


class ProbeKind(str, Enum):
    """The independently requestable capability categories."""

    CONFIGURATION = "configuration"
    BINARY = "binary"
    PROTOCOL = "protocol"
    TRANSPORT = "transport"
    STORAGE = "storage"
    HARNESS = "harness"


class ProbeEvidence(FrozenModel):
    """Safe evidence collected by one probe.

    ``output`` and ``details`` are redacted before this model is built.  This
    makes the model's normal repr and Pydantic serialization safe by
    construction rather than relying on consumers to remember to redact.
    """

    kind: ProbeKind
    target: str = Field(min_length=1, max_length=512)
    command: tuple[str, ...] = ()
    resolved_executable: str | None = None
    detected_version: str | None = None
    protocol_version: str | None = None
    output: str = Field(default="", max_length=65536)
    details: Mapping[str, Any] = Field(default_factory=dict)


class ProbeResult(FrozenModel):
    """One capability result and its separately inspectable evidence."""

    capability: Capability
    evidence: ProbeEvidence

    @property
    def status(self) -> CapabilityStatus:
        return self.capability.status


class ProbeReport(FrozenModel):
    """Aggregate readiness for exactly the requested probes."""

    readiness: Readiness
    results: tuple[ProbeResult, ...] = ()

    @property
    def capabilities(self) -> tuple[Capability, ...]:
        return self.readiness.capabilities

    def result_for(self, name: str) -> ProbeResult | None:
        """Return the result for ``name`` without guessing another target."""

        return next(
            (result for result in self.results if result.capability.name == name), None
        )


@dataclass(frozen=True, repr=False)
class ProbeRequest:
    """Typed request used by :meth:`Probes.probe_requested`.

    The repr intentionally omits command arguments and environment values;
    requests commonly contain credentials passed to a child process.
    """

    kind: ProbeKind
    name: str
    executable: str | None = None
    args: tuple[str, ...] = ()
    env: Mapping[str, str] | None = None
    module: str | None = None
    transport: str | None = None
    timeout_seconds: float | None = None

    def __repr__(self) -> str:
        kind = self.kind.value if isinstance(self.kind, ProbeKind) else str(self.kind)
        try:
            name = _safe_name(self.name, _secret_values(self.env))
        except ValueError:
            name = "invalid"
        return f"ProbeRequest(kind={kind!r}, name={name!r})"


_VERSION_RE = re.compile(
    r"(?<![A-Za-z0-9])v?(\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.-]+)?)(?![A-Za-z0-9])"
)
_SENSITIVE_KEY_RE = re.compile(
    r"(^|[-_])(authorization|proxy_authorization|cookie|set_cookie|token|api_key|apikey|secret|password|passwd|credential|private_key)([-_]|$)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT_RE = re.compile(
    r"(?i)(bearer\s+)[^\s,;]+|((?:api[_-]?key|token|secret|password|signature|credential)=)[^\s&;]+"
)
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
_REDACTED = "[REDACTED]"
_DEFAULT_OUTPUT_LIMIT = 64 * 1024
_DEFAULT_TIMEOUT = 5.0


def _redact_text(
    value: str, secrets: Iterable[str] = (), *, parse_json: bool = True
) -> str:
    """Redact known values and credential-shaped text without raising."""

    secrets = tuple(secrets)
    result = _SENSITIVE_TEXT_RE.sub(
        lambda match: f"{match.group(1) or match.group(2)}{_REDACTED}", value
    )
    for secret in secrets:
        if secret:
            result = result.replace(secret, _REDACTED)
    if parse_json:
        try:
            parsed = json.loads(result)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            # Use the recursive key-aware redactor for JSON-shaped command
            # output, then serialize the safe projection.  Strings recurse
            # through the scalar path to avoid parsing nested text repeatedly.
            result = json.dumps(
                _redact_value(parsed, set(secrets)),
                sort_keys=True,
                separators=(",", ":"),
            )
    return result[:_DEFAULT_OUTPUT_LIMIT]


def _ambient_secret_values() -> set[str]:
    return {
        value
        for key, value in os.environ.items()
        if value and len(value) >= 6 and _SENSITIVE_KEY_RE.search(key)
    }


def _redact_value(value: Any, secrets: set[str], key_hint: str | None = None) -> Any:
    if key_hint and _SENSITIVE_KEY_RE.search(key_hint):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(key): _redact_value(item, secrets, str(key))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, secrets) for item in value]
    if isinstance(value, str):
        return _redact_text(value, secrets, parse_json=False)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value), secrets)


def _safe_error(error: BaseException, secrets: set[str]) -> str:
    """Return a bounded, redacted error without exposing command arguments."""

    return _redact_text(f"{type(error).__name__}: {error}", secrets)


def _secret_values(environment: Mapping[str, Any] | None) -> set[str]:
    """Collect every explicit child-environment value before any execution."""

    explicit = {
        value
        for value in (environment or {}).values()
        if isinstance(value, str) and value
    }
    return _ambient_secret_values() | explicit


def _safe_name(value: str, secrets: set[str]) -> str:
    """Constrain an externally supplied capability name before persistence."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("capability name must be a non-empty string")
    safe = _SAFE_NAME_RE.sub("_", _redact_text(value.strip(), secrets))
    safe = safe.strip("._:-") or "capability"
    return safe[:128]


def _validate_timeout(value: float, *, field: str = "timeout_seconds") -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise ValueError(f"{field} must be finite and positive")
    return float(value)


def _resolve_executable(
    executable: str, environment: Mapping[str, str] | None
) -> str | None:
    """Resolve through the caller's PATH before constructing the child env."""

    if not isinstance(executable, str) or not executable or "\x00" in executable:
        return None
    caller_path = (environment or {}).get("PATH")
    if caller_path is None:
        caller_path = os.environ.get("PATH", os.defpath)
    resolved = shutil.which(executable, path=caller_path)
    if resolved is None:
        return None
    return os.path.realpath(resolved)


@dataclass
class _CommandOutput:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str | None = None


def _read_bounded(stream: Any, chunks: list[bytes], limit: int) -> None:
    """Drain a pipe while retaining only a bounded prefix."""

    retained = 0
    try:
        try:
            while True:
                data = stream.read(4096)
                if not data:
                    return
                if retained < limit:
                    piece = data[: limit - retained]
                    chunks.append(piece)
                    retained += len(piece)
        except (OSError, ValueError):
            return
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Clean up the owned process group, even after its parent exits."""

    # A child/grandchild can outlive the process object and retain our pipes.
    # ``start_new_session`` gives the probe an owned POSIX process group, so
    # signal the group regardless of the parent's current return code.
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            pass
        try:
            process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            pass
        try:
            process.wait(timeout=0.75)
        except subprocess.TimeoutExpired:
            # Reaping the direct child is still attempted below; descendants
            # are owned by the process group and have already been SIGKILLed.
            try:
                process.wait()
            except OSError:
                pass
        return

    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=0.75)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=0.75)
        except subprocess.TimeoutExpired:
            try:
                process.wait()
            except OSError:
                pass


def _run_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None,
    timeout_seconds: float,
    output_limit: int,
    secrets: set[str],
) -> _CommandOutput:
    if not argv or any(
        not isinstance(part, str) or not part or "\x00" in part for part in argv
    ):
        raise ValueError(
            "probe command must contain non-empty strings without NUL bytes"
        )
    child_env = {
        "PATH": os.defpath,
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONHASHSEED": "0",
    }
    if env:
        for key, value in env.items():
            if not isinstance(key, str) or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*", key
            ):
                raise ValueError("probe environment contains an invalid variable name")
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError(
                    "probe environment values must be strings without NUL bytes"
                )
            child_env[key] = value

    timeout_seconds = _validate_timeout(timeout_seconds)
    process: subprocess.Popen[bytes] | None = None
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    readers: list[threading.Thread] = []
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
            shell=False,
            start_new_session=os.name == "posix",
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        assert process.stdout is not None and process.stderr is not None
        readers = [
            threading.Thread(
                target=_read_bounded,
                args=(process.stdout, stdout_chunks, output_limit),
                daemon=True,
            ),
            threading.Thread(
                target=_read_bounded,
                args=(process.stderr, stderr_chunks, output_limit),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()
        try:
            returncode = process.wait(timeout=timeout_seconds)
            timed_out = False
        except subprocess.TimeoutExpired:
            _stop_process(process)
            returncode = process.returncode
            timed_out = True
        else:
            # Even a normally exited parent may have a descendant holding a
            # pipe open; reap/clean its owned process group before joining.
            _stop_process(process)
        for reader in readers:
            reader.join(timeout=1.5)
        return _CommandOutput(
            returncode=returncode,
            stdout=b"".join(stdout_chunks).decode("utf-8", errors="replace"),
            stderr=b"".join(stderr_chunks).decode("utf-8", errors="replace"),
            timed_out=timed_out,
        )
    except (OSError, ValueError) as error:
        if process is not None:
            _stop_process(process)
        return _CommandOutput(
            returncode=None, stdout="", stderr="", error=_safe_error(error, secrets)
        )
    finally:
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        for reader in readers:
            reader.join(timeout=1.5)


def _detected_version(text: str) -> str | None:
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def _transport_kind(value: str | TransportKind | None) -> TransportKind | None:
    if value is None:
        return None
    try:
        return TransportKind(value)
    except ValueError:
        return None


class Probes:
    """Probe only explicitly requested capability targets.

    All subprocesses receive a deterministic minimal environment and are
    started without a shell.  A timeout owns cleanup of the process group.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        output_limit: int = _DEFAULT_OUTPUT_LIMIT,
    ) -> None:
        timeout_seconds = _validate_timeout(timeout_seconds)
        if output_limit <= 0:
            raise ValueError("output_limit must be positive")
        self.timeout_seconds = timeout_seconds
        self.output_limit = min(output_limit, _DEFAULT_OUTPUT_LIMIT)
        self._lifecycle_guard: Callable[[], None] | None = None

    def _set_lifecycle_guard(self, guard: Callable[[], None]) -> None:
        self._lifecycle_guard = guard

    def _ensure_open(self) -> None:
        if self._lifecycle_guard is not None:
            self._lifecycle_guard()

    def _command_result(
        self,
        kind: ProbeKind,
        name: str,
        executable: str,
        *,
        args: Sequence[str],
        env: Mapping[str, str] | None,
        transport: str | TransportKind | None,
        timeout_seconds: float | None,
    ) -> ProbeResult:
        self._ensure_open()
        secrets = _secret_values(env)
        safe_name = _safe_name(name, secrets)
        effective_timeout = (
            self.timeout_seconds
            if timeout_seconds is None
            else _validate_timeout(timeout_seconds)
        )
        known_transport = _transport_kind(transport)
        if transport is not None and known_transport is None:
            capability = Capability(
                name=safe_name,
                status=CapabilityStatus.UNSUPPORTED,
                reason="unknown transport",
            )
            evidence = ProbeEvidence(
                kind=kind,
                target=safe_name,
                details={
                    "transport": _redact_text(str(transport), secrets),
                    "executed": False,
                },
            )
            return ProbeResult(capability=capability, evidence=evidence)
        resolved = _resolve_executable(executable, env)
        if resolved is None:
            capability = Capability(
                name=safe_name,
                status=CapabilityStatus.UNAVAILABLE,
                reason="executable is unavailable",
                transport=known_transport,
            )
            evidence = ProbeEvidence(
                kind=kind,
                target=safe_name,
                resolved_executable=None,
                protocol_version=None,
                details={"resolved_executable": None, "executed": False},
            )
            return ProbeResult(capability=capability, evidence=evidence)
        argv = (resolved, *args)
        output = _run_command(
            argv,
            env=env,
            timeout_seconds=effective_timeout,
            output_limit=self.output_limit,
            secrets=secrets,
        )
        combined = _redact_text(
            "\n".join(part for part in (output.stdout, output.stderr) if part), secrets
        )[: self.output_limit]
        detected = _detected_version(combined)
        details: dict[str, Any] = {
            "returncode": output.returncode,
            "timed_out": output.timed_out,
            "stdout_bytes_retained": len(output.stdout.encode("utf-8")),
            "stderr_bytes_retained": len(output.stderr.encode("utf-8")),
        }
        status = CapabilityStatus.READY
        reason: str | None = None
        if output.error:
            status = CapabilityStatus.UNAVAILABLE
            reason = output.error
            details["error"] = output.error
        elif output.timed_out:
            status = (
                CapabilityStatus.DEGRADED
                if combined.strip()
                else CapabilityStatus.UNAVAILABLE
            )
            reason = (
                "probe timed out after partial evidence"
                if combined.strip()
                else "probe timed out before evidence"
            )
        elif output.returncode != 0:
            status = CapabilityStatus.DEGRADED
            reason = "probe exited unsuccessfully"
        details["resolved_executable"] = _redact_text(resolved, secrets)
        details["executed"] = True
        capability = Capability(
            name=safe_name,
            status=status,
            reason=reason,
            detected_version=detected,
            protocol_version=detected if kind is ProbeKind.PROTOCOL else None,
            transport=known_transport,
        )
        evidence = ProbeEvidence(
            kind=kind,
            target=safe_name,
            # Never retain argv: split flags can contain secrets even when
            # their names do not look credential-shaped.
            command=(),
            resolved_executable=_redact_text(resolved, secrets),
            detected_version=detected,
            protocol_version=detected if kind is ProbeKind.PROTOCOL else None,
            output=combined,
            details=_redact_value(details, secrets),
        )
        return ProbeResult(capability=capability, evidence=evidence)

    def probe_binary(
        self,
        name: str,
        executable: str | os.PathLike[str],
        *,
        args: Sequence[str] = ("--version",),
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> ProbeResult:
        """Check one explicitly selected executable and record its version."""

        self._ensure_open()
        return self._command_result(
            ProbeKind.BINARY,
            name,
            os.fspath(executable),
            args=args,
            env=env,
            transport=None,
            timeout_seconds=timeout_seconds,
        )

    def probe_protocol(
        self,
        name: str,
        executable: str | os.PathLike[str],
        *,
        args: Sequence[str] = ("--protocol-version",),
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
        transport: str | TransportKind | None = None,
    ) -> ProbeResult:
        """Run the caller-selected protocol probe without version allowlists."""

        self._ensure_open()
        return self._command_result(
            ProbeKind.PROTOCOL,
            name,
            os.fspath(executable),
            args=args,
            env=env,
            transport=transport,
            timeout_seconds=timeout_seconds,
        )

    def probe_harness(
        self,
        name: str,
        executable: str | os.PathLike[str],
        *,
        args: Sequence[str] = ("--version",),
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
        transport: str | TransportKind | None = None,
    ) -> ProbeResult:
        """Probe exactly one harness executable; never select a fallback."""

        self._ensure_open()
        return self._command_result(
            ProbeKind.HARNESS,
            name,
            os.fspath(executable),
            args=args,
            env=env,
            transport=transport,
            timeout_seconds=timeout_seconds,
        )

    def probe_transport(
        self,
        name: str,
        *,
        transport: str | TransportKind,
        executable: str | os.PathLike[str] | None = None,
        args: Sequence[str] = ("--transport-ready",),
        module: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> ProbeResult:
        """Probe a selected transport through an explicit command or module.

        Supplying neither is ``unsupported``: the service does not maintain a
        transport allowlist or claim readiness based on a guessed fallback.
        """

        self._ensure_open()
        if timeout_seconds is not None:
            _validate_timeout(timeout_seconds)
        secrets = _secret_values(env)
        safe_name = _safe_name(name, secrets)
        kind = _transport_kind(transport)
        if kind is None:
            capability = Capability(
                name=safe_name,
                status=CapabilityStatus.UNSUPPORTED,
                reason="unknown transport",
            )
            evidence = ProbeEvidence(
                kind=ProbeKind.TRANSPORT,
                target=safe_name,
                details={
                    "transport": _redact_text(str(transport), secrets),
                    "executed": False,
                },
            )
            return ProbeResult(capability=capability, evidence=evidence)
        if executable is not None:
            return self._command_result(
                ProbeKind.TRANSPORT,
                name,
                os.fspath(executable),
                args=args,
                env=env,
                transport=kind,
                timeout_seconds=timeout_seconds,
            )
        if module:
            try:
                available = importlib.util.find_spec(module) is not None
                status = (
                    CapabilityStatus.READY
                    if available
                    else CapabilityStatus.UNAVAILABLE
                )
                reason = (
                    None
                    if available
                    else "optional transport dependency is unavailable"
                )
                details = {
                    "module": _redact_text(module, secrets),
                    "module_available": available,
                }
            except Exception as error:
                status = CapabilityStatus.DEGRADED
                reason = _safe_error(error, secrets)
                details = {"module": _redact_text(module, secrets), "error": reason}
            capability = Capability(
                name=safe_name, status=status, reason=reason, transport=kind
            )
            evidence = ProbeEvidence(
                kind=ProbeKind.TRANSPORT, target=safe_name, details=details
            )
            return ProbeResult(capability=capability, evidence=evidence)
        capability = Capability(
            name=safe_name,
            status=CapabilityStatus.UNSUPPORTED,
            reason="no transport probe was provided",
            transport=kind,
        )
        evidence = ProbeEvidence(
            kind=ProbeKind.TRANSPORT,
            target=safe_name,
            details={
                "transport": _redact_text(str(transport), secrets),
                "executed": False,
            },
        )
        return ProbeResult(capability=capability, evidence=evidence)

    def probe_storage(
        self, name: str = "memory", *, module: str | None = None
    ) -> ProbeResult:
        """Report in-memory storage as ready; check optional storage lazily."""

        self._ensure_open()
        secrets = _ambient_secret_values()
        safe_name = _safe_name(name, secrets)
        if module is None and name in {"memory", "in_memory"}:
            capability = Capability(name=safe_name, status=CapabilityStatus.READY)
            evidence = ProbeEvidence(
                kind=ProbeKind.STORAGE,
                target=safe_name,
                details={"implementation": "in_memory", "optional": False},
            )
            return ProbeResult(capability=capability, evidence=evidence)
        if not module:
            capability = Capability(
                name=safe_name,
                status=CapabilityStatus.UNSUPPORTED,
                reason="no storage implementation probe was provided",
            )
            evidence = ProbeEvidence(
                kind=ProbeKind.STORAGE, target=safe_name, details={"optional": True}
            )
            return ProbeResult(capability=capability, evidence=evidence)
        try:
            available = importlib.util.find_spec(module) is not None
            status = (
                CapabilityStatus.READY if available else CapabilityStatus.UNAVAILABLE
            )
            reason = None if available else "optional storage dependency is unavailable"
            details = {
                "module": _redact_text(module, secrets),
                "module_available": available,
                "optional": True,
            }
        except Exception as error:
            status = CapabilityStatus.DEGRADED
            reason = _safe_error(error, secrets)
            details = {
                "module": _redact_text(module, secrets),
                "error": reason,
                "optional": True,
            }
        capability = Capability(name=safe_name, status=status, reason=reason)
        evidence = ProbeEvidence(
            kind=ProbeKind.STORAGE, target=safe_name, details=details
        )
        return ProbeResult(capability=capability, evidence=evidence)

    def probe_requested(self, requests: Iterable[ProbeRequest]) -> ProbeReport:
        """Run only the supplied requests, retaining each independent result."""

        self._ensure_open()
        results: list[ProbeResult] = []
        for request in requests:
            if request.timeout_seconds is not None:
                _validate_timeout(request.timeout_seconds)
            try:
                kind = (
                    request.kind
                    if isinstance(request.kind, ProbeKind)
                    else ProbeKind(request.kind)
                )
            except (TypeError, ValueError):
                safe_name = _safe_name(request.name, _secret_values(request.env))
                results.append(
                    ProbeResult(
                        capability=Capability(
                            name=safe_name,
                            status=CapabilityStatus.UNAVAILABLE,
                            reason="unknown probe kind",
                        ),
                        evidence=ProbeEvidence(
                            kind=ProbeKind.BINARY,
                            target=safe_name,
                            details={"requested": True},
                        ),
                    )
                )
                continue
            if kind is ProbeKind.BINARY and request.executable is not None:
                result = self.probe_binary(
                    request.name,
                    request.executable,
                    args=request.args or ("--version",),
                    env=request.env,
                    timeout_seconds=request.timeout_seconds,
                )
            elif kind is ProbeKind.PROTOCOL and request.executable is not None:
                result = self.probe_protocol(
                    request.name,
                    request.executable,
                    args=request.args or ("--protocol-version",),
                    env=request.env,
                    timeout_seconds=request.timeout_seconds,
                    transport=request.transport,
                )
            elif kind is ProbeKind.HARNESS and request.executable is not None:
                result = self.probe_harness(
                    request.name,
                    request.executable,
                    args=request.args or ("--version",),
                    env=request.env,
                    timeout_seconds=request.timeout_seconds,
                    transport=request.transport,
                )
            elif kind is ProbeKind.TRANSPORT:
                result = self.probe_transport(
                    request.name,
                    transport=request.transport or request.name,
                    executable=request.executable,
                    args=request.args or ("--transport-ready",),
                    module=request.module,
                    env=request.env,
                    timeout_seconds=request.timeout_seconds,
                )
            elif kind is ProbeKind.STORAGE:
                result = self.probe_storage(request.name, module=request.module)
            else:
                safe_name = _safe_name(request.name, _secret_values(request.env))
                result = ProbeResult(
                    capability=Capability(
                        name=safe_name,
                        status=CapabilityStatus.UNAVAILABLE,
                        reason="probe target was not specified",
                    ),
                    evidence=ProbeEvidence(
                        kind=kind, target=safe_name, details={"requested": True}
                    ),
                )
            results.append(result)
        capabilities = tuple(result.capability for result in results)
        failures = tuple(
            result for result in results if result.status is not CapabilityStatus.READY
        )
        reason = (
            None
            if not failures
            else "; ".join(
                f"{result.capability.name}: {result.capability.reason or result.status.value}"
                for result in failures
            )
        )
        readiness = Readiness(
            ready=not failures, capabilities=capabilities, reason=reason
        )
        return ProbeReport(readiness=readiness, results=tuple(results))


class AsyncProbes:
    """Async namespace for capability probes.

    The underlying probe implementation remains shared with the synchronous
    service, but every potentially blocking operation is executed in a worker
    thread.  The synchronous implementation is intentionally private so an
    async kit cannot accidentally expose blocking probe methods.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        output_limit: int = _DEFAULT_OUTPUT_LIMIT,
    ) -> None:
        self._service = Probes(
            timeout_seconds=timeout_seconds, output_limit=output_limit
        )
        self._lifecycle_guard: Callable[[], None] | None = None

    def _set_lifecycle_guard(self, guard: Callable[[], None]) -> None:
        self._lifecycle_guard = guard
        self._service._set_lifecycle_guard(guard)

    def _ensure_open(self) -> None:
        if self._lifecycle_guard is not None:
            self._lifecycle_guard()

    async def probe_binary(
        self,
        name: str,
        executable: str | os.PathLike[str],
        *,
        args: Sequence[str] = ("--version",),
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> ProbeResult:
        self._ensure_open()
        return await _asyncio.to_thread(
            self._service.probe_binary,
            name,
            executable,
            args=args,
            env=env,
            timeout_seconds=timeout_seconds,
        )

    async def probe_protocol(
        self,
        name: str,
        executable: str | os.PathLike[str],
        *,
        args: Sequence[str] = ("--protocol-version",),
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
        transport: str | TransportKind | None = None,
    ) -> ProbeResult:
        self._ensure_open()
        return await _asyncio.to_thread(
            self._service.probe_protocol,
            name,
            executable,
            args=args,
            env=env,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    async def probe_harness(
        self,
        name: str,
        executable: str | os.PathLike[str],
        *,
        args: Sequence[str] = ("--version",),
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
        transport: str | TransportKind | None = None,
    ) -> ProbeResult:
        self._ensure_open()
        return await _asyncio.to_thread(
            self._service.probe_harness,
            name,
            executable,
            args=args,
            env=env,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    async def probe_transport(
        self,
        name: str,
        *,
        transport: str | TransportKind,
        executable: str | os.PathLike[str] | None = None,
        args: Sequence[str] = ("--transport-ready",),
        module: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> ProbeResult:
        self._ensure_open()
        return await _asyncio.to_thread(
            self._service.probe_transport,
            name,
            transport=transport,
            executable=executable,
            args=args,
            module=module,
            env=env,
            timeout_seconds=timeout_seconds,
        )

    async def probe_storage(
        self, name: str = "memory", *, module: str | None = None
    ) -> ProbeResult:
        self._ensure_open()
        return await _asyncio.to_thread(
            self._service.probe_storage, name, module=module
        )

    async def probe_requested(self, requests: Iterable[ProbeRequest]) -> ProbeReport:
        self._ensure_open()
        requested = tuple(requests)
        return await _asyncio.to_thread(self._service.probe_requested, requested)


__all__ = [
    "AsyncProbes",
    "ProbeEvidence",
    "ProbeKind",
    "ProbeReport",
    "ProbeRequest",
    "ProbeResult",
    "Probes",
]
