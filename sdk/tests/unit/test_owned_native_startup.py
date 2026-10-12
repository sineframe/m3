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


@pytest.mark.asyncio
async def test_total_budget_rejects_in_process_factory_before_preparation() -> None:
    from m3.async_api import AsyncMCPTestKit
    from m3.errors import UnsupportedFeature
    from m3.harness.claude import ClaudeCodeHarnessAdapter
    from m3.types import ClaudeCode, InProcessServer

    called = False

    def factory():
        nonlocal called
        called = True
        raise AssertionError("unsupported factory was invoked")

    spec = AgentSpec(
        harness=ClaudeCode(model="fixture"),
        harness_timeouts=HarnessTimeouts(total_seconds=5),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
    )
    async with AsyncMCPTestKit() as kit:
        session = kit.agent_session(
            spec,
            adapter=ClaudeCodeHarnessAdapter(executable="fixture"),
            runtime_servers=(InProcessServer(name="memory", factory=factory),),
        )
        with pytest.raises(UnsupportedFeature, match="in-process server factory"):
            await session.__aenter__()
        assert not called
        assert session._workspace._root is None


@pytest.mark.process_lifecycle
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["claude", "opencode"])
async def test_native_startup_retains_owner_when_root_cleanup_stalls(
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
    blocked_helper,
) -> None:
    import os

    from m3.harness.claude import ClaudeCodeHarnessAdapter
    from m3.harness.contracts import HarnessLaunch
    from m3.harness.native import ProcessOwner
    from m3.harness.opencode import OpenCodeHarnessAdapter
    from m3.server_group import ServerGroupSnapshot
    from m3.types import ClaudeCode, OpenCode

    _worker, _arm, marker, fifo, _late = blocked_helper("native_remove")
    clock = StartupBudget(HarnessTimeouts(startup_seconds=3))
    clock.resume()

    def short_cleanup_attempt() -> float:
        if clock.cleanup_deadline is None:
            clock.cleanup_deadline = time.monotonic() + 0.15
        return clock.cleanup_deadline

    monkeypatch.setattr(clock, "begin_cleanup", short_cleanup_attempt)
    original_spawn = ProcessOwner.spawn

    async def failing_spawn(self, *args, **kwargs):
        await original_spawn(self, *args, **kwargs)
        raise RuntimeError("fixture failure after owned spawn")

    monkeypatch.setattr(ProcessOwner, "spawn", failing_spawn)
    if kind == "claude":
        harness = ClaudeCode(model="fixture")
        fixture = Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py"
        adapter = ClaudeCodeHarnessAdapter(executable=str(fixture))
    else:
        harness = OpenCode(model="fixture")
        fixture = Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py"
        adapter = OpenCodeHarnessAdapter(executable=str(fixture))
    spec = AgentSpec(
        harness=harness,
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
    )
    launch = HarnessLaunch(
        spec, ServerGroupSnapshot(), (), spec.tool_policy, _startup_clock=clock
    )
    try:
        with pytest.raises(RuntimeError, match="fixture failure after owned spawn"):
            await adapter.open(launch)
        assert adapter._startup_cleanup_failed
        assert adapter._owner is not None
        root = adapter._owner.root
        assert root.exists() and marker.exists()
    finally:
        reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        try:
            clock.cleanup_deadline = time.monotonic() + 3
            await adapter.close()
        finally:
            os.close(reader)
    assert not root.exists()


@pytest.mark.process_lifecycle
@pytest.mark.asyncio
async def test_cancelled_native_root_adoption_reclaims_accepted_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from m3._bootstrap_operations import OwnedBootstrapOperations
    from m3.harness._native_preparation import NativePreparation
    from m3.harness.contracts import HarnessLaunch
    from m3.server_group import ServerGroupSnapshot
    from m3.types import ClaudeCode

    clock = StartupBudget(HarnessTimeouts(total_seconds=5))
    clock.resume()
    spec = AgentSpec(
        harness=ClaudeCode(model="fixture"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
    )
    launch = HarnessLaunch(
        spec, ServerGroupSnapshot(), (), spec.tool_policy, _startup_clock=clock
    )
    original_stage = OwnedBootstrapOperations.stage_native_launch
    accepted_roots: list[Path] = []
    facades: list[OwnedBootstrapOperations] = []
    loop = asyncio.get_running_loop()
    task = None

    def cancel_after_accept(self, *args, **kwargs):
        resource = original_stage(self, *args, **kwargs)
        accepted_roots.append(resource.root)
        facades.append(self)
        assert task is not None
        # The real helper has accepted the resource, but the awaiting controller
        # has not adopted its return value yet.
        loop.call_soon_threadsafe(task.cancel)
        return resource

    monkeypatch.setattr(
        OwnedBootstrapOperations, "stage_native_launch", cancel_after_accept
    )
    task = asyncio.create_task(
        NativePreparation.create(launch, "claude_code", {"mcp-config.json": b"{}"})
    )
    with pytest.raises(asyncio.CancelledError):
        await task
    assert accepted_roots
    assert facades[0].cleanup_complete
    assert not accepted_roots[0].exists()
