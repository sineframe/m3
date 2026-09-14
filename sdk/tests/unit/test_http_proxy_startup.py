"""Regression coverage for HTTP proxy startup failures."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import mcp_pal.transport.http_proxy as http_proxy_module
from mcp_pal.transport.http_proxy import McpHttpProxy


class _NeverStartedServer:
    def __init__(self, config: Any) -> None:
        self.started = False
        self.should_exit = False

    async def serve(self, *, sockets: list[Any]) -> None:
        while not self.should_exit:
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_start_times_out_and_releases_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(http_proxy_module, "_PROXY_START_TIMEOUT", 0.01)
    monkeypatch.setattr(http_proxy_module.uvicorn, "Server", _NeverStartedServer)
    proxy = McpHttpProxy(
        upstream_url="http://127.0.0.1/mcp",
        configured_headers=None,
        transport="streamable_http",
        capture_path=str(tmp_path / "capture.jsonl"),
        baseline_ns=0,
        allow_private=True,
    )

    with pytest.raises(TimeoutError, match=r"failed to start within 0\.01 seconds"):
        await proxy.start()

    assert proxy.task is not None and proxy.task.done()
    assert proxy.client is not None and proxy.client.is_closed
    assert proxy.socket is not None and proxy.socket.fileno() == -1
