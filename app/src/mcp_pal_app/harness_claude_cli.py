import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from mcp_pal.domain.validation import selected_server_config
from mcp_pal.harness.base import HarnessResult, HarnessRunner, RunSpec
from mcp_pal.harness.native import drain_bounded
from mcp_pal.harness.process_group import terminate_process_group
from mcp_pal.trace.capture import read_capture
from mcp_pal.trace.claude import transport_for_server
from mcp_pal.trace.redaction import known_secret_values, redact
from mcp_pal.transport.capture_proxy import write_stdio_handoff
from mcp_pal.transport.http_proxy import McpHttpProxy
from mcp_pal_app.domain.events import normalize_events

_STDERR_LIMIT = 64 * 1024

READ_ONLY_TOOLS = [
    "Agent",
    "Read",
    "Glob",
    "Grep",
    "LSP",
    "WebFetch",
    "WebSearch",
    "ToolSearch",
    "ListMcpResourcesTool",
    "ReadMcpResourceTool",
    "TaskGet",
    "TaskList",
    "TaskOutput",
]


def _redact(value: Any, secrets: set[str]) -> Any:
    try:
        return redact(value, secrets=secrets)[0]
    except Exception:
        return "[REDACTED]"


class ClaudeCodeRunner(HarnessRunner):
    def __init__(self, executable: str = "claude"):
        self.executable = executable
        self.process: Any = None
        self.cancel_requested = threading.Event()

    def request_cancel(self) -> None:
        """Thread-safe cancellation request; the async runner performs reap."""
        self.cancel_requested.set()
        self._terminate()

    def build_command(self, spec: RunSpec, config_path: str) -> list[str]:
        cmd = [
            self.executable,
            "--print",
            "--input-format",
            "text",
            "--bare",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--verbose",
            "--strict-mcp-config",
            "--mcp-config",
            config_path,
            "--no-session-persistence",
            "--model",
            spec.model,
            "--max-turns",
            str(spec.max_turns),
            "--max-budget-usd",
            str(spec.max_budget_usd),
        ]
        if spec.tool_mode == "mcp_only":
            cmd += ["--tools", "", "--allowedTools", f"mcp__{spec.enabled_server}__*"]
        elif spec.tool_mode == "mcp_read_only":
            cmd += [
                "--tools",
                ",".join(READ_ONLY_TOOLS),
                "--allowedTools",
                ",".join([*READ_ONLY_TOOLS, f"mcp__{spec.enabled_server}__*"]),
            ]
        else:
            cmd += ["--tools", "default", "--dangerously-skip-permissions"]
        return cmd

    async def run(
        self,
        spec: RunSpec,
        on_event: Any = None,
        cancel_event: asyncio.Event | None = None,
    ) -> HarnessResult:
        result = HarnessResult(status="running")
        partial_text_seen = False
        secrets: set[str] = set(known_secret_values())
        with tempfile.TemporaryDirectory(prefix="mcp-pal-") as td:
            path = os.path.join(td, "mcp.json")
            capture_path = os.path.join(td, "mcp-capture.jsonl")
            baseline_ns = time.perf_counter_ns()
            config = selected_server_config(spec.mcp_config, spec.enabled_server)
            server = config["mcpServers"][spec.enabled_server]
            result.transport = transport_for_server(server)
            proxy: McpHttpProxy | None = None
            try:
                if result.transport == "stdio":
                    original = dict(server)
                    command = original.get("command")
                    args = original.get("args", [])
                    handoff = os.path.join(td, "mcp-env.json")
                    secrets.update(
                        write_stdio_handoff(handoff, original.get("env") or {})
                    )
                    # The relay receives an argv vector after `--`; no shell or
                    # user-controlled command interpolation is involved.
                    original["command"] = sys.executable
                    original["env"] = {}
                    relay_args = [
                        "-m",
                        "mcp_pal.transport.stdio_proxy",
                        "--capture",
                        capture_path,
                        "--baseline",
                        str(baseline_ns),
                        "--env-file",
                        handoff,
                    ]
                    if isinstance(original.get("cwd"), str) and original["cwd"]:
                        relay_args.extend(["--cwd", original["cwd"]])
                    relay_args.extend(["--", str(command), *(str(arg) for arg in args)])
                    original["args"] = relay_args
                    config["mcpServers"][spec.enabled_server] = original
                elif result.transport in {"http", "sse"}:
                    proxy = McpHttpProxy(
                        upstream_url=server["url"],
                        configured_headers=server.get("headers"),
                        transport=result.transport,
                        capture_path=capture_path,
                        baseline_ns=baseline_ns,
                        secrets=secrets,
                    )
                    local_url = await proxy.start()
                    safe_server = {**server, "url": local_url}
                    safe_server.pop("headers", None)
                    config["mcpServers"][spec.enabled_server] = safe_server
                Path(path).write_text(json.dumps(config), encoding="utf-8")
                os.chmod(path, 0o600)
            except Exception:
                if proxy is not None:
                    try:
                        await proxy.stop()
                    except Exception:
                        pass
                result.status, result.error = "failed", "Claude transport setup failed"
                try:
                    result.protocol_events = read_capture(capture_path)
                except Exception:
                    result.protocol_events = []
                return result
            try:
                self.process = await asyncio.create_subprocess_exec(
                    *self.build_command(spec, path),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=td,
                    start_new_session=True,
                )
                self.process.stdin.write(spec.prompt.encode())
                await self.process.stdin.drain()
                self.process.stdin.close()

                async def read_stdout() -> None:
                    nonlocal partial_text_seen
                    async for line in self.process.stdout:
                        rawline = line.decode(errors="replace").rstrip("\n")
                        try:
                            raw = json.loads(rawline)
                        except Exception:
                            raw = rawline
                        raw = _redact(raw, secrets)
                        result.events.append(raw)
                        result.event_records.append(
                            {
                                "source": "claude",
                                "occurred_at": datetime.now(timezone.utc).isoformat(),
                                "offset_ms": max(
                                    0.0,
                                    (time.perf_counter_ns() - baseline_ns) / 1_000_000,
                                ),
                                "raw_event": raw,
                                "type": raw.get("type")
                                if isinstance(raw, dict)
                                else "malformed",
                            }
                        )
                        normalized = normalize_events(raw, spec.enabled_server)
                        result.normalized.extend(normalized)
                        for et, payload in normalized:
                            if et != "assistant_text":
                                continue
                            if not result.final_result_seen:
                                if payload.get("partial"):
                                    partial_text_seen = True
                                elif partial_text_seen:
                                    continue
                                txt = payload.get("text") or ""
                                result.final_text += (
                                    txt
                                    if isinstance(txt, str)
                                    else (
                                        json.dumps(txt, ensure_ascii=False)
                                        if txt
                                        else ""
                                    )
                                )
                        if isinstance(raw, dict):
                            result.session_id = result.session_id or raw.get(
                                "session_id"
                            )
                            result.cost_usd = raw.get(
                                "total_cost_usd", raw.get("cost_usd", result.cost_usd)
                            )
                            result.turns = raw.get("num_turns", result.turns)
                            if (
                                raw.get("type") in ("result", "final")
                                and raw.get("result") is not None
                            ):
                                result.final_text = (
                                    cast(str, raw.get("result"))
                                    if isinstance(raw.get("result"), str)
                                    else json.dumps(
                                        raw.get("result"), ensure_ascii=False
                                    )
                                )
                                result.final_result_seen = True
                        if on_event:
                            for et, payload in normalized:
                                value = on_event(raw, et, payload)
                                if asyncio.iscoroutine(value):
                                    await value

                task = asyncio.create_task(read_stdout())
                stderr_task = asyncio.create_task(
                    drain_bounded(self.process.stderr, maximum=_STDERR_LIMIT)
                )
                wait_task = asyncio.create_task(self.process.wait())
                cancel_task = (
                    asyncio.create_task(cancel_event.wait()) if cancel_event else None
                )
                try:
                    deadline = time.monotonic() + spec.timeout_seconds
                    while not wait_task.done():
                        async_cancel = cancel_task and cancel_task.done()
                        if self.cancel_requested.is_set() or async_cancel:
                            result.status = "cancelled"
                            await self._terminate_and_reap()
                            break
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            result.status = "timed_out"
                            await self._terminate_and_reap()
                            break
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(wait_task), timeout=min(0.1, remaining)
                            )
                        except asyncio.TimeoutError:
                            continue
                    if wait_task.done() and result.status == "running":
                        result.exit_code = self.process.returncode
                        if self.cancel_requested.is_set() or (
                            cancel_task and cancel_task.done()
                        ):
                            result.status = "cancelled"
                except asyncio.CancelledError:
                    result.status = "cancelled"
                    await self._terminate_and_reap()
                    raise
                finally:
                    if cancel_task:
                        cancel_task.cancel()
                await asyncio.gather(wait_task, return_exceptions=True)
                # A normally exited parent can leave a descendant holding an
                # inherited stderr pipe.  Reconcile the owned group before
                # awaiting the drain task so EOF is deterministic.
                await self._terminate_and_reap()
                await asyncio.gather(task, return_exceptions=True)
                result.stderr = _redact(
                    (await stderr_task).decode(errors="replace"), secrets
                )
                result.exit_code = self.process.returncode
                if result.status == "running":
                    result.status = "completed" if result.exit_code == 0 else "failed"
                if result.status != "completed" and result.exit_code not in (0, None):
                    result.error = f"Claude exited with code {result.exit_code}"
            except FileNotFoundError as e:
                result.status, result.error = "failed", _redact(str(e), secrets)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                result.status, result.error = "failed", _redact(str(e), secrets)
                if self.process and self.process.returncode is None:
                    await self._terminate_and_reap()
            finally:
                if proxy is not None:
                    await proxy.stop()
            result.protocol_events = read_capture(capture_path)
        return result

    async def _terminate_and_reap(self) -> None:
        if self.process:
            await asyncio.to_thread(
                terminate_process_group,
                pid=self.process.pid,
                pgid=self.process.pid if os.name == "posix" else None,
                grace_seconds=0.5,
            )
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                try:
                    self.process.kill()
                except (OSError, ProcessLookupError):
                    pass
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass

    def _terminate(self) -> None:
        """Synchronous best-effort signal used by API cancellation."""
        if self.process and self.process.returncode is None:
            # This path is intentionally best effort; the async owner will
            # perform the bounded TERM/KILL/reap sequence before returning.
            try:
                if os.name == "posix":
                    terminate_process_group(
                        pid=self.process.pid,
                        pgid=self.process.pid,
                        grace_seconds=0.0,
                    )
                else:
                    self.process.terminate()
            except (OSError, ProcessLookupError):
                pass
