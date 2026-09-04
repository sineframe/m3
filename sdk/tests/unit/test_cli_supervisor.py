from __future__ import annotations

from pathlib import Path
import socket
import os
import sys
from types import SimpleNamespace

import pytest

import mcp_pal.cli as cli
import mcp_pal.cli.supervisor as orchestrator


class _Process:
    def __init__(self, code: int = 0, alive: bool = False) -> None:
        self.returncode = None if alive else code
        self.pid = 12345
        self.wait_calls = 0
        self.terminated = False

    def wait(self, **_: object) -> int:
        self.wait_calls += 1
        return 0 if self.returncode is None else self.returncode

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.terminated = True
        self.returncode = 0


def test_cli_forwards_exact_args_after_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake(**kwargs: object) -> int:
        seen.update(kwargs)
        return 17

    monkeypatch.setattr(orchestrator, "run_test", fake)
    assert cli.main(["test", "--ui", "--", "-q", "--maxfail=1", "suite/test.py"]) == 17
    assert seen["pytest_args"] == ["-q", "--maxfail=1", "suite/test.py"]


def test_cli_package_remains_executable_as_a_module() -> None:
    result = orchestrator.subprocess.run(
        [sys.executable, "-m", "mcp_pal.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "mcp-pal" in result.stdout


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        ({"pytest": False, "sqlalchemy": True}, "pytest is unavailable"),
        ({"pytest": True, "sqlalchemy": False}, "SQLite storage is unavailable"),
    ],
)
def test_missing_test_prerequisite_is_operational_error_before_spawn(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    available: dict[str, bool],
    expected: str,
) -> None:
    monkeypatch.setattr(orchestrator, "_module_available", lambda name: available[name])
    monkeypatch.setattr(
        orchestrator.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("pytest was spawned"),
    )
    assert orchestrator.run_test() == orchestrator.OPERATIONAL_ERROR
    assert expected in capsys.readouterr().err


def test_missing_viewer_prerequisite_fails_before_pytest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(orchestrator, "_test_prerequisite_error", lambda: None)
    monkeypatch.setattr(
        orchestrator,
        "_viewer_prerequisite_error",
        lambda _ui_dir: "API application is unavailable; run from the mcp-pal repository root",
    )
    monkeypatch.setattr(
        orchestrator.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("pytest was spawned"),
    )
    assert orchestrator.run_test(ui=True) == orchestrator.OPERATIONAL_ERROR
    assert "API application is unavailable" in capsys.readouterr().err


def test_run_test_uses_python_module_and_propagates_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    process = _Process(code=9)
    seen: list[list[str]] = []

    def spawn(command: list[str], **_: object) -> _Process:
        seen.append(command)
        return process

    monkeypatch.setattr(orchestrator.subprocess, "Popen", spawn)
    assert orchestrator.run_test(pytest_args=["-q", "test.py"], database=str(tmp_path / "x.sqlite")) == 9
    assert seen[0][:3] == [orchestrator.sys.executable, "-m", "pytest"]
    assert seen[0][-2:] == ["-q", "test.py"]


def test_api_command_uses_dedicated_read_only_viewer() -> None:
    command = orchestrator._api_command(8123)
    assert command[:4] == [
        orchestrator.sys.executable,
        "-m",
        "uvicorn",
        "mcp_pal_app.viewer:app",
    ]
    assert command[command.index("--port") + 1] == "8123"


def test_interrupted_pytest_code_does_not_launch_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _Process(code=2)
    monkeypatch.setattr(orchestrator.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(orchestrator, "_run_viewer", lambda *_args: pytest.fail("viewer launched"))
    assert orchestrator.run_test(ui=True) == 2


def test_ready_requires_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def __init__(self, status: int) -> None:
            self.status = status

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

    monkeypatch.setattr(orchestrator, "urlopen", lambda *_args, **_kwargs: Response(404))
    assert orchestrator._ready("http://127.0.0.1/history") is False
    monkeypatch.setattr(orchestrator, "urlopen", lambda *_args, **_kwargs: Response(200))
    assert orchestrator._ready("http://127.0.0.1/history") is True


def test_api_child_environment_strips_live_pytest_controls() -> None:
    environment = {
        "DATABASE_PATH": "/tmp/results.sqlite",
        "PYTHONPATH": "/tmp/sdk/src",
        "OPENCODE_API_KEY": "configured",
        "UNRELATED_TEST_VALUE": "private",
        "MCP_PAL_RUN_LIVE_OPENCODE": "1",
        "MCP_PAL_LIVE_OPENCODE_MODEL": "opencode/big-pickle",
        "MCP_PAL_ARTIFACT_POLICY": "failed",
    }
    child_environment = orchestrator._api_child_environment(environment)
    assert "MCP_PAL_RUN_LIVE_OPENCODE" not in child_environment
    assert "MCP_PAL_LIVE_OPENCODE_MODEL" not in child_environment
    assert child_environment["DATABASE_PATH"] == environment["DATABASE_PATH"]
    assert child_environment["PYTHONPATH"] == environment["PYTHONPATH"]
    assert "OPENCODE_API_KEY" not in child_environment
    assert "UNRELATED_TEST_VALUE" not in child_environment
    assert child_environment["MCP_PAL_ARTIFACT_POLICY"] == "failed"


def test_windows_termination_signals_group_then_kills_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        pid = 321

        def __init__(self) -> None:
            self.signals: list[object] = []
            self.waits = 0

        def send_signal(self, value: object) -> None:
            self.signals.append(value)

        def wait(self, **_: object) -> int:
            self.waits += 1
            if self.waits == 1:
                raise orchestrator.subprocess.TimeoutExpired("child", 3)
            return 0

    process = Process()
    calls: list[list[str]] = []
    monkeypatch.setattr(orchestrator.os, "name", "nt")
    monkeypatch.setattr(orchestrator.signal, "CTRL_BREAK_EVENT", 999, raising=False)
    monkeypatch.setattr(
        orchestrator.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(command),
    )
    orchestrator._terminate_process(process)
    assert process.signals == [999]
    assert calls == [["taskkill", "/PID", "321", "/T", "/F"]]


def test_posix_termination_handlers_are_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    installed: list[tuple[int, object]] = []
    previous = object()
    monkeypatch.setattr(orchestrator.signal, "getsignal", lambda _signum: previous)
    monkeypatch.setattr(
        orchestrator.signal,
        "signal",
        lambda signum, handler: installed.append((int(signum), handler)),
    )
    with orchestrator._termination_signal_handlers():
        assert len(installed) == 2
        assert all(callable(handler) for _, handler in installed)
    assert installed[-2:] == [
        (int(orchestrator.signal.SIGTERM), previous),
        (int(orchestrator.signal.SIGHUP), previous),
    ]


def test_termination_signal_reaps_pytest_and_returns_signal_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _Process(alive=True)

    def wait(**_: object) -> int:
        raise orchestrator._TerminationSignal(int(orchestrator.signal.SIGTERM))

    process.wait = wait  # type: ignore[method-assign]
    terminated: list[object] = []
    monkeypatch.setattr(orchestrator, "_test_prerequisite_error", lambda: None)
    monkeypatch.setattr(orchestrator.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(orchestrator, "_terminate_process", lambda child: terminated.append(child))
    assert orchestrator.run_test() == 128 + int(orchestrator.signal.SIGTERM)
    assert terminated == [process]


def test_port_preflight_rejects_invalid_duplicate_and_busy_ports() -> None:
    assert orchestrator._validate_ports(0, 4173)
    assert orchestrator._validate_ports(8000, 8000)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    try:
        assert orchestrator._validate_ports(listener.getsockname()[1], 4173)
    finally:
        listener.close()


def test_viewer_startup_failure_reaps_started_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    child = SimpleNamespace(lines=["diagnostic"], alive=lambda: False, process=SimpleNamespace(pid=1))
    terminated: list[object] = []
    monkeypatch.setattr(orchestrator, "_validate_ports", lambda *_: None)
    monkeypatch.setattr(orchestrator, "_Child", lambda *_args, **_kwargs: child)
    monkeypatch.setattr(orchestrator, "_wait_ready", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(orchestrator, "_terminate", lambda selected: terminated.append(selected))
    assert orchestrator._run_viewer((tmp_path / "x.sqlite").resolve(), 1, 8000, 4173, str(tmp_path)) == 2
    assert terminated == [None, child]


def test_viewer_ctrl_c_returns_saved_pytest_code_and_reaps_children(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    children: list[object] = []

    class Child:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.lines: list[str] = []
            self.process = SimpleNamespace(pid=len(children) + 1)
            children.append(self)

        def alive(self) -> bool:
            return True

    terminated: list[object] = []
    monkeypatch.setattr(orchestrator, "_validate_ports", lambda *_: None)
    monkeypatch.setattr(orchestrator, "_Child", Child)
    monkeypatch.setattr(orchestrator, "_wait_ready", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(orchestrator, "_terminate", lambda selected: terminated.append(selected))
    monkeypatch.setattr(orchestrator.time, "sleep", lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert orchestrator._run_viewer((tmp_path / "x.sqlite").resolve(), 7, 8000, 4173, str(tmp_path)) == 7
    assert set(terminated) == set(children)


def test_native_pytest_setup_and_teardown_errors_remain_in_summary(tmp_path: Path) -> None:
    test_file = tmp_path / "test_failures.py"
    test_file.write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "def setup_failure():\n    raise RuntimeError('setup')\n"
        "@pytest.fixture\n"
        "def teardown_failure():\n    yield\n    raise RuntimeError('teardown')\n"
        "def test_setup(setup_failure): pass\n"
        "def test_teardown(teardown_failure): pass\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = orchestrator.subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "mcp_pal.pytest_plugin", "--mcp-pal-results-db", str((tmp_path / "results.sqlite").resolve()), str(test_file)],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "errors" in result.stdout


def test_progress_counts_setup_and_teardown_without_double_completion() -> None:
    from mcp_pal.pytest_plugin import _Progress

    reporter = SimpleNamespace(isatty=True, rewrite=lambda *_args, **_kwargs: None, write_line=lambda *_args: None)
    config = SimpleNamespace(option=SimpleNamespace(verbose=0, numprocesses=0), pluginmanager=SimpleNamespace(getplugin=lambda _: reporter))
    progress = _Progress(config)
    progress.reporter = reporter
    progress.enabled = True
    progress.pytest_collection_finish(SimpleNamespace(items=[1, 2, 3]))
    progress.pytest_runtest_logreport(SimpleNamespace(nodeid="skip", when="setup", outcome="skipped"))
    progress.pytest_runtest_logreport(SimpleNamespace(nodeid="fail", when="setup", outcome="failed"))
    progress.pytest_runtest_logreport(SimpleNamespace(nodeid="ok", when="setup", outcome="passed"))
    progress.pytest_runtest_logreport(SimpleNamespace(nodeid="ok", when="call", outcome="passed"))
    progress.pytest_runtest_logreport(SimpleNamespace(nodeid="ok", when="teardown", outcome="failed"))
    assert (progress.completed, progress.passed, progress.failed, progress.skipped) == (3, 0, 2, 1)


def test_progress_is_disabled_for_non_tty() -> None:
    from mcp_pal.pytest_plugin import _Progress

    reporter = SimpleNamespace(isatty=False)
    config = SimpleNamespace(option=SimpleNamespace(verbose=0, numprocesses=0), pluginmanager=SimpleNamespace(getplugin=lambda _: reporter))
    progress = _Progress(config)
    progress.reporter = reporter
    progress.enabled = progress.enabled and progress._is_tty()
    assert progress.enabled is False


def test_progress_restores_exact_native_reporter_mode() -> None:
    from mcp_pal.pytest_plugin import _Progress

    reporter = SimpleNamespace(isatty=True, _show_progress_info="count")
    config = SimpleNamespace(
        option=SimpleNamespace(verbose=0, numprocesses=0),
        pluginmanager=SimpleNamespace(getplugin=lambda _: reporter),
    )
    progress = _Progress(config)
    progress.reporter = reporter

    progress.disable_native_progress()
    assert reporter._show_progress_info is False
    progress.restore_native_progress()
    assert reporter._show_progress_info == "count"
