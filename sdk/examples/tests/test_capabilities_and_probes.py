"""Discover SDK readiness explicitly before running a larger test matrix."""

from __future__ import annotations

from m3 import CapabilityStatus, MCPTestKit, ProbeKind, ProbeRequest


def test_read_baseline_capabilities_and_explicit_probes() -> None:
    with MCPTestKit(env={}) as kit:
        baseline = kit.capabilities()
        assert baseline.readiness.ready is True
        assert {capability.name for capability in baseline.capabilities} == {
            "configuration",
            "memory",
        }
        assert kit.probes.probe_storage().status is CapabilityStatus.READY

        report = kit.capabilities(
            [
                ProbeRequest(
                    kind=ProbeKind.TRANSPORT,
                    name="mcp-package",
                    module="mcp",
                    transport="stdio",
                )
            ]
        )

    result = report.result_for("mcp-package")
    assert result is not None
    assert result.status is CapabilityStatus.READY
    assert result.evidence.details["module_available"] is True
