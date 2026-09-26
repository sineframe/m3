"""Contract coverage for the application-owned v2 control plane."""

import json
import os
import sqlite3
import stat
import sys
import time
from pathlib import Path

import pytest
from _local_client import TestClient
from _pid_marker import wait_for_pid

from m3 import ClaudeCode, MCPTestKit, TextContent, UserMessage
from m3._types.specs import AgentSpec
from m3.storage import SQLiteExecutionStore
from m3.types import (
    RevisionSelection,
    ServerBinding,
    ServerProfileRef,
)
from m3_app.api import create_app
from m3_app.services.app_service import AppRuntimeService
from m3_app.settings import Settings

_REPOSITORY_ROOT = Path(__file__).parents[3]
_MCP_FIXTURE = (
    _REPOSITORY_ROOT / "sdk" / "tests" / "fixtures" / "matrix_stdio_server.py"
)


def _agent(path: Path, *, slow: bool = False, pidfile: Path | None = None) -> str:
    delay = "import time; time.sleep(30)" if slow else ""
    marker = (
        f"tmp = {str(pidfile)!r} + '.' + str(os.getpid()) + '.tmp'; "
        f"pathlib.Path(tmp).write_text(str(os.getpid())); "
        f"os.replace(tmp, {str(pidfile)!r})"
        if pidfile
        else ""
    )
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json,os,pathlib,sys\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line); m=r.get('method')\n"
        " if m=='initialize': print(json.dumps({'jsonrpc':'2.0','id':r['id'],"
        "'result':{'protocolVersion':1,'agentInfo':{'name':'fixture','version':'1'}}}),flush=True)\n"
        " elif m=='session/new':\n"
        f"  {marker}\n"
        f"  {delay}\n"
        "  print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{'sessionId':'s'}}),flush=True)\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_v2_mcp_profile_lifecycle_and_v1_is_gone(tmp_path: Path) -> None:
    database = tmp_path / "profiles.sqlite"
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        assert client.get("/api/v2/profiles").json() == []
        created = client.post(
            "/api/v2/profiles",
            json={
                "name": "fixture",
                "description": "one",
                "mcp_json": {"mcpServers": {"echo": {"command": "echo"}}},
            },
        )
        assert created.status_code == 201
        profile = created.json()
        profile_id = profile["id"]
        assert profile["revisions"][0]["mcp_json"]["mcpServers"]["echo"]
        assert (
            client.patch(
                f"/api/v2/profiles/{profile_id}",
                json={"name": "fixture-renamed", "description": "two"},
            ).status_code
            == 200
        )
        revision = client.post(
            f"/api/v2/profiles/{profile_id}/revisions",
            json={"mcp_json": {"mcpServers": {"echo": {"command": "printf"}}}},
        )
        assert revision.status_code == 201
        assert len(revision.json()["revisions"]) == 2
        assert client.post(f"/api/v2/profiles/{profile_id}/archive").json()["archived"]
        assert (
            client.post(f"/api/v2/profiles/{profile_id}/restore").json()["archived"]
            is False
        )

        for method, path in (
            ("get", "/api/v1/health"),
            ("get", "/api/v1/capabilities"),
            ("get", "/api/v1/profiles"),
            ("post", "/api/v1/profiles"),
            ("get", "/api/v1/profiles/id"),
            ("post", "/api/v1/profiles/id/revisions"),
            ("post", "/api/v1/profiles/id/archive"),
            ("post", "/api/v1/profiles/id/restore"),
            ("get", "/api/v1/harness-profiles"),
            ("post", "/api/v1/harness-profiles"),
            ("get", "/api/v1/harness-profiles/id"),
            ("patch", "/api/v1/harness-profiles/id"),
            ("post", "/api/v1/harness-profiles/id/archive"),
            ("post", "/api/v1/harness-profiles/id/restore"),
            ("post", "/api/v1/harness-profiles/id/revisions"),
            ("get", "/api/v1/harness-profiles/id/export"),
            ("post", "/api/v1/harness-profiles/import"),
            ("get", "/api/v1/harness-profiles/id/probes"),
            ("post", "/api/v1/harness-profiles/id/probe"),
            ("post", "/api/v1/runs"),
            ("get", "/api/v1/runs"),
            ("get", "/api/v1/runs/id"),
            ("get", "/api/v1/runs/id/events"),
            ("post", "/api/v1/runs/id/clone"),
            ("post", "/api/v1/runs/id/cancel"),
            ("delete", "/api/v1/runs/id"),
            ("delete", "/api/v1/runs"),
            ("get", "/api/v1/runs/id/report"),
        ):
            assert getattr(client, method)(path).status_code == 404, (method, path)
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        restarted = client.get(f"/api/v2/profiles/{profile_id}")
        assert restarted.status_code == 200
        assert restarted.json()["name"] == "fixture-renamed"
        assert len(restarted.json()["revisions"]) == 2
    sdk_store = SQLiteExecutionStore(database)
    try:
        assert sdk_store.get_profile(profile_id) is not None
        assert sdk_store.get_profile(profile_id).name == "fixture-renamed"
    finally:
        sdk_store.close()


def test_legacy_profile_credentials_never_cross_v2_api_boundary(
    tmp_path: Path,
) -> None:
    """App-owned migration rejects legacy credentials before v2 can expose them."""
    database = tmp_path / "legacy-credential.sqlite"
    credential = "legacy-settings-credential"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE mcp_profiles (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
            archived BOOLEAN, current_revision_id TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE mcp_profile_revisions (
            id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
            revision_number INTEGER NOT NULL, mcp_json TEXT NOT NULL,
            created_at TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO mcp_profiles VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-credential",
            "legacy-credential",
            "legacy",
            0,
            "legacy-credential-revision",
            "2024-01-01T00:00:00+00:00",
            "2024-01-01T00:00:00+00:00",
        ),
    )
    connection.execute(
        "INSERT INTO mcp_profile_revisions VALUES (?, ?, ?, ?, ?)",
        (
            "legacy-credential-revision",
            "legacy-credential",
            1,
            json.dumps(
                {
                    "mcpServers": {
                        "legacy": {
                            "type": "http",
                            "url": "https://example.invalid/mcp",
                            "headers": {"Authorization": credential},
                        }
                    }
                }
            ),
            "2024-01-01T00:00:00+00:00",
        ),
    )
    connection.commit()
    connection.close()

    settings = Settings(database_path=str(database), anthropic_api_key=credential)
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v2/profiles/legacy-credential")

    assert response.status_code == 404
    assert response.json() == {
        "version": "v2",
        "error": {
            "code": "profile_not_found",
            "message": "profile was not found",
            "details": {},
        },
    }
    assert credential not in response.text


def test_profile_conflicts_preserve_metadata_and_archived_revisions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "profile-conflicts.sqlite"
    conflict_error = {
        "version": "v2",
        "error": {
            "code": "profile_conflict",
            "message": "profile already exists",
            "details": {},
        },
    }
    archived_error = {
        "version": "v2",
        "error": {
            "code": "profile_conflict",
            "message": "profile cannot be modified",
            "details": {},
        },
    }
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        server_one = client.post(
            "/api/v2/profiles",
            json={
                "name": "server-one",
                "mcp_json": {"mcpServers": {"echo": {"command": "echo"}}},
            },
        ).json()
        client.post(
            "/api/v2/profiles",
            json={
                "name": "server-two",
                "mcp_json": {"mcpServers": {"echo": {"command": "echo"}}},
            },
        )
        harness_one = client.post(
            "/api/v2/harness-profiles",
            json={
                "name": "harness-one",
                "manifest": {"command": "agent-one"},
                "trusted_unsandboxed": True,
            },
        ).json()
        client.post(
            "/api/v2/harness-profiles",
            json={
                "name": "harness-two",
                "manifest": {"command": "agent-two"},
                "trusted_unsandboxed": True,
            },
        )

        response = client.patch(
            f"/api/v2/profiles/{server_one['id']}",
            json={"name": "server-two"},
        )
        assert response.status_code == 409
        assert response.json() == conflict_error
        assert client.get(f"/api/v2/profiles/{server_one['id']}").json()["name"] == (
            "server-one"
        )

        response = client.patch(
            f"/api/v2/harness-profiles/{harness_one['id']}",
            json={"name": "harness-two"},
        )
        assert response.status_code == 409
        assert response.json() == conflict_error
        assert (
            client.get(f"/api/v2/harness-profiles/{harness_one['id']}").json()["name"]
            == "harness-one"
        )

        assert (
            client.post(f"/api/v2/profiles/{server_one['id']}/archive").status_code
            == 200
        )
        response = client.post(
            f"/api/v2/profiles/{server_one['id']}/revisions",
            json={"mcp_json": {"mcpServers": {"echo": {"command": "printf"}}}},
        )
        assert response.status_code == 409
        assert response.json() == archived_error
        assert (
            len(client.get(f"/api/v2/profiles/{server_one['id']}").json()["revisions"])
            == 1
        )

        assert (
            client.post(
                f"/api/v2/harness-profiles/{harness_one['id']}/archive"
            ).status_code
            == 200
        )
        response = client.post(
            f"/api/v2/harness-profiles/{harness_one['id']}/revisions",
            json={
                "manifest": {"command": "agent-three"},
                "trusted_unsandboxed": True,
            },
        )
        assert response.status_code == 409
        assert response.json() == archived_error
        assert (
            len(
                client.get(f"/api/v2/harness-profiles/{harness_one['id']}").json()[
                    "revisions"
                ]
            )
            == 1
        )


def test_v2_profile_validation_and_saved_profile_resolution_errors_are_enveloped(
    tmp_path: Path,
) -> None:
    database = tmp_path / "profile-errors.sqlite"

    def saved_profile_spec(profile_id: str) -> dict[str, object]:
        spec = AgentSpec(
            servers=(
                ServerBinding(
                    profile=ServerProfileRef(
                        profile_id=profile_id,
                        server_name="echo",
                        revision=RevisionSelection(mode="latest"),
                    )
                ),
            ),
            harness=ClaudeCode(model="fixture", executable="not-started"),
            message=UserMessage(content=(TextContent(text="probe"),)),
        )
        return spec.model_dump(mode="json")

    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        invalid = client.post(
            "/api/v2/profiles",
            json={
                "name": "invalid",
                "mcp_json": {"mcpServers": {"profile-error-secret": {}}},
            },
        )
        assert invalid.status_code == 422
        assert b"profile-error-secret" not in invalid.content
        assert invalid.json() == {
            "version": "v2",
            "error": {
                "code": "invalid_profile",
                "message": "profile request is invalid",
                "details": {},
            },
        }

        for path in (
            "/api/v2/profiles/missing-profile",
            "/api/v2/harness-profiles/missing-profile",
        ):
            not_found = client.get(path)
            assert not_found.status_code == 404
            assert not_found.json() == {
                "version": "v2",
                "error": {
                    "code": "profile_not_found",
                    "message": "profile was not found",
                    "details": {},
                },
            }

        missing = client.post(
            "/api/v2/executions",
            json={"spec": saved_profile_spec("missing-profile")},
        )
        assert missing.status_code == 404
        assert missing.json() == {
            "version": "v2",
            "error": {
                "code": "profile_resolution_failed",
                "message": "saved profile could not be resolved",
                "details": {"kind": "server", "reason": "missing"},
            },
        }

        profile = client.post(
            "/api/v2/profiles",
            json={
                "name": "archived",
                "mcp_json": {"mcpServers": {"echo": {"command": "echo"}}},
            },
        ).json()
        client.post(f"/api/v2/profiles/{profile['id']}/archive")
        archived = client.post(
            "/api/v2/executions",
            json={"spec": saved_profile_spec(profile["id"])},
        )
        assert archived.status_code == 409
        assert archived.json() == {
            "version": "v2",
            "error": {
                "code": "profile_resolution_failed",
                "message": "saved profile is archived",
                "details": {"kind": "server", "reason": "archived"},
            },
        }


def test_ui_created_profile_drives_real_sdk_execution_with_pinned_provenance(
    tmp_path: Path,
) -> None:
    """The UI and CLI-managed SDK tests consume one saved-profile database."""

    database = tmp_path / "shared.sqlite"
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        response = client.post(
            "/api/v2/profiles",
            json={
                "name": "saved matrix server",
                "mcp_json": {
                    "mcpServers": {
                        "matrix": {
                            "command": sys.executable,
                            "args": [str(_MCP_FIXTURE)],
                            "cwd": str(_REPOSITORY_ROOT),
                        }
                    }
                },
            },
        )
        assert response.status_code == 201
        profile = response.json()
        revision = profile["revisions"][0]

    store = SQLiteExecutionStore(database)
    try:
        binding = ServerBinding(
            profile=ServerProfileRef(
                profile_id=profile["id"],
                server_name="matrix",
                revision=RevisionSelection(
                    mode="pinned",
                    revision_id=revision["id"],
                    revision_number=revision["revision_number"],
                ),
            )
        )
        with MCPTestKit(store=store, env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
            direct = kit.direct(binding)
            with direct:
                result = direct.call_tool("echo", {"text": "from-saved-profile"})
                assert result.content[0]["text"] == "from-saved-profile"

            trace = direct.final_trace
            assert trace is not None
            resolved = store.resolved_bindings(trace.execution_id)
            assert resolved["servers"][0]["profile_id"] == profile["id"]
            assert resolved["servers"][0]["revision_id"] == revision["id"]
    finally:
        store.close()


def test_v2_profile_mutators_reject_the_opposite_profile_family(
    tmp_path: Path,
) -> None:
    database = tmp_path / "family-boundaries.sqlite"
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        server = client.post(
            "/api/v2/profiles",
            json={
                "name": "server",
                "mcp_json": {"mcpServers": {"echo": {"command": "echo"}}},
            },
        ).json()
        harness = client.post(
            "/api/v2/harness-profiles",
            json={
                "name": "harness",
                "manifest": {"command": sys.executable, "args": ["-c", "pass"]},
                "trusted_unsandboxed": True,
            },
        ).json()
        server_id = server["id"]
        harness_id = harness["id"]

        # Every family-specific mutator must fail before touching the record
        # from the other family.  Use valid payloads so this exercises the
        # storage family selection rather than request validation.
        wrong_server_routes = (
            ("patch", f"/api/v2/profiles/{harness_id}", {"name": "changed"}),
            (
                "post",
                f"/api/v2/profiles/{harness_id}/revisions",
                {"mcp_json": {"mcpServers": {"echo": {"command": "printf"}}}},
            ),
            ("post", f"/api/v2/profiles/{harness_id}/archive", None),
        )
        wrong_harness_routes = (
            ("patch", f"/api/v2/harness-profiles/{server_id}", {"name": "changed"}),
            (
                "post",
                f"/api/v2/harness-profiles/{server_id}/revisions",
                {
                    "manifest": {"command": sys.executable, "args": ["-c", "pass"]},
                    "trusted_unsandboxed": True,
                },
            ),
            ("post", f"/api/v2/harness-profiles/{server_id}/archive", None),
        )
        for method, path, body in (*wrong_server_routes, *wrong_harness_routes):
            if body:
                response = getattr(client, method)(path, json=body)
            else:
                response = getattr(client, method)(path)
            assert response.status_code == 404, (method, path, response.text)

        # Wrong-family archive attempts must also leave the target active.
        assert client.get(f"/api/v2/profiles/{server_id}").json()["archived"] is False
        assert (
            client.get(f"/api/v2/harness-profiles/{harness_id}").json()["archived"]
            is False
        )

        # Archive each record through its own family route before checking
        # wrong-family restore.  Otherwise a buggy restore could appear safe
        # simply because the target was active already.
        server_archive = client.post(f"/api/v2/profiles/{server_id}/archive")
        harness_archive = client.post(f"/api/v2/harness-profiles/{harness_id}/archive")
        assert server_archive.json()["archived"] is True
        assert harness_archive.json()["archived"] is True
        for path in (
            f"/api/v2/profiles/{harness_id}/restore",
            f"/api/v2/harness-profiles/{server_id}/restore",
        ):
            response = client.post(path)
            assert response.status_code == 404, (path, response.text)
        assert client.get(f"/api/v2/profiles/{server_id}").json()["archived"] is True
        assert (
            client.get(f"/api/v2/harness-profiles/{harness_id}").json()["archived"]
            is True
        )

        # Correct cleanup/restore keeps the test database reusable and proves
        # the records were not altered by the wrong-family requests.
        server_restore = client.post(f"/api/v2/profiles/{server_id}/restore")
        harness_restore = client.post(f"/api/v2/harness-profiles/{harness_id}/restore")
        assert server_restore.json()["archived"] is False
        assert harness_restore.json()["archived"] is False
        restored_server = client.get(f"/api/v2/profiles/{server_id}").json()
        restored_harness = client.get(f"/api/v2/harness-profiles/{harness_id}").json()
        assert restored_server["name"] == server["name"]
        assert restored_server["revisions"] == server["revisions"]
        assert restored_harness["name"] == harness["name"]
        assert restored_harness["revisions"] == harness["revisions"]


def test_v2_harness_profile_import_export_and_readiness(tmp_path: Path) -> None:
    database = tmp_path / "harness.sqlite"
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        body = {
            "name": "fixture",
            "description": "local",
            "manifest": {"command": sys.executable, "args": ["-c", "pass"]},
            "trusted_unsandboxed": True,
        }
        created = client.post("/api/v2/harness-profiles", json=body)
        assert created.status_code == 201
        profile_id = created.json()["id"]
        assert created.json()["revisions"][0]["manifest"]["command"] == sys.executable
        assert (
            client.patch(
                f"/api/v2/harness-profiles/{profile_id}", json={"name": "renamed"}
            ).status_code
            == 200
        )
        exported = client.get(f"/api/v2/harness-profiles/{profile_id}/export")
        assert exported.status_code == 200
        imported_payload = exported.json()
        imported_payload["name"] = "imported"
        imported = client.post("/api/v2/harness-profiles/import", json=imported_payload)
        assert imported.status_code == 201
        assert imported.json()["revisions"][0]["trusted_unsandboxed"] is False

        for path in ("/api/v2/health", "/api/v2/capabilities", "/api/v2/readiness"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.json()["version"] == "v2"
        assert client.get("/api/v2/health").json()["checks"]["database"] is True


@pytest.mark.process_lifecycle
def test_v2_probe_start_history_and_cancel_use_real_subprocess(tmp_path: Path) -> None:
    database = tmp_path / "probes.sqlite"
    executable = _agent(tmp_path / "agent.py")
    settings = Settings(database_path=str(database), run_timeout_seconds=5)
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/v2/harness-profiles",
            json={
                "name": "fixture",
                "manifest": {"command": executable},
                "trusted_unsandboxed": True,
            },
        )
        profile_id = created.json()["id"]
        started = client.post(f"/api/v2/harness-profiles/{profile_id}/probes", json={})
        assert started.status_code == 202
        probe_id = started.json()["probe"]["id"]
        for _ in range(100):
            history = client.get(
                f"/api/v2/harness-profiles/{profile_id}/probes"
            ).json()["probes"]
            if history and history[0]["status"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert history[0]["id"] == probe_id
        assert history[0]["status"] == "verified"

        cancelled = client.post(
            f"/api/v2/harness-profiles/{profile_id}/probes/{probe_id}/cancel"
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["probe"]["status"] == "verified"


def test_v2_probe_history_matches_full_probe_session_configuration(
    tmp_path: Path,
) -> None:
    database = tmp_path / "probe-history-dimension.sqlite"

    def runner(request):
        if request.probe_type.value == "protocol":
            return {
                "status": "verified",
                "agent_identity": {"name": "fixture", "version": "1"},
                "config_options": [
                    {"id": "quality", "type": "select", "options": [{"value": "high"}]}
                ],
            }
        return {
            "status": "verified",
            "agent_identity": {"name": "fixture", "version": "1"},
        }

    runtime = AppRuntimeService(
        Settings(database_path=str(database)),
        acp_probe_runner=runner,
        embedded_worker=False,
    )
    try:
        with TestClient(create_app(runtime=runtime)) as client:
            profile = client.post(
                "/api/v2/harness-profiles",
                json={
                    "name": "history",
                    "manifest": {"command": "echo"},
                    "trusted_unsandboxed": True,
                },
            ).json()
            profile_id = profile["id"]
            protocol = client.post(
                f"/api/v2/harness-profiles/{profile_id}/probes",
                json={"probe_type": "protocol"},
            )
            assert protocol.status_code == 202
            for _ in range(100):
                if client.get(f"/api/v2/harness-profiles/{profile_id}/probes").json()[
                    "probes"
                ]:
                    break
                time.sleep(0.01)

            config = {"quality": "high"}
            full = client.post(
                f"/api/v2/harness-profiles/{profile_id}/probes",
                json={"probe_type": "full", "session_config": config},
            )
            assert full.status_code == 202
            full_id = full.json()["probe"]["id"]
            query = {
                "kind": "full",
                "session_config": json.dumps(config, separators=(",", ":")),
            }
            for _ in range(100):
                history = client.get(
                    f"/api/v2/harness-profiles/{profile_id}/probes", params=query
                ).json()["probes"]
                if history and history[0]["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.01)
            assert [item["id"] for item in history] == [full_id]
            assert history[0]["session_config"] == config
    finally:
        runtime.close()


@pytest.mark.process_lifecycle
def test_v2_probe_cancel_running_subprocess_during_shutdown(tmp_path: Path) -> None:
    database = tmp_path / "cancel.sqlite"
    pidfile = tmp_path / "agent.pid"
    executable = _agent(tmp_path / "slow-agent.py", slow=True, pidfile=pidfile)
    application = create_app(Settings(database_path=str(database)))
    with TestClient(application) as client:
        created = client.post(
            "/api/v2/harness-profiles",
            json={
                "name": "slow",
                "manifest": {"command": executable},
                "trusted_unsandboxed": True,
            },
        )
        profile_id = created.json()["id"]
        other = client.post(
            "/api/v2/harness-profiles",
            json={
                "name": "other",
                "manifest": {"command": executable},
                "trusted_unsandboxed": True,
            },
        ).json()["id"]
        started = client.post(f"/api/v2/harness-profiles/{profile_id}/probes", json={})
        probe_id = started.json()["probe"]["id"]
        pid = wait_for_pid(pidfile)
        wrong_owner = client.post(
            f"/api/v2/harness-profiles/{other}/probes/{probe_id}/cancel"
        )
        assert wrong_owner.status_code == 404
        still_running = client.get(
            f"/api/v2/harness-profiles/{profile_id}/probes"
        ).json()["probes"][0]
        assert still_running["status"] in {"queued", "running"}
        cancelled = client.post(
            f"/api/v2/harness-profiles/{profile_id}/probes/{probe_id}/cancel"
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["probe"]["status"] == "cancelled"

    # Runtime teardown must not leave the ACP child alive after cancellation.
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("ACP probe subprocess survived runtime shutdown")
