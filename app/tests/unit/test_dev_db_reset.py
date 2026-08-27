from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "dev_db_reset.py"


def _run(root: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    selected = os.environ.copy()
    if env:
        selected.update(env)
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=root, env=selected, capture_output=True, text=True)


def test_reset_requires_exact_confirmation_and_removes_only_sidecars(tmp_path):
    db = tmp_path / "state.sqlite"
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db}{suffix}").write_bytes(b"state")
    refused = _run(tmp_path, "no")
    assert refused.returncode == 2
    assert db.exists()
    result = _run(tmp_path, env={"CONFIRM": "reset", "DATABASE_PATH": "state.sqlite"})
    assert result.returncode == 0, result.stderr
    assert not any(Path(f"{db}{suffix}").exists() for suffix in ("", "-wal", "-shm"))


def test_reset_rejects_outside_path_and_directories(tmp_path):
    outside = tmp_path.parent / "outside.sqlite"
    outside.write_bytes(b"keep")
    result = _run(tmp_path, env={"CONFIRM": "reset", "DATABASE_PATH": str(outside)})
    assert result.returncode == 2
    assert outside.exists()
    directory = tmp_path / "database-dir"
    directory.mkdir()
    result = _run(tmp_path, env={"CONFIRM": "reset", "DATABASE_PATH": "database-dir"})
    assert result.returncode == 2
    assert directory.exists()


def test_hostile_confirmation_is_data_not_shell(tmp_path):
    marker = tmp_path / "pwned"
    result = _run(
        tmp_path,
        env={
            "CONFIRM": f"reset; touch {marker}",
            "DATABASE_PATH": "state.sqlite",
        },
    )
    assert result.returncode == 2
    assert not marker.exists()


def test_reset_rejects_symlinked_database_and_sidecars(tmp_path):
    target = tmp_path / "target.sqlite"
    target.write_bytes(b"keep")
    link = tmp_path / "state.sqlite"
    link.symlink_to(target)
    result = _run(tmp_path, env={"CONFIRM": "reset", "DATABASE_PATH": "state.sqlite"})
    assert result.returncode == 2
    assert link.is_symlink()
    assert target.exists()

    database = tmp_path / "fresh.sqlite"
    database.write_bytes(b"state")
    sidecar_target = tmp_path / "sidecar-target"
    sidecar_target.write_bytes(b"keep")
    Path(f"{database}-wal").symlink_to(sidecar_target)
    result = _run(tmp_path, env={"CONFIRM": "reset", "DATABASE_PATH": database.name})
    assert result.returncode == 2
    assert Path(f"{database}-wal").is_symlink()
    assert sidecar_target.exists()


def test_reset_rejects_extra_arguments_even_with_environment_confirmation(tmp_path):
    database = tmp_path / "state.sqlite"
    database.write_bytes(b"keep")
    result = _run(tmp_path, "ignored", env={"CONFIRM": "reset", "DATABASE_PATH": database.name})
    assert result.returncode == 2
    assert database.exists()


def test_reset_then_fresh_sqlite_startup_recreates_schema(tmp_path):
    from mcp_pal.storage import SQLiteExecutionStore

    database = tmp_path / "state.sqlite"
    store = SQLiteExecutionStore(database, blob_root=tmp_path / "blobs")
    store.close()
    result = _run(tmp_path, env={"CONFIRM": "reset", "DATABASE_PATH": database.name})
    assert result.returncode == 0, result.stderr
    recreated = SQLiteExecutionStore(database, blob_root=tmp_path / "blobs")
    with sqlite3.connect(database) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "v2_executions" in names
    recreated.close()
