"""ACP v1 subprocess runner backed by the official ``agent-client-protocol`` SDK."""
from __future__ import annotations
import asyncio, os, shutil, tempfile, time, sys, json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from acp.client.connection import ClientSideConnection
from acp.schema import AcpMcpServer
from acp.connection import StreamDirection
from acp.schema import ClientCapabilities, Implementation, McpServerStdio, HttpMcpServer, SseMcpServer, TextContentBlock
from ..trace.claude import transport_for_server
from ..transport.http_proxy import McpHttpProxy
from ..trace.capture import read_capture
from .base import AcpRunSpec, HarnessResult
from .process_group import terminate_process_group


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
    def __init__(self, process, on_malformed, on_fault=None):
        self.process = process
        self.on_malformed = on_malformed
        self.on_fault = on_fault
        self.closed = False

    async def send(self, message):
        if self.closed or self.process.stdin is None:
            raise ConnectionError("ACP stdin closed")
        data = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    async def receive(self):
        if self.process.stdout is None:
            return None
        line = await self.process.stdout.readline()
        if not line:
            # EOF before the caller completes its expected lifecycle is an
            # early exit even when the child chose status 0.
            try: await self.process.wait()
            except Exception: pass
            error = _AcpEarlyExit(f"process exited with code {self.process.returncode}")
            if self.on_fault: self.on_fault(error)
            raise error
        if len(line) > _STDOUT_LIMIT:
            raise _AcpMalformedStdout("ACP stdout frame exceeds bounded limit")
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

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
                await self.process.stdin.wait_closed()
            except (AttributeError, OSError, RuntimeError):
                pass


async def _drain_stderr(reader, limit: int = _STDERR_LIMIT) -> bytes:
    if reader is None:
        return b""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await reader.read(8192)
        if not chunk:
            break
        if total < limit:
            chunks.append(chunk[: max(0, limit - total)])
            total += len(chunks[-1])
    return b"".join(chunks)


def _manifest_env(manifest: dict[str, Any]) -> tuple[dict[str, str], set[str]]:
    env = os.environ.copy()
    secrets: set[str] = set()
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
        output = {}
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
    def __init__(self, frames, output, callback=None, interaction_fault=None, secrets=None):
        self.frames, self.output, self.callback, self.interaction_fault = frames, output, callback, interaction_fault
        self.secrets = secrets or set()
    async def session_update(self, session_id, update, **kwargs):
        self.output.append(update)
        if self.callback:
            serial=_redact(self._json(update), self.secrets)
            value=self.callback(serial, "acp_update", {"session_id":session_id, "update":serial})
            if asyncio.iscoroutine(value): await value
    @staticmethod
    def _json(value):
        if hasattr(value,"model_dump"): return value.model_dump(mode="json")
        return value
    def _interaction(self, kind):
        error = RuntimeError(f"acp_interaction_required: {kind}")
        if self.interaction_fault is not None: self.interaction_fault.append(error)
        return error
    async def request_permission(self, *args, **kwargs): raise self._interaction("permission")
    async def create_elicitation(self, *args, **kwargs): raise self._interaction("elicitation")
    async def read_text_file(self, *args, **kwargs): raise self._interaction("filesystem")
    async def write_text_file(self, *args, **kwargs): raise self._interaction("filesystem")
    async def create_terminal(self, *args, **kwargs): raise self._interaction("terminal")
    async def authenticate(self, *args, **kwargs): raise RuntimeError("acp_auth_required: out-of-band authentication")
    async def ext_method(self, method, params, **kwargs): return {}
    async def ext_notification(self, method, params, **kwargs): return None

class AcpHarnessRunner:
    def __init__(self, manifest=None): self.manifest = manifest or {}; self._process = None; self._cancel = False
    def request_cancel(self): self._cancel = True
    async def run(self, spec: AcpRunSpec, on_event: Callable | None = None, cancel_event: Any = None) -> HarnessResult:
        result = HarnessResult("failed", transport="stdio")
        frames: list[dict[str, Any]] = []; updates: list[Any] = []
        started = time.monotonic(); deadline = started + max(0.01, float(spec.timeout_seconds)); process_started = started
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
        def observe(event):
            now = time.monotonic()
            payload = _redact_payload(event.message, secrets)
            frames.append({"direction": "client_to_server" if event.direction == StreamDirection.OUTGOING else "server_to_client", "received_at": datetime.now(timezone.utc).isoformat(), "occurred_at": datetime.now(timezone.utc).isoformat(), "offset_ms": max(0.0, (now - started) * 1000), "payload": payload})
            if event.direction != StreamDirection.OUTGOING and isinstance(event.message, dict):
                method = str(event.message.get("method") or "")
                if method == "authenticate":
                    auth_fault.append(RuntimeError("acp_auth_required: out-of-band authentication"))
                if method in {"request_permission", "session/request_permission", "fs/read_text_file", "fs/write_text_file", "read_text_file", "write_text_file", "terminal/create", "create_terminal", "elicitation/create", "create_elicitation"}:
                    interaction_fault.append(RuntimeError("acp_interaction_required: " + method))
        def malformed(excerpt):
            safe_excerpt = _redact(excerpt[:512], secrets)
            frames.append({"direction":"server_to_client", "received_at":datetime.now(timezone.utc).isoformat(), "occurred_at":datetime.now(timezone.utc).isoformat(), "offset_ms":max(0.0,(time.monotonic()-started)*1000), "payload":{"error":"acp_malformed_stdout","excerpt":safe_excerpt}})
        client = _Client(frames, updates, on_event, interaction_fault, secrets)

        async def guarded(awaitable, phase: str):
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
            baseline = int(time.perf_counter_ns())
            if selected_transport in {"http", "sse"}:
                proxy = McpHttpProxy(upstream_url=selected["url"], configured_headers=selected.get("headers"), transport=selected_transport, capture_path=capture_path, baseline_ns=baseline, allow_private=spec.allow_private_upstream)
                local_url = await guarded(proxy.start(), "session/new")
                cls = HttpMcpServer if selected_transport == "http" else SseMcpServer
                server = cls(name=spec.enabled_server, url=local_url, headers=[], type=selected_transport)
            else:
                server = McpServerStdio(name=spec.enabled_server, command=sys.executable, args=["-m", "mcp_pal.transport.stdio_proxy", "--capture", capture_path, "--baseline", str(baseline), "--", selected.get("command", ""), *(selected.get("args") or [])], env=selected_env)
            session = await guarded(connection.new_session(workdir, mcp_servers=[server]), "session/new"); result.session_id = session.session_id
            session_result = next((
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
            def text_of(value):
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
            if auth_fault: exc = auth_fault[0]
            elif interaction_fault: exc = interaction_fault[0]
            elif transport_fault: exc = transport_fault[0]
            result.error = _redact("acp_protocol_version_mismatch" if str(exc) == "acp_protocol_version_mismatch" else (str(exc) if str(exc).startswith("acp_") else f"{_dump_error(exc)}: {exc}"), secrets)
            result.error_phase = current_phase
            if str(exc) == "acp_protocol_version_mismatch": result.error_code = "acp_protocol_mismatch"
            elif str(exc).startswith("acp_transport_not_advertised"): result.error_code = "acp_transport_not_advertised"
            elif str(exc).startswith("acp_stale_option"): result.error_code = "acp_stale_option"
            else: result.error_code = _dump_error(exc)
        finally:
            if proxy:
                try: await proxy.stop()
                except Exception: pass
            if connection:
                try: await asyncio.wait_for(connection.close(), timeout=.5)
                except Exception: pass
            if process is not None:
                if agent_pgid is not None: terminate_process_group(pgid=agent_pgid, grace_seconds=.5)
                elif process.returncode is None: terminate_process_group(pid=process.pid, grace_seconds=.5)
                if process.returncode is None:
                    try: process.terminate()
                    except ProcessLookupError: pass
            if process is not None:
                try: await asyncio.wait_for(process.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    try: process.kill(); await process.wait()
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

async def protocol_probe(manifest: dict) -> dict:
    """Launch an ACP agent and complete initialize/session/new only."""
    frames: list[dict[str, Any]] = []; workdir = None; process = None; connection = None; stderr_task = None; pgid = None
    command = manifest.get("command"); executable = command if os.path.isabs(command or "") else shutil.which(command or "")
    if not executable: return {"status":"failed", "local_ready":False, "error":"executable_missing"}
    try:
        env, secrets = _manifest_env(manifest)
    except ValueError as exc:
        return {"status":"failed", "local_ready":True, "error":str(exc)}
    started = time.monotonic(); deadline = started + 5.0; outcome: dict[str, Any] = {"status":"failed", "local_ready":True}
    def observe(event):
        now = time.monotonic()
        frames.append({"direction":event.direction.value, "received_at":datetime.now(timezone.utc).isoformat(), "occurred_at":datetime.now(timezone.utc).isoformat(), "offset_ms":max(0.0,(now-started)*1000), "payload":_redact_payload(event.message, secrets)})
    client = _Client(frames, [], secrets=secrets)
    async def guarded(awaitable):
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
        dump = lambda x: x.model_dump(mode="json") if hasattr(x, "model_dump") else ([dump(v) for v in x] if isinstance(x, list) else x)
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
            if pgid is not None: terminate_process_group(pgid=pgid, grace_seconds=.5)
            elif process.returncode is None: terminate_process_group(pid=process.pid, grace_seconds=.5)
            if process.returncode is None:
                try: process.terminate()
                except ProcessLookupError: pass
        if process is not None:
            try: await asyncio.wait_for(process.wait(), timeout=1.0)
            except Exception: pass
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

async def full_probe(manifest: dict, mode_id: str | None = None, session_config: dict | None = None, transport: str = "stdio", allow_private_upstream: bool = False) -> dict:
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

    def payload(frame: dict[str, Any]) -> dict[str, Any] | None:
        value = frame.get("payload") if isinstance(frame, dict) else None
        return value if isinstance(value, dict) else None

    protocol = [frame for frame in result.protocol_events if payload(frame) is not None]
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
        if (value := payload(frame)) is not None
        and frame.get("direction") == "client_to_server"
        and value.get("method") == "session/prompt"
    }
    prompt_completed = any(
        (value := payload(frame)) is not None
        and frame.get("direction") == "server_to_client"
        and str(value.get("id")) in prompt_ids
        and isinstance(value.get("result"), dict)
        and value["result"].get("stopReason") == "end_turn"
        for frame in result.event_records
    )
    identity = next(
        (value["result"]["agentInfo"] for frame in result.event_records
         if (value := payload(frame)) is not None
         and frame.get("direction") == "server_to_client"
         and value.get("result", {}).get("agentInfo") is not None),
        None,
    )
    updates = [
        value.get("params", {}).get("update")
        for frame in result.event_records
        if (value := payload(frame)) is not None and value.get("method") == "session/update"
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
    mode_frames = [payload(frame) for frame in result.event_records if payload(frame) and payload(frame).get("method") == "session/set_mode"]
    config_frames = [payload(frame) for frame in result.event_records if payload(frame) and payload(frame).get("method") == "session/set_config_option"]
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
