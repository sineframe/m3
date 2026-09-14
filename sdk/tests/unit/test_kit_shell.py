"""Configuration/capability kit shell contracts."""

from __future__ import annotations

import asyncio
import inspect
import sys
import time
from pathlib import Path

import pytest

from mcp_pal import Config, MCPTestKit
from mcp_pal._exports import PUBLIC_EXPORTS
from mcp_pal.async_api import AsyncMCPTestKit, AsyncProbes
from mcp_pal.errors import KitClosed, UnsupportedFeature
from mcp_pal.services.probes import Probes
from mcp_pal.sync_api import ProbeKind, ProbeRequest
from mcp_pal.types import CapabilityStatus


def test_sync_kit_resolves_config_and_baseline_without_harness_probes(
    tmp_path: Path,
) -> None:
    kit = MCPTestKit(
        {"artifact_policy": "always"},
        env={"MCP_PAL_TELEMETRY_ENABLED": "true"},
        cwd=tmp_path,
    )

    report = kit.capabilities()

    assert isinstance(kit.config, Config)
    assert kit.config.artifact_policy == "always"
    assert kit.config.telemetry_enabled is True
    assert [result.capability.name for result in report.results] == [
        "configuration",
        "memory",
    ]
    assert report.readiness.ready is True
    assert all(result.status is CapabilityStatus.READY for result in report.results)


def test_sync_kit_nonempty_capability_requests_are_exactly_scoped() -> None:
    kit = MCPTestKit(env={}, cwd=Path("/tmp/mcp-pal-no-project"))

    report = kit.capabilities([ProbeRequest(ProbeKind.STORAGE, "memory")])

    assert [result.capability.name for result in report.results] == ["memory"]
    assert (
        kit.capabilities([ProbeRequest(ProbeKind.HARNESS, "missing")]).results[0].status
        is CapabilityStatus.UNAVAILABLE
    )


def test_sync_kit_lifecycle_and_unsupported_operations_are_explicit() -> None:
    kit = MCPTestKit(env={}, cwd=Path("/tmp/mcp-pal-no-project"))
    for operation in (
        lambda: kit.run(None),
        lambda: kit.submit(None),
        lambda: kit.agent_session(None),
    ):
        with pytest.raises(
            UnsupportedFeature, match="does not support the supplied specification"
        ):
            operation()
    with pytest.raises(UnsupportedFeature, match="runtime resolution"):
        kit.direct(None)

    with kit as entered:
        assert entered is kit
    kit.close()
    kit.close()

    with pytest.raises(KitClosed) as caught:
        kit.capabilities()
    assert caught.value.code == "kit_closed"
    with pytest.raises(KitClosed):
        _ = kit.probes
    with pytest.raises(KitClosed):
        kit.run(None)


def test_sync_probe_namespace_is_the_sync_service_and_checks_lifecycle() -> None:
    kit = MCPTestKit(env={}, cwd=Path("/tmp/mcp-pal-no-project"))
    assert isinstance(kit.probes, Probes)
    assert not inspect.iscoroutinefunction(kit.probes.probe_storage)
    kit.close()
    with pytest.raises(KitClosed):
        kit.probes.probe_storage("memory")


def test_kit_closed_error_is_stable_and_public() -> None:
    error = KitClosed("closed")
    assert error.code == "kit_closed"
    assert error.message == "closed"


def test_async_probe_namespace_is_async_only_and_matches_sync_surface() -> None:
    async_service_methods = {
        "probe_binary",
        "probe_protocol",
        "probe_harness",
        "probe_transport",
        "probe_storage",
        "probe_requested",
    }
    for method_name in async_service_methods:
        assert inspect.iscoroutinefunction(getattr(AsyncProbes, method_name))
        assert not hasattr(AsyncProbes, f"sync_{method_name}")

    kit = AsyncMCPTestKit(env={}, cwd=Path("/tmp/mcp-pal-no-project"))
    assert isinstance(kit.probes, AsyncProbes)
    assert "AsyncProbes" in PUBLIC_EXPORTS["mcp_pal.async_api"]
    assert "Probes" not in PUBLIC_EXPORTS["mcp_pal.async_api"]


def test_async_closed_namespace_raises_stable_error() -> None:
    async def scenario() -> None:
        kit = AsyncMCPTestKit(env={}, cwd=Path("/tmp/mcp-pal-no-project"))
        await kit.aclose()
        await kit.aclose()
        with pytest.raises(KitClosed) as caught:
            await kit.capabilities()
        assert caught.value.code == "kit_closed"
        with pytest.raises(KitClosed):
            _ = kit.probes

    asyncio.run(scenario())


def test_async_direct_probe_offloads_without_blocking_event_loop() -> None:
    async def scenario() -> None:
        kit = AsyncMCPTestKit(env={}, cwd=Path("/tmp/mcp-pal-no-project"))
        ticked = False

        async def ticker() -> None:
            nonlocal ticked
            await asyncio.sleep(0.01)
            ticked = True

        ticker_task = asyncio.create_task(ticker())
        result = await kit.probes.probe_binary(
            "python",
            sys.executable,
            args=("-c", "import time; time.sleep(0.1); print('python 1.0.0')"),
        )
        await ticker_task
        assert result.status is CapabilityStatus.READY
        assert ticked

    asyncio.run(scenario())


def test_async_kit_matches_sync_baseline_and_closes_idempotently() -> None:
    async def scenario() -> None:
        sync_report = MCPTestKit(
            env={}, cwd=Path("/tmp/mcp-pal-no-project")
        ).capabilities()
        kit = AsyncMCPTestKit(env={}, cwd=Path("/tmp/mcp-pal-no-project"))
        async with kit as entered:
            report = await entered.capabilities()
            assert [x.capability.name for x in report.results] == [
                x.capability.name for x in sync_report.results
            ]
            assert [x.status for x in report.results] == [
                x.status for x in sync_report.results
            ]
        await kit.aclose()
        await kit.aclose()

    asyncio.run(scenario())


def test_async_capabilities_offload_blocking_probe_work() -> None:
    async def scenario() -> None:
        kit = AsyncMCPTestKit(env={}, cwd=Path("/tmp/mcp-pal-no-project"))
        original = kit.probes._service.probe_requested

        def slow(requests):
            time.sleep(0.1)
            return original(requests)

        kit.probes._service.probe_requested = slow  # type: ignore[method-assign]
        ticked = False

        async def ticker() -> None:
            nonlocal ticked
            await asyncio.sleep(0.01)
            ticked = True

        ticker_task = asyncio.create_task(ticker())
        await kit.capabilities([ProbeRequest(ProbeKind.STORAGE, "memory")])
        await ticker_task
        assert ticked

    asyncio.run(scenario())
