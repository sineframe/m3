"""Non-provider preflight for the explicit migrated-profile live gate."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from m3.types import (
    AgentSpec,
    HarnessProfileRef,
    NativeToolPolicy,
    RevisionSelection,
    ServerBinding,
    ServerProfileRef,
    TextContent,
    UserMessage,
)
from m3_app.api import create_app
from m3_app.settings import Settings

_SCRIPTS = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location(
    "live_migrated_ui_opencode_acp_gate",
    _SCRIPTS / "live_migrated_ui_opencode_acp_gate.py",
)
assert _SPEC is not None and _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)


def test_live_gate_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("M3_RUN_LIVE_MIGRATED_OPENCODE", raising=False)
    with pytest.raises(gate.GateError, match="opt in"):
        gate._require_inputs()


def test_gate_cleanup_accepts_setup_failure_without_a_process() -> None:
    gate._stop(None)


def test_seeded_legacy_profiles_migrate_and_reach_ui_facing_v2_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "fixture-only-key")
    database = tmp_path.resolve() / "legacy-live-fixture.sqlite"
    config = tmp_path.resolve() / "opencode.json"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENCODE_GATE_CONFIG", str(config))
    gate._seed_legacy_database(database, executable=sys.executable, config_path=config)

    with TestClient(
        create_app(Settings(database_path=str(database))),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    ) as client:
        servers = client.get("/api/v2/profiles")
        harnesses = client.get("/api/v2/harness-profiles")
        capabilities = client.get("/api/v2/capabilities")
        assert (
            servers.status_code
            == harnesses.status_code
            == capabilities.status_code
            == 200
        )
        server = next(
            item for item in servers.json() if item["id"] == gate.SERVER_PROFILE_ID
        )
        harness = next(
            item for item in harnesses.json() if item["id"] == gate.HARNESS_PROFILE_ID
        )
        assert server["current_revision_id"] == gate.SERVER_REVISION_ID
        assert harness["current_revision_id"] == gate.HARNESS_REVISION_ID
        selected = next(
            item
            for item in capabilities.json()["harnesses"]
            if item["selection_id"] == f"profile:{gate.HARNESS_PROFILE_ID}"
        )
        assert selected["ready"] is True


def test_migrated_profiles_run_real_local_acp_and_persist_v2_graph(
    tmp_path: Path,
) -> None:
    """No mocks: an ACP child calls a real MCP fixture across process pipes."""
    database = tmp_path.resolve() / "legacy-local-acp.sqlite"
    config = tmp_path.resolve() / "opencode.json"
    config.write_text("{}", encoding="utf-8")
    gate._seed_legacy_database(database, executable=sys.executable, config_path=config)
    fixture = gate.ROOT / "sdk" / "tests" / "fixtures" / "acp_scenario_agent.py"
    server = {
        "mcpServers": {
            gate.SERVER_NAME: {
                "command": sys.executable,
                "args": ["-m", "m3.fixtures.echo_server"],
                "trust": "sdk_loopback",
            }
        }
    }
    manifest = {
        "command": sys.executable,
        "args": [str(fixture), "default"],
        "protocol": "acp",
        "protocol_version": 1,
        "env": {},
    }
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE mcp_profile_revisions SET mcp_json=? WHERE id=?",
            (json.dumps(server), gate.SERVER_REVISION_ID),
        )
        connection.execute(
            "UPDATE harness_profile_revisions SET manifest=? WHERE id=?",
            (json.dumps(manifest), gate.HARNESS_REVISION_ID),
        )
        connection.commit()
    finally:
        connection.close()

    spec = AgentSpec(
        servers=(
            ServerBinding(
                profile=ServerProfileRef(
                    profile_id=gate.SERVER_PROFILE_ID,
                    server_name=gate.SERVER_NAME,
                    revision=RevisionSelection(
                        mode="pinned",
                        revision_id=gate.SERVER_REVISION_ID,
                        revision_number=1,
                    ),
                )
            ),
        ),
        harness_profile=HarnessProfileRef(
            profile_id=gate.HARNESS_PROFILE_ID,
            revision=RevisionSelection(
                mode="pinned",
                revision_id=gate.HARNESS_REVISION_ID,
                revision_number=1,
            ),
        ),
        message=UserMessage(content=(TextContent(text="one fixture turn"),)),
        tool_policy=NativeToolPolicy(
            harness="acp",
            policy={"mode": "agent_default", "server": gate.SERVER_NAME},
            nonportable_reason="the local ACP fixture selects its tool",
        ),
        timeout_seconds=15,
    )
    with TestClient(
        create_app(Settings(database_path=str(database))),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    ) as client:
        created = client.post(
            "/api/v2/executions", json={"spec": spec.model_dump(mode="json")}
        )
        assert created.status_code == 202, created.json()
        execution_id = created.json()["execution_id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            current = client.get(f"/api/v2/executions/{execution_id}").json()
            if current["snapshot"]["lifecycle"] == "finished":
                assert current["snapshot"]["outcome"] == "completed", current
                break
            time.sleep(0.05)
        else:
            raise AssertionError("real local ACP execution did not finish")
    gate._assert_sqlite(database, execution_id, expected_tool="echo")
