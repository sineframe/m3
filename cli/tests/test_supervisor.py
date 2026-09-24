from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import sys
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from typing import ClassVar

import pytest

from m3_cli import main, supervisor
from m3_cli.branding import M3_ASCII_ART


def test_readiness_signal_does_not_contact_ambient_http_proxy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed_proxy_requests: list[tuple[str, str | None]] = []

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed_proxy_requests.append(
                (self.path, self.headers.get("Authorization"))
            )
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    proxy_server = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    proxy_thread = Thread(target=proxy_server.serve_forever, daemon=True)
    proxy_thread.start()
    proxy_url = f"http://127.0.0.1:{proxy_server.server_port}"
    monkeypatch.setenv("HTTP_PROXY", proxy_url)
    monkeypatch.setenv("http_proxy", proxy_url)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")

    port_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    port_socket.bind(("127.0.0.1", 0))
    port = port_socket.getsockname()[1]
    port_socket.close()
    token = "readiness-secret-token-1234567890"
    _use_fixture_ui_in_child(monkeypatch, Path(__file__).parent / "fixtures" / "ui")
    child = supervisor._ServerChild(tmp_path / "results.sqlite", port, token)
    try:
        ready = supervisor._wait_ready(child, timeout=5)
        if ready:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request(
                "GET", "/api/v2/health", headers={"Authorization": f"Bearer {token}"}
            )
            response = connection.getresponse()
            response.read()
            connection.close()
        else:
            response = None
    finally:
        supervisor._stop_server(child)
        proxy_server.shutdown()
        proxy_server.server_close()
        proxy_thread.join(timeout=1)

    assert ready is True
    assert response is not None and response.status == 200
    assert observed_proxy_requests == []


def test_competing_listener_never_receives_launch_token_or_ui_link(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """A listener that wins the port race must not be treated as the web child."""

    observed_authorization: list[str | None] = []

    class CompetingHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed_authorization.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    released = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    released.bind(("127.0.0.1", 0))
    port = released.getsockname()[1]
    released.close()
    competitor = ThreadingHTTPServer(("127.0.0.1", port), CompetingHandler)
    competitor_thread = Thread(target=competitor.serve_forever, daemon=True)
    competitor_thread.start()

    _use_fixture_ui_in_child(monkeypatch, Path(__file__).parent / "fixtures" / "ui")
    monkeypatch.setattr(
        supervisor.secrets, "token_urlsafe", lambda _size: "fixed-launch-token-123456"
    )
    try:
        result = supervisor._run_ui_server(tmp_path / "results.sqlite", port, 1, (), ())
    finally:
        competitor.shutdown()
        competitor.server_close()
        competitor_thread.join(timeout=1)
    captured = capsys.readouterr()
    assert result == 2
    assert observed_authorization == []
    assert "UI:" not in captured.out
    assert "fixed-launch-token-123456" not in captured.out + captured.err


@pytest.mark.parametrize("value", ["codex=", "unknown=model", "opencode=a,,b"])
def test_selection_options_validate_even_without_agent_tests(value: str) -> None:
    assert supervisor._validate_selection_options((value,), None, ()) is not None


def test_selection_options_reject_duplicate_scoped_credentials() -> None:
    assert (
        supervisor._validate_selection_options(
            (), 1, ("opencode:VENDOR_KEY=SOURCE", "opencode:VENDOR_KEY=OTHER")
        )
        == "duplicate credential target 'VENDOR_KEY'"
    )


def test_selection_options_reject_duplicate_harness_models_across_flags() -> None:
    assert (
        supervisor._validate_selection_options(
            ("opencode=provider/a,provider/b", "opencode=provider/b"), None, ()
        )
        == "duplicate harness/model selection opencode=provider/b"
    )


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_selection_options_reject_invalid_execution_timeout(value: float) -> None:
    assert supervisor._validate_selection_options((), None, (), value) == (
        "--execution-timeout must be a positive finite number"
    )


class _Process:
    def __init__(self, code: int = 0, wait_error: BaseException | None = None) -> None:
        self.pid = 12345
        self.returncode = None
        self.code = code
        self.wait_error = wait_error

    def wait(self, **_: object) -> int:
        if self.wait_error is not None:
            raise self.wait_error
        self.returncode = self.code
        return self.code

    def poll(self) -> int | None:
        return self.returncode


def test_only_doctor_and_test_commands_are_public() -> None:
    parser = main.__module__
    assert parser == "m3_cli.main"
    assert main(["ui"]) == 2


def test_old_port_flags_are_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["test", "--api-port", "8001"]) == 2
    assert "8001" not in capsys.readouterr().err


def test_ui_preflight_rejects_busy_port_before_pytest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        supervisor, "_validate_port", lambda _port: "port is already in use"
    )
    monkeypatch.setattr(
        supervisor,
        "_run_pytest_process",
        lambda *_args, **_kwargs: pytest.fail("pytest started"),
    )
    assert main(["test", "--ui", "--port", "8123"]) == 2
    assert "port is already in use" in capsys.readouterr().err


def test_ui_preflight_rejects_missing_bundle_before_pytest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(supervisor, "_validate_port", lambda _port: None)
    monkeypatch.setattr(
        supervisor,
        "_ui_prerequisite_error",
        lambda _ui_dir: "bundled M3 UI assets are unavailable",
    )
    monkeypatch.setattr(
        supervisor,
        "_run_pytest_process",
        lambda *_args, **_kwargs: pytest.fail("pytest started"),
    )
    assert supervisor.run_test(ui=True, port=8123) == 2
    assert "bundled M3 UI assets are unavailable" in capsys.readouterr().err


def test_ui_server_prints_links_and_returns_original_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Child:
        process = SimpleNamespace(poll=lambda: None)

        def alive(self) -> bool:
            return True

    monkeypatch.setattr(supervisor, "_ServerChild", lambda *_args: Child())
    monkeypatch.setattr(supervisor, "_wait_ready", lambda *_args: True)
    monkeypatch.setattr(supervisor, "_terminate_process", lambda _process: None)
    monkeypatch.setattr(
        supervisor.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    runs = (_run("run id/1", 1), _run("run-two", 2))
    assert supervisor._run_ui_server(Path("results.sqlite"), 8123, 1, runs, ()) == 1
    output = capsys.readouterr().out
    lines = output.splitlines()
    art_lines = M3_ASCII_ART.splitlines()
    assert lines[: len(art_lines)] == art_lines
    assert lines[len(art_lines)].startswith(
        "Run: http://127.0.0.1:8123/reports/runs/run%20id%2F1#m3_token="
    )
    token = lines[len(art_lines)].split("m3_token=", 1)[1]
    assert lines[len(art_lines) :] == [
        f"Run: http://127.0.0.1:8123/reports/runs/run%20id%2F1#m3_token={token}",
        f"Run: http://127.0.0.1:8123/reports/runs/run-two#m3_token={token}",
    ]


def test_ui_server_zero_runs_prints_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Child:
        process = SimpleNamespace(poll=lambda: None)

        def alive(self) -> bool:
            return True

    monkeypatch.setattr(supervisor, "_ServerChild", lambda *_args: Child())
    monkeypatch.setattr(supervisor, "_wait_ready", lambda *_args: True)
    monkeypatch.setattr(supervisor, "_terminate_process", lambda _process: None)
    monkeypatch.setattr(
        supervisor.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    assert supervisor._run_ui_server(Path("results.sqlite"), 8123, 0, (), ()) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[: len(M3_ASCII_ART.splitlines())] == M3_ASCII_ART.splitlines()
    assert lines[len(M3_ASCII_ART.splitlines()) :] == ["No new stored runs."]


@pytest.mark.parametrize("exit_code", [2, 130, 143])
def test_ui_is_not_started_for_interrupted_or_collection_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, exit_code: int
) -> None:
    monkeypatch.setattr(supervisor, "_validate_port", lambda _port: None)
    monkeypatch.setattr(supervisor, "_ui_prerequisite_error", lambda _ui_dir: None)
    monkeypatch.setattr(
        supervisor,
        "_prepare_test",
        lambda *_args: (Path(sys.executable), tmp_path / "results.sqlite"),
    )
    monkeypatch.setattr(
        supervisor, "list_stored_runs", lambda _db: supervisor.StoredRuns()
    )
    monkeypatch.setattr(
        supervisor, "_run_pytest_process", lambda *_args, **_kwargs: exit_code
    )
    monkeypatch.setattr(
        supervisor, "_run_ui_server", lambda *_args: pytest.fail("UI server started")
    )
    result = supervisor.run_test_with_runs(
        ui=True, port=8123, ui_dir=tmp_path, project_root=tmp_path
    )
    assert result.exit_code == exit_code


def test_ui_launches_for_ordinary_pytest_failure_and_keeps_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(supervisor, "_validate_port", lambda _port: None)
    monkeypatch.setattr(supervisor, "_ui_prerequisite_error", lambda _ui_dir: None)
    monkeypatch.setattr(
        supervisor,
        "_prepare_test",
        lambda *_args: (Path(sys.executable), tmp_path / "results.sqlite"),
    )
    monkeypatch.setattr(
        supervisor, "list_stored_runs", lambda _db: supervisor.StoredRuns()
    )
    monkeypatch.setattr(supervisor, "_run_pytest_process", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(
        supervisor, "_run_ui_server", lambda _db, _port, code, _runs, _warnings: code
    )
    result = supervisor.run_test_with_runs(
        ui=True, port=8123, ui_dir=tmp_path, project_root=tmp_path
    )
    assert result.exit_code == 1


def test_ui_server_readiness_failure_is_cleaned_up(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    token = "Z" * 43

    class Child:
        process = SimpleNamespace(poll=lambda: None)
        lines: ClassVar = deque(
            ["API_KEY=top-secret", f"echoed token {token}", "safe startup failure"]
        )

        def alive(self) -> bool:
            return True

    child = Child()
    monkeypatch.setattr(supervisor.secrets, "token_urlsafe", lambda _size: token)
    stopped: list[object] = []
    monkeypatch.setattr(supervisor, "_ServerChild", lambda *_args: child)
    monkeypatch.setattr(supervisor, "_wait_ready", lambda *_args: False)
    monkeypatch.setattr(
        supervisor, "_terminate_process", lambda process: stopped.append(process)
    )
    assert supervisor._run_ui_server(Path("results.sqlite"), 8123, 1, (), ()) == 2
    assert stopped == [child.process]
    error = capsys.readouterr().err
    assert "readiness failed" in error
    assert "safe startup failure" in error
    assert "top-secret" not in error
    assert token not in error


def test_ui_server_environment_excludes_project_settings_and_credentials() -> None:
    source = {
        "PATH": "/bin",
        "HOME": "/tmp/home",
        "VIRTUAL_ENV": "/project/.venv",
        "PYTHONPATH": "/project/src",
        "M3_RUN_LIVE_OPENCODE": "1",
        "M3_LIVE_OPENCODE_MODEL": "opencode/model",
        "OPENCODE_API_KEY": "provider-secret",
    }

    assert supervisor._server_environment(source) == {
        "PATH": "/bin",
        "HOME": "/tmp/home",
    }


def test_server_command_never_starts_node_or_npm() -> None:
    command = supervisor._server_command(Path("results.sqlite"), 8123)
    assert all(Path(part).name not in {"node", "npm", "vite"} for part in command)
    assert command[:3] == [sys.executable, "-m", "m3_cli.web"]


def _run(run_id: str, second: int) -> supervisor.StoredRun:
    return supervisor.StoredRun(run_id, datetime.fromtimestamp(second, tz=timezone.utc))


def _use_fixture_ui_in_child(monkeypatch: pytest.MonkeyPatch, fixture_ui: Path) -> None:
    child_code = (
        "import sys; from pathlib import Path; import m3_cli.web as web; "
        f"web.ui_directory = lambda _value=None: Path({str(fixture_ui)!r}); "
        "raise SystemExit(web.main(sys.argv[1:]))"
    )
    monkeypatch.setattr(
        supervisor,
        "_server_command",
        lambda database, port: [
            sys.executable,
            "-c",
            child_code,
            "--database-path",
            str(database),
            "--port",
            str(port),
        ],
    )


def test_find_new_runs_excludes_existing_and_sorts_stably() -> None:
    before = supervisor.StoredRuns((_run("existing", 1),))
    after = supervisor.StoredRuns(
        (_run("z", 3), _run("b", 2), _run("a", 2), _run("existing", 4), _run("b", 2))
    )
    assert supervisor.find_new_runs(before, after) == (
        _run("a", 2),
        _run("b", 2),
        _run("z", 3),
    )


def test_list_stored_runs_uses_manifests_even_without_executions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Store:
        def __init__(self, _database: Path) -> None:
            self.calls = 0

        def list_test_runs(self) -> tuple[dict[str, str], ...]:
            self.calls += 1
            return (
                {"run_id": "pytest-run", "created_at": "2026-09-20T12:00:00Z"},
                {"run_id": "bad", "created_at": "not a timestamp"},
                {"run_id": "naive", "created_at": "2026-09-20T12:00:00"},
            )

        def close(self) -> None:
            pass

    store = Store(tmp_path / "runs.sqlite")
    monkeypatch.setattr(
        supervisor, "_execution_store_type", lambda: lambda _database: store
    )
    result = supervisor.list_stored_runs(tmp_path / "runs.sqlite")
    assert result.warning is None
    assert result.runs == (
        supervisor.StoredRun(
            "pytest-run", datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
        ),
    )
    assert store.calls == 1


def test_list_stored_runs_returns_safe_warning_for_unreadable_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret_path = tmp_path / "private-token.sqlite"
    monkeypatch.setattr(
        supervisor,
        "_execution_store_type",
        lambda: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    result = supervisor.list_stored_runs(secret_path)
    assert result.runs == ()
    assert result.warning == "could not read stored run history"
    assert str(secret_path) not in result.warning


def test_run_url_encodes_every_path_separator_character() -> None:
    assert supervisor.build_run_url("run id/with?unsafe#chars", 8123) == (
        "http://127.0.0.1:8123/reports/runs/run%20id%2Fwith%3Funsafe%23chars"
    )
    assert supervisor.build_run_url("run-1", 8123, "A" * 43).endswith(
        "run-1#m3_token=" + "A" * 43
    )


def test_cli_forwards_exact_args_after_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        supervisor,
        "resolve_project_python",
        lambda *_args, **_kwargs: Path(sys.executable),
    )
    monkeypatch.setattr(
        supervisor, "validate_project_python", lambda *_args, **_kwargs: "0.2.0a13"
    )

    def spawn(command: list[str], **_: object) -> _Process:
        seen["command"] = command
        return _Process(17)

    monkeypatch.setattr(supervisor.subprocess, "Popen", spawn)
    assert main(["test", "--", "-q", "--maxfail=1", "suite/test.py"]) == 17
    assert seen["command"][-3:] == ["-q", "--maxfail=1", "suite/test.py"]


def test_cli_forwards_project_root_to_pytest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        supervisor,
        "resolve_project_python",
        lambda *_args, **_kwargs: Path(sys.executable),
    )
    monkeypatch.setattr(
        supervisor, "validate_project_python", lambda *_args, **_kwargs: "0.2.0a13"
    )

    def spawn(command: list[str], **_: object) -> _Process:
        seen["command"] = command
        return _Process(0)

    monkeypatch.setattr(supervisor.subprocess, "Popen", spawn)
    assert main(["test", "--project-root", str(tmp_path), "--", "-q"]) == 0
    command = seen["command"]
    assert isinstance(command, list)
    index = command.index("--project-root")
    assert command[index + 1] == str(tmp_path.resolve())


def test_cli_module_help() -> None:
    result = supervisor.subprocess.run(
        [sys.executable, "-m", "m3_cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert M3_ASCII_ART in result.stdout


def test_cli_test_help_documents_scoped_credential_mapping() -> None:
    result = supervisor.subprocess.run(
        [sys.executable, "-m", "m3_cli", "test", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--credential-env [KIND:]TARGET=SOURCE" in result.stdout


def test_posix_termination_handlers_are_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "posix":
        pytest.skip("POSIX signal behavior")
    installed: list[tuple[int, object]] = []
    previous = object()
    monkeypatch.setattr(supervisor.signal, "getsignal", lambda _signum: previous)
    monkeypatch.setattr(
        supervisor.signal,
        "signal",
        lambda signum, handler: installed.append((int(signum), handler)),
    )
    with supervisor._termination_signal_handlers():
        assert len(installed) == 2
        assert all(callable(handler) for _, handler in installed[:2])
    assert installed[-2:] == [
        (int(supervisor.signal.SIGTERM), previous),
        (int(supervisor.signal.SIGHUP), previous),
    ]


def test_sigterm_child_cleanup_returns_signal_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = _Process(wait_error=supervisor._TerminationSignal(int(signal.SIGTERM)))
    terminated: list[object] = []
    monkeypatch.setattr(
        supervisor,
        "resolve_project_python",
        lambda *_args, **_kwargs: Path(sys.executable),
    )
    monkeypatch.setattr(
        supervisor, "validate_project_python", lambda *_args, **_kwargs: "0.2.0a13"
    )
    monkeypatch.setattr(
        supervisor.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    monkeypatch.setattr(
        supervisor, "_terminate_process", lambda child: terminated.append(child)
    )
    assert supervisor.run_test(project_root=tmp_path) == 128 + int(signal.SIGTERM)
    assert terminated == [process]


def test_keyboard_interrupt_cleans_child_and_returns_interrupt_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = _Process(wait_error=KeyboardInterrupt())
    terminated: list[object] = []
    monkeypatch.setattr(
        supervisor,
        "resolve_project_python",
        lambda *_args, **_kwargs: Path(sys.executable),
    )
    monkeypatch.setattr(
        supervisor, "validate_project_python", lambda *_args, **_kwargs: "0.2.0a13"
    )
    monkeypatch.setattr(
        supervisor.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    monkeypatch.setattr(
        supervisor, "_terminate_process", lambda child: terminated.append(child)
    )
    assert supervisor.run_test(project_root=tmp_path) == 130
    assert terminated == [process]


def test_resolution_prefers_explicit_then_virtualenv_then_conda_then_project_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit"
    explicit.touch()
    virtual = tmp_path / "virtual" / "bin" / "python"
    virtual.parent.mkdir(parents=True)
    virtual.touch()
    conda = tmp_path / "conda" / "bin" / "python"
    conda.parent.mkdir(parents=True)
    conda.touch()
    local = tmp_path / ".venv" / "bin" / "python"
    local.parent.mkdir(parents=True)
    local.touch()
    monkeypatch.setattr(
        supervisor.shutil,
        "which",
        lambda name, **_: "/path/python" if name == "python3" else None,
    )

    assert (
        supervisor.resolve_project_python(explicit, project_root=tmp_path)
        == explicit.resolve()
    )
    assert (
        supervisor.resolve_project_python(
            project_root=tmp_path,
            environment={
                "VIRTUAL_ENV": str(virtual.parent.parent),
                "CONDA_PREFIX": str(conda.parent.parent),
            },
        )
        == virtual.resolve()
    )
    virtual.unlink()
    assert (
        supervisor.resolve_project_python(
            project_root=tmp_path,
            environment={
                "VIRTUAL_ENV": str(virtual.parent.parent),
                "CONDA_PREFIX": str(conda.parent.parent),
            },
        )
        == conda.resolve()
    )
    conda.unlink()
    assert (
        supervisor.resolve_project_python(
            project_root=tmp_path,
            environment={
                "VIRTUAL_ENV": str(virtual.parent.parent),
                "CONDA_PREFIX": str(conda.parent.parent),
            },
        )
        == local.resolve()
    )


def test_resolution_uses_python3_then_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def which(name: str, **_: object) -> str | None:
        seen.append(name)
        return "/tool/python" if name == "python" else None

    monkeypatch.setattr(supervisor.shutil, "which", which)
    assert (
        supervisor.resolve_project_python(project_root=tmp_path, environment={})
        == Path("/tool/python").resolve()
    )
    assert seen == ["python3", "python"]


def test_resolution_uses_passed_path_not_ambient_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str | None]] = []

    def which(name: str, path: str | None = None) -> str | None:
        seen.append((name, path))
        return "/passed/python" if path == "/passed/bin" else None

    monkeypatch.setattr(supervisor.shutil, "which", which)
    assert (
        supervisor.resolve_project_python(
            project_root=tmp_path, environment={"PATH": "/passed/bin"}
        )
        == Path("/passed/python").absolute()
    )
    assert seen == [("python3", "/passed/bin")]


def test_windows_candidate_layouts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Replace only this module's OS view. Mutating os.name changes pathlib's
    # global platform selection and can crash pytest itself on POSIX.
    monkeypatch.setattr(supervisor, "os", SimpleNamespace(name="nt"))
    virtual = tmp_path / "virtual" / "Scripts" / "python.exe"
    virtual.parent.mkdir(parents=True)
    virtual.touch()
    conda = tmp_path / "conda" / "python.exe"
    conda.parent.mkdir(parents=True)
    conda.touch()
    local = tmp_path / ".venv" / "Scripts" / "python.exe"
    local.parent.mkdir(parents=True)
    local.touch()
    assert (
        supervisor.resolve_project_python(
            project_root=tmp_path, environment={"VIRTUAL_ENV": str(virtual.parents[1])}
        )
        == virtual.absolute()
    )
    virtual.unlink()
    assert (
        supervisor.resolve_project_python(
            project_root=tmp_path, environment={"CONDA_PREFIX": str(conda.parent)}
        )
        == conda.absolute()
    )
    conda.unlink()
    assert (
        supervisor.resolve_project_python(project_root=tmp_path, environment={})
        == local.absolute()
    )


def test_rejected_candidate_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate = tmp_path / "python"
    candidate.touch()
    monkeypatch.setattr(
        supervisor,
        "validate_project_python",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            supervisor.ProjectPythonError("missing required M3 packages")
        ),
    )
    assert supervisor.run_test(python=candidate, project_root=tmp_path) == 2


def test_validation_requires_exact_sdk_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = type(
        "Result",
        (),
        {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "checks": {
                        "pytest": True,
                        "m3": True,
                        "m3.pytest_plugin": True,
                        "openai": True,
                        "SQLiteExecutionStore": True,
                    },
                    "version": "0.2.0a12",
                }
            ),
        },
    )()
    monkeypatch.setattr(supervisor.subprocess, "run", lambda *_args, **_kwargs: result)
    with pytest.raises(supervisor.ProjectPythonError, match="does not match"):
        supervisor.validate_project_python(
            Path(sys.executable), cli_sdk_version="0.2.0a13", project_root=tmp_path
        )


def test_validation_requires_judge_dependency(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = type(
        "Result",
        (),
        {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "checks": {
                        "pytest": True,
                        "m3": True,
                        "m3.pytest_plugin": True,
                        "SQLiteExecutionStore": True,
                    },
                    "version": "0.2.0a13",
                }
            ),
        },
    )()
    monkeypatch.setattr(supervisor.subprocess, "run", lambda *_args, **_kwargs: result)
    with pytest.raises(supervisor.ProjectPythonError, match="openai; run m3 setup"):
        supervisor.validate_project_python(
            Path(sys.executable), cli_sdk_version="0.2.0a13", project_root=tmp_path
        )


def test_command_uses_selected_python_and_absolute_database(tmp_path: Path) -> None:
    command = supervisor.pytest_command(
        Path("/project/.venv/bin/python"),
        (tmp_path / "results.sqlite").resolve(),
        ["-q", "tests"],
    )
    assert command == [
        "/project/.venv/bin/python",
        "-m",
        "pytest",
        "-p",
        "m3.pytest_plugin",
        "--results-db",
        str((tmp_path / "results.sqlite").resolve()),
        "-q",
        "tests",
    ]


def test_command_forwards_execution_timeout(tmp_path: Path) -> None:
    command = supervisor.pytest_command(
        Path("/project/.venv/bin/python"),
        (tmp_path / "results.sqlite").resolve(),
        ["-q", "tests"],
        execution_timeout=12.5,
    )
    assert "--execution-timeout" in command
    index = command.index("--execution-timeout")
    assert command[index + 1] == "12.5"


def test_command_forwards_judge_request_cap(tmp_path: Path) -> None:
    command = supervisor.pytest_command(
        Path("/project/.venv/bin/python"),
        (tmp_path / "results.sqlite").resolve(),
        ["-q", "tests"],
        judge_max_requests=7,
    )
    index = command.index("--judge-max-requests")
    assert command[index + 1] == "7"


def test_command_pins_project_root_when_supervisor_runs_pytest(tmp_path: Path) -> None:
    command = supervisor.pytest_command(
        Path("/project/.venv/bin/python"),
        (tmp_path / "results.sqlite").resolve(),
        ["-q", "tests"],
        project_root=tmp_path,
    )
    assert command[-6:] == [
        "--project-root",
        str(tmp_path),
        "--rootdir",
        str(tmp_path),
        "-q",
        "tests",
    ]


def test_default_database_parent_is_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = type(
        "Process", (), {"pid": 1, "wait": lambda self, **_: 0, "poll": lambda self: 0}
    )()
    monkeypatch.setattr(
        supervisor,
        "resolve_project_python",
        lambda *_args, **_kwargs: Path(sys.executable),
    )
    monkeypatch.setattr(
        supervisor, "validate_project_python", lambda *_args, **_kwargs: "0.2.0a13"
    )
    monkeypatch.setattr(
        supervisor.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    assert supervisor.run_test(project_root=tmp_path) == 0
    assert (tmp_path / ".m3").is_dir()


def test_real_subprocess_runs_plugin_and_keeps_pytest_summary(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    test_file = tmp_path / "test_one.py"
    test_file.write_text(
        "from m3 import MCPTestKit\n"
        "def test_one():\n"
        "    with MCPTestKit() as kit:\n"
        "        assert kit.store is not None\n",
        encoding="utf-8",
    )
    database = tmp_path / "nested" / "results.sqlite"
    assert (
        supervisor.run_test(
            python=sys.executable,
            project_root=tmp_path,
            database=database,
            pytest_args=["-q", str(test_file)],
        )
        == 0
    )
    assert database.is_file()
    assert "1 passed" in capfd.readouterr().out


def test_two_runs_keep_project_root_and_feedback_location_stable(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    test_file = tmp_path / "test_one.py"
    test_file.write_text("def test_one():\n    pass\n", encoding="utf-8")
    database = tmp_path / "nested" / "history.sqlite"
    assert (
        supervisor.run_test(
            python=sys.executable,
            project_root=tmp_path,
            database=database,
            pytest_args=["-q", str(test_file)],
        )
        == 0
    )
    from m3.storage import SQLiteExecutionStore

    history = SQLiteExecutionStore(database)
    run_id = str(history.list_test_runs()[0]["run_id"])
    history.close()
    assert (tmp_path / ".m3" / "reports" / run_id / "feedback.json").is_file()
    assert (
        supervisor.run_test(
            python=sys.executable,
            project_root=tmp_path,
            database=database,
            baseline=run_id,
            pytest_args=["-q", str(test_file)],
        )
        == 0
    )
    history = SQLiteExecutionStore(database)
    run_ids = [str(item["run_id"]) for item in history.list_test_runs()]
    history.close()
    second_run = next(item for item in run_ids if item != run_id)
    report = tmp_path / ".m3" / "reports" / second_run / "feedback.json"
    assert report.is_file()
    assert second_run != run_id


def test_run_result_keeps_pytest_status_when_history_listing_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = _Process(9)
    calls = 0

    def scan(_database: Path) -> supervisor.StoredRuns:
        nonlocal calls
        calls += 1
        return supervisor.StoredRuns(warning="could not read stored run history")

    monkeypatch.setattr(supervisor, "list_stored_runs", scan)
    monkeypatch.setattr(
        supervisor,
        "resolve_project_python",
        lambda *_args, **_kwargs: Path(sys.executable),
    )
    monkeypatch.setattr(
        supervisor, "validate_project_python", lambda *_args, **_kwargs: "0.2.0a13"
    )
    monkeypatch.setattr(
        supervisor.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    result = supervisor.run_test_with_runs(project_root=tmp_path)
    assert result.exit_code == 9
    assert result.new_runs == ()
    assert result.warnings == ("could not read stored run history",)
    assert calls == 2


def test_plain_test_does_not_scan_results_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = _Process(0)
    monkeypatch.setattr(
        supervisor,
        "list_stored_runs",
        lambda *_args: pytest.fail("plain test scanned history"),
    )
    monkeypatch.setattr(
        supervisor,
        "resolve_project_python",
        lambda *_args, **_kwargs: Path(sys.executable),
    )
    monkeypatch.setattr(
        supervisor, "validate_project_python", lambda *_args, **_kwargs: "0.2.0a13"
    )
    monkeypatch.setattr(
        supervisor.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    assert supervisor.run_test(project_root=tmp_path) == 0


def test_real_store_listing_uses_test_run_ids(tmp_path: Path) -> None:
    from m3.storage import SQLiteExecutionStore
    from m3.types import (
        ExecutionId,
        ExecutionOutcome,
        ExecutionState,
        ExecutionStatus,
    )

    database = tmp_path / "real.sqlite"
    store = SQLiteExecutionStore(database)
    store.create(
        ExecutionState(
            execution_id=ExecutionId("visible"),
            lifecycle=ExecutionStatus.FINISHED,
            outcome=ExecutionOutcome.COMPLETED,
            finished_at=datetime.now(timezone.utc),
        )
    )
    assert supervisor.list_stored_runs(database).runs == ()
    store.save_test_run(
        "pytest-run",
        {"run_id": "pytest-run", "created_at": "2026-09-20T12:00:00Z"},
    )
    store.close()
    assert [run.run_id for run in supervisor.list_stored_runs(database).runs] == [
        "pytest-run"
    ]


@pytest.mark.parametrize("code", [0, 1, 5, 17])
def test_pytest_exit_code_is_forwarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int
) -> None:
    process = type(
        "Process",
        (),
        {"pid": 1, "wait": lambda self, **_: code, "poll": lambda self: 0},
    )()
    monkeypatch.setattr(
        supervisor,
        "resolve_project_python",
        lambda *_args, **_kwargs: Path(sys.executable),
    )
    monkeypatch.setattr(
        supervisor, "validate_project_python", lambda *_args, **_kwargs: "0.2.0a13"
    )
    monkeypatch.setattr(
        supervisor.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    assert supervisor.run_test(project_root=tmp_path) == code
