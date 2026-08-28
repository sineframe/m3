"""OpenCode serve/session harness adapter.

One isolated ``opencode serve`` process and one HTTP session are retained for
the lifetime of the adapter.  The adapter never falls back to one-shot
``run`` invocations or copies ambient OpenCode configuration.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
import re
import shutil
import os
import subprocess
import tempfile
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import httpx

from ..agent_session import AdapterTurn
from ..trace.redaction import RedactionConfig, is_sensitive_key, redact_for_api
from ..policy import ToolDescriptor, ToolPolicyEvaluator, ToolPolicyEvidence
from ..types import Capability, CapabilityStatus, ErrorCode, ErrorInfo, FullToolPolicy, NativeToolPolicy, OpenCode, Readiness, RestrictiveToolPolicy, SecretReference, TextContent, TurnResponse, UserMessage
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
    _text,
    probe_help,
    read_bounded_line,
    _server_configuration,
    workspace_for_launch,
)


_URL = re.compile(r"https?://(?:127\.0\.0\.1|localhost|\[::1\]):\d+")
_DIALECT_PROBE_CONTROL_KEYS = frozenset({"MCP_PAL_OPENCODE_MODE", "MCP_PAL_PROBE_MARKER", "MCP_PAL_VERSION_MARKER"})
_TOKEN_COUNTERS = frozenset({"input", "output", "reasoning", "cache_read", "cache_write"})


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
                if response.status_code >= 400:
                    return HarnessTurnResult(
                        sequence=sequence,
                        status="failed",
                        error=ErrorInfo(code=ErrorCode.TRANSPORT_ERROR, message="OpenCode turn failed"),
                        evidence={"process_observed": True, "transport_observed": "http", "usage_state": "unavailable"},
                    )
                try:
                    body = json.loads(await self._read_bounded_response(response))
                except json.JSONDecodeError:
                    return HarnessTurnResult(
                        sequence=sequence,
                        status="failed",
                        error=ErrorInfo(code=ErrorCode.PROTOCOL_ERROR, message="OpenCode response was invalid"),
                        evidence={"process_observed": True, "transport_observed": "http", "usage_state": "unavailable"},
                    )
        except _ResponseTooLarge:
            return HarnessTurnResult(
                sequence=sequence,
                status="failed",
                error=ErrorInfo(code=ErrorCode.PROTOCOL_ERROR, message="OpenCode response exceeded safe frame size"),
                evidence={"process_observed": True, "transport_observed": "http", "usage_state": "unavailable"},
            )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return HarnessTurnResult(sequence=sequence, status="timed_out", error=ErrorInfo(code=ErrorCode.TIMEOUT, message="OpenCode turn timed out"), evidence={"process_observed": True, "transport_observed": "http", "usage_state": "unavailable"})
        except (httpx.HTTPError, OSError, ValueError, TypeError):
            return HarnessTurnResult(sequence=sequence, status="failed", error=ErrorInfo(code=ErrorCode.TRANSPORT_ERROR, message="OpenCode response was invalid"), evidence={"process_observed": True, "transport_observed": "http", "usage_state": "unavailable"})
        if not isinstance(body, Mapping):
            return HarnessTurnResult(sequence=sequence, status="failed", error=ErrorInfo(code=ErrorCode.PROTOCOL_ERROR, message="OpenCode response was invalid"), evidence={"process_observed": True, "transport_observed": "http", "usage_state": "unavailable"})
        info = body.get("info")
        parts = body.get("parts")
        if not isinstance(parts, list) or len(parts) > 256:
            return HarnessTurnResult(sequence=sequence, status="failed", error=ErrorInfo(code=ErrorCode.PROTOCOL_ERROR, message="OpenCode response was invalid"), evidence={"process_observed": True, "transport_observed": "http"})
        if not isinstance(info, Mapping):
            return HarnessTurnResult(sequence=sequence, status="failed", error=ErrorInfo(code=ErrorCode.PROTOCOL_ERROR, message="OpenCode response was invalid"), evidence={"process_observed": True, "transport_observed": "http"})
        text_parts: list[str] = []
        tool_calls_list: list[Mapping[str, Any]] = []
        for part in parts:
            if not isinstance(part, Mapping):
                return HarnessTurnResult(sequence=sequence, status="failed", error=ErrorInfo(code=ErrorCode.PROTOCOL_ERROR, message="OpenCode response was invalid"), evidence={"process_observed": True, "transport_observed": "http"})
            if part.get("type") == "text":
                value = part.get("text")
                if value is None:
                    continue
                if not isinstance(value, str):
                    return HarnessTurnResult(sequence=sequence, status="failed", error=ErrorInfo(code=ErrorCode.PROTOCOL_ERROR, message="OpenCode response was invalid"), evidence={"process_observed": True, "transport_observed": "http"})
                if value:
                    text_parts.append(value)
                continue
            if part.get("type") not in ("tool", "tool-call"):
                continue
            name = part.get("tool") or part.get("name")
            call_id = part.get("callID") or part.get("callId") or part.get("id")
            if not isinstance(name, str) or not name or not isinstance(call_id, str) or not call_id:
                return HarnessTurnResult(sequence=sequence, status="failed", error=ErrorInfo(code=ErrorCode.PROTOCOL_ERROR, message="OpenCode response was invalid"), evidence={"process_observed": True, "transport_observed": "http"})
            tool_calls_list.append({"tool": name, "call_id": call_id})
        text = "".join(text_parts)
        redaction = RedactionConfig.from_environment(secrets=self._runtime_secrets)
        if text:
            try:
                safe_text = redact_for_api(text, config=redaction)
                text = safe_text if isinstance(safe_text, str) else ""
            except Exception:
                text = "[REDACTED]"
        tool_calls = tuple(tool_calls_list)
        failed = bool(body.get("is_error", body.get("isError", False))) or bool(info and (info.get("error") or info.get("finish") in ("error", "failed")))
        evidence: dict[str, Any] = {"process_observed": True, "transport_observed": "http", "content_observed": bool(text), "tool_calls_observed": bool(tool_calls), "mcp_traffic_observed": bool(tool_calls), "usage_requested": True, "usage_enforced": False}
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
        return HarnessTurnResult(
            sequence=sequence,
            status="failed" if failed else "completed",
            response=None if failed or not text else TurnResponse(content=(TextContent(text=text),)),
            error=None if not failed else ErrorInfo(code=ErrorCode.TRANSPORT_ERROR, message="OpenCode turn failed"),
            tool_calls=tool_calls,
            evidence=evidence,
        )

    @staticmethod
    def _safe_evidence_identifier(value: object, config: RedactionConfig) -> str | None:
        if not isinstance(value, str) or not value or len(value) > 256:
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
