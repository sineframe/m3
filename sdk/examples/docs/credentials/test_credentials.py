from __future__ import annotations

import sys

import pytest

from m3.async_api import AsyncMCPTestKit
from m3.types import SecretReference, StdioServer


@pytest.mark.asyncio
async def test_stdio_server_receives_named_credential(monkeypatch):
    token = "dummy-service-token"
    monkeypatch.setenv("M3_DEMO_SERVICE_KEY", token)
    kit = AsyncMCPTestKit(env={})
    try:
        server = StdioServer(
            name="credential-demo",
            command=sys.executable,
            args=("stdio_server.py",),
            environment={
                "DEMO_SERVICE_TOKEN": SecretReference(
                    source="environment", name="M3_DEMO_SERVICE_KEY"
                )
            },
        )
        async with kit.direct(server) as client:
            result = await client.call_tool("credential_check", {})
            assert result.content[0]["text"] == "credential accepted"
            assert result.structured_content == {"authenticated": True}
            assert client.trace is not None
            assert token not in repr(client.trace.model_dump(mode="json"))
    finally:
        await kit.aclose()
