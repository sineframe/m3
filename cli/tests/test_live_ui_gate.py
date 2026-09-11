from __future__ import annotations

import importlib.util
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
    output = "\n".join(
        (
            "MCP-Pal UI: http://127.0.0.1:8123/history",
            "Run: http://127.0.0.1:8123/playground/run/run%20id%2Fpart",
        )
    )
    history, direct, run_id = _GATE.parse_ui_links(output, "http://127.0.0.1:8123")
    assert history.endswith("/history")
    assert direct.endswith("run%20id%2Fpart")
    assert run_id == "run id/part"


def test_execution_report_url_quotes_run_id_path_syntax() -> None:
    assert _GATE.execution_report_url("http://127.0.0.1:8123", "run id/part") == (
        "http://127.0.0.1:8123/api/v2/executions/run%20id%2Fpart/report"
    )


@pytest.mark.parametrize(
    "output, message",
    [
        (
            "MCP-Pal UI: http://127.0.0.1:8124/history\n"
            "Run: http://127.0.0.1:8124/playground/run/run-1",
            "history link",
        ),
        (
            "MCP-Pal UI: http://127.0.0.1:8123/history\n"
            "Run: http://127.0.0.1:8124/playground/run/run-1",
            "selected origin",
        ),
        (
            "MCP-Pal UI: http://127.0.0.1:8123/history\n"
            "Run: http://127.0.0.1:8123/playground/run/run-1?x=1",
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
        "opencode/big-pickle",
    )
    assert "OPENCODE_API_KEY" not in browser
    assert browser["MCP_PAL_LIVE_UI_BASE_URL"] == "http://127.0.0.1:8123"
    assert browser["MCP_PAL_LIVE_EXECUTION_ID"] == "run id/1"


def test_cli_command_runs_only_the_existing_live_target() -> None:
    command = _GATE.cli_test_command(
        Path("/tmp/mcp-pal"),
        Path("/tmp/project/.venv/bin/python"),
        Path("/tmp/project/runs.sqlite"),
        8123,
    )
    assert command[-2:] == ["-q", _GATE.TARGET]
    assert command[:2] == ["/tmp/mcp-pal", "test"]
    assert "--api-port" not in command
    assert "--ui-port" not in command
    assert "--ui-dir" not in command


def test_assert_report_validates_shipping_quote_contract() -> None:
    _GATE.assert_report(_report(), "opencode/big-pickle", "run-1")


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
