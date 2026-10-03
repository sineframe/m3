"""Per-request API timing log."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from _local_client import TestClient

from m3_app import api_log
from m3_app.api import create_app
from m3_app.settings import Settings


@pytest.fixture(autouse=True)
def log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "logs" / "api.log"
    monkeypatch.setenv(api_log.FILE_ENV, str(path))
    monkeypatch.delenv(api_log.LEVEL_ENV, raising=False)
    api_log._reset()
    yield path
    api_log._reset()


def _client(tmp_path: Path, **kwargs: object) -> TestClient:
    app = create_app(Settings(database_path=str(tmp_path / "api.sqlite")))
    return TestClient(app, **kwargs)  # type: ignore[arg-type]


def _lines(path: Path, level: str) -> list[str]:
    return [line for line in path.read_text().splitlines() if f" {level} " in line]


def test_request_logs_route_template_and_phases(tmp_path: Path, log_file: Path) -> None:
    with _client(tmp_path) as client:
        assert client.get("/api/v2/executions/exec-secret-id").status_code == 404
        assert client.get("/api/v2/executions").status_code == 200

    lines = [line for line in _lines(log_file, "INFO") if "session start" not in line]
    assert len(lines) == 2
    assert "GET /api/v2/executions/{execution_id} 404 total=" in lines[0]
    assert "GET /api/v2/executions 200 total=" in lines[1]
    for line in lines:
        for name in ("pre=", "handler=", "post=", "store="):
            assert name in line
    assert "store=0.0ms/0" not in lines[1]
    assert "exec-secret-id" not in log_file.read_text()
    assert not _lines(log_file, "DEBUG")


def test_debug_adds_store_breakdown_and_test_id(
    tmp_path: Path, log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(api_log.LEVEL_ENV, "debug")
    with _client(tmp_path) as client:
        client.get("/api/v2/executions")

    (debug,) = _lines(log_file, "DEBUG")
    assert "GET /api/v2/executions store: list_executions=" in debug
    assert "test_api_log.py::test_debug_adds_store_breakdown_and_test_id" in debug


def test_slow_request_logs_warning(
    tmp_path: Path, log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_log, "SLOW_NS", 0)
    with _client(tmp_path) as client:
        client.get("/api/v2/executions")

    (warning,) = _lines(log_file, "WARNING")
    assert "slow GET /api/v2/executions 200 total=" in warning


def test_unhandled_exception_logs_error_without_message(
    tmp_path: Path, log_file: Path
) -> None:
    app = create_app(Settings(database_path=str(tmp_path / "api.sqlite")))

    @app.get("/api/v2/boom")
    def boom() -> None:
        raise KeyError("private-value")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/v2/boom").status_code == 500

    (error,) = _lines(log_file, "ERROR")
    assert "GET /api/v2/boom 500 total=" in error
    assert error.endswith("error=KeyError")
    assert "private-value" not in log_file.read_text()


def test_non_api_paths_are_not_logged(tmp_path: Path, log_file: Path) -> None:
    with _client(tmp_path) as client:
        client.get("/openapi.json")

    assert [
        line for line in _lines(log_file, "INFO") if "session start" not in line
    ] == []


def test_unwritable_log_file_does_not_fail_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    monkeypatch.setenv(api_log.FILE_ENV, str(blocker / "api.log"))
    with _client(tmp_path) as client:
        assert client.get("/api/v2/executions").status_code == 200

    assert "API request log disabled" in capsys.readouterr().err


def test_configures_once_per_process(tmp_path: Path, log_file: Path) -> None:
    create_app(Settings(database_path=str(tmp_path / "one.sqlite")))
    create_app(Settings(database_path=str(tmp_path / "two.sqlite")))

    handlers = [h for h in api_log.logger.handlers if getattr(h, api_log._MARK, False)]
    assert len(handlers) == 1
    assert log_file.read_text().count("session start") == 1


def test_new_session_appends(tmp_path: Path, log_file: Path) -> None:
    with _client(tmp_path) as client:
        client.get("/api/v2/executions")
    api_log._reset()
    with _client(tmp_path) as client:
        client.get("/api/v2/executions")

    text = log_file.read_text()
    assert text.count("session start") == 2
    assert text.count("GET /api/v2/executions 200") == 2


def test_oversized_log_rolls_over_at_start(
    log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_log, "MAX_BYTES", 10)
    log_file.parent.mkdir(parents=True)
    log_file.write_text("previous session\n")
    api_log._configure()

    assert (log_file.parent / "api.log.1").read_text() == "previous session\n"
    assert "previous session" not in log_file.read_text()
    assert logging.getLogger("m3_app.api").propagate is False


def test_debug_test_id_drops_parameter_values(
    tmp_path: Path, log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(api_log.LEVEL_ENV, "DEBUG")
    app = create_app(Settings(database_path=str(tmp_path / "api.sqlite")))
    with TestClient(app) as client:
        monkeypatch.setenv(
            "PYTEST_CURRENT_TEST",
            "tests/test_auth.py::test_login[sk-live-secret] (call)",
        )
        client.get("/api/v2/executions")

    (debug,) = _lines(log_file, "DEBUG")
    assert debug.endswith("test=tests/test_auth.py::test_login[param]")
    assert "sk-live-secret" not in log_file.read_text()


def test_running_process_follows_rollover_by_another_process(
    tmp_path: Path, log_file: Path
) -> None:
    with _client(tmp_path) as client:
        client.get("/api/v2/executions")
        # Another process starting up rolls the file over.
        os.replace(log_file, log_file.parent / "api.log.1")
        client.get("/api/v2/profiles")

    assert "GET /api/v2/profiles" not in (log_file.parent / "api.log.1").read_text()
    assert "GET /api/v2/profiles 200" in log_file.read_text()


def test_rollover_waits_for_the_shared_lock(
    log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_log, "MAX_BYTES", 10)
    log_file.parent.mkdir(parents=True)
    log_file.write_text("previous session\n")
    backup = log_file.parent / "api.log.1"

    with api_log._file_lock(log_file.parent / "api.log.lock"):
        worker = threading.Thread(target=api_log._roll_over, args=(log_file,))
        worker.start()
        worker.join(timeout=0.2)
        assert worker.is_alive()
        assert not backup.exists()
    worker.join(timeout=5)

    assert backup.read_text() == "previous session\n"
    # A second process checking after the rotation sees the fresh file and
    # leaves the backup alone.
    log_file.write_text("new\n")
    api_log._roll_over(log_file)
    assert backup.read_text() == "previous session\n"
