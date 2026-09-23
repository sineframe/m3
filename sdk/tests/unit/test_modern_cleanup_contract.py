"""Removal contracts for the modern MCP protocol boundary."""

from __future__ import annotations

import importlib

import pytest
from mcp.types import LATEST_PROTOCOL_VERSION


def test_legacy_interaction_handlers_are_not_public() -> None:
    m3 = importlib.import_module("m3")
    for name in (
        "ElicitationHandler",
        "ElicitationRequest",
        "ElicitationResult",
        "ElicitationPolicy",
    ):
        assert not hasattr(m3, name)


def test_agent_spec_is_internal_only() -> None:
    m3 = importlib.import_module("m3")
    types = importlib.import_module("m3.types")
    assert not hasattr(m3, "AgentSpec")
    assert not hasattr(types, "AgentSpec")


def test_sse_server_is_not_a_supported_public_server() -> None:
    m3 = importlib.import_module("m3")
    types = importlib.import_module("m3.types")
    assert not hasattr(m3, "SSEServer")
    assert not hasattr(types, "SSEServer")


def test_streamable_http_remains_public() -> None:
    m3 = importlib.import_module("m3")
    assert hasattr(m3, "HTTPServer")


def test_client_protocol_baseline_tracks_mcp_sdk() -> None:
    from m3.async_api import _CURRENT_MCP_PROTOCOL as async_protocol
    from m3.sync_api import _CURRENT_MCP_PROTOCOL as sync_protocol

    assert async_protocol == LATEST_PROTOCOL_VERSION == "2026-07-28"
    assert sync_protocol == LATEST_PROTOCOL_VERSION


def test_removed_direct_callback_is_rejected() -> None:
    from mcp.server.lowlevel import Server

    from m3 import InProcessServer
    from m3.sync_api import MCPTestKit

    async def list_tools(_context: object, _params: object) -> object:
        return object()

    server = InProcessServer(
        name="fixture",
        factory=lambda: Server("fixture", on_list_tools=list_tools),
    )
    with MCPTestKit(env={}) as kit, pytest.raises(TypeError) as error:
        kit.direct(server, elicitation_callback=lambda _request: None)  # type: ignore[call-arg]
    assert str(error.value) == (
        "elicitation_callback was removed; pass elicitation= to call_tool(), "
        "get_prompt(), or read_resource() instead"
    )
