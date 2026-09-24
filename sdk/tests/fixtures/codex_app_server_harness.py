"""Isolated runner for the installed, unmodified Codex App Server binary."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class CodexUnavailable(RuntimeError):
    pass


def require_codex() -> tuple[str, str]:
    executable = os.environ.get("M3_CODEX_EXECUTABLE") or shutil.which("codex")
    if executable is None:
        raise CodexUnavailable("Codex is unavailable; set M3_CODEX_EXECUTABLE")
    try:
        version = subprocess.run(
            [executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodexUnavailable("Codex could not be executed") from exc
    expected = os.environ.get("M3_CODEX_MRTR_VERSION", "codex-cli 0.156.1")
    if version != expected:
        raise CodexUnavailable(f"expected {expected!r}, found {version!r}")
    return executable, version


class CodexAppServer:
    """One real app-server process with a temporary home and local fixtures."""

    def __init__(
        self,
        *,
        executable: str,
        codex_home: Path,
        workspace: Path,
        provider_url: str,
        mcp_server: Path,
        wire_marker: Path,
    ) -> None:
        self.executable = executable
        self.codex_home = codex_home
        self.workspace = workspace
        self.provider_url = provider_url
        self.mcp_server = mcp_server
        self.wire_marker = wire_marker
        self.process: asyncio.subprocess.Process | None = None
        self._frames: asyncio.Queue[Mapping[str, Any] | None] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None
        self._request_id = 0
        self.thread_id: str | None = None
        self.events: list[Mapping[str, Any]] = []

    def _write_config(self) -> None:
        self.codex_home.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        config = "\n".join(
            (
                'model = "m3-fixture-model"',
                'model_provider = "m3-fixture"',
                'approval_policy = "on-request"',
                'sandbox_mode = "read-only"',
                "disable_response_storage = true",
                "",
                "[features]",
                "mcp_2026_07_28 = true",
                "",
                "[model_providers.m3-fixture]",
                'name = "m3 local deterministic provider"',
                f"base_url = {json.dumps(self.provider_url)}",
                'wire_api = "responses"',
                "requires_openai_auth = false",
                "supports_websockets = false",
                "request_max_retries = 0",
                "stream_max_retries = 0",
                "",
                '[mcp_servers."fixture"]',
                f"command = {json.dumps(os.environ.get('PYTHON', 'python3'))}",
                f"args = [{json.dumps(str(self.mcp_server))}]",
                "env = {",
                '  "CODEX_MCP_PROTOCOL_VERSION" = "2026-07-28",',
                f'  "M3_CODEX_MRTR_WIRE_MARKER" = {json.dumps(str(self.wire_marker))},',
                "}",
                "",
            )
        )
        path = self.codex_home / "config.toml"
        path.write_text(config, encoding="utf-8")
        path.chmod(0o600)

    async def start(self) -> None:
        self._write_config()
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CODEX_HOME": str(self.codex_home),
            "CODEX_APP_SERVER_DISABLE_MANAGED_CONFIG": "1",
            "HOME": str(self.codex_home),
            "TMPDIR": str(self.codex_home),
            "TERM": "dumb",
            "NO_COLOR": "1",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "RUST_LOG": "error",
        }
        self.process = await asyncio.create_subprocess_exec(
            self.executable,
            "app-server",
            "--stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.workspace,
            env=environment,
        )
        self._reader = asyncio.create_task(self._read_stdout())
        await self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "m3-mrtr-characterization",
                    "version": "1.0.0",
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        await self.notify("initialized", {})
        started = await self.request(
            "thread/start",
            {
                "model": "m3-fixture-model",
                "cwd": str(self.workspace),
                "approvalPolicy": "on-request",
                "sandbox": "read-only",
                "ephemeral": True,
            },
        )
        thread = started.get("thread", started)
        if not isinstance(thread, Mapping) or not isinstance(thread.get("id"), str):
            raise AssertionError(
                f"thread/start response did not contain a thread id: {started!r}"
            )
        self.thread_id = thread["id"]

    async def request(
        self, method: str, params: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        await self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": dict(params),
            }
        )
        while True:
            frame = await self.next_frame()
            if frame is None:
                raise AssertionError(f"app-server exited waiting for {method}")
            if frame.get("id") == request_id:
                if frame.get("error") is not None:
                    raise AssertionError(
                        f"app-server {method} failed: {frame['error']!r}"
                    )
                result = frame.get("result")
                if not isinstance(result, Mapping):
                    raise AssertionError(
                        f"app-server {method} returned no result: {frame!r}"
                    )
                return result
            self.events.append(frame)

    async def notify(self, method: str, params: Mapping[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": dict(params)})

    async def start_turn(self, prompt: str) -> int:
        if self.thread_id is None:
            raise AssertionError("Codex thread is not initialized")
        self._request_id += 1
        request_id = self._request_id
        await self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "turn/start",
                "params": {
                    "threadId": self.thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "model": "m3-fixture-model",
                },
            }
        )
        return request_id

    async def read_until_turn_completed(
        self,
        *,
        on_elicitation: Any,
        timeout: float = 20.0,
    ) -> tuple[Mapping[str, Any], ...]:
        async def read() -> tuple[Mapping[str, Any], ...]:
            collected: list[Mapping[str, Any]] = []
            while True:
                frame = await self.next_frame()
                if frame is None:
                    raise AssertionError("app-server exited before turn/completed")
                collected.append(frame)
                method = frame.get("method")
                if method == "mcpServer/elicitation/request":
                    await on_elicitation(frame)
                if method == "turn/completed":
                    return tuple(collected)

        return await asyncio.wait_for(read(), timeout=timeout)

    async def cancel_turn(self, turn_id: str) -> Mapping[str, Any]:
        if self.thread_id is None:
            raise AssertionError("Codex thread is not initialized")
        self._request_id += 1
        await self._write(
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "turn/interrupt",
                "params": {"threadId": self.thread_id, "turnId": turn_id},
            }
        )
        return {}

    async def next_frame(self) -> Mapping[str, Any] | None:
        return await self._frames.get()

    async def _write(self, value: Mapping[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise AssertionError("app-server is not running")
        payload = json.dumps(value, separators=(",", ":")).encode() + b"\n"
        self.process.stdin.write(payload)
        await self.process.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        while True:
            line = await self.process.stdout.readline()
            if not line:
                await self._frames.put(None)
                return
            try:
                value = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                await self._frames.put(
                    {"__invalid_frame__": line.decode(errors="replace")}
                )
                continue
            if isinstance(value, Mapping):
                await self._frames.put(dict(value))
            else:
                await self._frames.put({"__invalid_frame__": value})

    async def close(self) -> None:
        process = self.process
        if process is None:
            return
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        self.process = None


__all__ = ["CodexAppServer", "CodexUnavailable", "require_codex"]
