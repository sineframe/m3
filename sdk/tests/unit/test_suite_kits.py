from __future__ import annotations

import asyncio

from mcp_pal import StdioServer
from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.sync_api import MCPTestKit
from mcp_pal.types import DirectSpec, Ping, RunId, ServerBinding


def _spec() -> DirectSpec:
    return DirectSpec(
        servers=(ServerBinding(server=StdioServer(name="s", command="true")),),
        operation=Ping(server="s"),
    )


def test_sync_kit_assigns_suite_and_preserves_explicit_run_id() -> None:
    kit = MCPTestKit(suite_name="catalog", env={})
    try:
        selected = kit._with_run_id(_spec())
        assert selected.suite_name == "catalog"
        assert selected.run_id is None
        explicit = _spec().model_copy(update={"run_id": RunId("explicit")})
        assert kit._with_run_id(explicit).run_id == RunId("explicit")
    finally:
        kit.close()


def test_async_kit_assigns_suite() -> None:
    async def check() -> None:
        kit = AsyncMCPTestKit(suite_name="catalog", env={})
        try:
            assert kit._with_run_id(_spec()).suite_name == "catalog"
        finally:
            await kit.aclose()

    asyncio.run(check())


def test_explicit_spec_suite_overrides_kit_default() -> None:
    kit = MCPTestKit(suite_name="catalog", env={})
    try:
        assert (
            kit._with_run_id(
                _spec().model_copy(update={"suite_name": "other"})
            ).suite_name
            == "other"
        )
    finally:
        kit.close()
