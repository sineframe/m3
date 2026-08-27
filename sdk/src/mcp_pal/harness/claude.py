"""Claude Code stream-JSON harness adapter.

This adapter starts one process per conversation and writes every turn to the
same stream.  It intentionally has no resume-based fallback: a binary that
cannot provide stream input/output is not a ready Claude adapter.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import tempfile
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from ..agent_session import AdapterTurn
from ..types import Capability, CapabilityStatus, ErrorCode, ErrorInfo, Readiness, TextContent, TurnResponse, UserMessage
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
    MAX_QUEUE_ITEMS,
    NativeSessionBase,
    ProcessOwner,
    _executable,
    _isolated_environment,
    _text,
    probe_help,
    read_bounded_line,
    write_config,
    workspace_for_launch,
)


def _response(output: Mapping[str, Any], text: str) -> TurnResponse:
    value = text or _text(output.get("result", output.get("message", output.get("content", ""))))
    return TurnResponse(content=(TextContent(text=value),))


class ClaudeCodeHarnessAdapter:
    """One continuous Claude Code stream-JSON conversation."""

    def __init__(self, *, executable: str = "claude", environment: Mapping[str, str] | None = None) -> None:
        self.executable = _executable(executable, "claude")
        self.environment = dict(environment or {})
        self._capabilities = HarnessAdapterCapabilities(
            name="claude-code",
            supports_multiturn=True,
            supports_cancellation=True,
            supports_timeout=True,
            # This adapter currently does not expose a portable policy
            # receipt; AgentSession must reject explicit policies rather than
            # treating the provider's process as proof of enforcement.
            supports_tool_policy=False,
            supports_streaming=True,
        )
        self._root: Path | None = None
        self._config: Path | None = None
        self._owner: ProcessOwner | None = None
        self._launch: HarnessLaunch | None = None
        self._session: ClaudeCodeSession | None = None
        self._output: asyncio.Queue[Mapping[str, Any] | None] | None = None
        self._reader_task: asyncio.Task[None] | None = None

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
        del launch
        if shutil.which(self.executable) is None and not Path(self.executable).is_file():
            return Readiness(
                ready=False,
                capabilities=(Capability(name="harness:claude-code", status=CapabilityStatus.UNAVAILABLE, reason="executable unavailable"),),
                reason="executable unavailable",
            )
        help_text = await asyncio.to_thread(probe_help, self.executable, ("--help",))
        if help_text is None or "stream-json" not in help_text:
            capability = Capability(name="harness:claude-code", status=CapabilityStatus.UNAVAILABLE, reason="stream-json unavailable")
            return Readiness(ready=False, capabilities=(capability,), reason="stream-json unavailable")
        return self._capabilities.readiness()

    async def open(self, launch: HarnessLaunch) -> HarnessSession:
        readiness = await self.preflight(launch)
        if not readiness.ready:
            raise HarnessStartupError("Claude Code streaming is unavailable")
        if self._session is not None:
            raise HarnessStartupError("Claude Code session is already open")
        root = Path(tempfile.mkdtemp(prefix="mcp-pal-claude-"))
        owner = ProcessOwner(root)
        try:
            environment = _isolated_environment(root, self.environment)
            config = write_config(root, launch)
            workspace = workspace_for_launch(launch, root)
            harness = launch.spec.harness
            if harness is None:
                raise HarnessStartupError("Claude Code harness is unavailable")
            argv = [
                self.executable,
                "--print",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--verbose",
                "--strict-mcp-config",
                "--mcp-config",
                str(config),
                "--model",
                harness.model,
            ]
            await owner.spawn(argv, environment, cwd=workspace)
            output: asyncio.Queue[Mapping[str, Any] | None] = asyncio.Queue(maxsize=MAX_QUEUE_ITEMS)
            process = owner.process
            assert process is not None
            stream = process.stdout
            assert stream is not None

            async def read_output() -> None:
                try:
                    while True:
                        line = await read_bounded_line(stream)
                        if line is None:
                            break
                        try:
                            decoded = line.decode("utf-8")
                            value = json.loads(decoded)
                        except (TypeError, ValueError):
                            await output.put({"__adapter_error__": "invalid_stream_frame"})
                            await owner.terminate()
                            break
                        if isinstance(value, Mapping):
                            await output.put(value)
                        else:
                            await output.put({"__adapter_error__": "invalid_stream_frame"})
                            await owner.terminate()
                            break
                except Exception:
                    await output.put({"__adapter_error__": "stream_frame_unavailable"})
                    try:
                        await owner.terminate()
                    except Exception:
                        pass
                finally:
                    await output.put(None)

            self._reader_task = asyncio.create_task(read_output())
            self._root = root
            # Claude may open --mcp-config after the child process has been
            # spawned.  Keep the mode-0600 file until the owned process and
            # its readers are reaped; deleting it here races startup.
            self._config = config
            self._owner = owner
            self._output = output
            self._launch = launch
            self._session = ClaudeCodeSession(self, owner, self._capabilities, "claude-" + uuid4().hex)
            return self._session
        except BaseException:
            try:
                await owner.close()
            except BaseException:
                pass
            raise

    async def start(self, spec: Any) -> None:
        raise HarnessStartupError("Claude Code requires a resolved harness launch")

    async def send(self, message: UserMessage, *, timeout: float | None = None, metadata: Mapping[str, object] | None = None) -> AdapterTurn:
        del metadata
        if self._session is None:
            raise HarnessAdapterError("Claude Code session is not open")
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
        if self._config is not None:
            try:
                self._config.unlink(missing_ok=True)
            except OSError:
                # ProcessOwner cleanup already removes the isolated root; a
                # missing file is therefore an expected terminal state.
                pass
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        self._reader_task = None
        self._owner = None
        self._config = None
        self._output = None
        self._launch = None
        self._root = None

    async def cancel(self) -> None:
        if self._session is not None:
            await self._session.cancel()

    async def _send(self, request: HarnessTurnRequest, sequence: int) -> HarnessTurnResult:
        owner = self._owner
        output = self._output
        if owner is None or owner.process is None or owner.process.stdin is None or output is None:
            raise HarnessAdapterError("Claude Code session is not open")
        payload = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": _text(request.message.model_dump(mode="python"))}],
            },
        }
        try:
            owner.process.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
            await owner.process.stdin.drain()
        except (BrokenPipeError, ConnectionError):
            return HarnessTurnResult(sequence=sequence, status="failed", error=ErrorInfo(code=ErrorCode.TRANSPORT_ERROR, message="Claude Code stream failed"))
        text = ""
        tool_calls: list[Mapping[str, Any]] = []
        while True:
            try:
                item = await asyncio.wait_for(output.get(), timeout=request.timeout_seconds)
            except asyncio.TimeoutError:
                await owner.terminate()
                return HarnessTurnResult(sequence=sequence, status="timed_out", error=ErrorInfo(code=ErrorCode.TIMEOUT, message="Claude Code turn timed out"))
            if item is None:
                return HarnessTurnResult(sequence=sequence, status="failed", error=ErrorInfo(code=ErrorCode.TRANSPORT_ERROR, message="Claude Code stream ended"))
            if "__adapter_error__" in item:
                return HarnessTurnResult(
                    sequence=sequence,
                    status="failed",
                    error=ErrorInfo(code=ErrorCode.PROTOCOL_ERROR, message="Claude Code stream was invalid"),
                    evidence={"process_observed": True, "transport_observed": "stream_json", "content_observed": bool(text), "usage_state": "unavailable"},
                )
            event_type = str(item.get("type", ""))
            text += _text(item.get("delta", item.get("message", item.get("content", ""))))
            if event_type in {"tool_use", "tool_call"}:
                tool_calls.append(dict(item))
            if event_type not in {"result", "turn_complete", "message_stop", "assistant_result"}:
                continue
            failed = bool(item.get("is_error", item.get("isError", False)))
            return HarnessTurnResult(
                sequence=sequence,
                status="failed" if failed else "completed",
                response=None if failed else _response(item, text),
                error=None if not failed else ErrorInfo(code=ErrorCode.TRANSPORT_ERROR, message="Claude Code turn failed"),
                tool_calls=tuple(tool_calls),
                evidence={
                    "process_observed": True,
                    "transport_observed": "stream_json",
                    "content_observed": bool(text),
                    "tool_calls_observed": bool(tool_calls),
                    "mcp_traffic_observed": bool(tool_calls),
                    "usage_requested": True,
                    "usage_enforced": False,
                    "usage_observed": "usage" in item,
                    "usage_state": "observed" if "usage" in item else "unavailable",
                    "usage_unavailable": "usage" not in item,
                },
            )


class ClaudeCodeSession(NativeSessionBase):
    def __init__(self, adapter: ClaudeCodeHarnessAdapter, owner: ProcessOwner, capabilities: HarnessAdapterCapabilities, session_id: str) -> None:
        super().__init__(
            owner,
            capabilities,
            session_id,
            server_configuration_count=len(adapter._launch.configurations) if adapter._launch is not None else 0,
            capture=adapter._launch.capture if adapter._launch is not None else None,
        )
        self._adapter = adapter

    async def send(self, request: HarnessTurnRequest) -> HarnessTurnResult:
        if self._closed:
            raise HarnessAdapterError("Claude Code session is closed")
        self._turns += 1
        return await self._adapter._send(request, self._turns)


__all__ = ["ClaudeCodeHarnessAdapter", "ClaudeCodeSession"]
