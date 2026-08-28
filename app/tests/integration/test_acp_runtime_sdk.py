"""Black-box coverage for the direct SDK ACP probe runtime seam."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from pathlib import Path

from acp_fixture import probe_agent
from mcp_pal.services.acp_probes import ACPProbeKind, ACPProbeRequest, ACPProbeStatus
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal_app.services.app_service import AppRuntimeService
from mcp_pal_app.services.profile_service import HarnessProfileInput
from mcp_pal_app.settings import Settings


class _ReopenedKit:
    """Minimal injected execution kit for inspecting a reopened runtime."""

    def __init__(self, store: SQLiteExecutionStore) -> None:
        self.store = store

    def submit(self, _spec: object) -> None:
        return None

    def close(self) -> None:
        return None


def test_runtime_sdk_acp_protocol_full_persists_and_reopens(tmp_path: Path) -> None:
    database = tmp_path / "runtime-acp.sqlite"
    settings = Settings(
        database_path=str(database),
        claude_executable="missing-claude",
        opencode_executable="missing-opencode",
    )
    agent = probe_agent(tmp_path / "agent.py")
    runtime = AppRuntimeService(settings)
    profile = runtime.create_harness(
        HarnessProfileInput(
            name="runtime-acp",
            manifest={"command": sys.executable, "args": [agent]},
            trusted_unsandboxed=True,
        )
    )
    revision = runtime.store.resolve_revision(profile.record.id)
    protocol_request = ACPProbeRequest(
        profile_id=profile.record.id,
        revision_id=str(revision.id.root),
        probe_type=ACPProbeKind.PROTOCOL,
    )
    protocol = asyncio.run(runtime.acp_probes.run(protocol_request))
    assert protocol.status is ACPProbeStatus.VERIFIED
    assert protocol.agent_identity is not None
    assert protocol.agent_identity.name == "probe-fixture"
    assert tuple(mode.id for mode in protocol.agent_modes) == ("mode-a",)
    assert protocol.config_options[0]["id"] == "quality"

    full_request = ACPProbeRequest(
        profile_id=profile.record.id,
        revision_id=str(revision.id.root),
        probe_type=ACPProbeKind.FULL,
        agent_mode_id="mode-a",
        session_config={"quality": "high"},
    )
    full = asyncio.run(runtime.acp_probes.run(full_request))
    assert full.status is ACPProbeStatus.VERIFIED
    assert full.agent_identity is not None
    assert full.agent_identity.name == "probe-fixture"
    nonce = full.evidence["nonce"]
    calls = full.evidence["calls"]
    assert isinstance(nonce, str)
    assert isinstance(calls, (list, tuple)) and calls
    call = calls[0]
    assert isinstance(call, Mapping)
    arguments = call.get("arguments")
    assert isinstance(arguments, Mapping) and dict(arguments) == {"text": nonce}
    result = call.get("result")
    assert isinstance(result, Mapping)
    content = result.get("content")
    assert isinstance(content, (list, tuple)) and content
    first_content = content[0]
    assert isinstance(first_content, Mapping)
    assert first_content.get("type") == "text" and first_content.get("text") == nonce

    view = runtime.capabilities().acp_profiles
    descriptor = next(item for item in view if item.profile_id == profile.record.id)
    assert descriptor.protocol_verified and descriptor.full_verified
    assert descriptor.agent_identity is not None
    assert descriptor.agent_identity["name"] == "probe-fixture"
    assert descriptor.agent_modes[0].id == "mode-a"
    assert descriptor.session_config_options[0].id == "quality"
    assert descriptor.protocol_verification is not None
    assert descriptor.protocol_verification["status"] == "verified"
    full_evidence = descriptor.full_verifications[0].get("evidence")
    assert isinstance(full_evidence, Mapping) and full_evidence.get("nonce") == nonce
    runtime.close()

    reopened_store = SQLiteExecutionStore(database)
    reopened = AppRuntimeService(settings, store=reopened_store, kit=_ReopenedKit(reopened_store))
    reopened_descriptor = next(
        item for item in reopened.capabilities().acp_profiles if item.profile_id == profile.record.id
    )
    assert reopened_descriptor.full_verified
    persisted = reopened.acp_probes.latest(full_request)
    assert persisted is not None and persisted.status is ACPProbeStatus.VERIFIED
    assert tuple(mode.id for mode in persisted.agent_modes) == ("mode-a",)
    assert persisted.evidence["nonce"] == nonce
    assert "HarnessProbe" not in type(reopened.acp_probes).__module__
    assert "persistence.models" not in type(reopened.acp_probes).__module__
    reopened.close()
    reopened_store.close()
