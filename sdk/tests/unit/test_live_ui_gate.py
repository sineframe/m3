"""Deterministic checks for the manual live UI gate helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


_SCRIPT = Path(__file__).parents[3] / "scripts" / "live_ui_gate.py"
_SPEC = importlib.util.spec_from_file_location("live_ui_gate", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)


def _report() -> dict[str, object]:
    return {
        "execution_id": "run-1",
        "report": {
            "snapshot": {
                "execution_id": "run-1",
                "lifecycle": "finished",
                "outcome": "completed",
            }
        },
        "spec": {"harness": "opencode", "model": "opencode/big-pickle"},
        "trace": {
            "runtime": {"kind": "opencode", "model_id": {"state": "observed", "value": "opencode/big-pickle"}},
            "timeline": [
                {
                    "kind": "tool_call",
                    "tool": {"state": "observed", "value": "shipping_quote"},
                    "arguments": {"state": "observed", "value": {"weight_kg": 2, "zone": "local"}},
                    "result": {"state": "observed", "value": {"structured_content": {"state": "observed", "value": {"currency": "USD", "amount": 12}}}},
                    "tool_status": "success",
                    "wire": {"state": "observed", "value": {"request_id": "wire-1"}},
                }
            ]
        },
    }


def test_redact_removes_secret_like_values() -> None:
    env = {"OPENCODE_API_KEY": "super-secret-key", "PATH": "/bin"}
    assert "super-secret-key" not in gate.redact("token=super-secret-key", env)
    assert "[REDACTED]" in gate.redact("token=super-secret-key", env)


def test_redact_ignores_short_secret_like_values() -> None:
    assert gate.redact("key=a data=a", {"API_KEY": "a"}) == "key=a data=a"


def test_free_loopback_ports_are_valid_and_distinct() -> None:
    first, second = gate.free_loopback_ports()
    assert first != second
    assert 1 <= first <= 65535
    assert 1 <= second <= 65535


def test_assert_report_validates_shipping_quote_contract() -> None:
    assert gate.assert_report(_report(), "opencode/big-pickle") == "run-1"


@pytest.mark.parametrize("state", ["redacted", "unavailable"])
def test_assert_report_requires_observed_tool_evidence(state: str) -> None:
    report = _report()
    report["trace"]["timeline"][0]["arguments"]["state"] = state  # type: ignore[index]
    with pytest.raises(gate.GateFailure, match="shipping_quote"):
        gate.assert_report(report, "opencode/big-pickle")


def test_assert_report_rejects_wrong_arguments() -> None:
    report = _report()
    timeline = report["trace"]["timeline"]  # type: ignore[index]
    timeline[0]["arguments"]["value"]["zone"] = "remote"  # type: ignore[index]
    with pytest.raises(gate.GateFailure, match="shipping_quote"):
        gate.assert_report(report, "opencode/big-pickle")


def test_assert_report_rejects_non_success_wire() -> None:
    report = _report()
    report["trace"]["timeline"][0]["wire"]["state"] = "missing"  # type: ignore[index]
    with pytest.raises(gate.GateFailure, match="wire"):
        gate.assert_report(report, "opencode/big-pickle")


def test_assert_report_rejects_wrong_status_runtime_or_currency() -> None:
    report = _report()
    report["trace"]["timeline"][0]["tool_status"] = "tool_error"  # type: ignore[index]
    with pytest.raises(gate.GateFailure, match="successful"):
        gate.assert_report(report, "opencode/big-pickle")

    report = _report()
    report["trace"]["runtime"]["kind"] = "direct"  # type: ignore[index]
    with pytest.raises(gate.GateFailure, match="runtime"):
        gate.assert_report(report, "opencode/big-pickle")

    report = _report()
    report["trace"]["timeline"][0]["result"]["value"]["structured_content"]["value"]["currency"] = "EUR"  # type: ignore[index]
    with pytest.raises(gate.GateFailure, match="USD"):
        gate.assert_report(report, "opencode/big-pickle")


def test_assert_report_rejects_wrong_model() -> None:
    with pytest.raises(gate.GateFailure, match="runtime/model"):
        gate.assert_report(_report(), "opencode/not-the-model")


def test_assert_report_rejects_execution_id_mismatch() -> None:
    with pytest.raises(gate.GateFailure, match="does not match"):
        gate.assert_report(_report(), "opencode/big-pickle", expected_execution_id="other")


def test_assert_report_rejects_second_tool_call() -> None:
    report = _report()
    report["trace"]["timeline"].append(  # type: ignore[index]
        {"kind": "tool_call", "tool": {"state": "observed", "value": "other"}}
    )
    with pytest.raises(gate.GateFailure, match="exactly one"):
        gate.assert_report(report, "opencode/big-pickle")


def test_prerequisites_require_key_before_external_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    with pytest.raises(gate.GateFailure, match="OPENCODE_API_KEY"):
        gate.check_prerequisites(Path("/does/not/exist"))


def test_playwright_timeout_is_a_safe_gate_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def timeout(*_args: object, **_kwargs: object) -> object:
        raise gate.subprocess.TimeoutExpired("playwright", gate.PLAYWRIGHT_TIMEOUT)

    monkeypatch.setattr(gate.subprocess, "run", timeout)
    with pytest.raises(gate.GateFailure, match="timed out"):
        gate.run_playwright(tmp_path / "playwright", tmp_path, {"PATH": "/bin"})


def test_windows_terminate_signals_group_then_kills_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        pid = 654

        def __init__(self) -> None:
            self.signals: list[object] = []
            self.waits = 0

        def poll(self) -> None:
            return None

        def send_signal(self, value: object) -> None:
            self.signals.append(value)

        def wait(self, **_: object) -> int:
            self.waits += 1
            if self.waits == 1:
                raise gate.subprocess.TimeoutExpired("child", 3)
            return 0

    process = Process()
    calls: list[list[str]] = []
    monkeypatch.setattr(gate.os, "name", "nt")
    monkeypatch.setattr(gate.signal, "CTRL_BREAK_EVENT", 999, raising=False)
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(command),
    )
    gate.terminate(SimpleNamespace(process=process))
    assert process.signals == [999]
    assert calls == [["taskkill", "/PID", "654", "/T", "/F"]]
