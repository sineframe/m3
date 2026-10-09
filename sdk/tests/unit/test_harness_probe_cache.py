from __future__ import annotations

import asyncio
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
async def test_concurrent_opencode_preflights_probe_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRun(_OPENCODE_OUTPUTS, delay=0.1)
    monkeypatch.setattr(subprocess, "run", fake)
    executable = _executable(tmp_path / "opencode")
    adapters = [OpenCodeHarnessAdapter(executable=executable) for _ in range(8)]

    results = await asyncio.gather(
        *(adapter.preflight(_launch()) for adapter in adapters)
    )
    # open() preflights again; that must reuse the cached probe results.
    again = await adapters[0].preflight(_launch())

    assert all(result.ready for result in (*results, again))
    assert fake.calls == Counter({("serve", "--help"): 1, ("--version",): 1})


def test_probe_timeout_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRun({("--help",): "usage"}, timeouts={("--help",): 100})
    monkeypatch.setattr(subprocess, "run", fake)
    executable = _executable(tmp_path / "tool")

    assert native_module.probe_help(executable, ("--help",)) is None
    attempts = fake.calls[("--help",)]
    assert attempts >= 2

    fake.timeouts.clear()
    assert native_module.probe_help(executable, ("--help",)) == "usage\n"
    assert fake.calls[("--help",)] == attempts + 1


def test_probe_cache_follows_executable_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRun({("--help",): "usage"})
    monkeypatch.setattr(subprocess, "run", fake)
    path = tmp_path / "tool"
    executable = _executable(path)

    native_module.probe_help(executable, ("--help",))
    native_module.probe_help(executable, ("--help",))
    assert fake.calls[("--help",)] == 1

    path.write_text("#!/bin/sh\n# upgraded\nexit 0\n", encoding="utf-8")
    native_module.probe_help(executable, ("--help",))
    assert fake.calls[("--help",)] == 2


@pytest.mark.asyncio
async def test_missing_opencode_executable_is_not_ready(tmp_path: Path) -> None:
    adapter = OpenCodeHarnessAdapter(executable=str(tmp_path / "missing"))

    readiness = await adapter.preflight(_launch())

    assert not readiness.ready
    assert readiness.reason == "executable unavailable"
    assert native_module.probe_help(str(tmp_path / "missing"), ("--help",)) is None


def test_concurrent_callers_share_a_timed_out_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    lock = threading.Lock()

    def hanging(argv: Sequence[str], *args: Any, **kwargs: Any) -> None:
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.2)
        raise subprocess.TimeoutExpired(list(argv), kwargs.get("timeout") or 0)

    monkeypatch.setattr(subprocess, "run", hanging)
    executable = _executable(tmp_path / "tool")
    barrier = threading.Barrier(8)

    def call() -> str | None:
        barrier.wait()
        return native_module.probe_help(executable, ("--help",))

    started = time.monotonic()
    threads = [threading.Thread(target=call) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    # Callers already waiting share the failed result instead of each
    # repeating every attempt one after another.
    assert calls == native_module.PROBE_ATTEMPTS
    assert time.monotonic() - started < 1.5

    # A later call still retries.
    assert native_module.probe_help(executable, ("--help",)) is None
    assert calls == 2 * native_module.PROBE_ATTEMPTS


def test_codex_retries_mrtr_version_probe_after_a_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from m3.harness.codex import CodexHarnessAdapter

    fake = _FakeRun(
        {("--version",): "codex-cli 0.156.1"},
        timeouts={("--version",): native_module.PROBE_ATTEMPTS},
    )
    monkeypatch.setattr(subprocess, "run", fake)
    adapter = CodexHarnessAdapter(executable=_executable(tmp_path / "codex"))

    assert not adapter.capabilities.interaction.supports_elicitation
    # The timeout is not remembered as "unsupported": the next check probes.
    assert adapter.capabilities.interaction.supports_elicitation


@pytest.mark.asyncio
async def test_codex_prepares_capabilities_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from m3.harness import codex as codex_module

    threads: list[threading.Thread] = []

    def probe(executable: str, args: tuple[str, ...]) -> str:
        threads.append(threading.current_thread())
        return "codex-cli 0.156.1"

    monkeypatch.setattr(codex_module, "probe_help", probe)
    adapter = codex_module.CodexHarnessAdapter(
        executable=_executable(tmp_path / "codex")
    )
    await adapter.prepare_capabilities()
    assert threads and threads[0] is not threading.main_thread()
    # Later synchronous reads use the prepared result.
    assert adapter.capabilities.interaction.supports_elicitation
    assert len(threads) == 1
