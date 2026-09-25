from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "live_ui_gate.py"
_SPEC = importlib.util.spec_from_file_location("live_ui_gate", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_GATE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_GATE)

_LOOP_SPEC = importlib.util.spec_from_file_location(
    "live_agent_loop",
    Path(__file__).parents[2] / "sdk" / "examples" / "live_agent_loop.py",
)
assert _LOOP_SPEC is not None and _LOOP_SPEC.loader is not None
_LOOP = importlib.util.module_from_spec(_LOOP_SPEC)
_LOOP_SPEC.loader.exec_module(_LOOP)


def _report() -> dict[str, Any]:
    return {
        "report": {
            "snapshot": {
                "execution_id": "run-1",
                "lifecycle": "finished",
                "outcome": "completed",
            }
        },
        "spec": {"harness": {"kind": "opencode", "model": "opencode/big-pickle"}},
        "trace": {
            "runtime": {
                "kind": "opencode",
                "model_id": {"state": "observed", "value": "opencode/big-pickle"},
            },
            "timeline": [
                {
                    "kind": "tool_call",
                    "tool": {"state": "observed", "value": "shipping_quote"},
                    "arguments": {
                        "state": "observed",
                        "value": {"weight_kg": 2, "zone": "local"},
                    },
                    "result": {
                        "state": "observed",
                        "value": {
                            "structured_content": {
                                "state": "observed",
                                "value": {"currency": "USD", "amount": 12},
                            }
                        },
                    },
                    "tool_status": "success",
                    "wire": {"state": "observed", "value": {"request_id": "wire-1"}},
                }
            ],
        },
    }


def test_parse_ui_links_preserves_the_complete_encoded_run_id() -> None:
    output = "Run: http://127.0.0.1:8123/reports/runs/run%20id%2Fpart"
    direct, run_id = _GATE.parse_ui_links(output, "http://127.0.0.1:8123")
    assert direct[0].endswith("run%20id%2Fpart")
    assert run_id[0] == "run id/part"


def test_execution_report_url_quotes_run_id_path_syntax() -> None:
    assert _GATE.execution_report_url("http://127.0.0.1:8123", "run id/part") == (
        "http://127.0.0.1:8123/api/v2/executions/run%20id%2Fpart/report"
    )


@pytest.mark.parametrize(
    "output, message",
    [
        (
            "Run: http://127.0.0.1:8124/reports/runs/run-1",
            "selected origin",
        ),
        (
            "Run: http://127.0.0.1:8123/playground/run/run-1",
            "reports run route",
        ),
        (
            "Run: http://127.0.0.1:8123/reports/runs/run-1?x=1",
            "selected origin",
        ),
    ],
)
def test_parse_ui_links_rejects_wrong_origin_or_query(
    output: str, message: str
) -> None:
    with pytest.raises(_GATE.GateFailure, match=message):
        _GATE.parse_ui_links(output, "http://127.0.0.1:8123")


def test_clean_environment_removes_credentials_and_environment_links() -> None:
    source = {
        "PATH": "/bin",
        "OPENCODE_API_KEY": "secret",
        "SERVICE_TOKEN": "token",
        "VIRTUAL_ENV": "/project/.venv",
        "PYTHONPATH": "/project/src",
        "SAFE_VALUE": "kept",
    }
    assert _GATE.clean_environment(source) == {"PATH": "/bin", "SAFE_VALUE": "kept"}


def test_redact_bounds_and_hides_secret_like_values() -> None:
    secret = "secret-value"
    output = "OPENAI_API_KEY=" + secret + " token: another-secret " + ("x" * 10_000)
    redacted = _GATE.safe_diagnostics(output, {"OPENAI_API_KEY": secret})
    assert secret not in redacted
    assert "another-secret" not in redacted
    assert len(redacted) <= _GATE.MAX_DIAGNOSTICS


def test_browser_environment_does_not_receive_provider_credential() -> None:
    browser = _GATE.browser_environment(
        {"PATH": "/bin", "OPENCODE_API_KEY": "must-not-leak"},
        "http://127.0.0.1:8123",
        "run id/1",
    )
    assert "OPENCODE_API_KEY" not in browser
    assert browser["MCP_PAL_LIVE_UI_BASE_URL"] == "http://127.0.0.1:8123"
    assert browser["MCP_PAL_LIVE_EXECUTION_ID"] == "run id/1"
    assert "MCP_PAL_LIVE_MODEL" not in browser
    assert "MCP_PAL_LIVE_HARNESS" not in browser


def test_assert_report_uses_selected_harness_for_runtime_kind() -> None:
    report = _report()
    report["spec"]["harness"] = {"kind": "codex", "model": "gpt-5.6-sol"}
    report["trace"]["runtime"] = {
        "kind": "codex",
        "model_id": {"state": "observed", "value": "gpt-5.6-sol"},
    }
    _GATE.assert_report(report, "gpt-5.6-sol", "run-1", "codex")


def test_provider_secret_values_uses_only_selected_provider_keys() -> None:
    values = _GATE.provider_secret_values(
        ("opencode", "codex"),
        {"OPENCODE_API_KEY": "ambient-open", "OTHER_SECRET": "ignored"},
        {"OPENAI_API_KEY": "file-codex", "OTHER_TOKEN": "ignored"},
    )
    assert values == ("ambient-open", "file-codex")


def test_normal_python_loop_model_argument_is_explicit() -> None:
    assert (
        _LOOP.selected_model(["--model", "opencode/test-model"])
        == "opencode/test-model"
    )
    assert (
        _LOOP._options(
            ["--model", "opencode/test-model", "--execution-timeout", "300"]
        ).execution_timeout
        == 300
    )


@pytest.mark.parametrize("value", ["0", "-1", "inf", "nan"])
def test_normal_python_loop_rejects_invalid_execution_timeout(value: str) -> None:
    with pytest.raises(SystemExit):
        _LOOP._options(["--execution-timeout", value])


def test_live_loop_diagnostics_use_only_event_identity_fields() -> None:
    event = SimpleNamespace(
        sequence=7,
        kind=SimpleNamespace(value="diagnostic"),
        lifecycle_phase=SimpleNamespace(value="turn"),
        payload={"prompt": "must-not-print", "token": "secret"},
    )
    assert _LOOP.event_progress(event) == (
        "event sequence=7 kind=diagnostic phase=turn"
    )
    assert "must-not-print" not in _LOOP.event_progress(event)
    assert _LOOP.diagnostic_stage([event], "running_turn") == "turn/provider response"


def test_live_loop_terminal_summary_is_safe_and_includes_diagnostics() -> None:
    diagnostic = SimpleNamespace(
        code="operation_timeout",
        stage="waiting_for_harness_response",
        operation="harness.response",
        elapsed_seconds=1.2,
        timeout_seconds=1.0,
    )
    trace = SimpleNamespace(
        outcome=SimpleNamespace(value="timed_out"),
        completeness="partial",
        diagnostics=(diagnostic,),
        timeline=(object(),),
    )
    lines = _LOOP.trace_summary(trace, event_count=4)
    assert lines[0] == "final trace outcome=timed_out completeness=partial events=4"
    assert "stage=waiting_for_harness_response" in lines[1]
    assert all("prompt" not in line and "token" not in line for line in lines)


def test_live_loop_timeout_cancels_and_reports_safe_final_trace() -> None:
    event = SimpleNamespace(
        sequence=1,
        kind=SimpleNamespace(value="turn.state_changed"),
        lifecycle_phase=SimpleNamespace(value="turn"),
        payload={"prompt": "private", "secret": "do-not-print"},
    )
    trace = SimpleNamespace(
        outcome=SimpleNamespace(value="timed_out"), completeness="partial"
    )

    class Handle:
        def __init__(self) -> None:
            self.cancelled = False
            self.waits = 0

        def events(self):
            yield event

        def result(self, timeout=None):
            del timeout
            self.waits += 1
            if self.waits == 1:
                raise TimeoutError("wait only")
            return SimpleNamespace(trace_view=trace)

        def snapshot(self):
            return SimpleNamespace(lifecycle=SimpleNamespace(value="running_turn"))

        def cancel(self):
            self.cancelled = True

    handle = Handle()
    agent = SimpleNamespace(submit=lambda *args, **kwargs: handle)
    output: list[str] = []
    with pytest.raises(TimeoutError):
        _LOOP.run_with_diagnostics(
            agent, "private prompt", server=object(), emit=output.append
        )
    assert handle.cancelled
    assert any("stage=turn/provider response" in line for line in output)
    assert any("final trace outcome=timed_out" in line for line in output)
    assert all("private" not in line and "do-not-print" not in line for line in output)


def test_cli_command_runs_only_the_existing_live_target() -> None:
    command = _GATE.cli_test_command(
        Path("/tmp/m3"),
        Path("/tmp/project/.venv/bin/python"),
        Path("/tmp/project/runs.sqlite"),
        8123,
        Path("/tmp/project/.env"),
    )
    assert command[-2:] == ["-q", _GATE.TARGET]
    assert command[:2] == ["/tmp/m3", "test"]
    assert "--api-port" not in command
    assert "--ui-port" not in command
    assert "--ui-dir" not in command
    assert command[command.index("--execution-timeout") + 1] == "300.0"


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_live_gate_rejects_invalid_process_timeout(value: float) -> None:
    with pytest.raises(_GATE.GateFailure, match="process-timeout"):
        _GATE.check(process_timeout=value)


def test_live_gate_requires_process_deadline_above_execution_deadline() -> None:
    with pytest.raises(_GATE.GateFailure, match="must exceed"):
        _GATE.check(process_timeout=300, execution_timeout=300)


def test_cli_command_can_select_only_opencode() -> None:
    command = _GATE.cli_test_command(
        Path("/tmp/m3"),
        Path("/tmp/python"),
        Path("/tmp/runs.sqlite"),
        8123,
        Path("/tmp/project/.env"),
        providers=("opencode",),
    )
    assert command.count("--harness") == 1
    assert "opencode=opencode/big-pickle" in command
    assert not any("codex=" in value for value in command)


def test_cli_command_uses_selected_models_for_both_providers() -> None:
    command = _GATE.cli_test_command(
        Path("/tmp/m3"),
        Path("/tmp/python"),
        Path("/tmp/runs.sqlite"),
        8123,
        Path("/tmp/project/.env"),
        opencode_model="opencode/cheap",
        codex_model="gpt-current",
    )
    assert "opencode=opencode/cheap" in command
    assert "codex=gpt-current" in command


def test_cli_command_passes_selected_execution_timeout() -> None:
    command = _GATE.cli_test_command(
        Path("/tmp/m3"),
        Path("/tmp/python"),
        Path("/tmp/runs.sqlite"),
        8123,
        Path("/tmp/project/.env"),
        execution_timeout=420,
    )
    assert command[command.index("--execution-timeout") + 1] == "420"


def test_cli_command_rejects_unknown_live_provider() -> None:
    with pytest.raises(ValueError, match="unsupported live provider"):
        _GATE.cli_test_command(
            Path("/tmp/m3"),
            Path("/tmp/python"),
            Path("/tmp/runs.sqlite"),
            8123,
            Path("/tmp/project/.env"),
            providers=("unknown",),
        )


def test_parse_ui_links_accepts_multiple_distinct_report_runs() -> None:
    output = "\n".join(
        (
            "Run: http://127.0.0.1:8123/reports/runs/opencode-run",
            "Run: http://127.0.0.1:8123/reports/runs/codex-run",
        )
    )
    _direct, run_ids = _GATE.parse_ui_links(output, "http://127.0.0.1:8123")
    assert run_ids == ("opencode-run", "codex-run")


def test_copy_live_target_includes_normal_python_loop(tmp_path: Path) -> None:
    _GATE._copy_live_target(tmp_path)
    assert (tmp_path / "sdk" / "examples" / "live_agent_loop.py").is_file()
    assert (
        tmp_path
        / "sdk"
        / "examples"
        / "nondeterministic"
        / "test_live_agent_selection.py"
    ).is_file()


def test_ui_probe_uses_activity_tool_filter(tmp_path: Path) -> None:
    probe = _GATE._write_ui_probe(tmp_path)
    content = probe.read_text(encoding="utf-8")
    assert "Filter activity" in content
    assert ".activity-row--tool_call" in content
    assert "shipping_quote" in content


def test_assert_report_validates_shipping_quote_contract() -> None:
    _GATE.assert_report(_report(), "opencode/big-pickle", "run-1")


def test_assert_aggregate_requires_each_selected_provider_configuration() -> None:
    payload = {
        "aggregate": {
            "groups": [
                {
                    "key": {"metadata.harness_config": "opencode:opencode/big-pickle"},
                    "values": {},
                },
                {"key": {"metadata.harness_config": "codex:gpt-current"}, "values": {}},
            ]
        }
    }
    _GATE.assert_aggregate(
        payload,
        ("opencode", "codex"),
        {"opencode": "opencode/big-pickle", "codex": "gpt-current"},
    )
    with pytest.raises(_GATE.GateFailure, match="aggregate response is malformed"):
        _GATE.assert_aggregate(
            {"aggregate": {"groups": [payload["aggregate"]["groups"][0]]}},
            ("opencode", "codex"),
            {"opencode": "opencode/big-pickle", "codex": "gpt-current"},
        )


def _persistence_fixture(path: Path, *, include_events: bool = True) -> None:
    from datetime import datetime, timezone

    from m3.storage import SQLiteExecutionStore
    from m3.types import (
        DirectSpec,
        ExecutionId,
        ExecutionOutcome,
        ExecutionState,
        ExecutionStatus,
        Ping,
        ServerBinding,
        StdioServer,
    )

    store = SQLiteExecutionStore(path)
    execution_id = ExecutionId("execution-1")
    spec = DirectSpec(
        servers=(ServerBinding(server=StdioServer(name="server", command="echo")),),
        operation=Ping(server="server"),
    )
    state = ExecutionState(
        execution_id=execution_id,
        run_id="pytest-1",
        lifecycle=ExecutionStatus.FINISHED,
        outcome=ExecutionOutcome.COMPLETED,
        finished_at=datetime.now(timezone.utc),
    )
    store.create(state, specification=spec.model_dump(mode="json"))
    store.save_test_run("pytest-1", {"run_id": "pytest-1", "status": "finished"})
    store.save_test_result(
        "pytest-1",
        "attempt-1",
        {
            "node_id": "test",
            "suite_name": "live-ui-gate",
            "execution_ids": ["execution-1"],
        },
    )
    store.close()
    connection = sqlite3.connect(path)
    now = datetime.now(timezone.utc).isoformat()
    connection.execute(
        "INSERT INTO v2_sessions(id,execution_id,state,created_at,closed_at) VALUES(?,?,?,?,?)",
        ("session-1", "execution-1", "finished", now, now),
    )
    connection.execute(
        "INSERT INTO v2_turns(id,session_id,number,snapshot_json,result_json,created_at) VALUES(?,?,?,?,?,?)",
        (
            "turn-1",
            "session-1",
            1,
            '{"lifecycle":"finished","outcome":"completed"}',
            "{}",
            now,
        ),
    )
    if include_events:
        connection.execute(
            "INSERT INTO v2_events(id,execution_id,sequence,event_json,timestamp) VALUES(?,?,?,?,?)",
            ("event-1", "execution-1", 0, '{"kind":"execution.created"}', now),
        )
    connection.commit()
    connection.close()


def test_assert_sqlite_persistence_checks_the_live_execution_graph(
    tmp_path: Path,
) -> None:
    database = tmp_path / "executions.sqlite"
    _persistence_fixture(database)

    _GATE.assert_sqlite_persistence(database, "execution-1")
    assert _GATE._pytest_run_id(database, "execution-1") == "pytest-1"


def test_assert_sqlite_persistence_rejects_incomplete_execution_graph(
    tmp_path: Path,
) -> None:
    database = tmp_path / "executions.sqlite"
    _persistence_fixture(database, include_events=False)

    with pytest.raises(_GATE.GateFailure, match="v2_events"):
        _GATE.assert_sqlite_persistence(database, "execution-1")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda report: report["trace"]["timeline"][0]["arguments"].update(
                state="redacted"
            ),
            "shipping_quote",
        ),
        (
            lambda report: report["trace"]["timeline"][0]["arguments"]["value"].update(
                zone="remote"
            ),
            "shipping_quote",
        ),
        (
            lambda report: report["trace"]["timeline"][0].update(
                tool_status="tool_error"
            ),
            "successful",
        ),
        (
            lambda report: report["trace"]["timeline"][0]["result"]["value"][
                "structured_content"
            ]["value"].update(currency="EUR"),
            "USD",
        ),
        (
            lambda report: report["trace"]["timeline"][0]["wire"].update(
                state="missing"
            ),
            "wire",
        ),
        (
            lambda report: report["trace"]["runtime"].update(kind="direct"),
            "runtime",
        ),
    ],
)
def test_assert_report_rejects_invalid_live_evidence(
    mutation: Callable[[dict[str, Any]], object], message: str
) -> None:
    report = _report()
    mutation(report)
    with pytest.raises(_GATE.GateFailure, match=message):
        _GATE.assert_report(report, "opencode/big-pickle", "run-1")


def test_assert_report_rejects_wrong_model_id_and_second_tool_call() -> None:
    with pytest.raises(_GATE.GateFailure, match="model"):
        _GATE.assert_report(_report(), "opencode/not-the-model", "run-1")

    report = _report()
    report["trace"]["timeline"].append({"kind": "tool_call"})
    with pytest.raises(_GATE.GateFailure, match="exactly one"):
        _GATE.assert_report(report, "opencode/big-pickle", "run-1")


def test_assert_report_rejects_run_id_mismatch() -> None:
    with pytest.raises(_GATE.GateFailure, match="does not match"):
        _GATE.assert_report(_report(), "opencode/big-pickle", "another-run")


def test_playwright_timeout_is_a_safe_gate_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def timeout(*_args: object, **_kwargs: object) -> object:
        raise _GATE.subprocess.TimeoutExpired("playwright", _GATE.PLAYWRIGHT_TIMEOUT)

    monkeypatch.setattr(_GATE.subprocess, "run", timeout)
    with pytest.raises(_GATE.GateFailure, match="could not complete"):
        _GATE.run_playwright(tmp_path / "playwright", tmp_path, {"PATH": "/bin"})


def test_run_streaming_emits_and_retains_redacted_output(tmp_path: Path) -> None:
    output_path = tmp_path / "command.log"
    result = _GATE._run_streaming(
        [sys.executable, "-c", "print('live progress')"],
        cwd=tmp_path,
        env={"PATH": "/bin"},
        timeout=5,
        label="fixture",
        output_path=output_path,
    )
    assert result.stdout.strip() == "live progress"
    assert output_path.read_text(encoding="utf-8").strip() == "live progress"


def test_run_streaming_bounds_a_stalled_command(tmp_path: Path) -> None:
    output_path = tmp_path / "timeout.log"
    with pytest.raises(
        _GATE.GateFailure, match=r"(?s)fixture timed out after.*before-timeout"
    ):
        _GATE._run_streaming(
            [
                sys.executable,
                "-c",
                "import time; print('before-timeout', flush=True); time.sleep(2)",
            ],
            cwd=tmp_path,
            env={"PATH": "/bin"},
            timeout=0.05,
            label="fixture",
            output_path=output_path,
        )
    assert output_path.read_text(encoding="utf-8").strip() == "before-timeout"


@pytest.mark.skipif(
    os.name != "posix", reason="process-group assertion is POSIX-specific"
)
def test_run_streaming_terminates_process_group_on_timeout(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child.pid"
    code = (
        "import pathlib, subprocess, sys, time; "
        "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
        "time.sleep(30)"
    )
    with pytest.raises(_GATE.GateFailure, match="fixture timed out after"):
        _GATE._run_streaming(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            env={"PATH": "/bin"},
            timeout=0.2,
            label="fixture",
        )
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    for _ in range(20):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"timed-out child process {child_pid} survived")


@pytest.mark.skipif(
    os.name != "posix", reason="detached process assertion is POSIX-specific"
)
def test_run_streaming_terminates_detached_descendant_on_timeout(
    tmp_path: Path,
) -> None:
    heartbeat = tmp_path / "detached-heartbeat"
    child_code = "\n".join(
        (
            "import pathlib, time",
            f"heartbeat = pathlib.Path({str(heartbeat)!r})",
            "counter = 0",
            "while True:",
            "    counter += 1",
            "    heartbeat.write_text(str(counter))",
            "    time.sleep(0.03)",
        )
    )
    code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}], start_new_session=True); "
        "time.sleep(30)"
    )
    with pytest.raises(_GATE.GateFailure, match="fixture timed out after"):
        _GATE._run_streaming(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            env={"PATH": "/bin"},
            timeout=0.2,
            label="fixture",
        )
    first = heartbeat.read_text(encoding="utf-8")
    time.sleep(0.2)
    assert heartbeat.read_text(encoding="utf-8") == first


@pytest.mark.skipif(
    os.name != "posix", reason="detached process assertion is POSIX-specific"
)
def test_run_streaming_kills_detached_descendant_ignoring_sigterm(
    tmp_path: Path,
) -> None:
    heartbeat = tmp_path / "ignoring-heartbeat"
    child_code = "\n".join(
        (
            "import pathlib, signal, time",
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
            f"heartbeat = pathlib.Path({str(heartbeat)!r})",
            "counter = 0",
            "while True:",
            "    counter += 1",
            "    heartbeat.write_text(str(counter))",
            "    time.sleep(0.03)",
        )
    )
    code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}], start_new_session=True); "
        "time.sleep(30)"
    )
    with pytest.raises(_GATE.GateFailure, match="fixture timed out after"):
        _GATE._run_streaming(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            env={"PATH": "/bin"},
            timeout=0.2,
            label="fixture",
        )
    first = heartbeat.read_text(encoding="utf-8")
    time.sleep(0.2)
    assert heartbeat.read_text(encoding="utf-8") == first


def test_child_log_is_redacted_before_it_reaches_disk(tmp_path: Path) -> None:
    canary = "provider-canary-value"
    output_path = tmp_path / "app.log"
    child = _GATE.Child(
        [sys.executable, "-c", "import os; print(os.environ['CANARY_API_KEY'])"],
        tmp_path,
        {"PATH": "/bin", "CANARY_API_KEY": canary},
        label="app",
        output_path=output_path,
    )
    child.process.wait(timeout=5)
    child.join()
    assert canary not in output_path.read_text(encoding="utf-8")
    assert "<redacted>" in output_path.read_text(encoding="utf-8")


def test_windows_terminate_does_not_mutate_global_os_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        pid = 654

        def __init__(self) -> None:
            self.terminated = 0
            self.killed = 0
            self.waits = 0

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated += 1

        def kill(self) -> None:
            self.killed += 1

        def wait(self, **_: object) -> int:
            self.waits += 1
            if self.waits == 1:
                raise _GATE.subprocess.TimeoutExpired("child", 5)
            return 0

    process = Process()
    monkeypatch.setattr(_GATE, "os", SimpleNamespace(name="nt"))
    _GATE.terminate(SimpleNamespace(process=process))
    assert process.terminated == 1
    assert process.killed == 1
