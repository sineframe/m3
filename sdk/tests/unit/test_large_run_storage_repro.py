"""The contention driver's failure cleanup must cover every launched CLI."""

import importlib.util
import subprocess
from pathlib import Path

import pytest


def test_cleanup_escalates_all_stalled_cli_processes_before_raising() -> None:
    path = Path(__file__).resolve().parents[3] / "scripts/repro_large_run_storage.py"
    spec = importlib.util.spec_from_file_location("large_run_storage_repro", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    class StalledCLI:
        def __init__(self, name: str) -> None:
            self.name = name
            self.killed = False

        def poll(self) -> int | None:
            return -9 if self.killed else None

        def terminate(self) -> None:
            calls.append((self.name, "terminate"))

        def kill(self) -> None:
            calls.append((self.name, "kill"))
            self.killed = True

        def wait(self, *, timeout: float) -> int:
            if not self.killed:
                raise subprocess.TimeoutExpired(self.name, timeout)
            calls.append((self.name, "reaped"))
            return -9

    with pytest.raises(RuntimeError, match="termination cleanup"):
        module.stop([StalledCLI("first"), StalledCLI("second")])
    assert calls == [
        ("first", "terminate"),
        ("second", "terminate"),
        ("first", "kill"),
        ("second", "kill"),
        ("first", "reaped"),
        ("second", "reaped"),
    ]
