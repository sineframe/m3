"""Harness-neutral multi-turn session contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from m3.agent_session import AdapterTurn, AsyncAgentSession, HarnessTurnError
from m3.async_api import AsyncMCPTestKit
from m3.errors import (
    CleanupError,
    SessionBusy,
    SessionStillOpen,
    UnsupportedFeature,
)
from m3.sync_api import MCPTestKit
from m3.types import (
    ACPAgent,
    AgentSpec,
    ArtifactPolicy,
    ErrorCode,
    ExecutionOutcome,
    ServerBinding,
    SessionForkRequest,
    StdioServer,
    TextContent,
    TurnOutcome,
    TurnResponse,
    TurnResult,
    UserMessage,
    WorkspaceKind,
    WorkspacePolicy,
)
from m3.workspace import WorkspaceManager


def _spec() -> AgentSpec:
    return AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="memory", command="echo")),),
        harness=ACPAgent(model="fixture"),
    )


class FakeHarness:
    supported_content_kinds = frozenset({"text"})

    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.started = 0
        self.closed = 0
        self.cancelled = 0
        self.messages: list[str] = []
        self.tool_error = False

    async def start(self, spec: AgentSpec) -> None:
        self.started += 1

    async def send(
        self, message, *, timeout=None, metadata: Mapping[str, object] | None = None
    ):
        if self.delay:
            await asyncio.sleep(self.delay)
        text = message.content[0].text
        self.messages.append(text)
        if self.tool_error:
            self.tool_error = False
            return AdapterTurn(
                error=None,
                terminal=False,
                outcome=TurnOutcome.FAILED,
            )
        return TurnResponse(content=(TextContent(text=text),))

    async def cancel(self) -> None:
        self.cancelled += 1

    async def close(self) -> None:
        self.closed += 1


class EvidenceHarness(FakeHarness):
    async def send(self, message, *, timeout=None, metadata=None):
        return AdapterTurn(
            response=TurnResponse(content=(TextContent(text=message.content[0].text),)),
            evidence={
                "usage_requested": True,
                "usage_enforced": False,
                "usage_observed": False,
                "usage_unavailable": "provider_did_not_emit_usage",
                "secret_echo": "evidence-canary",
            },
        )


class HostileEvidenceHarness(FakeHarness):
    async def send(self, message, *, timeout=None, metadata=None):
        return AdapterTurn(
            response=TurnResponse(content=(TextContent(text="safe"),)),
            evidence={"hostile": object()},
        )


class BlockingHarness(FakeHarness):
    def __init__(self) -> None:
        super().__init__()
        self.started_send = asyncio.Event()
        self.release_send = asyncio.Event()

    async def send(self, message, *, timeout=None, metadata=None):
        self.started_send.set()
        await self.release_send.wait()
        return await super().send(message, timeout=timeout, metadata=metadata)

    async def cancel(self) -> None:
        self.cancelled += 1
        self.release_send.set()


class ExplodingCloseHarness(FakeHarness):
    async def close(self) -> None:
        raise RuntimeError("cleanup-secret-must-not-escape")


class TransientCloseHarness(FakeHarness):
    def __init__(self, failures: int = 1) -> None:
        super().__init__()
        self.failures = failures

    async def close(self) -> None:
        self.closed += 1
        if self.failures:
            self.failures -= 1
            raise RuntimeError("cleanup-secret-must-not-escape")


class StartupBarrierHarness(FakeHarness):
    def __init__(self) -> None:
        super().__init__()
        self.start_called = asyncio.Event()
        self.release_start = asyncio.Event()

    async def start(self, spec: AgentSpec) -> None:
        self.started += 1
        self.start_called.set()
        await self.release_start.wait()


class PreflightFailureHarness(FakeHarness):
    def __init__(self) -> None:
        super().__init__()
        self.workspace_root: Path | None = None

    async def preflight(self, launch: object) -> object:
        self.workspace_root = Path(str(launch.workspace_root))
        raise UnsupportedFeature("preflight unavailable")


class FatalStartup(BaseException):
    pass


class FatalStartupHarness(FakeHarness):
    async def start(self, spec: AgentSpec) -> None:
        self.started += 1
        raise FatalStartup()


class CloseBarrierHarness(FakeHarness):
    def __init__(self) -> None:
        super().__init__()
        self.close_called = asyncio.Event()
        self.release_close = asyncio.Event()

    async def close(self) -> None:
        self.closed += 1
        self.close_called.set()
        await self.release_close.wait()


class DeletesWorkspaceOnClose(FakeHarness):
    def __init__(self) -> None:
        super().__init__()
        self.session: AsyncAgentSession | None = None
        self.deleted = False

    async def send(self, message, *, timeout=None, metadata=None):
        assert self.session is not None
        (self.session._workspace.root / "result.txt").write_text(
            "captured", encoding="utf-8"
        )
        return await super().send(message, timeout=timeout, metadata=metadata)

    async def close(self) -> None:
        assert self.session is not None
        (self.session._workspace.root / "result.txt").unlink(missing_ok=True)
        self.deleted = True


@pytest.mark.asyncio
async def test_session_preserves_adapter_and_returns_terminal_result_on_clean_close() -> (
    None
):
    adapter = FakeHarness()
    session = AsyncAgentSession(_spec(), adapter)

    with pytest.raises(SessionStillOpen):
        _ = session.result

    async with session:
        first = await session.send("first", metadata={"turn": 1})
        second = await session.send("second")
        assert first.response is not None and first.response.text == "first"
        assert second.response is not None and second.response.text == "second"
        assert (await session.snapshot()).lifecycle.value == "idle"

    assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert adapter.started == 1
    assert adapter.closed == 1
    assert adapter.messages == ["first", "second"]


@pytest.mark.asyncio
async def test_workspace_is_captured_before_adapter_teardown() -> None:
    adapter = DeletesWorkspaceOnClose()
    spec = _spec().model_copy(
        update={
            "workspace": WorkspacePolicy(kind=WorkspaceKind.TEMPORARY),
            "artifact_policy": ArtifactPolicy.ALWAYS,
            "declared_artifacts": ("result.txt",),
        }
    )
    session = AsyncAgentSession(spec, adapter)
    adapter.session = session
    async with session:
        await session.send("write")
    assert adapter.deleted
    assert session.result.artifacts


@pytest.mark.asyncio
async def test_public_turn_result_preserves_redacted_evidence_and_roundtrips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("M3_EVIDENCE_TOKEN", "evidence-canary")
    session = AsyncAgentSession(_spec(), EvidenceHarness())
    async with session:
        result = await session.send("capture")
        assert result.evidence["usage_requested"] is True
        assert result.evidence["usage_enforced"] is False
        assert result.evidence["usage_observed"] is False
        assert result.evidence["usage_unavailable"] == "provider_did_not_emit_usage"
        assert result.evidence["secret_echo"] == "[REDACTED]"
        restored = TurnResult.model_validate(result.model_dump(mode="json"))
        assert restored == result


@pytest.mark.asyncio
async def test_hostile_adapter_evidence_fails_closed() -> None:
    session = AsyncAgentSession(_spec(), HostileEvidenceHarness())
    async with session:
        result = await session.send("capture")
        assert result.evidence == {"evidence_state": "unavailable"}


@pytest.mark.asyncio
async def test_fork_creates_fresh_identity_and_records_source_provenance() -> None:
    source_adapter = FakeHarness()
    source = AsyncAgentSession(_spec(), source_adapter)
    async with source:
        await source.send("source")
    source_result = source.result
    request = SessionForkRequest(
        mode="replay",
        replay_inputs=(UserMessage(content=(TextContent(text="replayed"),)),),
        source_turn_id=source_result.turns[0].snapshot.turn_id,
    )
    child_adapter = FakeHarness()

    async def factory(spec, provenance):
        assert spec == source.spec
        assert provenance.source_execution_id == source_result.snapshot.execution_id
        return child_adapter

    child = await source.fork(request, adapter_factory=factory)
    assert child.provenance is not None
    assert child.provenance.source_execution_id == source_result.snapshot.execution_id
    assert (
        child.provenance.source_session_id == source_result.turns[0].snapshot.session_id
    )
    assert child.provenance.source_turn_id == request.source_turn_id
    assert child.provenance.mode == "replay"
    assert child.provenance == (await child.snapshot()).provenance
    assert child._execution_id != source_result.snapshot.execution_id
    assert child._session_id != source_result.turns[0].snapshot.session_id
    async with child:
        await child.send(request.replay_inputs[0])
    assert child.result.provenance == child.provenance


@pytest.mark.asyncio
async def test_fork_requires_terminal_source_and_fresh_adapter() -> None:
    source_adapter = FakeHarness()
    source = AsyncAgentSession(_spec(), source_adapter)
    request = SessionForkRequest(
        mode="fork", replay_inputs=(UserMessage(content=(TextContent(text="input"),)),)
    )
    with pytest.raises(SessionStillOpen):
        await source.fork(
            request, adapter_factory=lambda spec, provenance: FakeHarness()
        )
    async with source:
        await source.send("source")
    with pytest.raises(UnsupportedFeature, match="fresh adapter"):
        await source.fork(
            request, adapter_factory=lambda spec, provenance: source_adapter
        )


@pytest.mark.asyncio
async def test_fork_recreates_server_manager_for_runtime_state() -> None:
    class Manager:
        def __init__(self) -> None:
            self.started = 0
            self.closed = 0

        async def start(self):
            self.started += 1

        async def close(self):
            self.closed += 1

    managers: list[Manager] = []

    def make_manager() -> Manager:
        manager = Manager()
        managers.append(manager)
        return manager

    source_manager = make_manager()
    source_adapter = FakeHarness()
    source = AsyncAgentSession(
        _spec(),
        source_adapter,
        server_manager=source_manager,
        server_manager_factory=make_manager,
    )
    async with source:
        await source.send("source")
    request = SessionForkRequest(
        mode="replay",
        replay_inputs=(UserMessage(content=(TextContent(text="child"),)),),
    )
    child = await source.fork(
        request, adapter_factory=lambda spec, provenance: FakeHarness()
    )
    assert len(managers) == 2
    assert managers[0] is source_manager and managers[1] is not source_manager
    async with child:
        await child.send("child")
    assert source_manager.started == 1 and source_manager.closed == 1
    assert managers[1].started == 1 and managers[1].closed == 1


@pytest.mark.asyncio
async def test_concurrent_send_is_busy_but_enqueue_is_fifo() -> None:
    adapter = FakeHarness(delay=0.03)
    async with AsyncAgentSession(_spec(), adapter) as session:
        active = asyncio.create_task(session.send("active"))
        await asyncio.sleep(0)
        with pytest.raises(SessionBusy):
            await session.send("rejected")
        queued_one = await session.enqueue_turn("queued-1")
        queued_two = await session.enqueue_turn("queued-2")
        await active
        assert (await queued_one.result()).response is not None
        assert (await queued_two.wait()).response is not None

    assert adapter.messages == ["active", "queued-1", "queued-2"]


@pytest.mark.asyncio
async def test_nonterminal_tool_error_leaves_session_usable() -> None:
    adapter = FakeHarness()
    async with AsyncAgentSession(_spec(), adapter) as session:
        adapter.tool_error = True
        failed = await session.send("tool-error")
        recovered = await session.send("recovered")
        assert failed.snapshot.outcome is TurnOutcome.FAILED
        assert recovered.response is not None and recovered.response.text == "recovered"


@pytest.mark.asyncio
async def test_timeout_is_terminal_and_redacted() -> None:
    adapter = FakeHarness(delay=0.1)
    async with AsyncAgentSession(_spec(), adapter) as session:
        result = await session.send("slow", timeout=0.001)
        assert result.snapshot.outcome is TurnOutcome.TIMED_OUT
        assert result.error is not None and result.error.code is ErrorCode.TIMEOUT
        assert session.result.snapshot.outcome is ExecutionOutcome.TIMED_OUT
        assert adapter.cancelled == 1


@pytest.mark.asyncio
async def test_unsupported_content_fails_preflight_without_starting_or_sending() -> (
    None
):
    adapter = FakeHarness()
    async with AsyncAgentSession(_spec(), adapter) as session:
        with pytest.raises(UnsupportedFeature):
            await session.send(
                {"kind": "opaque", "provider": "other", "payload": {"x": 1}}
            )  # type: ignore[arg-type]
        assert adapter.messages == []


@pytest.mark.asyncio
async def test_hostile_adapter_error_is_value_free_and_terminal_when_declared() -> None:
    class Hostile(FakeHarness):
        async def send(self, message, *, timeout=None, metadata=None):
            raise HarnessTurnError("secret must never escape", terminal=True)

    adapter = Hostile()
    async with AsyncAgentSession(_spec(), adapter) as session:
        result = await session.send("x")
        assert result.error is not None
        assert "secret" not in repr(result)
        assert session.result.snapshot.outcome is ExecutionOutcome.FAILED


@pytest.mark.asyncio
async def test_snapshot_is_running_during_turn_and_cancel_waits_before_close() -> None:
    adapter = BlockingHarness()
    async with AsyncAgentSession(_spec(), adapter) as session:
        sending = asyncio.create_task(session.send("in-flight"))
        await adapter.started_send.wait()
        assert (await session.snapshot()).lifecycle.value == "running_turn"
        adapter.release_send.set()
        await session.cancel()
        await sending
    assert adapter.closed == 1
    assert session.result.snapshot.outcome is ExecutionOutcome.CANCELLED


@pytest.mark.asyncio
async def test_cancel_forcefully_reaps_uncooperative_turn_without_closing_race() -> (
    None
):
    class Uncooperative(FakeHarness):
        async def send(self, message, *, timeout=None, metadata=None):
            await asyncio.sleep(10)
            return await super().send(message, timeout=timeout, metadata=metadata)

    adapter = Uncooperative()
    session = AsyncAgentSession(_spec(), adapter)
    await session.__aenter__()
    sending = asyncio.create_task(session.send("stuck"))
    await asyncio.sleep(0)
    await session.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sending
    assert adapter.closed == 1
    assert session.result.snapshot.outcome is ExecutionOutcome.CANCELLED


@pytest.mark.asyncio
async def test_cleanup_failure_is_generic_and_result_remains_terminal() -> None:
    session = AsyncAgentSession(_spec(), ExplodingCloseHarness())
    await session.__aenter__()
    with pytest.raises(CleanupError) as exc_info:
        await session.aclose()
    assert "cleanup-secret" not in repr(exc_info.value)
    assert session.result.snapshot.outcome is ExecutionOutcome.FAILED
    assert session.result.error is not None
    assert session.result.error.code is ErrorCode.CLEANUP_FAILED


@pytest.mark.asyncio
async def test_transient_cleanup_failure_retains_owner_for_retry() -> None:
    adapter = TransientCloseHarness()
    session = AsyncAgentSession(_spec(), adapter)
    await session.__aenter__()
    with pytest.raises(CleanupError):
        await session.aclose()
    assert session._closed is False
    assert session.result.error is not None
    assert session.result.error.code is ErrorCode.CLEANUP_FAILED
    await session.aclose()
    assert session._closed is True
    assert adapter.closed == 2


@pytest.mark.asyncio
async def test_permanent_cleanup_failure_remains_retryable_and_terminal() -> None:
    adapter = TransientCloseHarness(failures=10)
    session = AsyncAgentSession(_spec(), adapter)
    await session.__aenter__()
    for _ in range(2):
        with pytest.raises(CleanupError):
            await session.aclose()
    assert session._closed is False
    assert session.result.snapshot.outcome is ExecutionOutcome.FAILED
    assert adapter.closed == 2


@pytest.mark.asyncio
async def test_concurrent_close_attempts_are_serialized_and_retry_cleanup() -> None:
    adapter = TransientCloseHarness()
    session = AsyncAgentSession(_spec(), adapter)
    await session.__aenter__()
    results = await asyncio.gather(
        session.aclose(), session.aclose(), return_exceptions=True
    )
    assert sum(isinstance(item, CleanupError) for item in results) == 1
    assert session._closed is True
    assert adapter.closed == 2


def test_sync_kit_close_retries_a_failed_session_cleanup() -> None:
    adapter = TransientCloseHarness()
    kit = MCPTestKit(env={}, cwd="/tmp/m3-no-project")
    session = kit.agent_session(_spec(), adapter=adapter)
    session.__enter__()
    with pytest.raises(CleanupError):
        kit.close()
    assert session._closed is False
    kit.close()
    assert session._closed is True
    assert session.result.error is not None
    assert adapter.closed == 2


@pytest.mark.asyncio
async def test_async_kit_close_retries_a_failed_session_cleanup() -> None:
    adapter = TransientCloseHarness()
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project")
    session = kit.agent_session(_spec(), adapter=adapter)
    await session.__aenter__()
    with pytest.raises(CleanupError):
        await kit.aclose()
    assert session._closed is False
    await kit.aclose()
    assert session._closed is True
    assert adapter.closed == 2


def test_concurrent_sync_kit_close_has_one_retryable_failure() -> None:
    adapter = TransientCloseHarness()
    kit = MCPTestKit(env={}, cwd="/tmp/m3-no-project")
    session = kit.agent_session(_spec(), adapter=adapter)
    session.__enter__()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _value: _close_kit(kit), (0, 1)))
    assert all(isinstance(item, CleanupError) for item in results)
    kit.close()
    assert session._closed is True
    assert adapter.closed == 2


def _close_kit(kit: MCPTestKit) -> BaseException | None:
    try:
        kit.close()
    except BaseException as exc:
        return exc
    return None


@pytest.mark.asyncio
async def test_close_during_startup_prevents_late_activation_and_closes_once() -> None:
    adapter = StartupBarrierHarness()
    session = AsyncAgentSession(_spec(), adapter)
    entering = asyncio.create_task(session.__aenter__())
    await adapter.start_called.wait()
    closing = asyncio.create_task(session.aclose())
    await asyncio.sleep(0)
    adapter.release_start.set()
    entered_result, close_result = await asyncio.gather(
        entering, closing, return_exceptions=True
    )
    assert isinstance(entered_result, Exception)
    assert close_result is None
    assert adapter.started == 1
    assert adapter.closed == 1
    assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED


@pytest.mark.asyncio
async def test_preflight_failure_retains_workspace_owner_for_cleanup_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PreflightFailureHarness()
    session = AsyncAgentSession(_spec(), adapter)
    original_cleanup = session._workspace.cleanup
    attempts = 0

    def transient_cleanup() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("workspace-startup-secret-must-not-escape")
        original_cleanup()

    monkeypatch.setattr(session._workspace, "cleanup", transient_cleanup)
    with pytest.raises(UnsupportedFeature) as exc_info:
        await session.__aenter__()
    assert "workspace-startup-secret" not in repr(exc_info.value)
    assert adapter.workspace_root is not None and adapter.workspace_root.exists()
    assert session._closed is False
    assert session.result.error is not None
    assert session.result.error.details.get("cleanup") == "failed"

    await session.aclose()
    assert session._closed is True
    assert not adapter.workspace_root.exists()
    assert attempts == 2


@pytest.mark.asyncio
async def test_permanent_preflight_cleanup_failure_is_retryable_and_kit_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PreflightFailureHarness()
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project")
    session = kit.agent_session(_spec(), adapter=adapter)
    original_cleanup = WorkspaceManager.cleanup

    def permanent_cleanup(_manager: WorkspaceManager) -> None:
        raise RuntimeError("workspace-startup-secret-must-not-escape")

    monkeypatch.setattr(WorkspaceManager, "cleanup", permanent_cleanup)
    with pytest.raises(UnsupportedFeature):
        await session.__aenter__()
    assert adapter.workspace_root is not None and adapter.workspace_root.exists()
    with pytest.raises(CleanupError):
        await kit.aclose()
    assert session._closed is False
    assert adapter.workspace_root.exists()

    monkeypatch.setattr(WorkspaceManager, "cleanup", original_cleanup)
    await kit.aclose()
    assert session._closed is True
    assert not adapter.workspace_root.exists()


def test_sync_kit_retries_cleanup_after_preflight_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PreflightFailureHarness()
    kit = MCPTestKit(env={}, cwd="/tmp/m3-no-project")
    session = kit.agent_session(_spec(), adapter=adapter)
    original_cleanup = WorkspaceManager.cleanup
    attempts = 0

    def transient_cleanup(manager: WorkspaceManager) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("workspace-startup-secret-must-not-escape")
        original_cleanup(manager)

    monkeypatch.setattr(WorkspaceManager, "cleanup", transient_cleanup)
    with pytest.raises(UnsupportedFeature):
        session.__enter__()
    assert adapter.workspace_root is not None and adapter.workspace_root.exists()
    kit.close()
    assert session._closed is True
    assert not adapter.workspace_root.exists()
    assert attempts == 2


@pytest.mark.asyncio
async def test_cancelled_startup_terminalizes_and_cleans_once() -> None:
    adapter = StartupBarrierHarness()
    session = AsyncAgentSession(_spec(), adapter)
    entering = asyncio.create_task(session.__aenter__())
    await adapter.start_called.wait()
    entering.cancel()
    with pytest.raises(asyncio.CancelledError):
        await entering
    assert adapter.closed == 1
    assert session.result.snapshot.outcome is ExecutionOutcome.CANCELLED
    await session.aclose()
    assert adapter.closed == 1


@pytest.mark.asyncio
async def test_cancelled_startup_with_cleanup_failure_retries_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = StartupBarrierHarness()
    session = AsyncAgentSession(_spec(), adapter)
    original_cleanup = session._workspace.cleanup
    attempts = 0

    def transient_cleanup() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("workspace-startup-secret-must-not-escape")
        original_cleanup()

    monkeypatch.setattr(session._workspace, "cleanup", transient_cleanup)
    entering = asyncio.create_task(session.__aenter__())
    await adapter.start_called.wait()
    entering.cancel()
    with pytest.raises(asyncio.CancelledError):
        await entering
    assert session._closed is False
    assert session.result.snapshot.outcome is ExecutionOutcome.CANCELLED
    await session.aclose()
    assert session._closed is True
    assert attempts == 2


@pytest.mark.asyncio
async def test_base_exception_startup_terminalizes_before_reraising() -> None:
    adapter = FatalStartupHarness()
    session = AsyncAgentSession(_spec(), adapter)
    with pytest.raises(FatalStartup):
        await session.__aenter__()
    assert adapter.closed == 1
    assert session.result.snapshot.outcome is ExecutionOutcome.FAILED


@pytest.mark.asyncio
async def test_cancellation_of_close_still_finishes_cleanup_and_terminal_state() -> (
    None
):
    adapter = CloseBarrierHarness()
    session = AsyncAgentSession(_spec(), adapter)
    await session.__aenter__()
    closing = asyncio.create_task(session.aclose())
    await adapter.close_called.wait()
    closing.cancel()
    adapter.release_close.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert adapter.closed == 1
    assert session._closed is True
    assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED


@pytest.mark.asyncio
async def test_hostile_content_capability_failure_is_value_free() -> None:
    class Hostile(FakeHarness):
        def supports_content(self, block):
            raise RuntimeError("content-secret-must-not-escape")

    async with AsyncAgentSession(_spec(), Hostile()) as session:
        with pytest.raises(UnsupportedFeature) as exc_info:
            await session.send("content")
    assert "content-secret" not in repr(exc_info.value)


@pytest.mark.asyncio
async def test_session_configuration_is_read_only_and_timeout_is_validated_preflight() -> (
    None
):
    adapter = FakeHarness()
    session = AsyncAgentSession(_spec(), adapter)
    with pytest.raises(AttributeError):
        session.spec = _spec()  # type: ignore[misc]
    async with session:
        with pytest.raises(ValueError, match="timeout"):
            await session.send("bad", timeout=0)
        assert adapter.messages == []


def test_sync_session_proxy_keeps_async_adapter_off_caller_thread(tmp_path) -> None:
    adapter = FakeHarness()
    with MCPTestKit(env={}, cwd=tmp_path) as kit:
        with kit.agent_session(_spec(), adapter=adapter) as session:
            result = session.send("sync")
            assert result.response is not None and result.response.text == "sync"
            assert session.snapshot().lifecycle.value == "idle"
            queued = session.enqueue_turn("queued")
            queued_result = queued.result()
            assert (
                queued_result.response is not None
                and queued_result.response.text == "queued"
            )
        assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert adapter.messages == ["sync", "queued"]


def test_sync_session_fork_keeps_fresh_adapter_and_provenance(tmp_path) -> None:
    source_adapter = FakeHarness()
    request = SessionForkRequest(
        mode="fork", replay_inputs=(UserMessage(content=(TextContent(text="child"),)),)
    )
    with MCPTestKit(env={}, cwd=tmp_path) as kit:
        with kit.agent_session(_spec(), adapter=source_adapter) as source:
            source.send("source")
        child_adapter = FakeHarness()
        child = source.fork(
            request, adapter_factory=lambda spec, provenance: child_adapter
        )
        with child:
            child_result = child.send("child")
        assert child_result.response is not None
        assert child.provenance is not None
        assert (
            child.provenance.source_execution_id == source.result.snapshot.execution_id
        )
    assert child_adapter.started == 1
    assert child_adapter.closed == 1
