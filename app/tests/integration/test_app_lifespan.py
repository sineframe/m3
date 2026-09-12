from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from mcp_pal import MCPTestKit
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal_app.api import create_app
from mcp_pal_app.persistence.database import make_engine
from mcp_pal_app.persistence.models import McpProfile, McpProfileRevision, Run
from mcp_pal_app.settings import Settings

app_module = importlib.import_module("mcp_pal_app.api.app")


def _add_legacy_runs(application, statuses: tuple[str, ...]) -> tuple[str, ...]:
    session = application.state.session_factory()
    profile = McpProfile(id="test-profile", name="Test profile")
    revision = McpProfileRevision(
        id="test-revision",
        profile_id=profile.id,
        revision_number=1,
        mcp_json={},
    )
    session.add_all([profile, revision])
    session.flush()
    profile.current_revision_id = revision.id
    run_ids: list[str] = []
    for index, status in enumerate(statuses):
        run_id = f"legacy-run-{index}"
        run_ids.append(run_id)
        session.add(
            Run(
                id=run_id,
                profile_revision_id=revision.id,
                enabled_server="server",
                model="model",
                prompt="prompt",
                expected_output="output",
                timeout_seconds=30,
                max_turns=1,
                max_budget_usd=1.0,
                status=status,
            )
        )
    session.commit()
    session.close()
    return tuple(run_ids)


def _statuses(application, run_ids: tuple[str, ...]) -> dict[str, str]:
    session = application.state.session_factory()
    values = {run_id: session.get(Run, run_id).status for run_id in run_ids}
    session.close()
    return values


def test_database_import_does_not_construct_a_global_engine() -> None:
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sqlalchemy
def fail(*args, **kwargs):
    raise AssertionError('global engine construction')
sqlalchemy.create_engine = fail
import mcp_pal_app.persistence.database as database
assert not hasattr(database, 'engine')
assert not hasattr(database, 'SessionLocal')
assert not hasattr(database, 'init_db')
assert not hasattr(database, 'get_db')
""",
        ],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr


def test_create_app_does_not_recover_legacy_runs_until_lifespan(tmp_path: Path) -> None:
    application = create_app(Settings(database_path=str(tmp_path / "lifespan.sqlite")))
    run_ids = _add_legacy_runs(application, ("queued", "running", "completed"))
    assert _statuses(application, run_ids) == {
        run_ids[0]: "queued",
        run_ids[1]: "running",
        run_ids[2]: "completed",
    }
    with TestClient(application):
        recovered = _statuses(application, run_ids)
    assert recovered[run_ids[0]] == "failed"
    assert recovered[run_ids[1]] == "failed"
    assert recovered[run_ids[2]] == "completed"

    session = application.state.session_factory()
    failed = [session.get(Run, run_id) for run_id in run_ids[:2]]
    assert all(run.error_message == "Interrupted by backend restart" for run in failed)
    assert all(run.finished_at is not None for run in failed)
    session.close()


def test_builtin_profile_is_seeded_on_startup(tmp_path: Path) -> None:
    application = create_app(Settings(database_path=str(tmp_path / "seed.sqlite")))
    with TestClient(application) as client:
        response = client.get("/api/v1/profiles")
    assert response.status_code == 200
    assert any(profile["name"] == "Excalidraw" for profile in response.json())


def test_lifespan_startup_failure_rolls_back_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(database_path=str(tmp_path / "startup-failure.sqlite"))
    engine = make_engine(settings.database_url)
    base_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    closed_sessions = 0

    def tracking_factory():
        nonlocal closed_sessions
        session = base_factory()
        close = session.close

        def tracked_close() -> None:
            nonlocal closed_sessions
            closed_sessions += 1
            close()

        session.close = tracked_close
        return session

    def fail_after_write(session) -> None:
        session.add(McpProfile(id="rolled-back", name="Rolled back"))
        session.flush()
        raise RuntimeError("startup seed failed")

    manager_shutdowns = 0
    kit_closes = 0
    store_closes = 0
    original_shutdown = app_module.RunManager.shutdown
    original_kit_close = MCPTestKit.close
    original_store_close = SQLiteExecutionStore.close

    def tracked_shutdown(manager) -> None:
        nonlocal manager_shutdowns
        manager_shutdowns += 1
        original_shutdown(manager)

    def tracked_kit_close(kit) -> None:
        nonlocal kit_closes
        kit_closes += 1
        original_kit_close(kit)

    def tracked_store_close(store) -> None:
        nonlocal store_closes
        store_closes += 1
        original_store_close(store)

    monkeypatch.setattr(app_module, "ensure_builtin_profiles", fail_after_write)
    monkeypatch.setattr(app_module.RunManager, "shutdown", tracked_shutdown)
    monkeypatch.setattr(MCPTestKit, "close", tracked_kit_close)
    monkeypatch.setattr(SQLiteExecutionStore, "close", tracked_store_close)

    application = create_app(
        settings, engine_override=engine, session_factory=tracking_factory
    )
    with pytest.raises(RuntimeError, match="startup seed failed"):
        with TestClient(application):
            pass

    check = base_factory()
    assert check.get(McpProfile, "rolled-back") is None
    check.close()
    assert closed_sessions == 1
    assert manager_shutdowns == 1
    assert kit_closes == 1
    assert store_closes == 1
    engine.dispose()
