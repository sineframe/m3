"""OpenCode serve/session harness adapter.

One isolated ``opencode serve`` process and one HTTP session are retained for
the lifetime of the adapter.  The adapter never falls back to one-shot
``run`` invocations or copies ambient OpenCode configuration.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast

import httpx

from ..agent_session import AdapterTurn
from ..policy import ToolDescriptor, ToolPolicyEvaluator, ToolPolicyEvidence
from ..trace.redaction import RedactionConfig, is_sensitive_key, redact_for_api
from ..types import (
    Capability,
    CapabilityStatus,
    ErrorCode,
    ErrorInfo,
    FullToolPolicy,
    NativeToolPolicy,
    OpenCode,
    Readiness,
    RestrictiveToolPolicy,
    SecretReference,
    TextContent,
    TurnResponse,
    UserMessage,
)
from .contracts import (
    HarnessAdapterCapabilities,
    HarnessAdapterError,
    HarnessLaunch,
    HarnessSession,
    HarnessStartupError,
    HarnessTurnRequest,
    HarnessTurnResult,
)
from .native import (
    MAX_FRAME_BYTES,
    NativeSessionBase,
    ProcessOwner,
    _executable,
    _isolated_environment,
    _server_configuration,
    _text,
    probe_help,
    read_bounded_line,
    workspace_for_launch,
)
from .observations import (
    HarnessObservation,
    MessageChunkObservation,
    MetadataObservedObservation,
    RawEvidenceInput,
    RawFrameObservation,
    ReasoningChunkObservation,
    ToolCallObservedObservation,
    ToolResultObservedObservation,
    TurnEvidence,
    UsageObservedObservation,
)

_URL = re.compile(r"https?://(?:127\.0\.0\.1|localhost|\[::1\]):\d+")
_DIALECT_PROBE_CONTROL_KEYS = frozenset({"MCP_PAL_OPENCODE_MODE", "MCP_PAL_PROBE_MARKER", "MCP_PAL_VERSION_MARKER"})
_TOKEN_COUNTERS = frozenset({"input", "output", "reasoning", "cache_creation", "cache_read", "cache_write", "total"})
_ToolStatus = Literal[
    "success",
    "tool_error",
    "protocol_error",
    "transport_error",
    "cancelled",
    "timed_out",
    "incomplete",
]
_TurnStatus = Literal["completed", "failed", "timed_out", "cancelled", "interrupted"]
_MALFORMED_MARKER: dict[str, str] = {
    "capture": "unavailable",
    "reason": "malformed_source",
}
_INVALID_METADATA = object()
_MISSING_IDENTIFIER = object()


def _valid_identifier(value: object) -> bool:
    """Accept only bounded, printable provider identifiers."""

    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 256
        and all(ord(char) >= 0x20 and ord(char) != 0x7F for char in value)
    )


def opencode_configuration(launch: HarnessLaunch, *, dialect: str = "legacy") -> dict[str, Any]:
    """Render OpenCode's config dialect (never Claude's ``mcpServers``)."""
    if dialect == "v2" and any(config.transport.value == "sse" for config in launch.configurations):
        # V2 documents only Streamable HTTP for remote MCP servers.
        raise HarnessStartupError("OpenCode V2 does not support SSE MCP servers")
    # Keep credentials as OpenCode's documented `{env:NAME}` substitutions in
    # the transient config.  Resolving a SecretReference to its value here
    # would put the secret in a file that OpenCode (and a fixture) can read.
    raw_servers = _server_configuration(launch, resolve_credentials=False)["mcpServers"]
    source_servers = {config.key: config for config in launch.configurations}
    servers: dict[str, dict[str, Any]] = {}
    for name, value in raw_servers.items():
        source = source_servers.get(name)
        if "command" in value:
            item: dict[str, Any] = {"type": "local", "command": [value["command"], *value.get("args", [])]}
            if source is not None and source.environment:
                item["environment"] = {
                    key: _opencode_config_value(key, raw)
                    for key, raw in source.environment.items()
                }
            elif value.get("env"):
                item["environment"] = value["env"]
            if value.get("cwd"):
                item["cwd"] = value["cwd"]
        else:
            endpoint = source.endpoint if source is not None else value["url"]
            headers = source.headers if source is not None else value.get("headers", {})
            item = {
                "type": "remote",
                "url": _opencode_config_value("url", endpoint),
                "headers": {key: _opencode_config_value(key, raw) for key, raw in headers.items()},
            }
        servers[name] = item
    policy = launch.tool_policy
    if isinstance(policy, NativeToolPolicy):
        mode = policy.policy.get("mode")
        server = policy.policy.get("server")
        if mode not in {"mcp_only", "mcp_read_only", "full"} or not isinstance(server, str) or server not in servers:
            raise HarnessStartupError("OpenCode native tool policy is invalid")
        if mode == "full":
            tools: dict[str, Any] = {"*": True}
            permissions: dict[str, Any] = {"*": "allow"}
        else:
            pattern = f"{server}_*"
            read_only = tuple(str(item) for item in (policy.policy.get("read_only_tools") or ()))
            tools = {"*": False, pattern: True, **({item: True for item in read_only} if mode == "mcp_read_only" else {})}
            permissions = {"*": "deny", pattern: "allow", **({item: "allow" for item in read_only} if mode == "mcp_read_only" else {})}
    else:
        tools = {}
        permissions = {}
    if dialect == "v2":
        return {"$schema": "https://opencode.ai/config.json", "mcp": {"servers": servers}, **({"tools": tools, "permission": permissions} if tools else {})}
    if dialect == "legacy":
        return {"$schema": "https://opencode.ai/config.json", "mcp": {name: {**value, "enabled": True} for name, value in servers.items()}, **({"tools": tools, "permission": permissions} if tools else {})}
    raise HarnessStartupError("unsupported OpenCode configuration dialect")


def _opencode_config_value(key: str, value: Any) -> str:
    if isinstance(value, SecretReference):
        if value.source != "environment":
            raise HarnessStartupError("OpenCode credential reference is unavailable")
        return "{env:" + value.name + "}"
    if not isinstance(value, str) or "\x00" in value:
        raise HarnessStartupError("OpenCode configuration value is invalid")
    # Preserve the SDK's historical `${NAME}` spelling while emitting the
    # OpenCode dialect's documented `{env:NAME}` substitution.
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value)
    if match:
        return "{env:" + match.group(1) + "}"
    return "[REDACTED]" if is_sensitive_key(key) else value


def _resolve_opencode_environment_value(
    value: Any,
    environment: dict[str, str],
    secrets: set[str],
    *,
    resolver_environment: Mapping[str, str] | None = None,
) -> None:
    """Make an explicit env reference available to OpenCode's child MCP."""

    if isinstance(value, SecretReference):
        if value.source != "environment":
            raise HarnessStartupError("OpenCode credential reference is unavailable")
        name = value.name
    elif isinstance(value, str):
        match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value)
        if match is None:
            return
        name = match.group(1)
    else:
        return
    # The explicit map takes precedence, but a reference names its own
    # credential.  Falling back to the ambient value for that named reference
    # preserves the SDK contract without copying unrelated ambient keys.
    resolved = (
        (resolver_environment.get(name) if resolver_environment is not None else None)
        or environment.get(name)
        or os.environ.get(name)
    )
    if not resolved:
        raise HarnessStartupError("OpenCode credential is unavailable")
    environment[name] = resolved
    secrets.add(resolved)


def _write_opencode_config(root: Path, launch: HarnessLaunch, dialect: str) -> Path:
    config = root / "opencode.json"
    descriptor: int | None = None
    try:
        payload = json.dumps(opencode_configuration(launch, dialect=dialect), separators=(",", ":"))
        descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = None
            output.write(payload)
    except (OSError, TypeError, ValueError, HarnessStartupError):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            config.unlink(missing_ok=True)
        except OSError:
            pass
        raise HarnessStartupError("OpenCode configuration could not be prepared") from None
    return config


class _ResponseTooLarge(ValueError):
    """The provider response exceeded the bounded native frame size."""


class _HistorySnapshot(NamedTuple):
    items: tuple[Mapping[str, Any], ...]
    body: bytes | None
    status_code: int | None
    content_type: object
    truncated: bool
    valid: bool
    start_offset_ms: float = 0.0
    end_offset_ms: float = 0.0


class OpenCodeHarnessAdapter:
    """One isolated OpenCode server and attached conversation session."""

    def __init__(self, *, executable: str = "opencode", environment: Mapping[str, str] | None = None) -> None:
        self.executable = _executable(executable, "opencode")
        self.environment = dict(environment or {})
        self._resolver_environment = environment
        self._capabilities = HarnessAdapterCapabilities(
            name="opencode",
            supports_multiturn=True,
            supports_cancellation=True,
            supports_timeout=True,
            # OpenCode permissions are not yet represented as portable
            # requested/enforced/observed evidence by this adapter.
            supports_tool_policy=False,
            supports_streaming=True,
        )
        self._owner: ProcessOwner | None = None
        self._client: httpx.AsyncClient | None = None
        self._base_url: str | None = None
        self._launch: HarnessLaunch | None = None
        self._session: OpenCodeSession | None = None
        self._root: Path | None = None
        self._detected_dialect: str | None = None
        # Runtime-only values resolved from explicit SecretReferences.  They
        # are used to protect adapter results before they reach the public
        # session boundary; they are never included in a launch descriptor.
        self._runtime_secrets: set[str] = set()
        self.last_policy_evidence: Any = None

    @property
    def name(self) -> str:
        return self._capabilities.name

    @property
    def capabilities(self) -> HarnessAdapterCapabilities:
        return self._capabilities

    @property
    def supported_content_kinds(self) -> frozenset[str]:
        return self._capabilities.supported_content_kinds

    async def preflight(self, launch: HarnessLaunch) -> Readiness:
        if shutil.which(self.executable) is None and not Path(self.executable).is_file():
            capability = Capability(name="harness:opencode", status=CapabilityStatus.UNAVAILABLE, reason="executable unavailable")
            return Readiness(ready=False, capabilities=(capability,), reason="executable unavailable")
        help_text = await asyncio.to_thread(probe_help, self.executable, ("serve", "--help"))
        if help_text is None or "serve" not in help_text.lower():
            capability = Capability(name="harness:opencode", status=CapabilityStatus.UNAVAILABLE, reason="serve unavailable")
            return Readiness(ready=False, capabilities=(capability,), reason="serve unavailable")
        harness = launch.spec.harness
        if isinstance(harness, OpenCode) and harness.dialect not in ("auto", "legacy", "v2"):
            return Readiness(ready=False, reason="unsupported OpenCode configuration dialect")
        try:
            detected = self._detect_dialect(launch)
        except HarnessStartupError as error:
            return Readiness(ready=False, reason=str(error))
        if isinstance(harness, OpenCode) and harness.dialect != "auto" and harness.dialect != detected:
            return Readiness(ready=False, reason="OpenCode configuration dialect mismatch")
        self._detected_dialect = detected
        self.last_policy_evidence = None
        self._capabilities = replace(self._capabilities, supports_tool_policy=False)
        policy = launch.tool_policy
        if isinstance(policy, NativeToolPolicy):
            if policy.harness != self.name or policy.policy.get("mode") not in {"mcp_only", "mcp_read_only", "full"}:
                return self._capabilities.readiness(ready=False, reason="tool_policy_unsupported")
            server = policy.policy.get("server")
            if not isinstance(server, str) or server not in {record.key for record in launch.servers.records}:
                return self._capabilities.readiness(ready=False, reason="tool_policy_unsupported")
            self.last_policy_evidence = ToolPolicyEvidence(
                requested="native", enforced="native", observed="preflight", portable=False,
                nonportable_reason="OpenCode configuration permissions",
            )
            self._capabilities = replace(self._capabilities, supports_tool_policy=True)
            return self._capabilities.readiness()
        policy_requested = isinstance(policy, (FullToolPolicy, NativeToolPolicy)) or (
            isinstance(policy, RestrictiveToolPolicy) and bool(policy.allowed_tools or policy.denied_tools)
        )
        if policy_requested:
            if isinstance(policy, NativeToolPolicy) or not isinstance(policy, (RestrictiveToolPolicy, FullToolPolicy)):
                return self._capabilities.readiness(ready=False, reason="tool_policy_unsupported")
            available_connections = tuple(
                str(config.connection_id)
                for config in launch.configurations
                if config.available
            )
            enforcement = getattr(launch.capture, "enforces_portable_policy", None)
            if not callable(enforcement) or not enforcement(available_connections):
                return self._capabilities.readiness(ready=False, reason="tool_policy_unsupported")
            self._capabilities = replace(self._capabilities, supports_tool_policy=True)
            descriptors = tuple(
                ToolDescriptor(server=record.key, name=tool)
                for record in launch.servers.records
                if record.available
                for tool in record.tools
            )
            self.last_policy_evidence = ToolPolicyEvaluator(descriptors).preflight(
                policy,
                harness_name=self.name,
                supports_enforcement=True,
            )
        return self._capabilities.readiness()

    def _detect_dialect(self, launch: HarnessLaunch) -> str:
        del launch
        with tempfile.TemporaryDirectory(prefix="mcp-pal-opencode-probe-") as probe_root:
            probe_environment = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": probe_root,
                "XDG_CONFIG_HOME": str(Path(probe_root) / "config"),
                "XDG_DATA_HOME": str(Path(probe_root) / "data"),
                "XDG_STATE_HOME": str(Path(probe_root) / "state"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "TZ": "UTC",
            }
            probe_environment.update(
                (key, value)
                for key, value in self.environment.items()
                if key in _DIALECT_PROBE_CONTROL_KEYS
            )
            try:
                result = subprocess.run((self.executable, "--version"), capture_output=True, text=True, timeout=5, check=False, env=probe_environment, cwd=probe_root)
            except (OSError, subprocess.SubprocessError):
                raise HarnessStartupError("OpenCode configuration dialect could not be detected") from None
        # Stable 1.x exposes the flat `mcp` schema. Unknown versions fail
        # closed rather than receiving a potentially incompatible config.
        if result.returncode == 0 and re.search(r"\b1\.\d+", result.stdout):
            return "legacy"
        if result.returncode == 0 and re.search(r"\b2\.\d+", result.stdout):
            return "v2"
        raise HarnessStartupError("OpenCode configuration dialect is unsupported")

    async def open(self, launch: HarnessLaunch) -> HarnessSession:
        readiness = await self.preflight(launch)
        if not readiness.ready:
            raise HarnessStartupError("OpenCode server is unavailable")
        if self._session is not None:
            raise HarnessStartupError("OpenCode session is already open")
        root = Path(tempfile.mkdtemp(prefix="mcp-pal-opencode-"))
        owner = ProcessOwner(root)
        client: httpx.AsyncClient | None = None
        try:
            harness = launch.spec.harness
            environment = _isolated_environment(root, self.environment)
            runtime_secrets: set[str] = set()
            if isinstance(harness, OpenCode):
                for variable, reference in harness.credential_references.items():
                    if not isinstance(variable, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
                        raise HarnessStartupError("OpenCode credential reference is invalid")
                    if not isinstance(reference, SecretReference) or reference.source != "environment":
                        raise HarnessStartupError("OpenCode credential reference is unavailable")
                    value = environment.get(reference.name)
                    if value is None:
                        value = os.environ.get(reference.name)
                    if not value:
                        raise HarnessStartupError("OpenCode credential is unavailable")
                    environment[variable] = value
                    runtime_secrets.add(value)
            for server in launch.configurations:
                for value in server.environment.values():
                    _resolve_opencode_environment_value(
                        value,
                        environment,
                        runtime_secrets,
                        resolver_environment=self._resolver_environment,
                    )
                for value in server.headers.values():
                    _resolve_opencode_environment_value(
                        value,
                        environment,
                        runtime_secrets,
                        resolver_environment=self._resolver_environment,
                    )
                _resolve_opencode_environment_value(
                    server.endpoint,
                    environment,
                    runtime_secrets,
                    resolver_environment=self._resolver_environment,
                )
            self._runtime_secrets = runtime_secrets
            capture = launch.capture
            add_secrets = getattr(capture, "add_secrets", None)
            if callable(add_secrets) and runtime_secrets:
                add_secrets(runtime_secrets)
            writer_for = getattr(capture, "writer_for", None)
            if callable(writer_for) and runtime_secrets:
                for configuration in launch.configurations:
                    try:
                        writer = writer_for(configuration.connection_id, configuration.transport.value)
                        writer_add_secrets = getattr(writer, "add_secrets", None)
                        if callable(writer_add_secrets):
                            writer_add_secrets(runtime_secrets)
                    except Exception:
                        # Capture registration is best effort; the adapter's
                        # public result redaction remains authoritative.
                        pass
            dialect = self._detected_dialect
            if dialect not in ("legacy", "v2"):
                raise HarnessStartupError("OpenCode configuration dialect could not be detected")
            config = _write_opencode_config(root, launch, dialect)
            workspace = workspace_for_launch(launch, root)
            environment["OPENCODE_CONFIG"] = str(config)
            await owner.spawn(
                [self.executable, "serve", "--hostname", "127.0.0.1", "--port", "0"],
                environment,
                cwd=workspace,
            )
            assert owner.process is not None and owner.process.stdout is not None
            base_url = await self._read_server_url(owner.process.stdout)
            # OpenCode scopes server sessions to a project directory.  The
            # official API accepts this as a request header, not as a
            # session-body field.
            client = httpx.AsyncClient(
                base_url=base_url,
                timeout=httpx.Timeout(30.0, connect=5.0),
                headers={"x-opencode-directory": str(workspace)},
            )
            async with client.stream("POST", "/session", json={}) as response:
                if response.status_code >= 400:
                    raise HarnessStartupError("OpenCode session could not be created")
                try:
                    body = json.loads(await self._read_bounded_response(response))
                except json.JSONDecodeError:
                    raise HarnessStartupError("OpenCode session response is invalid") from None
            session_id = body.get("id") if isinstance(body, Mapping) else None
            if not isinstance(session_id, str) or not session_id:
                raise HarnessStartupError("OpenCode session identity was unavailable")
            self._root = root
            self._owner = owner
            self._client = client
            self._base_url = base_url
            self._launch = launch
            self._session = OpenCodeSession(self, owner, client, self._capabilities, session_id)
            config.unlink(missing_ok=True)
            return self._session
        except BaseException:
            self._runtime_secrets.clear()
            if client is not None:
                await client.aclose()
            try:
                await owner.close()
            except BaseException:
                pass
            raise

    async def start(self, spec: Any) -> None:
        raise HarnessStartupError("OpenCode requires a resolved harness launch")

    async def send(self, message: UserMessage, *, timeout: float | None = None, metadata: Mapping[str, object] | None = None) -> AdapterTurn:
        del metadata
        if self._session is None:
            raise HarnessAdapterError("OpenCode session is not open")
        result = await self._session.send(HarnessTurnRequest.from_message(message, timeout_seconds=timeout))
        return AdapterTurn(
            response=result.response,
            error=result.error,
            terminal=result.status != "completed",
            tool_calls=result.tool_calls,
            evidence=result.evidence,
            trace_limitations=result.trace_limitations,
            turn_evidence=result.turn_evidence,
        )

    async def close(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            await session.close()
        elif self._owner is not None:
            await self._owner.close()
        if self._client is not None:
            await self._client.aclose()
        self._client = None
        self._owner = None
        self._base_url = None
        self._launch = None
        self._root = None
        self._runtime_secrets.clear()

    async def cancel(self) -> None:
        if self._session is not None:
            await self._session.cancel()

    async def _read_server_url(self, stream: asyncio.StreamReader) -> str:
        for _ in range(100):
            try:
                line = await asyncio.wait_for(read_bounded_line(stream), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            if not line:
                break
            match = _URL.search(line.decode("utf-8"))
            if match is not None:
                return match.group(0)
        raise HarnessStartupError("OpenCode server endpoint was unavailable")

    @staticmethod
    async def _read_bounded_response(response: httpx.Response) -> bytes:
        """Read an HTTP body incrementally, before retaining it for parsing."""

        header = response.headers.get("content-length")
        if header is not None:
            try:
                content_length = int(header)
            except ValueError:
                # An invalid length is not trusted; the streamed bound below
                # remains authoritative.
                content_length = None
            if content_length is not None and content_length > MAX_FRAME_BYTES:
                raise _ResponseTooLarge("response exceeded safe frame size")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > MAX_FRAME_BYTES:
                raise _ResponseTooLarge("response exceeded safe frame size")
            body.extend(chunk)
        return bytes(body)

    async def _send(self, request: HarnessTurnRequest, sequence: int, session_id: str) -> HarnessTurnResult:
        client = self._client
        if client is None:
            raise HarnessAdapterError("OpenCode session is not open")
        turn_started = time.monotonic()
        turn_wall_time = datetime.now(timezone.utc)
        payload = {
            "model": self._model_reference(),
            "parts": [{"type": "text", "text": _text(request.message.model_dump(mode="python"))}],
        }
        try:
            async with client.stream(
                "POST",
                f"/session/{session_id}/message",
                json=payload,
                timeout=request.timeout_seconds,
            ) as response:
                try:
                    response_body = await self._read_bounded_response(response)
                except _ResponseTooLarge:
                    return self._failure_result(
                        sequence,
                        "failed",
                        ErrorCode.PROTOCOL_ERROR,
                        "OpenCode response exceeded safe frame size",
                        status_code=response.status_code,
                        content_type=response.headers.get("content-type"),
                        turn_started=turn_started,
                        turn_wall_time=turn_wall_time,
                    )
                if response.status_code >= 400:
                    return self._failure_result(
                        sequence,
                        "failed",
                        ErrorCode.TRANSPORT_ERROR,
                        "OpenCode turn failed",
                        raw_body=response_body,
                        status_code=response.status_code,
                        content_type=response.headers.get("content-type"),
                        turn_started=turn_started,
                        turn_wall_time=turn_wall_time,
                    )
                try:
                    body = json.loads(response_body)
                except json.JSONDecodeError:
                    return self._failure_result(
                        sequence,
                        "failed",
                        ErrorCode.PROTOCOL_ERROR,
                        "OpenCode response was invalid",
                        raw_body=response_body,
                        status_code=response.status_code,
                        content_type=response.headers.get("content-type"),
                        turn_started=turn_started,
                        turn_wall_time=turn_wall_time,
                    )
        except _ResponseTooLarge:
            return self._failure_result(
                sequence,
                "failed",
                ErrorCode.PROTOCOL_ERROR,
                "OpenCode response exceeded safe frame size",
                turn_started=turn_started,
                turn_wall_time=turn_wall_time,
            )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return self._failure_result(
                sequence,
                "timed_out",
                ErrorCode.TIMEOUT,
                "OpenCode turn timed out",
                turn_started=turn_started,
                turn_wall_time=turn_wall_time,
            )
        except (httpx.HTTPError, OSError, ValueError, TypeError):
            return self._failure_result(
                sequence,
                "failed",
                ErrorCode.TRANSPORT_ERROR,
                "OpenCode response was invalid",
                turn_started=turn_started,
                turn_wall_time=turn_wall_time,
            )
        if not isinstance(body, Mapping):
            return self._failure_result(
                sequence,
                "failed",
                ErrorCode.PROTOCOL_ERROR,
                "OpenCode response was invalid",
                raw_body=response_body,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                turn_started=turn_started,
                turn_wall_time=turn_wall_time,
            )
        info = body.get("info")
        parts = body.get("parts")
        if not isinstance(parts, list) or len(parts) > 256:
            return self._failure_result(
                sequence,
                "failed",
                ErrorCode.PROTOCOL_ERROR,
                "OpenCode response was invalid",
                raw_body=response_body,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                turn_started=turn_started,
                turn_wall_time=turn_wall_time,
            )
        if not isinstance(info, Mapping):
            return self._failure_result(
                sequence,
                "failed",
                ErrorCode.PROTOCOL_ERROR,
                "OpenCode response was invalid",
                raw_body=response_body,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                turn_started=turn_started,
                turn_wall_time=turn_wall_time,
            )
        # Some 1.18 servers return the completed assistant message without
        # intermediate tool parts on POST.  Only then do we read one bounded
        # history page.  The final assistant parent is the authoritative
        # lineage; there is deliberately no pre-request full-history cursor.
        post_has_tool_parts = any(
            isinstance(part, Mapping)
            and part.get("type") in {"tool", "tool-call"}
            for part in parts
        )
        history_after: _HistorySnapshot | None = None
        supplemental_parts: list[Mapping[str, Any]] = []
        history_ambiguous = False
        if not post_has_tool_parts:
            final_parent = info.get("parentID", info.get("parentId"))
            if not isinstance(final_parent, str) or not final_parent:
                history_ambiguous = True
            else:
                get_messages = getattr(client, "get", None)
                remaining = None
                if request.timeout_seconds is not None:
                    remaining = request.timeout_seconds - (time.monotonic() - turn_started)
                    if remaining <= 0:
                        get_messages = None
                if callable(get_messages):
                    history_after = await self._history_snapshot(
                        client, session_id, timeout=remaining, turn_started=turn_started
                    )
                if history_after is None or not history_after.valid:
                    history_ambiguous = True
                if history_after is not None and history_after.valid:
                    candidates: list[Mapping[str, Any]] = []
                    for item in history_after.items:
                        item_info = item.get("info")
                        if not isinstance(item_info, Mapping):
                            continue
                        if (
                            item_info.get("sessionID") != session_id
                            or item_info.get("role") != "assistant"
                            or item_info.get("parentID", item_info.get("parentId"))
                            != final_parent
                        ):
                            continue
                        if isinstance(item.get("parts"), list):
                            candidates.append(item)
                        else:
                            history_ambiguous = True
                    seen_part_ids: set[str] = set()
                    for item in parts:
                        if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                            seen_part_ids.add(item["id"])
                    for candidate in candidates:
                        candidate_parts = candidate.get("parts")
                        if not isinstance(candidate_parts, list):
                            continue
                        for candidate_part in candidate_parts:
                            if not isinstance(candidate_part, Mapping):
                                continue
                            part_id = candidate_part.get("id")
                            if isinstance(part_id, str):
                                if part_id in seen_part_ids:
                                    continue
                                seen_part_ids.add(part_id)
                            if candidate_part.get("type") in {
                                "tool",
                                "tool-call",
                                "reasoning",
                                "text",
                            }:
                                supplemental_parts.append(candidate_part)
                    # A bounded page is incomplete even when it contains a
                    # matching candidate: older same-parent parts may remain
                    # behind the provider cursor.
                    history_ambiguous = history_after.truncated
            parts = [*parts, *supplemental_parts]
        # The v2 response identifies the assistant message and repeats the
        # session identity.  A present malformed/mismatching identity is a
        # protocol failure; it must not be guessed into a successful turn.
        if "sessionID" in info and (
            not isinstance(info["sessionID"], str)
            or not info["sessionID"]
            or info["sessionID"] != session_id
        ):
            return self._failure_result(
                sequence,
                "failed",
                ErrorCode.PROTOCOL_ERROR,
                "OpenCode response was invalid",
                raw_body=response_body,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                turn_started=turn_started,
                turn_wall_time=turn_wall_time,
            )
        text_parts: list[str] = []
        tool_calls_list: list[Mapping[str, Any]] = []
        observations: list[HarnessObservation] = []
        limitations: list[str] = []
        info_wall_time, malformed_info_time = self._source_wall_time(
            info.get("time"), turn_wall_time
        )
        if "time" in info and malformed_info_time:
            limitations.append("capture_incomplete")
        observations.append(
            self._raw_frame_observation(
                sequence,
                response_body,
                response.headers.get("content-type"),
                turn_wall_time,
                turn_started,
            )
        )
        if history_after is not None:
            if history_after.body is not None:
                observations.append(
                    self._raw_frame_observation(
                        sequence,
                        history_after.body,
                        history_after.content_type,
                        turn_wall_time,
                        turn_started,
                        observation_id=f"opencode-history-after-{sequence}",
                    )
                )
            observations.extend(
                self._http_metadata_observations(
                    sequence,
                    history_after.status_code,
                    history_after.content_type,
                    turn_wall_time,
                    turn_started,
                    prefix="http_history_after",
                    start_offset_ms=history_after.start_offset_ms,
                    end_offset_ms=history_after.end_offset_ms,
                )
            )
        observations.extend(
            self._metadata_observations(sequence, info, info_wall_time, turn_started)
        )
        observations.append(
            MetadataObservedObservation(
                observation_id=f"opencode-{sequence}-session",
                harness_kind="opencode",
                turn_sequence=sequence,
                wall_time=turn_wall_time,
                monotonic_offset_ms=self._offset_ms(turn_started),
                name="session_id",
                value=session_id,
            )
        )
        observations.extend(
            self._http_metadata_observations(
                sequence,
                response.status_code,
                response.headers.get("content-type"),
                turn_wall_time,
                turn_started,
            )
        )
        for part_index, part in enumerate(parts):
            if not isinstance(part, Mapping):
                return self._failure_result(
                    sequence,
                    "failed",
                    ErrorCode.PROTOCOL_ERROR,
                    "OpenCode response was invalid",
                    raw_body=response_body,
                    status_code=response.status_code,
                    content_type=response.headers.get("content-type"),
                    turn_started=turn_started,
                    turn_wall_time=turn_wall_time,
                )
            part_wall_time, malformed_part_time = self._source_wall_time(
                part.get("time"), turn_wall_time
            )
            if "time" in part and malformed_part_time:
                limitations.append("capture_incomplete")
            if part.get("type") == "text":
                value = part.get("text")
                if value is None:
                    return self._failure_result(
                        sequence,
                        "failed",
                        ErrorCode.PROTOCOL_ERROR,
                        "OpenCode response was invalid",
                        raw_body=response_body,
                        status_code=response.status_code,
                        content_type=response.headers.get("content-type"),
                        turn_started=turn_started,
                        turn_wall_time=turn_wall_time,
                    )
                if not isinstance(value, str):
                    return self._failure_result(
                        sequence,
                        "failed",
                        ErrorCode.PROTOCOL_ERROR,
                        "OpenCode response was invalid",
                        raw_body=response_body,
                        status_code=response.status_code,
                        content_type=response.headers.get("content-type"),
                        turn_started=turn_started,
                        turn_wall_time=turn_wall_time,
                    )
                if value:
                    text_parts.append(value)
                    observations.append(
                        self._message_observation(
                            sequence,
                            value,
                            part,
                            part_index,
                            part_wall_time,
                            turn_started,
                            info.get("id", _MISSING_IDENTIFIER),
                        )
                    )
                continue
            if part.get("type") in ("reasoning", "thinking"):
                reasoning = self._reasoning_observation(
                    sequence, part, part_index, part_wall_time, turn_started
                )
                if reasoning is None:
                    limitations.append("capture_incomplete")
                else:
                    observations.append(reasoning)
                continue
            if part.get("type") not in ("tool", "tool-call"):
                continue
            name = part.get("tool")
            if name is None:
                name = part.get("name")
            call_id = part.get("callID")
            if call_id is None:
                call_id = part.get("callId")
            if call_id is None:
                call_id = part.get("id")
            if not isinstance(name, str) or not name or not isinstance(call_id, str) or not call_id:
                limitations.append("capture_incomplete")
                continue
            server_name, tool_name = self._normalize_tool_name(name)
            state = part.get("state")
            state_mapping = state if isinstance(state, Mapping) else {}
            arguments_present = (
                "arguments" in part
                or "input" in part
                or "input" in state_mapping
            )
            arguments = part.get("arguments", part.get("input", state_mapping.get("input")))
            status = self._tool_status(state_mapping, part)
            result_present = "output" in state_mapping or "result" in state_mapping
            result_value = state_mapping.get("output", state_mapping.get("result"))
            error_present = "error" in state_mapping or "error" in part
            error_value = state_mapping.get("error", part.get("error"))
            if status == "success" and not result_present and not error_present:
                # A completed state without output is malformed, not a
                # successful tool invocation with an invented null result.
                status = "incomplete"
            try:
                call_kwargs: dict[str, Any] = {}
                if arguments_present:
                    call_kwargs["arguments"] = arguments
                if status is not None:
                    call_kwargs["status"] = status
                observations.append(
                    ToolCallObservedObservation(
                        observation_id=f"opencode-{session_id}-{sequence}-call-{len(tool_calls_list)}",
                        harness_kind="opencode",
                        turn_sequence=sequence,
                        wall_time=part_wall_time,
                        monotonic_offset_ms=self._offset_ms(turn_started),
                        call_id=call_id,
                        server=server_name,
                        tool=tool_name,
                        **call_kwargs,
                    )
                )
            except (TypeError, ValueError):
                return self._failure_result(
                    sequence,
                    "failed",
                    ErrorCode.PROTOCOL_ERROR,
                    "OpenCode response was invalid",
                    raw_body=response_body,
                    status_code=response.status_code,
                    content_type=response.headers.get("content-type"),
                    turn_started=turn_started,
                    turn_wall_time=turn_wall_time,
                )
            if result_present or error_present or status is not None:
                try:
                    result_kwargs: dict[str, Any] = {}
                    if result_present:
                        result_kwargs["result"] = result_value
                    if status in {"tool_error", "protocol_error"}:
                        result_kwargs["is_error"] = True
                    if status is not None:
                        result_kwargs["status"] = status
                    if isinstance(error_value, str):
                        result_kwargs["error_message"] = error_value
                    observations.append(
                        ToolResultObservedObservation(
                            observation_id=f"opencode-{session_id}-{sequence}-result-{len(tool_calls_list)}",
                            harness_kind="opencode",
                            turn_sequence=sequence,
                            wall_time=part_wall_time,
                            monotonic_offset_ms=self._offset_ms(turn_started),
                            call_id=call_id,
                            **result_kwargs,
                        )
                    )
                except (TypeError, ValueError):
                    return self._failure_result(
                        sequence,
                        "failed",
                        ErrorCode.PROTOCOL_ERROR,
                        "OpenCode response was invalid",
                        raw_body=response_body,
                        status_code=response.status_code,
                        content_type=response.headers.get("content-type"),
                        turn_started=turn_started,
                        turn_wall_time=turn_wall_time,
                    )
            call_record: dict[str, Any] = {"tool": tool_name, "call_id": call_id}
            if server_name is not None:
                call_record["server"] = server_name
            tool_calls_list.append(call_record)
        if history_ambiguous:
            limitations.append("capture_incomplete")
        if limitations and not history_ambiguous:
            return self._failure_result(
                sequence,
                "failed",
                ErrorCode.PROTOCOL_ERROR,
                "OpenCode response was invalid",
                raw_body=response_body,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                turn_started=turn_started,
                turn_wall_time=turn_wall_time,
            )
        raw_error_flag = body.get("is_error", body.get("isError"))
        malformed_error_flag = raw_error_flag is not None and not isinstance(
            raw_error_flag, bool
        )
        if malformed_error_flag:
            return self._failure_result(
                sequence,
                "failed",
                ErrorCode.PROTOCOL_ERROR,
                "OpenCode response was invalid",
                raw_body=response_body,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                turn_started=turn_started,
                turn_wall_time=turn_wall_time,
            )
        text = "".join(text_parts)
        redaction = RedactionConfig.from_environment(secrets=self._runtime_secrets)
        if text:
            try:
                safe_text = redact_for_api(text, config=redaction)
                text = safe_text if isinstance(safe_text, str) else ""
            except Exception:
                text = "[REDACTED]"
        tool_calls = tuple(tool_calls_list)
        failed = (
            isinstance(body.get("is_error", body.get("isError", False)), bool)
            and bool(body.get("is_error", body.get("isError", False)))
        ) or bool(info.get("error") or info.get("finish") in ("error", "failed"))
        if failed:
            observations.append(
                MetadataObservedObservation(
                    observation_id=f"opencode-{session_id}-{sequence}-error",
                    harness_kind="opencode",
                    turn_sequence=sequence,
                    wall_time=turn_wall_time,
                    monotonic_offset_ms=self._offset_ms(turn_started),
                    name="error",
                    value=True,
                )
            )
        evidence: dict[str, Any] = {"process_observed": False, "transport_observed": "http", "content_observed": bool(text), "tool_calls_observed": bool(tool_calls), "mcp_traffic_observed": bool(tool_calls), "usage_requested": True, "usage_enforced": False}
        if isinstance(info, Mapping):
            tokens = info.get("tokens")
            if isinstance(tokens, Mapping):
                numeric_tokens = {
                    key: value
                    for key, value in tokens.items()
                    if isinstance(key, str) and key in _TOKEN_COUNTERS
                    and isinstance(value, (int, float)) and not isinstance(value, bool)
                }
                cache = tokens.get("cache")
                if isinstance(cache, Mapping):
                    for key in ("read", "write"):
                        value = cache.get(key)
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            numeric_tokens[f"cache_{key}"] = value
                if numeric_tokens:
                    evidence["tokens_observed"] = True
                    for key, value in numeric_tokens.items():
                        evidence[f"tokens_{key}"] = value
            cost = info.get("cost")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool): evidence["cost_observed"] = cost
            provider_id = self._safe_evidence_identifier(info.get("providerID"), redaction)
            model_id = self._safe_evidence_identifier(info.get("modelID"), redaction)
            if provider_id is not None: evidence["provider_observed"] = provider_id
            if model_id is not None: evidence["model_observed"] = model_id
        usage_present = "usage" in body or (isinstance(info, Mapping) and "tokens" in info)
        evidence.update({"usage_observed": usage_present, "usage_state": "observed" if usage_present else "unavailable", "usage_unavailable": not usage_present})
        turn_status: _TurnStatus = "failed" if failed else "completed"
        return HarnessTurnResult(
            sequence=sequence,
            status=turn_status,
            response=None if failed or not text else TurnResponse(content=(TextContent(text=text),)),
            error=None if not failed else ErrorInfo(code=ErrorCode.TRANSPORT_ERROR, message="OpenCode turn failed"),
            tool_calls=tool_calls,
            evidence=evidence,
            trace_limitations=tuple(dict.fromkeys(limitations)),
            turn_evidence=TurnEvidence(
                sequence=sequence,
                status=turn_status,
                observations=tuple(observations),
                limitations=tuple(dict.fromkeys(limitations)),
            ),
        )

    def _normalize_tool_name(self, value: str) -> tuple[str | None, str]:
        """Translate OpenCode's ``server_tool`` display name to MCP identity.

        The launch configuration is the only authority used here. An
        unrecognized provider name is retained verbatim rather than guessed.
        """

        launch = self._launch
        configurations = launch.configurations if launch is not None else ()
        keys = sorted(
            {
                configuration.key
                for configuration in configurations
                if isinstance(configuration.key, str) and configuration.key
            },
            key=len,
            reverse=True,
        )
        for server in keys:
            prefix = f"{server}_"
            if value.startswith(prefix) and len(value) > len(prefix):
                return server, value[len(prefix) :]
        return None, value

    @staticmethod
    def _offset_ms(started: float) -> float:
        return max(0.0, (time.monotonic() - started) * 1000.0)

    @staticmethod
    def _source_wall_time(value: object, fallback: datetime) -> tuple[datetime, bool]:
        """Use official OpenCode time fields when valid; otherwise receipt time."""

        if value is None:
            return fallback, False
        candidate: object = value
        if isinstance(value, Mapping):
            candidate = next(
                (value[key] for key in ("created", "start", "started", "updated") if key in value),
                None,
            )
        if isinstance(candidate, datetime):
            if candidate.tzinfo is not None and candidate.utcoffset() is not None:
                return candidate.astimezone(timezone.utc), False
            return fallback, True
        if isinstance(candidate, str):
            try:
                parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            except ValueError:
                return fallback, True
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return fallback, True
            return parsed.astimezone(timezone.utc), False
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool) and isfinite(candidate) and candidate >= 0:
            seconds = candidate / 1000.0 if candidate > 100_000_000_000 else float(candidate)
            try:
                return datetime.fromtimestamp(seconds, tz=timezone.utc), False
            except (OverflowError, OSError, ValueError):
                pass
        return fallback, True

    @staticmethod
    async def _history_snapshot(
        client: Any,
        session_id: str,
        *,
        timeout: float | None = None,
        turn_started: float | None = None,
    ) -> _HistorySnapshot | None:
        """Read one bounded history page without trusting its data."""

        get_messages = getattr(client, "get", None)
        if not callable(get_messages):
            return None
        request_started = time.monotonic()

        def snapshot(
            items: tuple[Mapping[str, Any], ...],
            body: bytes | None,
            status_code: int | None,
            content_type: object,
            truncated: bool,
            valid: bool,
        ) -> _HistorySnapshot:
            request_ended = time.monotonic()
            base = turn_started if turn_started is not None else request_started
            return _HistorySnapshot(
                items,
                body,
                status_code,
                content_type,
                truncated,
                valid,
                max(0.0, (request_started - base) * 1000.0),
                max(0.0, (request_ended - base) * 1000.0),
            )

        try:
            response = await get_messages(
                f"/session/{session_id}/message",
                params={"limit": 256},
                timeout=timeout,
            )
            status_code = response.status_code
            content_type = response.headers.get("content-type")
            try:
                raw = await OpenCodeHarnessAdapter._read_bounded_response(response)
            except _ResponseTooLarge:
                return snapshot((), None, status_code, content_type, True, False)
            if status_code < 200 or status_code >= 300:
                return snapshot((), raw, status_code, content_type, False, False)
            try:
                decoded = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return snapshot((), raw, status_code, content_type, False, False)
            if not isinstance(decoded, list) or any(
                not isinstance(item, Mapping) for item in decoded
            ):
                return snapshot((), raw, status_code, content_type, False, False)
            next_cursor = bool(
                response.headers.get("x-next-cursor")
                or response.headers.get("link")
            )
            return snapshot(
                tuple(decoded), raw, status_code, content_type, next_cursor, True
            )
        except (httpx.HTTPError, OSError, ValueError, TypeError):
            return None

    def _raw_frame_observation(
        self,
        sequence: int,
        body: bytes,
        content_type: object,
        wall_time: datetime,
        started: float,
        observation_id: str | None = None,
    ) -> RawFrameObservation:
        media_type = (
            content_type
            if isinstance(content_type, str)
            and content_type
            and len(content_type) <= 256
            and all(ord(char) >= 0x20 for char in content_type)
            else "application/json"
        )
        try:
            text = body.decode("utf-8")
            safe = redact_for_api(text, config=RedactionConfig.from_environment(secrets=self._runtime_secrets))
            if not isinstance(safe, str):
                raise ValueError
            return RawFrameObservation(
                observation_id=observation_id or f"opencode-http-{sequence}",
                harness_kind="opencode",
                turn_sequence=sequence,
                wall_time=wall_time,
                monotonic_offset_ms=self._offset_ms(started),
                direction="inbound",
                media_type=media_type,
                text=safe,
                raw_evidence=RawEvidenceInput(content=safe, media_type=media_type),
            )
        except (UnicodeDecodeError, TypeError, ValueError):
            encoded = base64.b64encode(body).decode("ascii")
            return RawFrameObservation(
                observation_id=observation_id or f"opencode-http-{sequence}",
                harness_kind="opencode",
                turn_sequence=sequence,
                wall_time=wall_time,
                monotonic_offset_ms=self._offset_ms(started),
                direction="inbound",
                media_type=media_type,
                raw_evidence=RawEvidenceInput(
                    content=encoded, media_type=media_type, encoding="base64"
                ),
            )

    def _metadata_observations(
        self,
        sequence: int,
        info: Mapping[str, Any],
        wall_time: datetime,
        started: float,
    ) -> tuple[HarnessObservation, ...]:
        values: list[HarnessObservation] = []
        for name, key in (
            ("provider", "providerID"),
            ("model", "modelID"),
            ("finish", "finish"),
            ("message_id", "id"),
            ("session_id", "sessionID"),
            ("role", "role"),
        ):
            if key not in info:
                continue
            value = info[key]
            emitted: str | dict[str, str] = (
                value if _valid_identifier(value) else dict(_MALFORMED_MARKER)
            )
            values.append(
                MetadataObservedObservation(
                    observation_id=f"opencode-{sequence}-{name}",
                    harness_kind="opencode",
                    turn_sequence=sequence,
                    wall_time=wall_time,
                    monotonic_offset_ms=self._offset_ms(started),
                    name=name,
                    value=cast(Any, emitted),
                )
            )
        for name in ("time", "error"):
            if name in info:
                safe_value = self._safe_metadata_value(info[name])
                if safe_value is _INVALID_METADATA:
                    safe_value = dict(_MALFORMED_MARKER)
                if safe_value is not _INVALID_METADATA:
                    values.append(
                        MetadataObservedObservation(
                            observation_id=f"opencode-{sequence}-{name}",
                            harness_kind="opencode",
                            turn_sequence=sequence,
                            wall_time=wall_time,
                            monotonic_offset_ms=self._offset_ms(started),
                            name=f"info_{name}",
                            value=safe_value,
                        )
                    )
        tokens = info.get("tokens")
        raw_cost = info.get("cost")
        if "tokens" in info or "cost" in info or "currency" in info:
            kwargs: dict[str, Any] = {}
            token_values = tokens if isinstance(tokens, Mapping) else {}
            if "tokens" in info and not isinstance(tokens, Mapping):
                values.append(self._metadata_marker(sequence, "tokens", wall_time, started))
            for source, target in (
                ("input", "input_tokens"),
                ("output", "output_tokens"),
                ("reasoning", "reasoning_tokens"),
                ("cache_creation", "cache_creation_tokens"),
                ("total", "total_tokens"),
            ):
                value = token_values.get(source)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    kwargs[target] = value
                elif isinstance(tokens, Mapping) and source in tokens:
                    values.append(
                        self._metadata_marker(
                            sequence, target, wall_time, started
                        )
                    )
            cache = token_values.get("cache")
            if isinstance(cache, Mapping):
                for source, target in (("read", "cache_read_tokens"), ("write", "cache_write_tokens")):
                    value = cache.get(source)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        kwargs[target] = value
                    elif source in cache:
                        values.append(
                            self._metadata_marker(
                                sequence, target, wall_time, started
                            )
                        )
            cost = raw_cost
            if (
                isinstance(cost, (int, float))
                and not isinstance(cost, bool)
                and cost >= 0
                and isfinite(cost)
            ):
                kwargs["cost"] = cost
            elif "cost" in info:
                values.append(self._metadata_marker(sequence, "cost", wall_time, started))
            currency = info.get("currency")
            if isinstance(currency, str) and currency:
                kwargs["currency"] = currency[:16]
            elif "currency" in info:
                values.append(self._metadata_marker(sequence, "currency", wall_time, started))
            if kwargs:
                values.append(
                    UsageObservedObservation(
                        observation_id=f"opencode-{sequence}-usage",
                        harness_kind="opencode",
                        turn_sequence=sequence,
                        wall_time=wall_time,
                        monotonic_offset_ms=self._offset_ms(started),
                        **kwargs,
                    )
                )
        return tuple(values)

    @staticmethod
    def _metadata_marker(
        sequence: int, name: str, wall_time: datetime, started: float
    ) -> MetadataObservedObservation:
        return MetadataObservedObservation(
            observation_id=f"opencode-{sequence}-invalid-{name}",
            harness_kind="opencode",
            turn_sequence=sequence,
            wall_time=wall_time,
            monotonic_offset_ms=max(0.0, (time.monotonic() - started) * 1000.0),
            name=f"usage_{name}_state",
            value=dict(_MALFORMED_MARKER),
        )

    def _http_metadata_observations(
        self,
        sequence: int,
        status_code: int | None,
        content_type: object,
        wall_time: datetime,
        started: float,
        prefix: str = "http",
        start_offset_ms: float | None = None,
        end_offset_ms: float | None = None,
    ) -> tuple[HarnessObservation, ...]:
        """Expose only safe, stable HTTP lifecycle facts (never headers/URLs)."""

        values: list[HarnessObservation] = []
        fields: list[tuple[str, Any]] = [
            (f"{prefix}.method", "GET" if prefix.startswith("http_history") else "POST"),
            (f"{prefix}.route", "/session/{session_id}/message"),
        ]
        if status_code is not None and not isinstance(status_code, bool):
            fields.append((f"{prefix}.status_code", status_code))
        if isinstance(content_type, str) and 0 < len(content_type) <= 128 and all(
            ord(char) >= 0x20 and ord(char) != 0x7F for char in content_type
        ):
            fields.append((f"{prefix}.content_type", content_type.split(";", 1)[0].strip()))
        elapsed = self._offset_ms(started)
        start = 0.0 if start_offset_ms is None else max(0.0, start_offset_ms)
        end = elapsed if end_offset_ms is None else max(start, end_offset_ms)
        fields.extend(
            ((f"{prefix}.start_offset_ms", start), (f"{prefix}.end_offset_ms", end), (f"{prefix}.duration_ms", end - start))
        )
        for index, (name, value) in enumerate(fields):
            values.append(
                MetadataObservedObservation(
                    observation_id=f"opencode-{sequence}-{prefix}-{index}",
                    harness_kind="opencode",
                    turn_sequence=sequence,
                    wall_time=wall_time,
                    monotonic_offset_ms=elapsed,
                    name=name,
                    value=value,
                )
            )
        return tuple(values)

    def _safe_metadata_value(self, value: object) -> Any:
        """Redact and JSON-validate provider metadata before it crosses R5."""

        try:
            safe = redact_for_api(value, config=RedactionConfig.from_environment(secrets=self._runtime_secrets))
            json.dumps(safe, ensure_ascii=False, allow_nan=False)
            return safe
        except (TypeError, ValueError, OverflowError):
            return _INVALID_METADATA

    @staticmethod
    def _message_observation(
        sequence: int,
        text: str,
        part: Mapping[str, Any],
        index: int,
        wall_time: datetime,
        started: float,
        fallback_message_id: object = None,
    ) -> MessageChunkObservation:
        message_id = part.get("id", fallback_message_id)
        message_kwargs: dict[str, Any] = {}
        if "id" in part or fallback_message_id is not _MISSING_IDENTIFIER:
            # None is intentional here: the sink preserves the field's
            # presence so TraceView can report malformed-present as unavailable.
            message_kwargs["message_id"] = message_id if _valid_identifier(message_id) else None
        return MessageChunkObservation(
            observation_id=f"opencode-{sequence}-message-{index}",
            harness_kind="opencode",
            turn_sequence=sequence,
            wall_time=wall_time,
            monotonic_offset_ms=max(0.0, (time.monotonic() - started) * 1000.0),
            role="assistant",
            text=text,
            complete=True,
            **message_kwargs,
        )

    @staticmethod
    def _reasoning_observation(
        sequence: int,
        part: Mapping[str, Any],
        index: int,
        wall_time: datetime,
        started: float,
    ) -> ReasoningChunkObservation | None:
        visibility = "visible"
        if part.get("encrypted") is True or part.get("signature") is not None:
            visibility = "encrypted"
            text = None
        elif part.get("hidden") is True:
            visibility = "provider_hidden"
            text = None
        else:
            text = part.get("text")
            if text is not None and not isinstance(text, str):
                return None
        return ReasoningChunkObservation(
            observation_id=f"opencode-{sequence}-reasoning-{index}",
            harness_kind="opencode",
            turn_sequence=sequence,
            wall_time=wall_time,
            monotonic_offset_ms=max(0.0, (time.monotonic() - started) * 1000.0),
            text=text,
            visibility=visibility,  # type: ignore[arg-type]
            complete=True,
        )

    @staticmethod
    def _tool_status(
        state: Mapping[str, Any], part: Mapping[str, Any]
    ) -> _ToolStatus | None:
        raw = state.get("status", part.get("status"))
        if not isinstance(raw, str):
            return None
        lowered = raw.lower()
        if lowered in {"completed", "complete", "success", "succeeded", "done"}:
            return "success"
        if lowered in {"error", "failed", "failure"}:
            return "tool_error"
        if lowered in {"cancelled", "canceled"}:
            return "cancelled"
        if lowered in {"timed_out", "timeout"}:
            return "timed_out"
        if lowered in {"running", "pending", "started"}:
            return "incomplete"
        return "incomplete"

    def _failure_result(
        self,
        sequence: int,
        status: _TurnStatus,
        code: ErrorCode,
        message: str,
        *,
        raw_body: bytes | None = None,
        status_code: int | None = None,
        content_type: object = None,
        turn_started: float,
        turn_wall_time: datetime,
    ) -> HarnessTurnResult:
        observations: list[HarnessObservation] = []
        if raw_body is not None:
            observations.append(
                self._raw_frame_observation(
                    sequence,
                    raw_body,
                    content_type,
                    turn_wall_time,
                    turn_started,
                )
            )
        observations.extend(
            self._http_metadata_observations(
                sequence,
                status_code,
                content_type,
                turn_wall_time,
                turn_started,
            )
        )
        limitation = "capture_incomplete" if raw_body is None else ""
        limitations = (limitation,) if limitation else ()
        return HarnessTurnResult(
            sequence=sequence,
            status=status,
            error=ErrorInfo(code=code, message=message),
            evidence={
                "process_observed": False,
                "transport_observed": "http",
                "usage_state": "unavailable",
                **({"http_status_code": status_code} if status_code is not None else {}),
            },
            trace_limitations=limitations,
            turn_evidence=TurnEvidence(
                sequence=sequence,
                status=status,
                observations=tuple(observations),
                limitations=limitations,
            ),
        )

    @staticmethod
    def _safe_evidence_identifier(value: object, config: RedactionConfig) -> str | None:
        if not _valid_identifier(value):
            return None
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
            return None
        try:
            projected = redact_for_api(value, config=config)
        except Exception:
            return None
        return projected if isinstance(projected, str) and projected == value else None

    def _model_reference(self, harness: Any | None = None) -> dict[str, str]:
        harness = harness if harness is not None else (self._launch.spec.harness if self._launch is not None else None)
        model = getattr(harness, "model", "")
        provider = getattr(harness, "provider", None)
        qualified_provider = None
        if "/" in model:
            qualified_provider, model = model.split("/", 1)
        if provider is not None and qualified_provider is not None and provider != qualified_provider:
            raise HarnessAdapterError("OpenCode provider and model do not agree")
        if provider is None and qualified_provider is not None:
            provider = qualified_provider
        if not model:
            raise HarnessAdapterError("OpenCode provider and model are required")
        # OpenCode's built-in provider is the stable default for simple local
        # fixtures; real launches should provide an explicit provider or use
        # the documented ``provider/model`` form.
        provider = provider or "opencode"
        return {"providerID": provider, "modelID": model}


class OpenCodeSession(NativeSessionBase):
    def __init__(self, adapter: OpenCodeHarnessAdapter, owner: ProcessOwner, client: httpx.AsyncClient, capabilities: HarnessAdapterCapabilities, session_id: str) -> None:
        super().__init__(
            owner,
            capabilities,
            session_id,
            server_configuration_count=len(adapter._launch.configurations) if adapter._launch is not None else 0,
            capture=adapter._launch.capture if adapter._launch is not None else None,
        )
        self._adapter = adapter
        self._client = client

    async def send(self, request: HarnessTurnRequest) -> HarnessTurnResult:
        if self._closed:
            raise HarnessAdapterError("OpenCode session is closed")
        self._turns += 1
        return await self._adapter._send(request, self._turns, self.session_id)

    async def close(self) -> None:
        if self._closed:
            return
        try:
            await self._client.delete(f"/session/{self.session_id}")
        except httpx.HTTPError:
            # Process cleanup remains authoritative; a missing delete endpoint
            # is recorded as an incomplete server-side session cleanup.
            pass
        await super().close()


__all__ = ["OpenCodeHarnessAdapter", "OpenCodeSession"]
