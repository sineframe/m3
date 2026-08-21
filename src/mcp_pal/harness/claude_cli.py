import asyncio, json, os, signal, tempfile, threading, time
from pathlib import Path
from typing import Any, Callable
from .base import HarnessResult, HarnessRunner, RunSpec
from ..domain.events import normalize_events
from ..domain.validation import selected_server_config

READ_ONLY_TOOLS = ["Agent", "Read", "Glob", "Grep", "LSP", "WebFetch", "WebSearch", "ToolSearch", "ListMcpResourcesTool", "ReadMcpResourceTool", "TaskGet", "TaskList", "TaskOutput"]

class ClaudeCodeRunner(HarnessRunner):
    def __init__(self, executable: str = "claude"):
        self.executable = executable; self.process = None; self.cancel_requested = threading.Event()
    def request_cancel(self):
        """Thread-safe cancellation request; the async runner performs reap."""
        self.cancel_requested.set(); self._terminate()
    def build_command(self, spec: RunSpec, config_path: str) -> list[str]:
        cmd = [self.executable, "--print", "--input-format", "text", "--bare", "--output-format", "stream-json", "--verbose", "--strict-mcp-config", "--mcp-config", config_path, "--no-session-persistence", "--model", spec.model, "--max-turns", str(spec.max_turns), "--max-budget-usd", str(spec.max_budget_usd)]
        if spec.tool_mode == "mcp_only": cmd += ["--tools", "", "--allowedTools", f"mcp__{spec.enabled_server}__*"]
        elif spec.tool_mode == "mcp_read_only": cmd += ["--tools", ",".join(READ_ONLY_TOOLS), "--allowedTools", ",".join(READ_ONLY_TOOLS + [f"mcp__{spec.enabled_server}__*"])]
        else: cmd += ["--tools", "default", "--dangerously-skip-permissions"]
        return cmd
    async def run(self, spec: RunSpec, on_event=None, cancel_event=None) -> HarnessResult:
        result = HarnessResult(status="running")
        with tempfile.TemporaryDirectory(prefix="mcp-pal-") as td:
            path = os.path.join(td, "mcp.json")
            Path(path).write_text(json.dumps(selected_server_config(spec.mcp_config, spec.enabled_server)), encoding="utf-8")
            os.chmod(path, 0o600)
            try:
                self.process = await asyncio.create_subprocess_exec(*self.build_command(spec, path), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=td, start_new_session=True)
                self.process.stdin.write(spec.prompt.encode()); await self.process.stdin.drain(); self.process.stdin.close()
                async def read_stdout():
                    async for line in self.process.stdout:
                        rawline = line.decode(errors="replace").rstrip("\n")
                        try: raw = json.loads(rawline)
                        except Exception: raw = rawline
                        result.events.append(raw)
                        normalized = normalize_events(raw, spec.enabled_server)
                        result.normalized.extend(normalized)
                        for et, payload in normalized:
                            if et != "assistant_text": continue
                            if not result.final_result_seen:
                                txt = payload.get("text") or ""; result.final_text += txt if isinstance(txt, str) else (json.dumps(txt, ensure_ascii=False) if txt else "")
                        if isinstance(raw, dict):
                            result.session_id = result.session_id or raw.get("session_id")
                            result.cost_usd = raw.get("total_cost_usd", raw.get("cost_usd", result.cost_usd))
                            result.turns = raw.get("num_turns", result.turns)
                            if raw.get("type") in ("result", "final") and raw.get("result") is not None:
                                result.final_text = raw.get("result") if isinstance(raw.get("result"), str) else json.dumps(raw.get("result"), ensure_ascii=False)
                                result.final_result_seen = True
                        if on_event:
                            for et, payload in normalized:
                                value = on_event(raw, et, payload)
                                if asyncio.iscoroutine(value): await value
                task = asyncio.create_task(read_stdout())
                stderr_task = asyncio.create_task(self.process.stderr.read())
                wait_task = asyncio.create_task(self.process.wait())
                cancel_task = asyncio.create_task(cancel_event.wait()) if cancel_event else None
                try:
                    deadline=time.monotonic()+spec.timeout_seconds
                    while not wait_task.done():
                        async_cancel=cancel_task and cancel_task.done()
                        if self.cancel_requested.is_set() or async_cancel:
                            result.status="cancelled"; await self._terminate_and_reap(); break
                        remaining=deadline-time.monotonic()
                        if remaining <= 0:
                            result.status="timed_out"; await self._terminate_and_reap(); break
                        try: await asyncio.wait_for(asyncio.shield(wait_task), timeout=min(.1,remaining))
                        except asyncio.TimeoutError: continue
                    if wait_task.done() and result.status == "running":
                        result.exit_code = self.process.returncode
                        if self.cancel_requested.is_set() or (cancel_task and cancel_task.done()): result.status="cancelled"
                except asyncio.CancelledError:
                    result.status = "cancelled"; await self._terminate_and_reap(); raise
                finally:
                    if cancel_task: cancel_task.cancel()
                await asyncio.gather(wait_task, return_exceptions=True)
                await asyncio.gather(task, return_exceptions=True)
                result.stderr = (await stderr_task).decode(errors="replace")
                result.exit_code = self.process.returncode
                if result.status == "running": result.status = "completed" if result.exit_code == 0 else "failed"
                if result.status != "completed" and result.exit_code not in (0, None): result.error = f"Claude exited with code {result.exit_code}"
            except FileNotFoundError as e: result.status, result.error = "failed", str(e)
            except asyncio.CancelledError: raise
            except Exception as e:
                result.status, result.error = "failed", str(e)
                if self.process and self.process.returncode is None: await self._terminate_and_reap()
        return result
    async def _terminate_and_reap(self):
        if self.process and self.process.returncode is None:
            try: os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: await asyncio.wait_for(self.process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                try: os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                await self.process.wait()

    def _terminate(self):
        """Synchronous best-effort signal used by API cancellation."""
        if self.process and self.process.returncode is None:
            try: os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
