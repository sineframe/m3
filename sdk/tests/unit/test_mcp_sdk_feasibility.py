"""Guard the public MCP v2 APIs."""

from __future__ import annotations

import importlib.metadata
import inspect

from mcp import ClientSession
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


def test_official_mcp_v2_exposes_supported_client_transports() -> None:
    assert importlib.metadata.version("mcp").split(".", 1)[0] == "2"
    assert inspect.isasyncgenfunction(
        getattr(stdio_client, "__wrapped__", stdio_client)
    )
    assert inspect.isasyncgenfunction(
        getattr(streamable_http_client, "__wrapped__", streamable_http_client)
    )

    session_parameters = inspect.signature(ClientSession).parameters
    assert {"read_stream", "write_stream"} <= session_parameters.keys()
    assert "protocol_version" not in session_parameters
    assert hasattr(ClientSession, "initialize")
