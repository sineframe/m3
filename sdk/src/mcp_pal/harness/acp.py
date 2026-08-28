"""ACP v1 subprocess runner backed by the official ``agent-client-protocol`` SDK."""
from __future__ import annotations
import asyncio, os, shutil, tempfile, time, sys, json
from dataclasses import replace
from contextlib import asynccontextmanager
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlsplit
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Protocol, cast
from acp.client.connection import ClientSideConnection
from acp.schema import AcpMcpServer
from acp.connection import StreamDirection
from acp.schema import (
    AcceptElicitationResponse,
    AllowedOutcome,
    ClientCapabilities,
    CreateElicitationResponse,
    CreateTerminalResponse,
    DeclineElicitationResponse,
    DeniedOutcome,
    EnvVariable,
    HttpHeader,
    HttpMcpServer,
    Implementation,
    KillTerminalResponse,
    McpServerStdio,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    SseMcpServer,
    TerminalOutputResponse,
    TextContentBlock,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)
from ..trace.claude import transport_for_server
from ..transport.http_proxy import McpHttpProxy
from ..transport.capture_proxy import write_stdio_handoff
from ..trace.capture import read_capture
from ..trace.redaction import is_sensitive_key, known_secret_values
from .native import workspace_for_launch
from .base import AcpRunSpec, HarnessResult
from .process_group import terminate_process_group
from ..interaction_handlers import (
    ElicitationRequest,
    FilesystemRequest,
    InteractionController,
    PermissionRequest,
    SamplingRequest,
    TerminalRequest,
)
from ..types import FullToolPolicy, NativeToolPolicy, RestrictiveToolPolicy, SecretReference
from ..policy import ToolDescriptor, ToolPolicyEvaluator, ToolPolicyEvidence


_STDERR_LIMIT = 64 * 1024
_STDOUT_LIMIT = 4 * 1024 * 1024


class _AcpMalformedStdout(RuntimeError):
    pass


class _AcpEarlyExit(RuntimeError):
    pass


class _AcpCancelled(RuntimeError):
    pass


class _AcpTimedOut(RuntimeError):
    pass


class _AcpTransport:
    """Small bounded NDJSON transport with parse/EOF visibility.

    The SDK's stock transport intentionally logs and skips malformed lines,
    which makes a broken agent indistinguishable from a hung one.  Keeping
    this transport local still uses the official ClientSideConnection while
    allowing the runtime to classify malformed stdout and early exits.
    """
    def __init__(
        self,
        process: Any,
        on_malformed: Callable[[str], None],
        on_fault: Callable[[BaseException], None] | None = None,
    ) -> None:
        self.process = process
        self.on_malformed = on_malformed
        self.on_fault = on_fault
        self.closed = False
        self._frame_buffer = bytearray()

    async def send(self, message: Mapping[str, Any]) -> None:
        if self.closed or self.process.stdin is None:
            raise ConnectionError("ACP stdin closed")
        data = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        if len(data) > _STDOUT_LIMIT:
            raise _AcpMalformedStdout("ACP outbound frame exceeds bounded limit")
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    async def receive(self) -> dict[str, Any] | None:
        if self.process.stdout is None:
            return None
        while True:
            newline = self._frame_buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._frame_buffer[:newline])
                del self._frame_buffer[: newline + 1]
                break
            if len(self._frame_buffer) >= _STDOUT_LIMIT:
                raise _AcpMalformedStdout("ACP stdout frame exceeds bounded limit")
            chunk = await self.process.stdout.read(min(8192, _STDOUT_LIMIT - len(self._frame_buffer)))
            if not chunk:
                # EOF before the caller completes its expected lifecycle is
                # an early exit even when the child chose status 0.
                try: await self.process.wait()
                except Exception: pass
                if self._frame_buffer:
                    raise _AcpMalformedStdout("ACP stdout frame is missing a newline")
                error: BaseException = _AcpEarlyExit(f"process exited with code {self.process.returncode}")
                if self.on_fault: self.on_fault(error)
                raise error
            self._frame_buffer.extend(chunk)
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.on_malformed(line[:512].decode("utf-8", errors="replace"))
            error = _AcpMalformedStdout("malformed ACP stdout")
            if self.on_fault: self.on_fault(error)
            raise error from exc
        if not isinstance(value, dict):
            self.on_malformed(line[:512].decode("utf-8", errors="replace"))
            error = _AcpMalformedStdout("ACP stdout frame must be an object")
            if self.on_fault: self.on_fault(error)
            raise error
        return value

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
                await self.process.stdin.wait_closed()
            except (AttributeError, OSError, RuntimeError):
                pass


async def _drain_stderr(reader: Any, limit: int = _STDERR_LIMIT) -> bytes:
    # Keep the historical private helper for callers/tests while sharing the
    # drain-to-EOF implementation with the native process owner.
    from .native import drain_bounded

    return await drain_bounded(reader, maximum=limit)


def _manifest_env(manifest: dict[str, Any]) -> tuple[dict[str, str], set[str]]:
    env = os.environ.copy()
    secrets: set[str] = set(known_secret_values())
    for child, ref in (manifest.get("env") or {}).items():
        if not isinstance(ref, str) or not (ref.startswith("${") and ref.endswith("}")):
            raise ValueError("acp_manifest_invalid: env values must be references")
        key = ref[2:-1]
        value = os.environ.get(key)
        if value is None:
            raise ValueError(f"acp_environment_missing: {key}")
        env[child] = value
        secrets.update((ref, value))
    return env, secrets


def _selected_env(server: dict[str, Any]) -> tuple[list[dict[str, str]], set[str]]:
    values: list[dict[str, str]] = []
    secrets: set[str] = set()
    for name, raw in (server.get("env") or {}).items():
        value = raw
        if isinstance(raw, str) and raw.startswith("${") and raw.endswith("}"):
            value = os.environ.get(raw[2:-1], "")
        values.append({"name": str(name), "value": str(value)})
        secrets.update((str(raw), str(value)))
    return values, secrets


def _redact(value: Any, secrets: set[str], *, env_context: bool = False) -> Any:
    """Redact resolved env values from all persisted/raw ACP evidence."""
    if isinstance(value, dict):
        output: Any = {}
        for key, item in value.items():
            if env_context and str(key).lower() == "value":
                output[key] = "[REDACTED]"
                continue
            if env_context and str(key).lower() == "name":
                output[key] = item
                continue
            child_env = env_context or str(key).lower() in {"env", "environment", "headers"}
            output[key] = _redact(item, secrets, env_context=child_env)
        return output
    if isinstance(value, list):
        return [_redact(item, secrets, env_context=env_context) for item in value]
    if isinstance(value, str):
        if env_context:
            # ACP env is a list of {name,value}; redact every value while
            # preserving names and shape. Header/url secrets are redacted by
            # exact or embedded literal matching below.
            return "[REDACTED]"
        output = value
        for secret in sorted((s for s in secrets if s), key=len, reverse=True):
            output = output.replace(secret, "[REDACTED]")
        return output
    return value


def _redact_payload(payload: Any, secrets: set[str]) -> Any:
    if isinstance(payload, dict) and isinstance(payload.get("params"), dict):
        # Avoid treating arbitrary protocol fields named `headers` as env
        # context; only the session/new MCP server env list needs structural
        # redaction. Literal/reference matching handles the rest.
        payload = dict(payload)
        params = dict(payload["params"])
        servers = params.get("mcpServers")
        if isinstance(servers, list):
            safe_servers = []
            for server in servers:
                safe = dict(server) if isinstance(server, dict) else server
                if isinstance(safe, dict) and isinstance(safe.get("env"), list):
                    safe["env"] = [{**entry, "value": "[REDACTED]"} if isinstance(entry, dict) and "value" in entry else entry for entry in safe["env"]]
                safe_servers.append(safe)
            params["mcpServers"] = safe_servers
        payload["params"] = params
    return _redact(payload, secrets)


def _dump_error(exc: BaseException) -> str:
    if isinstance(exc, _AcpMalformedStdout): return "acp_malformed_stdout"
    if isinstance(exc, _AcpEarlyExit): return "acp_early_exit"
    text = str(exc)
    low = text.lower()
    if "auth" in low: return "acp_auth_required"
    if any(x in low for x in ("permission", "elicitation", "filesystem", "terminal", "read_text", "write_text")): return "acp_interaction_required"
    if "stale" in low or "unknown" in low or "invalid" in low: return "acp_stale_option"
    return "acp_protocol_error"

class _Client:
    def __init__(
        self,
        frames: list[dict[str, Any]],
        output: list[Any],
        callback: Callable[..., Any] | None = None,
        interaction_fault: list[BaseException] | None = None,
        secrets: set[str] | None = None,
        interactions: InteractionController | None = None,
    ) -> None:
        self.frames, self.output, self.callback, self.interaction_fault = frames, output, callback, interaction_fault
        self.secrets = secrets if secrets is not None else set()
        self.interactions: InteractionController | None = interactions
        self._terminals: dict[str, Any] = {}
    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.output.append(update)
        if self.callback:
            serial=_redact(self._json(update), self.secrets)
            value=self.callback(serial, "acp_update", {"session_id":session_id, "update":serial})
            if asyncio.iscoroutine(value): await value
    @staticmethod
    def _json(value: Any) -> Any:
        if hasattr(value,"model_dump"): return value.model_dump(mode="json")
        return value
    def _interaction(self, kind: str) -> RuntimeError:
        error = RuntimeError(f"acp_interaction_required: {kind}")
        if self.interaction_fault is not None: self.interaction_fault.append(error)
        return error

    async def request_permission(self, session_id: str, tool_call: Any, options: list[Any], **kwargs: Any) -> RequestPermissionResponse:
        del session_id, kwargs
        if self.interactions is None:
            raise self._interaction("permission")
        title = getattr(tool_call, "title", None)
        request = PermissionRequest("mcp_tool", title if isinstance(title, str) else "")
        result = await self.interactions.permission(request)
        if not result.allowed:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        selected = next(
            (option for option in options if getattr(option, "kind", "").startswith("allow_")),
            None,
        )
        option_id = getattr(selected, "option_id", None) or "allow_once"
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=str(option_id)))

    async def create_elicitation(self, message: str, mode: Any, **kwargs: Any) -> CreateElicitationResponse:
        del mode, kwargs
        if self.interactions is None:
            raise self._interaction("elicitation")
        result = await self.interactions.elicit(ElicitationRequest(str(message)))
        if not result.accepted:
            return DeclineElicitationResponse(action="decline")
        value = result.value if isinstance(result.value, dict) else None
        return AcceptElicitationResponse(action="accept", content=value)

    async def read_text_file(self, session_id: str, path: str, line: int | None = None, limit: int | None = None, **kwargs: Any) -> ReadTextFileResponse:
        del session_id, line, kwargs
        if self.interactions is None:
            raise self._interaction("filesystem")
        result = await self.interactions.filesystem(
            FilesystemRequest("read", str(path), max_bytes=min(int(limit or (1 << 20)), 1 << 20))
        )
        if not result.allowed or not isinstance(result.data, bytes):
            raise self._interaction("filesystem")
        return ReadTextFileResponse(content=result.data.decode("utf-8", errors="replace"))

    async def write_text_file(self, session_id: str, path: str, content: str, **kwargs: Any) -> WriteTextFileResponse:
        del session_id, kwargs
        if self.interactions is None:
            raise self._interaction("filesystem")
        result = await self.interactions.filesystem(FilesystemRequest("write", str(path), str(content).encode("utf-8")))
        if not result.allowed:
            raise self._interaction("filesystem")
        return WriteTextFileResponse()

    async def create_terminal(self, session_id: str, command: str, args: list[str] | None = None, env: Any = None, cwd: str | None = None, output_byte_limit: int | None = None, **kwargs: Any) -> CreateTerminalResponse:
        del session_id, kwargs
        if self.interactions is None:
            raise self._interaction("terminal")
        if isinstance(env, Mapping):
            terminal_env = {str(key): str(value) for key, value in env.items()}
        else:
            terminal_env = {
                str(getattr(item, "name", "")): str(getattr(item, "value", ""))
                for item in (env or [])
                if getattr(item, "name", None)
            }
        result = await self.interactions.terminal(
            TerminalRequest(
                (str(command), *(str(item) for item in (args or []))),
                cwd=str(cwd) if cwd is not None else None,
                environment=terminal_env,
                max_output_bytes=min(int(output_byte_limit or (1 << 20)), 1 << 20),
            )
        )
        if not result.allowed:
            raise self._interaction("terminal")
        terminal_id = "acp-terminal-" + str(len(self._terminals) + 1)
        self._terminals[terminal_id] = result
        return CreateTerminalResponse(terminal_id=terminal_id)

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any) -> TerminalOutputResponse:
        del session_id, kwargs
        result = self._terminals.get(str(terminal_id))
        if result is None:
            raise self._interaction("terminal")
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        return TerminalOutputResponse(output=output, truncated=result.truncated)

    async def wait_for_terminal_exit(self, session_id: str, terminal_id: str, **kwargs: Any) -> WaitForTerminalExitResponse:
        del session_id, kwargs
        result = self._terminals.get(str(terminal_id))
        if result is None:
            raise self._interaction("terminal")
        return WaitForTerminalExitResponse(exit_code=result.returncode if result.returncode is not None and result.returncode >= 0 else None)

    async def release_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> ReleaseTerminalResponse:
        del session_id, kwargs
        self._terminals.pop(str(terminal_id), None)
        return ReleaseTerminalResponse()

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> KillTerminalResponse:
        del session_id, kwargs
        self._terminals.pop(str(terminal_id), None)
        return KillTerminalResponse()

    async def authenticate(self, *args: Any, **kwargs: Any) -> None:
        # Authentication is never delegated to a generic user handler. An ACP
        # auth flow must be explicitly implemented by a future adapter.
        del args, kwargs
        raise RuntimeError("acp_auth_required: out-of-band authentication")

    async def ext_method(self, method: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        del kwargs
        if "sampling" not in str(method).lower() or self.interactions is None:
            return {}
        prompt = params.get("prompt", params.get("message", "")) if isinstance(params, Mapping) else ""
        result = await self.interactions.sample(SamplingRequest(str(prompt)))
        if not result.accepted:
            raise self._interaction("sampling")
        return {"content": [{"type": "text", "text": result.content or ""}]}

    async def ext_notification(self, method: str, params: dict[str, Any], **kwargs: Any) -> None:
        del method, params, kwargs
        return None

    async def complete_elicitation(self, elicitation_id: str, **kwargs: Any) -> None:
        del elicitation_id, kwargs

    def on_connect(self, conn: Any) -> None:
        del conn


def _safe_json_value(value: Any) -> Any:
    """Convert an ACP model to a plain value without invoking unsafe repr().

    ACP evidence is an observation boundary.  A malformed third-party model
    must not make its exception text part of a result or a log.  Returning a
    small marker is preferable to persisting an object whose serializer we do
    not control.
    """

    try:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, Mapping):
            return {str(key): _safe_json_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_safe_json_value(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
    except Exception:
        return {"kind": "unserializable_acp_value"}
    return {"kind": "unsupported_acp_value", "type": type(value).__name__}


def _acp_error(exc: BaseException, *, phase: str) -> tuple[str, str]:
    """Map arbitrary ACP failures to value-free stable categories."""

    del exc
    if phase == "initialize":
        return "acp_initialize_failed", "ACP initialization failed"
    if phase == "session/new":
        return "acp_session_failed", "ACP session creation failed"
    if phase == "prompt":
        return "acp_prompt_failed", "ACP prompt failed"
    if phase == "cancel":
        return "acp_cancel_failed", "ACP cancellation failed"
    return "acp_protocol_error", "ACP protocol operation failed"


def _credential_query(url: str) -> bool:
    """Reject credential-shaped URL query parameters before ACP startup."""

    try:
        names = {name.lower().replace("-", "_") for name, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)}
    except ValueError:
        return True
    parsed = urlsplit(url)
    return bool(
        parsed.username
        or parsed.password
        or names & {"token", "access_token", "api_key", "apikey", "secret", "password", "credential", "auth"}
    )


class _AcpContractSession:
    """One ACP process, connection, and session for the generic harness API."""

    def __init__(self, launch: Any, manifest: Mapping[str, Any], executable: str, secret_resolver: Callable[[Any], str] | None = None, environment: Mapping[str, str] | None = None) -> None:
        from .contracts import HarnessAdapterCapabilities

        self._launch = launch
        self._manifest = dict(manifest)
        self._executable = executable
        self._secret_resolver = secret_resolver
        self._environment = None if environment is None else dict(environment)
        self._capabilities = HarnessAdapterCapabilities(
            name="acp",
            supports_multiturn=True,
            supports_cancellation=True,
            supports_timeout=True,
            # ACP itself has no portable tool allowlist primitive. Until an
            # adapter-side enforcement bridge is supplied, requested
            # portable policies must fail preflight rather than be implied.
            supports_tool_policy=False,
            supports_streaming=True,
            supported_content_kinds=frozenset({"text"}),
        )
        self._session_id = "acp-pending"
        self._process: Any = None
        self._connection: Any = None
        self._transport: _AcpTransport | None = None
        self._stderr_task: asyncio.Task[bytes] | None = None
        self._workdir: str | None = None
        self._workspace_root: str | None = None
        self._capture_path: str | None = None
        self._pgid: int | None = None
        self._frames: list[dict[str, Any]] = []
        self._updates: list[Any] = []
        self._secrets: set[str] = set()
        self._turns = 0
        self._closed = False
        self._cancel_requested = False
        self._prompt_task: asyncio.Task[Any] | None = None
        self._send_lock = asyncio.Lock()
        self._cleanup_failure = False
        self._transport_fault: BaseException | None = None
        self._prepared_servers: list[Any] | None = None
        # Register ambient credential-shaped values before the child can emit
        # even its first byte.  Explicit server values are added by
        # ``_prepare_servers`` below, still in this process.
        self._secrets.update(known_secret_values())
        self._baseline = time.monotonic()

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def capabilities(self) -> Any:
        return self._capabilities

    @staticmethod
    def _message_blocks(message: Any) -> list[Any]:
        from ..types import TextContent

        blocks = list(getattr(message, "content", ()))
        if any(not isinstance(block, TextContent) for block in blocks):
            raise ValueError("acp_attachment_unsupported")
        return [TextContentBlock(type="text", text=block.text) for block in blocks]

    def _observe(self, event: Any) -> None:
        direction = "client_to_server" if event.direction == StreamDirection.OUTGOING else "server_to_client"
        self._frames.append(
            {
                "direction": direction,
                "received_at": datetime.now(timezone.utc).isoformat(),
                "offset_ms": max(0.0, (time.monotonic() - self._baseline) * 1000.0),
                "payload": _redact_payload(_safe_json_value(event.message), self._secrets),
            }
        )

    def _server_value(self, name: str, value: Any) -> tuple[str, bool]:
        """Resolve one server value and report whether ACP must omit it.

        MCP credentials belong to the owned proxy/stdio handoff.  ACP only
        needs the non-sensitive portion of the server descriptor; sending a
        resolved header or environment value in ``session/new`` would expose
        it to the harness process itself.
        """

        if isinstance(value, SecretReference):
            resolved = self._config_value(value)
            self._secrets.update((resolved, value.name))
            return resolved, True
        if not isinstance(value, str):
            raise ValueError("secret_reference_unresolved")
        if value.startswith("${") and value.endswith("}"):
            reference = value[2:-1]
            environment_value = (
                self._environment.get(reference)
                if self._environment is not None
                else os.environ.get(reference)
            )
            if environment_value is None:
                environment_value = os.environ.get(reference)
            if not environment_value:
                raise ValueError("secret_reference_unresolved")
            resolved = environment_value
            self._secrets.update((value, resolved))
            return resolved, True
        resolved = value
        classified = is_sensitive_key(name) or resolved in known_secret_values()
        if classified and resolved:
            self._secrets.add(resolved)
        return resolved, classified

    def _prepare_servers(self) -> list[Any]:
        servers: list[Any] = []
        for config in self._launch.configurations:
            if not config.available:
                if config.required:
                    raise ValueError("required_mcp_server_unavailable")
                continue
            name = str(config.key)
            if config.transport.value == "stdio":
                if not config.command:
                    raise ValueError("stdio_mcp_command_missing")
                env: list[EnvVariable] = []
                for key, value in config.environment.items():
                    resolved, classified = self._server_value(str(key), value)
                    if not classified:
                        env.append(EnvVariable(name=str(key), value=resolved))
                servers.append(
                    McpServerStdio(name=name, command=config.command, args=list(config.args), env=env)
                )
            elif config.transport.value in {"streamable_http", "sse"}:
                if not config.endpoint:
                    raise ValueError("http_mcp_endpoint_missing")
                headers: list[HttpHeader] = []
                for key, value in config.headers.items():
                    resolved, classified = self._server_value(str(key), value)
                    if not classified:
                        headers.append(HttpHeader(name=str(key), value=resolved))
                if config.transport.value == "streamable_http":
                    servers.append(HttpMcpServer(name=name, url=config.endpoint, headers=headers, type="http"))
                else:
                    servers.append(SseMcpServer(name=name, url=config.endpoint, headers=headers, type="sse"))
            elif config.endpoint:
                # SDK-hosted in-process servers are exposed as loopback HTTP
                # endpoints by ServerGroupManager before the adapter opens.
                servers.append(HttpMcpServer(name=name, url=config.endpoint, headers=[], type="http"))
            else:
                raise ValueError("mcp_transport_unsupported")
        return servers

    def _servers(self) -> list[Any]:
        # ``open`` prepares this before subprocess creation.  Keep the lazy
        # fallback for narrow adapter/unit callers, but never resolve values
        # while the ACP process is already emitting protocol evidence.
        if self._prepared_servers is None:
            self._prepared_servers = self._prepare_servers()
        return self._prepared_servers

    def _config_value(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            resolver = self._secret_resolver or (lambda ref: _resolve_environment_secret(ref, self._environment))
            resolved = resolver(value)
        except Exception as exc:
            del exc
            raise ValueError("secret_reference_unresolved") from None
        if not isinstance(resolved, str) or not resolved:
            raise ValueError("secret_reference_unresolved")
        return resolved

    def _record_transport_fault(self, error: BaseException) -> None:
        self._transport_fault = error

    async def open(self) -> None:
        if self._process is not None:
            return
        # Resolve and register every selected server credential before the
        # ACP process exists.  In particular, do not defer this until the
        # session/new request: a fixture may emit startup output immediately.
        self._prepared_servers = self._prepare_servers()
        self._workdir = tempfile.mkdtemp(prefix="mcp-pal-acp-control-")
        self._capture_path = os.path.join(self._workdir, "acp-capture.jsonl")
        try:
            self._workspace_root = str(workspace_for_launch(self._launch, Path(self._workdir)))
            env = _isolated_acp_env(
                self._manifest,
                self._executable,
                self._secrets,
                root=self._workdir,
                environment=self._environment,
            )
            self._process = await asyncio.create_subprocess_exec(
                self._executable,
                *(str(item) for item in (self._manifest.get("args") or [])),
                cwd=self._workspace_root,
                env=env,
                start_new_session=(os.name != "nt"),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._stderr_task = asyncio.create_task(_drain_stderr(self._process.stderr))
            if os.name != "nt":
                # Native ACP launches use ``start_new_session``; retain the
                # group identity even when an immediately exiting process is
                # already unreapable by ``getpgid``.
                self._pgid = self._process.pid
                try:
                    self._pgid = os.getpgid(self._process.pid)
                except (ProcessLookupError, OSError):
                    pass
            client = _Client(
                self._frames,
                self._updates,
                secrets=self._secrets,
                # Missing callbacks are an explicit default-deny controller,
                # not an implicit provider-side allow or an unhandled method.
                interactions=self._launch.interactions or InteractionController(),
            )
            self._transport = _AcpTransport(
                self._process,
                lambda _: None,
                on_fault=self._record_transport_fault,
            )
            self._connection = ClientSideConnection(client, self._transport, observers=[self._observe])
            init = await self._connection.initialize(1, ClientCapabilities(), Implementation(name="mcp-pal", version="0.2"))
            if getattr(init, "protocol_version", None) != 1:
                raise ValueError("acp_protocol_version_mismatch")
            response = await self._connection.new_session(self._workspace_root, mcp_servers=self._servers())
            self._session_id = str(response.session_id)
            # ACP exposes session modes and configuration options only after
            # session/new. Apply the immutable public request before the
            # first prompt, and fail closed when a saved option is stale.
            harness = getattr(self._launch.spec, "harness", None)
            mode_id = getattr(harness, "agent_mode_id", None)
            if mode_id:
                modes = getattr(getattr(response, "modes", None), "available_modes", None) or ()
                available = {str(getattr(item, "id", "")) for item in modes if getattr(item, "id", None)}
                if not available:
                    raise ValueError("acp_stale_option: mode")
                if mode_id not in available:
                    raise ValueError("acp_stale_option: mode")
                await self._connection.set_session_mode(self._session_id, mode_id)
            session_config = getattr(harness, "session_config", {}) or {}
            options = getattr(response, "config_options", None) or ()
            option_ids = {
                str(getattr(item, "id", None) or getattr(item, "config_id", None))
                for item in options
                if getattr(item, "id", None) is not None or getattr(item, "config_id", None) is not None
            }
            for key, value in dict(session_config).items():
                if str(key) not in option_ids:
                    raise ValueError("acp_stale_option: config")
                await self._connection.set_config_option(str(key), self._session_id, value)
        except (asyncio.CancelledError, SystemExit, KeyboardInterrupt):
            await self.close()
            raise
        except Exception:
            await self.close()
            raise

    async def send(self, request: Any) -> Any:
        from .contracts import HarnessTurnResult
        from ..types import ErrorCode, ErrorInfo, TurnResponse, TextContent

        async with self._send_lock:
            if self._closed:
                return HarnessTurnResult(self._turns + 1, "failed", error=ErrorInfo(code=ErrorCode.TRANSPORT_ERROR, message="ACP session is closed"))
            self._turns += 1
            sequence = self._turns
            self._cancel_requested = False
            before = len(self._updates)
            try:
                blocks = self._message_blocks(request.message)
                operation = self._connection.prompt(self._session_id, blocks)
                self._prompt_task = asyncio.create_task(operation)
                if request.timeout_seconds is None:
                    await self._prompt_task
                else:
                    await asyncio.wait_for(self._prompt_task, request.timeout_seconds)
                updates = self._updates[before:]
                text = _redact(_updates_text(updates), self._secrets)
                response = TurnResponse(content=(TextContent(text=text),))
                calls = tuple(_updates_tool_calls(updates))
                usage_observed = any("usage" in update.__class__.__name__.lower() for update in updates)
                evidence: dict[str, str | int | float | bool | None] = {
                    "transport": "acp",
                    "updates": len(updates),
                    "session_id": self._session_id,
                    # ACP does not provide a portable budget/cost control.
                    # Keep usage truthfully unavailable instead of estimating.
                    "usage_requested": False,
                    "usage_enforced": False,
                    "usage_observed": usage_observed,
                    "usage_unavailable": not usage_observed,
                    "usage_unavailable_reason": None if usage_observed else "provider_did_not_emit_usage",
                }
                return HarnessTurnResult(sequence, "completed", response=response, tool_calls=calls, evidence=evidence)
            except asyncio.TimeoutError:
                await self.cancel()
                return HarnessTurnResult(sequence, "timed_out", error=ErrorInfo(code=ErrorCode.TIMEOUT, message="ACP turn timed out"), evidence={"transport": "acp"})
            except asyncio.CancelledError:
                if self._cancel_requested:
                    return HarnessTurnResult(sequence, "cancelled", error=ErrorInfo(code=ErrorCode.CANCELLED, message="ACP turn cancelled"), evidence={"transport": "acp"})
                raise
            except Exception as exc:
                code, message = _acp_error(exc, phase="prompt")
                error_code = ErrorCode.TRANSPORT_ERROR if code == "acp_prompt_failed" else ErrorCode.PROTOCOL_ERROR
                return HarnessTurnResult(
                    sequence,
                    "failed",
                    error=ErrorInfo(code=error_code, message=message),
                    evidence={"transport": "acp", "error_code": code},
                    trace_limitations=("partial_trace",)
                    if isinstance(exc, _AcpEarlyExit) or isinstance(self._transport_fault, _AcpEarlyExit)
                    else (),
                )
            finally:
                self._prompt_task = None

    async def cancel(self) -> None:
        if self._closed:
            return
        self._cancel_requested = True
        if self._connection is not None and self._session_id != "acp-pending":
            try:
                await asyncio.wait_for(self._connection.cancel(self._session_id), timeout=0.5)
            except Exception:
                pass
        task = self._prompt_task
        if task is not None and not task.done():
            task.cancel()

    def snapshot(self) -> Any:
        from .contracts import HarnessSessionSnapshot

        return HarnessSessionSnapshot(
            session_id=self._session_id,
            turns=self._turns,
            server_configuration_count=len(self._launch.configurations),
            closed=self._closed,
            evidence={
                "transport": "acp",
                "capture": "acp_frames",
                "cleanup_failure": self._cleanup_failure,
            },
        )

    async def close(self) -> None:
        if self._closed:
            return
        await self.cancel()
        self._closed = True
        if self._connection is not None:
            try:
                await asyncio.wait_for(self._connection.close(), timeout=0.75)
            except Exception:
                # A receive-loop fault is already terminal when the ACP
                # child has exited.  The official connection's close may
                # re-raise that fault; it is not an ownership leak and must
                # not turn an otherwise successful process-group reap into a
                # cleanup failure.  Keep treating a live-child close error
                # as a real cleanup failure.
                if not isinstance(self._transport_fault, _AcpEarlyExit):
                    self._cleanup_failure = True
        process = self._process
        if process is not None:
            if self._pgid is not None:
                # Pass both identities.  A pgid without its owned child is
                # not enough evidence because process-group ids can be
                # reused after a leader exits.
                await asyncio.to_thread(
                    terminate_process_group,
                    pid=process.pid,
                    pgid=self._pgid,
                    grace_seconds=0.25,
                )
            elif process.returncode is None:
                try:
                    process.terminate()
                except (ProcessLookupError, PermissionError, OSError):
                    pass
        if process is not None:
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except Exception:
                self._cleanup_failure = True
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=1.0)
                except Exception:
                    pass
        if self._stderr_task is not None:
            try:
                await asyncio.wait_for(self._stderr_task, timeout=0.75)
            except asyncio.TimeoutError:
                self._cleanup_failure = True
                self._stderr_task.cancel()
                await asyncio.gather(self._stderr_task, return_exceptions=True)
            except Exception:
                self._cleanup_failure = True
        self._process = None
        if self._workdir is not None:
            shutil.rmtree(self._workdir, ignore_errors=True)
        self._workspace_root = None
        # Raw ACP model updates may contain values that were only safe to
        # retain while the redaction set was alive.  Keep only the already
        # redacted frame projection after terminal cleanup.
        self._updates.clear()
        self._secrets.clear()


def _updates_text(updates: list[Any]) -> str:
    chunks: list[str] = []
    for update in updates:
        name = update.__class__.__name__.lower()
        if "agentmessagechunk" not in name:
            continue
        content = getattr(update, "content", None)
        text = getattr(content, "text", None)
        if isinstance(text, str):
            chunks.append(text)
    return "".join(chunks)


def _updates_tool_calls(updates: list[Any]) -> list[Mapping[str, Any]]:
    calls: list[Mapping[str, Any]] = []
    for update in updates:
        name = update.__class__.__name__.lower()
        if "toolcall" not in name:
            continue
        tool_name = getattr(update, "title", None) or getattr(update, "name", None)
        if isinstance(tool_name, str) and tool_name:
            calls.append({"name": tool_name})
        else:
            calls.append({"kind": "tool_call"})
    return calls


def _resolve_environment_secret(value: Any, environment: Mapping[str, str] | None = None) -> str:
    """Resolve only environment-backed references for the ACP child."""

    if not isinstance(value, SecretReference) or value.source != "environment":
        raise ValueError("secret_reference_unresolved")
    resolved = environment[value.name] if environment is not None and value.name in environment else os.environ.get(value.name)
    if not resolved:
        raise ValueError("secret_reference_unresolved")
    return resolved


def _isolated_acp_env(
    manifest: Mapping[str, Any],
    executable: str,
    secrets: set[str],
    *,
    root: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build an allowlisted ACP environment from named manifest references."""

    home = os.path.join(root, "home") if root is not None else tempfile.mkdtemp(prefix="mcp-pal-acp-home-")
    os.makedirs(home, exist_ok=True)
    env = {
        "PATH": os.pathsep.join((os.path.dirname(executable), os.defpath)),
        "HOME": home,
        "XDG_CONFIG_HOME": os.path.join(home, "config"),
        "XDG_DATA_HOME": os.path.join(home, "data"),
        "XDG_CACHE_HOME": os.path.join(home, "cache"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "NO_COLOR": "1",
        "CI": "1",
    }
    raw_env = manifest.get("env") or {}
    if not isinstance(raw_env, Mapping):
        raise ValueError("acp_manifest_invalid")
    for key, raw in raw_env.items():
        name = str(key)
        if not name or "=" in name or "\x00" in name:
            raise ValueError("acp_manifest_invalid")
        if isinstance(raw, str) and raw.startswith("${") and raw.endswith("}"):
            ref = raw[2:-1]
            value = environment[ref] if environment is not None and ref in environment else os.environ.get(ref)
            if value is None:
                raise ValueError("acp_environment_missing")
            secrets.update((value, raw))
        elif isinstance(raw, str):
            value = raw
            secrets.add(value)
        else:
            raise ValueError("acp_manifest_invalid")
        env[name] = value
    return env


class AcpHarnessAdapter:
    """Real ACP-v1 adapter with one process and session per opened launch."""

    def __init__(self, manifest: Mapping[str, Any] | None = None, *, secret_resolver: Callable[[Any], str] | None = None, environment: Mapping[str, str] | None = None) -> None:
        self._manifest = dict(manifest or {})
        self._secret_resolver = secret_resolver
        self._environment = None if environment is None else dict(environment)
        self._active: _AcpContractSession | None = None
        self._capabilities: Any = None
        self.last_policy_evidence: Any = None

    @property
    def name(self) -> str:
        return "acp"

    @property
    def capabilities(self) -> Any:
        from .contracts import HarnessAdapterCapabilities

        if self._capabilities is None:
            self._capabilities = HarnessAdapterCapabilities(
                name="acp", supports_multiturn=True, supports_cancellation=True,
                supports_timeout=True, supports_tool_policy=False, supports_streaming=True,
                supported_content_kinds=frozenset({"text"}),
            )
        return self._capabilities

    @property
    def supported_content_kinds(self) -> frozenset[str]:
        return frozenset(self.capabilities.supported_content_kinds)

    def _effective_manifest(self, launch: Any) -> dict[str, Any]:
        harness = getattr(launch.spec, "harness", None)
        value = getattr(harness, "manifest", None)
        return dict(value or self._manifest)

    async def preflight(self, launch: Any) -> Any:
        manifest = self._effective_manifest(launch)
        from .contracts import HarnessAdapterCapabilities

        command = manifest.get("command")
        executable = command if isinstance(command, str) and os.path.isabs(command) and os.access(command, os.X_OK) else shutil.which(str(command or ""))
        if not executable or not os.access(executable, os.X_OK):
            return self.capabilities.readiness(ready=False, reason="acp_executable_missing")
        if manifest.get("protocol", "acp") != "acp" or manifest.get("protocol_version", 1) != 1:
            return self.capabilities.readiness(ready=False, reason="acp_manifest_invalid")
        probe_home: str | None = None
        self.last_policy_evidence = None
        self._capabilities = replace(self.capabilities, supports_tool_policy=False)
        try:
            probe_env = _isolated_acp_env(manifest, executable, set(), environment=self._environment)
            probe_home = probe_env["HOME"]
            for config in launch.configurations:
                if config.endpoint and _credential_query(config.endpoint):
                    return self.capabilities.readiness(ready=False, reason="credential_query_unsupported")
                for value in tuple(config.environment.values()) + tuple(config.headers.values()):
                    if not isinstance(value, str):
                        try:
                            (self._secret_resolver or (lambda ref: _resolve_environment_secret(ref, self._environment)))(value)
                        except Exception:
                            return self.capabilities.readiness(ready=False, reason="credential_unavailable")
            for block in getattr(launch.spec.message, "content", ()):
                if getattr(block, "kind", "text") not in self.supported_content_kinds:
                    return self.capabilities.readiness(ready=False, reason="attachment_unsupported")
            if isinstance(launch.tool_policy, NativeToolPolicy):
                native = launch.tool_policy
                if native.harness != self.name or native.policy.get("mode") != "agent_default":
                    return self.capabilities.readiness(ready=False, reason="tool_policy_unsupported")
                server = native.policy.get("server")
                if not isinstance(server, str) or server not in {record.key for record in launch.servers.records}:
                    return self.capabilities.readiness(ready=False, reason="tool_policy_unsupported")
                # ACP owns its tool/permission negotiation. This evidence is
                # explicitly non-portable; interaction callbacks remain the
                # default-deny boundary for terminal/filesystem/permission
                # requests.
                self.last_policy_evidence = ToolPolicyEvidence(
                    requested="native", enforced="native", observed="preflight", portable=False,
                    nonportable_reason="ACP agent_default MCP selection",
                )
                return self.capabilities.readiness()
            if not isinstance(launch.tool_policy, (RestrictiveToolPolicy, FullToolPolicy)):
                return self.capabilities.readiness(ready=False, reason="tool_policy_unsupported")
            available_connections = tuple(
                str(config.connection_id)
                for config in launch.configurations
                if getattr(config, "available", False)
            )
            capture = getattr(launch, "capture", None)
            enforcement = getattr(capture, "enforces_portable_policy", None)
            if not callable(enforcement) or not enforcement(available_connections):
                return self.capabilities.readiness(ready=False, reason="tool_policy_unsupported")
            self._capabilities = replace(self.capabilities, supports_tool_policy=True)
            descriptors = tuple(
                ToolDescriptor(server=record.key, name=tool)
                for record in getattr(launch.servers, "records", ())
                if getattr(record, "available", False)
                for tool in getattr(record, "tools", ())
            )
            self.last_policy_evidence = ToolPolicyEvaluator(descriptors).preflight(
                launch.tool_policy,
                harness_name=self.name,
                supports_enforcement=self.capabilities.supports_tool_policy,
            )
        except ValueError as exc:
            del exc
            return self.capabilities.readiness(ready=False, reason="acp_environment_unavailable")
        except Exception:
            self.last_policy_evidence = None
            return self.capabilities.readiness(ready=False, reason="tool_policy_unsupported")
        finally:
            if probe_home is not None:
                shutil.rmtree(probe_home, ignore_errors=True)
        return self.capabilities.readiness()

    async def open(self, launch: Any) -> Any:
        readiness = await self.preflight(launch)
        if not readiness.ready:
            from .contracts import HarnessStartupError

            raise HarnessStartupError("ACP harness is not ready")
        manifest = self._effective_manifest(launch)
        command = manifest.get("command")
        executable = command if isinstance(command, str) and os.path.isabs(command) and os.access(command, os.X_OK) else shutil.which(str(command or ""))
        if not executable or not os.access(executable, os.X_OK):
            from .contracts import HarnessStartupError

            raise HarnessStartupError("ACP harness executable is unavailable")
        if self._active is not None:
            await self.close()
        session = _AcpContractSession(launch, manifest, executable, self._secret_resolver, self._environment)
        try:
            await session.open()
        except Exception as exc:
            from .contracts import HarnessStartupError

            del exc
            raise HarnessStartupError("ACP harness could not start") from None
        self._active = session
        return session

    async def start(self, spec: Any) -> None:
        from .contracts import HarnessLaunch
        from ..server_group import ServerGroupSnapshot

        await self.open(HarnessLaunch(spec, ServerGroupSnapshot(), (), spec.tool_policy))

    async def send(self, message: Any, *, timeout: float | None = None, metadata: Mapping[str, Any] | None = None) -> Any:
        from .contracts import HarnessTurnRequest
        from ..agent_session import AdapterTurn
        from ..types import TurnOutcome

        if self._active is None:
            from .contracts import HarnessAdapterError

            raise HarnessAdapterError("ACP harness session is not open")
        request = HarnessTurnRequest.from_message(message, timeout_seconds=timeout, metadata={str(k): v for k, v in (metadata or {}).items() if isinstance(v, (str, int, float, bool)) or v is None})
        result = await self._active.send(request)
        outcome = {
            "completed": TurnOutcome.COMPLETED,
            "timed_out": TurnOutcome.TIMED_OUT,
            "cancelled": TurnOutcome.CANCELLED,
        }.get(result.status, TurnOutcome.FAILED)
        return AdapterTurn(
            response=result.response,
            error=result.error,
            terminal=result.status != "completed",
            outcome=outcome,
            tool_calls=result.tool_calls,
            evidence=result.evidence,
            trace_limitations=result.trace_limitations,
        )

    async def cancel(self) -> None:
        if self._active is not None:
            await self._active.cancel()

    async def close(self) -> None:
        if self._active is not None:
            session = self._active
            self._active = None
            await session.close()


# Short alias used by integrations that call the adapter ``ACP``.
ACPAdapter = AcpHarnessAdapter
ACPAgentAdapter = AcpHarnessAdapter

class AcpHarnessRunner:
    def __init__(self, manifest: dict[str, Any] | None = None) -> None:
        self.manifest = manifest or {}
        self._process: Any = None
        self._cancel = False

    def request_cancel(self) -> None:
        self._cancel = True

    async def run(self, spec: AcpRunSpec, on_event: Callable[..., Any] | None = None, cancel_event: Any = None) -> HarnessResult:
        result = HarnessResult("failed", transport="stdio")
        frames: list[dict[str, Any]] = []; updates: list[Any] = []
        started = time.monotonic(); capture_baseline_ns = time.perf_counter_ns(); deadline = started + max(0.01, float(spec.timeout_seconds)); process_started = started
        workdir = None; process = None; stderr_task = None; connection = None; proxy = None; agent_pgid = None
        manifest = spec.manifest; command = manifest.get("command")
        executable = command if os.path.isabs(command or "") else shutil.which(command or "")
        if not executable:
            return HarnessResult("failed", error=f"acp_executable_missing: {command}", error_code="acp_executable_missing", error_phase="preflight")
        try:
            env, secrets = _manifest_env(manifest)
        except ValueError as exc:
            text = str(exc); code = "acp_environment_missing" if text.startswith("acp_environment_missing") else "acp_manifest_invalid"
            return HarnessResult("failed", error=text, error_code=code, error_phase="preflight")
        selected = dict((spec.mcp_config.get("mcpServers") or {}).get(spec.enabled_server) or {})
        # Check every selected-server field (URL, headers, stdio env, etc.)
        # before creating a workspace or proxy. Literal env values are valid.
        for ref in sorted(set(__import__("mcp_pal.domain.validation", fromlist=["referenced_environment_variables"]).referenced_environment_variables(selected))):
            if ref not in os.environ:
                return HarnessResult("failed", error=f"acp_environment_missing: {ref}", error_code="acp_environment_missing", error_phase="preflight")
            secrets.update((f"${{{ref}}}", os.environ[ref]))
        selected_env, selected_secrets = _selected_env(selected); secrets.update(selected_secrets)
        selected_transport = transport_for_server(selected)
        result.transport = result.configured_transport = result.instrumented_transport = selected_transport
        capture_path = None; current_phase = "preflight"; transport_fault: list[BaseException] = []; interaction_fault: list[BaseException] = []; auth_fault: list[BaseException] = []
        def observe(event: Any) -> None:
            now = time.monotonic()
            payload = _redact_payload(event.message, secrets)
            frames.append({"direction": "client_to_server" if event.direction == StreamDirection.OUTGOING else "server_to_client", "received_at": datetime.now(timezone.utc).isoformat(), "occurred_at": datetime.now(timezone.utc).isoformat(), "offset_ms": max(0.0, (now - started) * 1000), "payload": payload})
            if event.direction != StreamDirection.OUTGOING and isinstance(event.message, dict):
                method = str(event.message.get("method") or "")
                if method == "authenticate":
                    auth_fault.append(RuntimeError("acp_auth_required: out-of-band authentication"))
                if method in {"request_permission", "session/request_permission", "fs/read_text_file", "fs/write_text_file", "read_text_file", "write_text_file", "terminal/create", "create_terminal", "elicitation/create", "create_elicitation"}:
                    interaction_fault.append(RuntimeError("acp_interaction_required: " + method))
        def malformed(excerpt: str) -> None:
            safe_excerpt = _redact(excerpt[:512], secrets)
            frames.append({"direction":"server_to_client", "received_at":datetime.now(timezone.utc).isoformat(), "occurred_at":datetime.now(timezone.utc).isoformat(), "offset_ms":max(0.0,(time.monotonic()-started)*1000), "payload":{"error":"acp_malformed_stdout","excerpt":safe_excerpt}})
        client = _Client(frames, updates, on_event, interaction_fault, secrets)

        async def guarded(awaitable: Coroutine[Any, Any, Any], phase: str) -> Any:
            nonlocal current_phase
            current_phase = phase
            task = asyncio.create_task(awaitable)
            try:
                while True:
                    if self._cancel or (cancel_event is not None and cancel_event.is_set()):
                        if phase == "initialize" and time.monotonic() - process_started < 1.0:
                            await asyncio.sleep(.01)
                            continue
                        task.cancel(); await asyncio.gather(task, return_exceptions=True); raise _AcpCancelled()
                    if process is not None and process.returncode is not None and not task.done():
                        task.cancel(); await asyncio.gather(task, return_exceptions=True); raise _AcpEarlyExit(f"process exited with code {process.returncode}")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        task.cancel(); await asyncio.gather(task, return_exceptions=True); raise _AcpTimedOut(phase)
                    done, _ = await asyncio.wait({task}, timeout=min(remaining, .05))
                    if done: return task.result()
            except asyncio.CancelledError:
                task.cancel(); await asyncio.gather(task, return_exceptions=True); raise

        try:
            workdir = tempfile.mkdtemp(prefix="mcp-pal-acp-"); capture_path = os.path.join(workdir, "mcp-capture.jsonl")
            launch_command, launch_args = executable, list(manifest.get("args") or [])
            process = await asyncio.create_subprocess_exec(launch_command, *launch_args, env=env, cwd=workdir, start_new_session=(os.name != "nt"), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            self._process = process; stderr_task = asyncio.create_task(_drain_stderr(process.stderr))
            # The deadline governs the ACP lifecycle once the child exists;
            # interpreter startup/import time must not consume a tiny test or
            # user-selected lifecycle timeout before initialize is sent.
            # A subprocess may need a few hundred milliseconds to import the
            # harness and execute the group launcher on a cold machine. Keep
            # a small floor so a 100ms lifecycle timeout still reaches the
            # requested phase and can be classified (rather than killing the
            # wrapper before the agent has written its pid/handshake).
            deadline = time.monotonic() + max(0.5, float(spec.timeout_seconds))
            process_started = time.monotonic()
            if os.name != "nt":
                # ``start_new_session`` means the process PID is also the
                # owned process-group ID. Keep it for the early-exit case.
                agent_pgid = process.pid
                for _ in range(100):
                    try:
                        candidate = os.getpgid(process.pid)
                        if candidate == process.pid: agent_pgid = candidate; break
                    except (ProcessLookupError, OSError): break
                    await asyncio.sleep(.01)
            transport = _AcpTransport(process, malformed, lambda exc: transport_fault.append(exc))
            connection = ClientSideConnection(client, transport, observers=[observe])
            init = await guarded(connection.initialize(1, ClientCapabilities(), Implementation(name="mcp-pal", version="0.1")), "initialize")
            if auth_fault: raise auth_fault[0]
            if getattr(init, "protocol_version", None) != 1: raise RuntimeError("acp_protocol_version_mismatch")
            advertised = getattr(getattr(getattr(init, "agent_capabilities", None), "mcp_capabilities", None), selected_transport, False)
            if selected_transport in {"http", "sse"} and not advertised: raise RuntimeError(f"acp_transport_not_advertised: {selected_transport}")
            # ACP frames and captured MCP frames must share the same clock
            # origin so trace correlation remains valid across startup.
            baseline = capture_baseline_ns
            if selected_transport in {"http", "sse"}:
                proxy = McpHttpProxy(upstream_url=selected["url"], configured_headers=selected.get("headers"), transport=selected_transport, capture_path=capture_path, baseline_ns=baseline, allow_private=spec.allow_private_upstream, secrets=secrets)
                local_url = await guarded(proxy.start(), "session/new")
                if selected_transport == "http":
                    server: Any = HttpMcpServer(name=spec.enabled_server, url=local_url, headers=[], type="http")
                else:
                    server = SseMcpServer(name=spec.enabled_server, url=local_url, headers=[], type="sse")
            else:
                # The ACP agent launches this legacy relay. Keep credentials out
                # of the ACP payload and pass them through a strict one-shot
                # handoff that the relay removes before starting the MCP child.
                handoff = os.path.join(workdir, "mcp-env.json")
                handoff_secrets = write_stdio_handoff(
                    handoff,
                    selected.get("env") or {},
                )
                secrets.update(handoff_secrets)
                relay_args = [
                    "-m", "mcp_pal.transport.stdio_proxy",
                    "--capture", capture_path,
                    "--baseline", str(baseline),
                    "--env-file", handoff,
                ]
                if isinstance(selected.get("cwd"), str) and selected["cwd"]:
                    relay_args.extend(["--cwd", selected["cwd"]])
                relay_args.extend(["--", selected.get("command", ""), *(selected.get("args") or [])])
                server = McpServerStdio(
                    name=spec.enabled_server,
                    command=sys.executable,
                    args=relay_args,
                    env=[],
                )
            session = await guarded(connection.new_session(workdir, mcp_servers=[server]), "session/new"); result.session_id = session.session_id
            session_result: dict[str, Any] = next((
                frame.get("payload", {}).get("result", {}) for frame in reversed(frames)
                if isinstance(frame.get("payload"), dict)
                and isinstance(frame["payload"].get("result"), dict)
                and frame["payload"]["result"].get("sessionId") == result.session_id
            ), {})
            if spec.agent_mode_id:
                current_phase = "set_session_mode"
                modes = getattr(session, "modes", None); available = [getattr(x, "id", None) for x in (getattr(modes, "available_modes", None) or [])]
                if not available:
                    raw_modes=session_result.get("modes") or {}
                    available=[x.get("id") for x in (raw_modes.get("availableModes") or raw_modes.get("available_modes") or []) if isinstance(x,dict)]
                if spec.agent_mode_id not in available: raise RuntimeError("acp_stale_option: mode")
                await guarded(connection.set_session_mode(result.session_id, spec.agent_mode_id), "set_session_mode")
            options = {getattr(option, "id", None): option for option in (getattr(session, "config_options", None) or [])}
            if not options:
                options={str(option.get("id") or option.get("configId")):option for option in (session_result.get("configOptions") or session_result.get("config_options") or []) if isinstance(option,dict) and (option.get("id") or option.get("configId")) is not None}
            for key, value in spec.session_config.items():
                current_phase = "set_config_option"
                if key not in options: raise RuntimeError(f"acp_stale_option: config {key}")
                await guarded(connection.set_config_option(key, result.session_id, value), "set_config_option")
            await guarded(connection.prompt(result.session_id, [TextContentBlock(type="text", text=spec.prompt)]), "prompt")
            if interaction_fault: raise interaction_fault[0]
            result.status = "completed"
            def text_of(value: Any) -> str:
                content = getattr(value, "content", None)
                if isinstance(content, list): return "".join(str(getattr(item, "text", item if isinstance(item, str) else "")) for item in content)
                if content is not None and hasattr(content, "text"): return str(content.text)
                return str(getattr(value, "text", content or ""))
            # Only agent message chunks are final output. Tool-call updates
            # may also carry content (and thoughts are separate updates), but
            # including them duplicates or contaminates the user-visible text.
            result.final_text = _redact("".join(
                text_of(x) for x in updates
                if str(getattr(x, "session_update", "")).lower() == "agent_message_chunk"
                or "agentmessagechunk" in x.__class__.__name__.lower()
            ), secrets)
            result.final_result_seen = True
        except _AcpCancelled:
            result.status = "cancelled"
            result.error_code = "cancelled"; result.error_phase = current_phase
            if connection:
                try: await asyncio.wait_for(connection.cancel(result.session_id or ""), timeout=min(.5, max(.05, deadline-time.monotonic())))
                except Exception: pass
        except _AcpTimedOut:
            result.status = "timed_out"
            result.error = "timed_out"; result.error_code = "timed_out"; result.error_phase = current_phase
            if connection and result.session_id:
                try: await asyncio.wait_for(connection.cancel(result.session_id), timeout=.5)
                except Exception: pass
        except Exception as exc:
            reported_exc: BaseException = exc
            if auth_fault: reported_exc = auth_fault[0]
            elif interaction_fault: reported_exc = interaction_fault[0]
            elif transport_fault: reported_exc = transport_fault[0]
            result.error = _redact("acp_protocol_version_mismatch" if str(reported_exc) == "acp_protocol_version_mismatch" else (str(reported_exc) if str(reported_exc).startswith("acp_") else f"{_dump_error(reported_exc)}: {reported_exc}"), secrets)
            result.error_phase = current_phase
            if str(reported_exc) == "acp_protocol_version_mismatch": result.error_code = "acp_protocol_mismatch"
            elif str(reported_exc).startswith("acp_transport_not_advertised"): result.error_code = "acp_transport_not_advertised"
            elif str(reported_exc).startswith("acp_stale_option"): result.error_code = "acp_stale_option"
            else: result.error_code = _dump_error(reported_exc)
        finally:
            if proxy:
                try: await proxy.stop()
                except Exception: pass
            if connection:
                try: await asyncio.wait_for(connection.close(), timeout=.5)
                except Exception: pass
            if process is not None:
                if agent_pgid is not None: terminate_process_group(pid=process.pid, pgid=agent_pgid, grace_seconds=.5)
                elif process.returncode is None: terminate_process_group(pid=process.pid, grace_seconds=.5)
                if process.returncode is None:
                    try: process.terminate()
                    except ProcessLookupError: pass
            if process is not None:
                try: await asyncio.wait_for(process.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    try:
                        process.kill()
                        await asyncio.wait_for(process.wait(), timeout=1.0)
                    except Exception: pass
                result.exit_code = process.returncode
            if stderr_task:
                try:
                    result.stderr = (await asyncio.wait_for(stderr_task, timeout=1.0)).decode("utf-8", errors="replace")[:_STDERR_LIMIT]
                    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
                        if secret: result.stderr = result.stderr.replace(secret, "[REDACTED]")
                except Exception: pass
            self._process = None
            if capture_path:
                try: result.protocol_events = [_redact(frame, secrets) for frame in read_capture(capture_path)]
                except Exception: pass
            result.event_records = frames
            if workdir: shutil.rmtree(workdir, ignore_errors=True)
        return result

async def protocol_probe(manifest: dict[str, Any]) -> dict[str, Any]:
    """Launch an ACP agent and complete initialize/session/new only."""
    frames: list[dict[str, Any]] = []; workdir = None; process = None; connection = None; stderr_task = None; pgid = None
    command = manifest.get("command"); executable = command if os.path.isabs(command or "") else shutil.which(command or "")
    if not executable: return {"status":"failed", "local_ready":False, "error":"executable_missing"}
    try:
        env, secrets = _manifest_env(manifest)
    except ValueError as exc:
        return {"status":"failed", "local_ready":True, "error":str(exc)}
    started = time.monotonic(); deadline = started + 5.0; outcome: dict[str, Any] = {"status":"failed", "local_ready":True}
    def observe(event: Any) -> None:
        now = time.monotonic()
        frames.append({"direction":event.direction.value, "received_at":datetime.now(timezone.utc).isoformat(), "occurred_at":datetime.now(timezone.utc).isoformat(), "offset_ms":max(0.0,(now-started)*1000), "payload":_redact_payload(event.message, secrets)})
    client = _Client(frames, [], secrets=secrets)
    async def guarded(awaitable: Coroutine[Any, Any, Any]) -> Any:
        task = asyncio.create_task(awaitable)
        try:
            while True:
                if process is not None and process.returncode is not None and not task.done():
                    task.cancel(); await asyncio.gather(task, return_exceptions=True); raise _AcpEarlyExit(f"process exited with code {process.returncode}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    task.cancel(); await asyncio.gather(task, return_exceptions=True); raise _AcpTimedOut("probe")
                done, _ = await asyncio.wait({task}, timeout=min(remaining,.05))
                if done: return task.result()
        except asyncio.CancelledError:
            task.cancel(); await asyncio.gather(task, return_exceptions=True); raise
    try:
        workdir = tempfile.mkdtemp(prefix="mcp-pal-probe-")
        launch_command, launch_args = executable, list(manifest.get("args") or [])
        process = await asyncio.create_subprocess_exec(launch_command, *launch_args, cwd=workdir, env=env, start_new_session=(os.name != "nt"), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stderr_task = asyncio.create_task(_drain_stderr(process.stderr))
        if os.name != "nt":
            pgid = process.pid
            for _ in range(100):
                try:
                    candidate = os.getpgid(process.pid)
                    if candidate == process.pid: pgid = candidate; break
                except (ProcessLookupError, OSError): break
                await asyncio.sleep(.01)
        connection = ClientSideConnection(client, _AcpTransport(process, lambda _: None), observers=[observe])
        init = await guarded(connection.initialize(1, ClientCapabilities(), Implementation(name="mcp-pal", version="0.1")))
        if getattr(init, "protocol_version", None) != 1: raise RuntimeError("protocol_version_mismatch")
        server = McpServerStdio(name="echo", command=sys.executable, args=["-m", "mcp_pal.fixtures.echo_server"], env=[])
        session = await guarded(connection.new_session(workdir, mcp_servers=[server]))
        def dump(x: Any) -> Any:
            if hasattr(x, "model_dump"):
                return x.model_dump(mode="json")
            if isinstance(x, list):
                return [dump(v) for v in x]
            return x
        outcome = _redact({"status":"verified", "local_ready":True, "protocol_version":1, "agent_info":dump(getattr(init,"agent_info",None)), "auth_methods":dump(getattr(init,"auth_methods",None)), "agent_capabilities":dump(getattr(init,"agent_capabilities",None)), "session_id":session.session_id, "modes":dump(getattr(session,"modes",None)), "config_options":dump(getattr(session,"config_options",None))}, secrets)
    except _AcpTimedOut: outcome = {"status":"failed", "local_ready":True, "error":"timed_out"}
    except _AcpMalformedStdout: outcome = {"status":"failed", "local_ready":True, "error":"malformed_stdout"}
    except _AcpEarlyExit: outcome = {"status":"failed", "local_ready":True, "error":"early_exit"}
    except Exception as exc: outcome = {"status":"failed", "local_ready":True, "error":_redact(str(exc), secrets)}
    finally:
        if connection:
            try: await asyncio.wait_for(connection.close(), timeout=.5)
            except Exception: pass
        if process is not None:
            if pgid is not None: terminate_process_group(pid=process.pid, pgid=pgid, grace_seconds=.5)
            elif process.returncode is None: terminate_process_group(pid=process.pid, grace_seconds=.5)
            if process.returncode is None:
                try: process.terminate()
                except ProcessLookupError: pass
        if process is not None:
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=1.0)
                except Exception:
                    pass
            except Exception:
                pass
            outcome["exit_code"] = process.returncode
        if stderr_task:
            try:
                outcome["stderr"] = (await asyncio.wait_for(stderr_task, timeout=1.0)).decode("utf-8", errors="replace")[:_STDERR_LIMIT]
                for secret in sorted((s for s in secrets if s), key=len, reverse=True):
                    if secret: outcome["stderr"] = outcome["stderr"].replace(secret, "[REDACTED]")
            except Exception: pass
        outcome["frames"] = frames
        if workdir: shutil.rmtree(workdir, ignore_errors=True)
    return outcome

async def full_probe(manifest: dict[str, Any], mode_id: str | None = None, session_config: dict[str, Any] | None = None, transport: str = "stdio", allow_private_upstream: bool = False) -> dict[str, Any]:
    """Run a strict nonce turn against the packaged echo MCP server.

    HTTP/SSE probes start their own loopback service and opt into private
    upstream access only for that service.  Evidence is accepted only when
    the complete ACP lifecycle, wire call, streamed update, and correlation
    checks pass; a matching substring in an unrelated frame is insufficient.
    """
    import secrets
    from ..fixtures.echo_http import EchoMcpHttpServer

    nonce = "mcp-pal-probe-" + secrets.token_hex(8)
    config = dict(session_config or {})
    if transport not in {"stdio", "http", "sse"}:
        return {"status": "failed", "error": "unsupported_transport", "transport": transport, "calls": [], "nonce": nonce}

    echo_service = None
    try:
        if transport == "stdio":
            server = {"command": sys.executable, "args": ["-m", "mcp_pal.fixtures.echo_server"]}
        else:
            echo_service = EchoMcpHttpServer(transport)
            upstream_url = await echo_service.start()
            server = {"type": transport, "url": upstream_url}
        spec = AcpRunSpec(
            prompt="Call the echo tool with the exact nonce in the text argument: " + nonce,
            model="agent-default",
            mcp_config={"mcpServers": {"echo": server}},
            enabled_server="echo",
            manifest=manifest,
            tool_mode="agent_default",
            agent_mode_id=mode_id,
            session_config=config,
            timeout_seconds=30,
            # Packaged probe endpoints are loopback-only and ephemeral.  This
            # opt-in never comes from a persisted user manifest.
            allow_private_upstream=True,
        )
        result = await AcpHarnessRunner(manifest).run(spec)
    except Exception as exc:
        return {"status": "failed", "error": str(exc), "transport": transport, "calls": [], "nonce": nonce}
    finally:
        if echo_service is not None:
            try:
                await echo_service.stop()
            except Exception:
                pass

    def payload(frame: dict[str, Any]) -> dict[str, Any]:
        value = frame.get("payload") if isinstance(frame, dict) else None
        return value if isinstance(value, dict) else {}

    protocol = [frame for frame in result.protocol_events if payload(frame)]
    def id_key(value: Any) -> tuple[str, str]:
        return type(value).__name__, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    pending: dict[tuple[str, str], list[dict[str, Any]]] = {}
    calls: list[dict[str, Any]] = []
    for frame in protocol:
        value = payload(frame) or {}
        if frame.get("direction") == "client_to_server" and value.get("method") == "tools/call" and value.get("id") is not None:
            pending.setdefault(id_key(value["id"]), []).append(frame)
            continue
        key = id_key(value["id"]) if value.get("id") is not None else None
        if frame.get("direction") == "server_to_client" and key in pending:
            request = pending[key].pop(0)
            if not pending[key]: pending.pop(key)
            request_payload = payload(request) or {}
            latency = float(frame.get("offset_ms", 0)) - float(request.get("offset_ms", 0))
            arguments = ((request_payload.get("params") or {}).get("arguments"))
            calls.append({
                "request": request,
                "response": frame,
                "nonce": nonce,
                "arguments": arguments,
                "result": value.get("result"),
                "latency_ms": latency,
            })

    def exact_echo(call: dict[str, Any]) -> bool:
        arguments = call.get("arguments")
        response = call.get("result")
        latency = call.get("latency_ms")
        return (
            arguments == {"text": nonce}
            and isinstance(latency, (int, float)) and not isinstance(latency, bool) and latency >= 0
            and isinstance(response, dict) and response.get("isError") is False
            and response.get("content") == [{"type": "text", "text": nonce}]
        )

    prompt_ids = {
        str(value.get("id"))
        for frame in result.event_records
        if (value := payload(frame))
        and frame.get("direction") == "client_to_server"
        and value.get("method") == "session/prompt"
    }
    prompt_completed = any(
        (value := payload(frame))
        and frame.get("direction") == "server_to_client"
        and str(value.get("id")) in prompt_ids
        and isinstance(value.get("result"), dict)
        and value["result"].get("stopReason") == "end_turn"
        for frame in result.event_records
    )
    identity = next(
        (value["result"]["agentInfo"] for frame in result.event_records
         if (value := payload(frame))
         and frame.get("direction") == "server_to_client"
         and value.get("result", {}).get("agentInfo") is not None),
        None,
    )
    updates = [
        value.get("params", {}).get("update")
        for frame in result.event_records
        if (value := payload(frame)) and value.get("method") == "session/update"
    ]

    def update_has_nonce(update: Any) -> bool:
        if not isinstance(update, dict) or update.get("sessionUpdate") != "tool_call_update":
            return False
        for item in update.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "content":
                content = item.get("content")
                if isinstance(content, dict) and content.get("type") == "text" and content.get("text") == nonce:
                    return True
        return False

    tool_update = any(update_has_nonce(update) for update in updates)
    message_text = "".join(
        str(update["content"].get("text", ""))
        for update in updates
        if isinstance(update, dict)
        and update.get("sessionUpdate") == "agent_message_chunk"
        and isinstance(update.get("content"), dict)
        and update["content"].get("type") == "text"
    )
    message_update = message_text == nonce
    mode_frames = [payload(frame) for frame in result.event_records if payload(frame).get("method") == "session/set_mode"]
    config_frames = [payload(frame) for frame in result.event_records if payload(frame).get("method") == "session/set_config_option"]
    applied_mode = mode_id is None or any((frame.get("params") or {}).get("modeId") == mode_id for frame in mode_frames)
    applied_config = {
        key: any((frame.get("params") or {}).get("configId") == key and (frame.get("params") or {}).get("value") == value for frame in config_frames)
        for key, value in config.items()
    }
    valid = bool(
        result.status == "completed"
        and result.final_result_seen
        and prompt_completed
        and len(calls) == 1
        and exact_echo(calls[0])
        and tool_update
        and message_update
        and applied_mode
        and all(applied_config.values())
    )
    return {
        "status": "verified" if valid else "failed",
        "nonce": nonce,
        "calls": calls,
        "agent_identity": identity,
        "identity_available": identity is not None,
        "mode_id": mode_id,
        "session_config": config,
        "applied_mode": applied_mode,
        "applied_config": applied_config,
        "transport": transport,
        "configured_transport": result.configured_transport,
        "instrumented_transport": result.instrumented_transport,
        "frames": result.event_records,
        "mcp_frames": result.protocol_events,
        "updates": updates,
        "prompt_completed": prompt_completed,
        "tool_update": tool_update,
        "message_update": message_update,
        "error": result.error,
    }
