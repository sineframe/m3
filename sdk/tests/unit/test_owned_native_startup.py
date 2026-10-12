from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from m3._startup_deadlines import StartupBudget
from m3._types.specs import AgentSpec
from m3.types import HarnessTimeouts, ServerBinding, StdioServer


@pytest.mark.process_lifecycle
@pytest.mark.asyncio
async def test_codex_explicit_startup_exceeds_default_thread_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from m3._bootstrap_operations import OwnedBootstrapOperations
    from m3.harness import codex as codex_module
    from m3.harness.codex import CodexHarnessAdapter
    from m3.harness.contracts import HarnessLaunch
    from m3.server_group import ServerGroupSnapshot
    from m3.types import Codex, RestrictiveToolPolicy

    fixture = Path(__file__).parents[1] / "fixtures" / "codex_app_server_fixture.py"
    monkeypatch.setattr(codex_module, "_THREAD_START_TIMEOUT_SECONDS", 0.15)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-login"))
    clock = StartupBudget(HarnessTimeouts(startup_seconds=3, total_seconds=5))
    clock.resume()
    clock.begin_native()
    operations = OwnedBootstrapOperations()
    spec = AgentSpec(
        harness=Codex(model="fixture"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
        tool_policy=RestrictiveToolPolicy(),
    )
    launch = HarnessLaunch(
        spec,
        ServerGroupSnapshot(),
        (),
        spec.tool_policy,
        _credential_deadline=clock.deadline,
        _native_auth_operations=operations,
        _startup_clock=clock,
    )
    adapter = CodexHarnessAdapter(
        executable=str(fixture), environment={"M3_CODEX_FIXTURE_THREAD_DELAY": "0.4"}
    )
    try:
        session = await adapter.open(launch)
        owner = session.owner
        root = owner.root
        assert owner._guardian is not None
        assert (root / "codex-home" / "config.toml").exists()
        assert clock.deadline is None
    finally:
        await adapter.close()
        operations.close(deadline=time.monotonic() + 5)
    assert not root.exists()
    assert operations.cleanup_complete


@pytest.mark.process_lifecycle
@pytest.mark.asyncio
async def test_codex_progress_frames_do_not_restart_session_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from m3.harness import codex as codex_module
    from m3.harness.codex import CodexHarnessAdapter
    from m3.harness.contracts import HarnessLaunch
    from m3.server_group import ServerGroupSnapshot
    from m3.types import Codex, RestrictiveToolPolicy

    fixture = Path(__file__).parents[1] / "fixtures" / "codex_app_server_fixture.py"
    monkeypatch.setattr(codex_module, "_THREAD_START_TIMEOUT_SECONDS", 0.15)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-login"))
    spec = AgentSpec(
        harness=Codex(model="fixture"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
        tool_policy=RestrictiveToolPolicy(),
    )
    launch = HarnessLaunch(spec, ServerGroupSnapshot(), (), spec.tool_policy)
    adapter = CodexHarnessAdapter(
        executable=str(fixture), environment={"M3_CODEX_FIXTURE_THREAD_DELAY": "0.4"}
    )
    started = time.monotonic()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await adapter.open(launch)
        assert time.monotonic() - started < 2
    finally:
        await adapter.close()


@pytest.mark.process_lifecycle
@pytest.mark.asyncio
@pytest.mark.parametrize("initialize_seconds", [0.15, 2.0])
async def test_claude_progress_does_not_extend_initialization(
    initialize_seconds: float,
) -> None:
    from m3.harness.claude import ClaudeCodeHarnessAdapter
    from m3.harness.contracts import HarnessLaunch, HarnessTurnRequest
    from m3.server_group import ServerGroupSnapshot
    from m3.types import ClaudeCode, RestrictiveToolPolicy

    fixture = Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py"
    clock = StartupBudget(
        HarnessTimeouts(startup_seconds=3, initialize_seconds=initialize_seconds)
    )
    clock.resume()
    spec = AgentSpec(
        harness=ClaudeCode(model="fixture"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
        tool_policy=RestrictiveToolPolicy(),
    )
    launch = HarnessLaunch(
        spec, ServerGroupSnapshot(), (), spec.tool_policy, _startup_clock=clock
    )
    adapter = ClaudeCodeHarnessAdapter(
        executable=str(fixture),
        environment={"M3_CLAUDE_EMIT_INIT": "1", "M3_CLAUDE_INIT_DELAY": "0.4"},
    )
    try:
        session = await adapter.open(launch)
        root = session.owner.root
        started = time.monotonic()
        result = await session.send(
            HarnessTurnRequest.from_message("hello", timeout_seconds=3)
        )
        assert result.status == (
            "timed_out" if initialize_seconds < 0.4 else "completed"
        )
        assert time.monotonic() - started < 2
        assert clock.finished == (initialize_seconds > 0.4)
    finally:
        await adapter.close()
    assert not root.exists()


@pytest.mark.process_lifecycle
@pytest.mark.asyncio
@pytest.mark.parametrize("session_seconds", [0.15, 2.0])
async def test_opencode_streamed_session_body_uses_one_deadline(
    session_seconds: float,
) -> None:
    from m3.harness.contracts import HarnessLaunch
    from m3.harness.opencode import OpenCodeHarnessAdapter
    from m3.server_group import ServerGroupSnapshot
    from m3.types import OpenCode, RestrictiveToolPolicy

    fixture = Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py"
    clock = StartupBudget(
        HarnessTimeouts(startup_seconds=3, session_seconds=session_seconds)
    )
    clock.resume()
    spec = AgentSpec(
        harness=OpenCode(model="fixture", dialect="legacy"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
        tool_policy=RestrictiveToolPolicy(),
    )
    launch = HarnessLaunch(
        spec, ServerGroupSnapshot(), (), spec.tool_policy, _startup_clock=clock
    )
    adapter = OpenCodeHarnessAdapter(
        executable=str(fixture),
        environment={"M3_OPENCODE_SESSION_CHUNK_DELAY": "0.1"},
    )
    try:
        started = time.monotonic()
        if session_seconds < 0.3:
            with pytest.raises(asyncio.TimeoutError):
                await adapter.open(launch)
            assert not clock.finished
        else:
            session = await adapter.open(launch)
            root = session.owner.root
            assert clock.finished
        assert time.monotonic() - started < 2
    finally:
        await adapter.close()
    if session_seconds > 0.3:
        assert not root.exists()


@pytest.mark.process_lifecycle
@pytest.mark.asyncio
async def test_native_session_entry_is_lazy_and_idle_does_not_consume_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from m3.agent_session import AsyncAgentSession
    from m3.harness.codex import CodexHarnessAdapter
    from m3.types import Codex, RestrictiveToolPolicy, TurnOutcome

    fixture = Path(__file__).parents[1] / "fixtures" / "codex_app_server_fixture.py"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-login"))
    spec = AgentSpec(
        harness=Codex(model="fixture"),
        harness_timeouts=HarnessTimeouts(total_seconds=5, startup_seconds=3),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
        tool_policy=RestrictiveToolPolicy(),
    )
    adapter = CodexHarnessAdapter(executable=str(fixture))
    session = AsyncAgentSession(spec, adapter)
    async with session:
        workspace = session._workspace.root
        assert adapter._session is None
        remaining = session._startup_clock._remaining
        await asyncio.sleep(0.2)
        assert session._startup_clock._remaining == remaining
        result = await session.send("hello", timeout=3)
        assert result.snapshot.outcome is TurnOutcome.COMPLETED
        assert adapter._session is not None
        native_root = adapter._session.owner.root
        assert session._startup_clock.finished
    assert not workspace.exists()
    assert not native_root.exists()
    assert session._native_auth_operations is not None
    assert session._native_auth_operations.cleanup_complete


@pytest.mark.process_lifecycle
@pytest.mark.asyncio
async def test_startup_cause_survives_stalled_cleanup_and_owner_is_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    blocked_helper,
) -> None:
    import json
    import os

    from m3.harness._native_errors import NativeHarnessStartupError
    from m3.harness.codex import CodexHarnessAdapter
    from m3.harness.contracts import HarnessLaunch
    from m3.server_group import ServerGroupSnapshot
    from m3.types import Codex, RestrictiveToolPolicy

    _worker, _arm, marker, fifo, _late = blocked_helper("native_remove")
    fixture = Path(__file__).parents[1] / "fixtures" / "codex_app_server_fixture.py"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-login"))
    clock = StartupBudget(HarnessTimeouts(startup_seconds=3))
    clock.resume()
    cleanup_started: list[float] = []

    def short_cleanup_attempt() -> float:
        if clock.cleanup_deadline is None:
            cleanup_started.append(time.monotonic())
            clock.cleanup_deadline = cleanup_started[0] + 0.15
        return clock.cleanup_deadline

    monkeypatch.setattr(clock, "begin_cleanup", short_cleanup_attempt)
    spec = AgentSpec(
        harness=Codex(model="fixture"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
        tool_policy=RestrictiveToolPolicy(),
    )
    launch = HarnessLaunch(
        spec, ServerGroupSnapshot(), (), spec.tool_policy, _startup_clock=clock
    )
    adapter = CodexHarnessAdapter(
        executable=str(fixture),
        environment={"M3_CODEX_FIXTURE_INITIALIZE_ERROR": "1"},
    )
    try:
        with pytest.raises(NativeHarnessStartupError) as failure:
            await adapter.open(launch)
        assert "fixture_initialize_cause" in failure.value.error.message
        assert cleanup_started and time.monotonic() - cleanup_started[0] < 0.6
        assert adapter._startup_cleanup_failed
        assert adapter._process is not None and not adapter._process._closed
        root = adapter._process.owner.root
        assert root.exists() and marker.exists()
        assert json.loads(marker.read_text())["root"] == str(root)
    finally:
        reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        try:
            clock.cleanup_deadline = time.monotonic() + 3
            await adapter.close()
        finally:
            os.close(reader)
    assert not root.exists()
