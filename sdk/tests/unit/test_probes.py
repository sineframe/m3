"""Deterministic core capability/readiness probe contracts."""

from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

import pytest

from m3.services.probes import (
    ProbeKind,
    ProbeRequest,
    Probes,
)
from m3.types import CapabilityStatus

_PID_WRITING_CHILD = (
    "import os, pathlib, sys, time; "
    "path = pathlib.Path(sys.argv[1]); "
    "temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp'); "
    "temporary.write_text(str(os.getpid())); temporary.replace(path); "
    "time.sleep(30)"
)


def _fake_executable(tmp_path: Path, body: str, name: str = "fake-agent") -> Path:
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _wait_for_pid(path: Path, timeout: float = 2.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            contents = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            contents = ""
        if contents.isdecimal():
            pid = int(contents)
            if pid > 0:
                return pid
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for PID in {path.name}")


def test_binary_probe_records_version_and_redacts_output_and_environment(
    tmp_path: Path,
) -> None:
    executable = _fake_executable(
        tmp_path,
        "import os; print('fake 1.2.3 token=' + os.environ.get('PROBE_TOKEN', 'missing') + ' custom=' + os.environ.get('CUSTOM_VALUE', 'missing'))",
    )
    result = Probes().probe_binary(
        "requested-agent",
        executable,
        env={
            "PROBE_TOKEN": "super-secret-token",
            "CUSTOM_VALUE": "ordinary-secret-value",
        },
    )

    assert result.status is CapabilityStatus.READY
    assert result.capability.detected_version == "1.2.3"
    assert "super-secret-token" not in repr(result)
    assert "ordinary-secret-value" not in repr(result)
    assert "super-secret-token" not in result.model_dump_json()
    assert "ordinary-secret-value" not in result.model_dump_json()
    assert "[REDACTED]" in result.evidence.output


def test_command_arguments_are_never_persisted(tmp_path: Path) -> None:
    executable = _fake_executable(tmp_path, "print('fake 1.0.0')")
    result = Probes().probe_binary(
        "argument-test",
        executable,
        args=("--api-key", "split-secret-value"),
    )

    assert result.status is CapabilityStatus.READY
    assert result.evidence.command == ()
    assert "split-secret-value" not in repr(result)
    assert "--api-key" not in result.model_dump_json()


def test_json_shaped_output_is_redacted_by_sensitive_keys(tmp_path: Path) -> None:
    executable = _fake_executable(
        tmp_path,
        'print(\'{"token":"json-secret","nested":{"password":"json-password"},"ok":true}\')',
    )
    result = Probes().probe_binary("json-output", executable)

    assert result.status is CapabilityStatus.READY
    assert "json-secret" not in result.model_dump_json()
    assert "json-password" not in result.model_dump_json()
    assert "[REDACTED]" in result.evidence.output


def test_missing_harness_does_not_select_or_initialize_another_harness() -> None:
    report = Probes().probe_requested(
        [ProbeRequest(ProbeKind.HARNESS, "claude", "/definitely/missing/claude")]
    )

    assert [cap.name for cap in report.capabilities] == ["claude"]
    assert report.readiness.ready is False
    assert report.result_for("opencode") is None
    assert report.results[0].status is CapabilityStatus.UNAVAILABLE


def test_nonzero_and_timeout_are_degraded_without_fallback(tmp_path: Path) -> None:
    failing = _fake_executable(
        tmp_path,
        "print('fake 4.5.6', flush=True); raise SystemExit(7)",
        "failing-agent",
    )
    hanging = _fake_executable(tmp_path, "import time; time.sleep(30)", "hanging-agent")
    failed = Probes(timeout_seconds=1.0).probe_binary("failed", failing)
    timed_out = Probes(timeout_seconds=0.1).probe_binary("hanging", hanging)

    assert failed.status is CapabilityStatus.DEGRADED
    assert timed_out.status is CapabilityStatus.UNAVAILABLE
    assert failed.evidence.details["returncode"] == 7
    assert timed_out.evidence.details["timed_out"] is True


def test_bare_executable_uses_caller_path_but_child_environment_stays_minimal(
    tmp_path: Path,
) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    executable = bindir / "path-agent"
    executable.write_text(
        f"#!{sys.executable}\nprint('path-agent 2.0.0')\n", encoding="utf-8"
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    result = Probes().probe_binary(
        "path-agent", "path-agent", env={"PATH": str(bindir)}
    )

    assert result.status is CapabilityStatus.READY
    assert result.evidence.details["resolved_executable"].endswith("/path-agent")
    assert result.evidence.resolved_executable is not None
    assert result.evidence.resolved_executable.endswith("/path-agent")


def test_unknown_transport_does_not_execute_selected_command(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    executable = _fake_executable(
        tmp_path, f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ran')"
    )

    result = Probes().probe_transport(
        "unknown", transport="vendor-private", executable=executable
    )

    assert result.status is CapabilityStatus.UNSUPPORTED
    assert result.evidence.details["executed"] is False
    assert not marker.exists()


def test_all_timeout_values_must_be_finite_and_positive(tmp_path: Path) -> None:
    executable = _fake_executable(tmp_path, "print('fake 1.0.0')")
    for invalid in (0, -1, float("inf"), float("nan"), True):
        try:
            Probes(timeout_seconds=invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid service timeout {invalid!r}")
        try:
            Probes().probe_binary(
                "invalid-timeout", executable, timeout_seconds=invalid
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid probe timeout {invalid!r}")


@pytest.mark.process_lifecycle
def test_parent_exit_does_not_leave_grandchild_in_owned_process_group(
    tmp_path: Path,
) -> None:
    if os.name != "posix":
        return
    child_pid = tmp_path / "child.pid"
    executable = _fake_executable(
        tmp_path,
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {_PID_WRITING_CHILD!r}, sys.argv[1]]); "
        "time.sleep(30)",
    )

    result = Probes(timeout_seconds=2.0).probe_binary(
        "descendant", executable, args=(str(child_pid),)
    )

    assert result.status is CapabilityStatus.UNAVAILABLE
    pid = _wait_for_pid(child_pid)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        raise AssertionError(f"grandchild {pid} survived probe cleanup")


@pytest.mark.process_lifecycle
def test_normal_parent_exit_still_cleans_owned_grandchild(tmp_path: Path) -> None:
    if os.name != "posix":
        return
    child_pid = tmp_path / "normal-child.pid"
    executable = _fake_executable(
        tmp_path,
        f"""import pathlib, subprocess, sys, time
subprocess.Popen([sys.executable, '-c', {_PID_WRITING_CHILD!r}, sys.argv[1]])
deadline = time.monotonic() + 1
path = pathlib.Path(sys.argv[1])
while not path.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
raise SystemExit(0)""",
        "normal-exit-agent",
    )

    result = Probes(timeout_seconds=2.0).probe_binary(
        "normal-exit", executable, args=(str(child_pid),)
    )

    assert result.status is CapabilityStatus.READY
    pid = _wait_for_pid(child_pid)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        raise AssertionError(f"grandchild {pid} survived normal-exit cleanup")


def test_output_is_bounded_and_child_environment_is_minimal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AMBIENT_PROVIDER_KEY", "must-not-be-inherited")
    executable = _fake_executable(
        tmp_path,
        "import os; print(os.environ.get('AMBIENT_PROVIDER_KEY', 'absent')); print('x' * 200000)",
    )
    result = Probes(output_limit=128).probe_binary("noisy", executable)

    assert len(result.evidence.output) <= 128
    assert "must-not-be-inherited" not in result.evidence.output
    assert "absent" in result.evidence.output


def test_transport_and_optional_storage_have_explicit_statuses() -> None:
    service = Probes()
    unsupported = service.probe_transport("custom", transport="custom")
    available = service.probe_storage("memory")
    missing = service.probe_storage("sql", module="module_that_does_not_exist")

    assert unsupported.status is CapabilityStatus.UNSUPPORTED
    assert available.status is CapabilityStatus.READY
    assert missing.status is CapabilityStatus.UNAVAILABLE


def test_module_transport_probe_does_not_import_or_fallback() -> None:
    service = Probes()
    result = service.probe_transport("stdio", transport="stdio", module="sys")

    assert result.status is CapabilityStatus.READY
    assert result.evidence.details["module_available"] is True


def test_protocol_probe_records_detected_protocol_revision(tmp_path: Path) -> None:
    executable = _fake_executable(tmp_path, "print('MCP protocol 2025.06.18')")
    result = Probes().probe_protocol("mcp", executable, transport="stdio")

    assert result.status is CapabilityStatus.READY
    assert result.capability.protocol_version == "2025.06.18"
    assert result.capability.transport is not None
    assert result.capability.transport.value == "stdio"


def test_probe_request_without_target_is_unavailable_and_safe_repr() -> None:
    request = ProbeRequest(
        ProbeKind.BINARY, "missing", env={"API_TOKEN": "secret-value"}
    )
    report = Probes().probe_requested([request])

    assert report.results[0].status is CapabilityStatus.UNAVAILABLE
    assert "secret-value" not in repr(request)
    assert report.readiness.reason == "missing: probe target was not specified"
