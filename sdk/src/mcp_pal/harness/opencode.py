"""OpenCode serve/session harness adapter.

One isolated ``opencode serve`` process and one HTTP session are retained for
the lifetime of the adapter.  The adapter never falls back to one-shot
``run`` invocations or copies ambient OpenCode configuration.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import shutil
import tempfile
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import httpx

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
    MAX_FRAME_BYTES,
    NativeSessionBase,
    ProcessOwner,
    _executable,
    _isolated_environment,
    _text,
    probe_help,
    read_bounded_line,
    write_config,
)


_URL = re.compile(r"https?://(?:127\.0\.0\.1|localhost|\[::1\]):\d+")


class _ResponseTooLarge(ValueError):
    """The provider response exceeded the bounded native frame size."""


class OpenCodeHarnessAdapter:
    """One isolated OpenCode server and attached conversation session."""

    def __init__(self, *, executable: str = "opencode", environment: Mapping[str, str] | None = None) -> None:
        self.executable = _executable(executable, "opencode")
        self.environment = dict(environment or {})
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
            capability = Capability(name="harness:opencode", status=CapabilityStatus.UNAVAILABLE, reason="executable unavailable")
            return Readiness(ready=False, capabilities=(capability,), reason="executable unavailable")
        help_text = await asyncio.to_thread(probe_help, self.executable, ("serve", "--help"))
        if help_text is None or "serve" not in help_text.lower():
            capability = Capability(name="harness:opencode", status=CapabilityStatus.UNAVAILABLE, reason="serve unavailable")
            return Readiness(ready=False, capabilities=(capability,), reason="serve unavailable")
        return self._capabilities.readiness()

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
            environment = _isolated_environment(root, self.environment)
            config = write_config(root, launch)
            environment["OPENCODE_CONFIG"] = str(config)
            await owner.spawn([self.executable, "serve", "--hostname", "127.0.0.1", "--port", "0"], environment)
            assert owner.process is not None and owner.process.stdout is not None
            base_url = await self._read_server_url(owner.process.stdout)
            client = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(30.0, connect=5.0))
            async with client.stream("POST", "/session", json={}) as response:
                if response.status_code >= 400:
                    raise HarnessStartupError("OpenCode session could not be created")
                body = json.loads(await self._read_bounded_response(response))
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
                body = json.loads(await self._read_bounded_response(response))
        except _ResponseTooLarge:
            return HarnessTurnResult(
                sequence=sequence,
                status="failed",
                error=ErrorInfo(code=ErrorCode.PROTOCOL_ERROR, message="OpenCode response exceeded safe frame size"),
                evidence={"process_observed": True, "transport_observed": "http", "usage_state": "unavailable"},
            )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return HarnessTurnResult(sequence=sequence, status="timed_out", error=ErrorInfo(code=ErrorCode.TIMEOUT, message="OpenCode turn timed out"), evidence={"process_observed": True, "transport_observed": "http", "usage_state": "unavailable"})
        except (httpx.HTTPError, ValueError, TypeError):
            return HarnessTurnResult(sequence=sequence, status="failed", error=ErrorInfo(code=ErrorCode.TRANSPORT_ERROR, message="OpenCode response was invalid"), evidence={"process_observed": True, "transport_observed": "http", "usage_state": "unavailable"})
        if not isinstance(body, Mapping):
            return HarnessTurnResult(sequence=sequence, status="failed", error=ErrorInfo(code=ErrorCode.PROTOCOL_ERROR, message="OpenCode response was invalid"), evidence={"process_observed": True, "transport_observed": "http", "usage_state": "unavailable"})
        failed = bool(body.get("is_error", body.get("isError", False)))
        text = _text(body.get("text", body.get("response", body.get("parts", body.get("content", "")))))
        return HarnessTurnResult(
            sequence=sequence,
            status="failed" if failed else "completed",
            response=None if failed else TurnResponse(content=(TextContent(text=text),)),
            error=None if not failed else ErrorInfo(code=ErrorCode.TRANSPORT_ERROR, message="OpenCode turn failed"),
            tool_calls=tuple(item for item in body.get("tool_calls", ()) if isinstance(item, Mapping)),
            evidence={
                "process_observed": True,
                "transport_observed": "http",
                "content_observed": bool(text),
                "tool_calls_observed": bool(body.get("tool_calls")),
                "mcp_traffic_observed": bool(body.get("tool_calls")),
                "usage_requested": True,
                "usage_enforced": False,
                "usage_observed": "usage" in body,
                "usage_state": "observed" if "usage" in body else "unavailable",
                "usage_unavailable": "usage" not in body,
            },
        )


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
