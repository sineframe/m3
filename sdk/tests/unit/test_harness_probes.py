from __future__ import annotations

import subprocess
import threading
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from m3._types.specs import AgentSpec
from m3.harness import native as native_module
from m3.harness.contracts import HarnessLaunch
from m3.harness.opencode import OpenCodeHarnessAdapter
from m3.server_group import ServerGroupSnapshot
from m3.types import ACPAgent, ServerBinding, StdioServer


def _launch() -> HarnessLaunch:
    spec = AgentSpec(
        harness=ACPAgent(model="fixture"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
    )
    return HarnessLaunch(spec, ServerGroupSnapshot(), (), spec.tool_policy)


def _executable(path: Path) -> str:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


class _FakeRun:
    """Stand in for subprocess.run with scripted per-argv outputs."""

    def __init__(
        self,
        outputs: dict[tuple[str, ...], str],
        *,
        timeouts: dict[tuple[str, ...], int] | None = None,
        delay: float = 0.0,
    ) -> None:
        self.outputs = outputs
        self.timeouts = dict(timeouts or {})
        self.delay = delay
        self.calls: Counter[tuple[str, ...]] = Counter()
        self._lock = threading.Lock()

    def __call__(
        self, argv: Sequence[str], *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        key = tuple(argv[1:])
        with self._lock:
            self.calls[key] += 1
            remaining = self.timeouts.get(key, 0)
            if remaining:
                self.timeouts[key] = remaining - 1
        if remaining:
            raise subprocess.TimeoutExpired(list(argv), kwargs.get("timeout") or 0)
        if self.delay:
            time.sleep(self.delay)
        return subprocess.CompletedProcess(list(argv), 0, self.outputs[key], "")


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native_module, "PROBE_RETRY_DELAY_SECONDS", 0.0, raising=False)


_OPENCODE_OUTPUTS = {("serve", "--help"): "serve", ("--version",): "1.18.15"}


@pytest.mark.asyncio
async def test_opencode_preflight_retries_probe_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRun(
        _OPENCODE_OUTPUTS,
        timeouts={("serve", "--help"): 1, ("--version",): 1},
    )
    monkeypatch.setattr(subprocess, "run", fake)
    adapter = OpenCodeHarnessAdapter(executable=_executable(tmp_path / "opencode"))

    readiness = await adapter.preflight(_launch())

    assert readiness.ready, readiness.reason
    assert fake.calls[("serve", "--help")] == 2
    assert fake.calls[("--version",)] == 2


@pytest.mark.asyncio
async def test_missing_opencode_executable_is_not_ready(tmp_path: Path) -> None:
    adapter = OpenCodeHarnessAdapter(executable=str(tmp_path / "missing"))

    readiness = await adapter.preflight(_launch())

    assert not readiness.ready
    assert readiness.reason == "executable unavailable"
    assert native_module.probe_help(str(tmp_path / "missing"), ("--help",)) is None


def test_probe_retries_a_timeout_and_never_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRun({("--help",): "usage"}, timeouts={("--help",): 1})
    monkeypatch.setattr(subprocess, "run", fake)
    executable = _executable(tmp_path / "tool")

    assert native_module.probe_help(executable, ("--help",)) == "usage\n"
    assert fake.calls[("--help",)] == 2
    # Results are not cached: a later call (say, after a transient failure)
    # probes again.
    assert native_module.probe_help(executable, ("--help",)) == "usage\n"
    assert fake.calls[("--help",)] == 3


def _codex_with_versions(
    monkeypatch: pytest.MonkeyPatch, versions: dict[str, list[str | None]]
) -> tuple[Any, list[tuple[str, threading.Thread]]]:
    from m3.harness import codex as codex_module

    probes: list[tuple[str, threading.Thread]] = []

    def probe(executable: str, args: tuple[str, ...]) -> str | None:
        assert args == ("--version",)
        probes.append((executable, threading.current_thread()))
        return versions[executable].pop(0)

    monkeypatch.setattr(codex_module, "probe_help", probe)
    return codex_module, probes


def _elicitation(adapter: Any) -> bool:
    return bool(adapter.capabilities.interaction.supports_elicitation)


@pytest.mark.asyncio
async def test_codex_capabilities_never_probe_and_prepare_probes_off_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _executable(tmp_path / "codex")
    codex_module, probes = _codex_with_versions(
        monkeypatch, {executable: ["codex-cli 0.156.1"]}
    )
    adapter = codex_module.CodexHarnessAdapter(executable=executable)

    assert not _elicitation(adapter)
    assert probes == []
    await adapter.prepare_capabilities()
    assert _elicitation(adapter)
    await adapter.prepare_capabilities()
    assert len(probes) == 1
    assert probes[0][1] is not threading.main_thread()


@pytest.mark.asyncio
async def test_codex_retries_a_failed_version_probe_on_the_next_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _executable(tmp_path / "codex")
    codex_module, probes = _codex_with_versions(
        monkeypatch, {executable: [None, "codex-cli 0.156.1"]}
    )
    adapter = codex_module.CodexHarnessAdapter(executable=executable)

    await adapter.prepare_capabilities()
    assert not _elicitation(adapter)
    assert len(probes) == 1
    await adapter.prepare_capabilities()
    assert _elicitation(adapter)
    assert len(probes) == 2


@pytest.mark.asyncio
async def test_codex_capabilities_stay_with_their_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _executable(tmp_path / "codex-a")
    second = _executable(tmp_path / "codex-b")
    codex_module, probes = _codex_with_versions(
        monkeypatch, {first: ["codex-cli 0.156.1"], second: [None]}
    )
    adapter = codex_module.CodexHarnessAdapter(executable=first)

    await adapter.prepare_capabilities()
    assert _elicitation(adapter)
    adapter.executable = second
    await adapter.prepare_capabilities()
    assert not _elicitation(adapter)
    # A failed probe of another binary leaves the first binary's result alone.
    adapter.executable = first
    assert _elicitation(adapter)
    await adapter.prepare_capabilities()
    assert _elicitation(adapter)
    assert [executable for executable, _ in probes] == [first, second]
