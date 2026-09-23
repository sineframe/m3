"""Managed runtime lifecycle integration at the SDK session boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

import pytest

from m3._types.specs import AgentSpec
from m3.agent_session import AsyncAgentSession
from m3.errors import TransportError
from m3.types import (
    ClaudeCode,
    EventKind,
    Readiness,
    ServerBinding,
    SessionForkRequest,
    StdioServer,
    UserMessage,
)


def _spec(*, runtime: str | None, version: str | None = None) -> AgentSpec:
    return AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="fixture", command="echo")),),
        harness=ClaudeCode(model="fixture", runtime=runtime, version=version),
    )


def _empty_spec(*, runtime: str = "managed") -> AgentSpec:
    return AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="fixture", command="echo")),),
        harness=ClaudeCode(model="fixture", runtime=runtime),
    )


class Lease:
    executable = Path.cwd() / "managed-claude"
    provenance: ClassVar[dict[str, str]] = {
        "version": "1.2.3",
        "target": "darwin-arm64",
        "sha256": "a" * 64,
    }

    def __init__(self) -> None:
        self.released = 0
        self.release_event = asyncio.Event()

    async def release(self) -> None:
        self.released += 1
        self.release_event.set()


class Manager:
    def __init__(
        self, lease: Lease | None = None, timeline: list[str] | None = None
    ) -> None:
        self.lease = lease or Lease()
        self.calls: list[tuple[str, str]] = []
        self.started = asyncio.Event()
        self.timeline = timeline

    async def acquire(self, kind: str, selector: str) -> Lease:
        if self.timeline is not None:
            self.timeline.append("acquire")
        self.calls.append((kind, selector))
        self.started.set()
        return self.lease


class Adapter:
    managed_runtime_supported = True
    executable = "claude"

    def __init__(self, order: list[str], *, fail_open: bool = False) -> None:
        self.order = order
        self.fail_open = fail_open
        self.opened = False

    async def preflight(self, launch: Any) -> Readiness:
        self.order.append("preflight")
        return Readiness(ready=True)

    async def open(self, launch: Any) -> Any:
        self.order.append("open")
        if self.fail_open:
            raise RuntimeError("provider failed")
        self.opened = True
        return object()

    async def close(self) -> None:
        self.order.append("close")


@pytest.mark.asyncio
async def test_managed_acquire_precedes_preflight_and_open_and_emits_resolution() -> (
    None
):
    order: list[str] = []
    manager = Manager(timeline=order)
    adapter = Adapter(order)
    events: list[tuple[EventKind, dict[str, Any]]] = []
    session = AsyncAgentSession(
        _spec(runtime="managed", version="1.2.3"),
        adapter,
        runtime_manager=manager,
        event_sink=lambda kind, payload, *_: (
            events.append((kind, dict(payload))),
            order.append(kind.value),
        ),
    )
    await session.__aenter__()
    assert manager.calls == [("claude", "1.2.3")]
    assert adapter.executable == str(manager.lease.executable)
    assert [kind for kind, _ in events if kind is EventKind.HARNESS_RUNTIME_RESOLVED]
    resolved = next(
        payload
        for kind, payload in events
        if kind is EventKind.HARNESS_RUNTIME_RESOLVED
    )
    assert resolved["verification_method"] is None
    assert (
        order.index("acquire")
        < order.index(EventKind.HARNESS_RUNTIME_RESOLVED.value)
        < order.index("open")
    )
    await session.aclose()
    assert manager.lease.released == 1


@pytest.mark.asyncio
async def test_direct_result_snapshot_contains_managed_runtime_identity() -> None:
    session = AsyncAgentSession(
        _spec(runtime="managed", version="1.2.3"),
        Adapter([]),
        runtime_manager=Manager(),
    )

    async with session:
        pass

    identity = session.result.snapshot.agent
    assert identity is not None
    assert identity == session.result.trace_view.agent
    assert identity.harness.resolved_version == "1.2.3"
    assert identity.harness.digest == "a" * 64


@pytest.mark.asyncio
async def test_system_runtime_never_calls_manager_and_startup_failure_releases_lease() -> (
    None
):
    manager = Manager()
    adapter = Adapter([], fail_open=True)
    session = AsyncAgentSession(
        _spec(runtime="system"), adapter, runtime_manager=manager
    )
    with pytest.raises(TransportError):
        await session.__aenter__()
    assert manager.calls == []
    assert manager.lease.released == 0
    assert (await session.snapshot()).execution_id


@pytest.mark.asyncio
async def test_unversioned_managed_runtime_uses_latest_selector() -> None:
    manager = Manager()
    session = AsyncAgentSession(
        _spec(runtime="managed"), Adapter([]), runtime_manager=manager
    )
    await session.__aenter__()
    assert manager.calls == [("claude", "latest")]
    await session.aclose()


@pytest.mark.asyncio
async def test_explicit_latest_managed_runtime_is_preserved() -> None:
    manager = Manager()
    session = AsyncAgentSession(
        _spec(runtime="managed", version="latest"), Adapter([]), runtime_manager=manager
    )
    await session.__aenter__()
    assert manager.calls == [("claude", "latest")]
    await session.aclose()


@pytest.mark.asyncio
async def test_each_managed_execution_gets_distinct_writable_workspace_and_cleanup() -> (
    None
):
    class WorkspaceAdapter(Adapter):
        def __init__(self) -> None:
            super().__init__([])
            self.root: Path | None = None

        async def open(self, launch: Any) -> Any:
            self.root = Path(launch.workspace_root)
            assert self.root.is_dir()
            probe = self.root / "credential-scratch"
            probe.write_text("isolated", encoding="utf-8")
            return await super().open(launch)

    first_adapter = WorkspaceAdapter()
    second_adapter = WorkspaceAdapter()
    first = AsyncAgentSession(
        _spec(runtime="managed", version="1.2.3"),
        first_adapter,
        runtime_manager=Manager(),
    )
    second = AsyncAgentSession(
        _spec(runtime="managed", version="1.2.3"),
        second_adapter,
        runtime_manager=Manager(),
    )
    await first.__aenter__()
    await second.__aenter__()
    assert first_adapter.root is not None and second_adapter.root is not None
    assert first_adapter.root != second_adapter.root
    assert (first_adapter.root / "credential-scratch").read_text(
        encoding="utf-8"
    ) == "isolated"
    await first.aclose()
    await second.aclose()
    assert not first_adapter.root.exists()
    assert not second_adapter.root.exists()


@pytest.mark.asyncio
async def test_managed_open_failure_releases_lease_and_keeps_selector_event() -> None:
    manager = Manager()
    events: list[tuple[EventKind, dict[str, Any]]] = []
    session = AsyncAgentSession(
        _spec(runtime="managed", version="1.2.3"),
        Adapter([], fail_open=True),
        runtime_manager=manager,
        event_sink=lambda kind, payload, *_: events.append((kind, dict(payload))),
    )
    with pytest.raises(TransportError):
        await session.__aenter__()
    assert manager.lease.released == 1
    selection = next(
        payload for kind, payload in events if kind is EventKind.HARNESS_SELECTION
    )
    assert selection["harness"]["requested_selector"] == "1.2.3"
    assert any(kind is EventKind.HARNESS_RUNTIME_RESOLVED for kind, _ in events)


@pytest.mark.asyncio
async def test_cancelled_managed_acquire_is_cleaned_up() -> None:
    class BlockingManager(Manager):
        async def acquire(self, kind: str, selector: str) -> Lease:
            self.calls.append((kind, selector))
            self.started.set()
            await asyncio.Future()

    manager = BlockingManager()
    session = AsyncAgentSession(
        _spec(runtime="managed", version="1.2.3"), Adapter([]), runtime_manager=manager
    )
    task = asyncio.create_task(session.__aenter__())
    await manager.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await session.aclose()
    assert manager.calls == [("claude", "1.2.3")]


@pytest.mark.asyncio
async def test_cancelled_delayed_acquisition_releases_late_lease_once() -> None:
    class DelayedManager(Manager):
        async def acquire(self, kind: str, selector: str) -> Lease:
            self.calls.append((kind, selector))
            self.started.set()
            await asyncio.sleep(0.02)
            return self.lease

    manager = DelayedManager()
    session = AsyncAgentSession(
        _spec(runtime="managed", version="1.2.3"), Adapter([]), runtime_manager=manager
    )
    task = asyncio.create_task(session.__aenter__())
    await asyncio.wait_for(manager.started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(manager.lease.release_event.wait(), timeout=1)
    assert manager.lease.released == 1
    await session.aclose()
    assert manager.lease.released == 1


def test_runtime_fields_reject_system_version() -> None:
    with pytest.raises(ValueError, match="requires managed"):
        ClaudeCode(model="fixture", runtime="system", version="1.2.3")


@pytest.mark.parametrize(
    "version",
    [
        "../1.2.3",
        "/tmp/1.2.3",
        "1.2.3/evil",
        "01.2.3",
        "1.2.3..4",
        "1.2.3\n",
        "1.2.3-" + "a" * 60,
    ],
)
def test_hostile_runtime_versions_are_rejected(version: str) -> None:
    with pytest.raises(ValueError):
        ClaudeCode(model="fixture", runtime="managed", version=version)


def test_managed_fields_round_trip_without_none_noise() -> None:
    value = ClaudeCode(model="fixture", runtime="managed", version="1.2.3")
    dumped = value.model_dump(mode="json")
    assert dumped["runtime"] == "managed"
    assert dumped["version"] == "1.2.3"
    assert dumped["executable"] is None
    assert "runtime" not in ClaudeCode(model="fixture").model_dump(mode="json")


def test_system_runtime_nested_serialization_matches_implicit_default() -> None:
    implicit_spec = _spec(runtime=None)
    explicit_spec = _spec(runtime="system")
    implicit = implicit_spec.model_dump(mode="json")
    explicit = explicit_spec.model_dump(mode="json")

    assert implicit == explicit
    assert "runtime" not in implicit["harness"]
    assert "version" not in implicit["harness"]
    assert implicit_spec.harness.runtime == "system"
    assert explicit_spec == AgentSpec.model_validate(explicit)
    assert explicit_spec.harness.with_model("other").runtime == "system"


@pytest.mark.asyncio
async def test_managed_fork_preserves_runtime_configuration(tmp_path: Path) -> None:
    manager = Manager()
    invocation = tmp_path / "invocation"
    project = tmp_path / "project"
    source = AsyncAgentSession(
        _spec(runtime="managed", version="1.2.3"),
        Adapter([]),
        harness_cache_dir=str(tmp_path / "cache"),
        runtime_manager=manager,
        runtime_invocation_dir=invocation,
        runtime_project_root=project,
    )
    async with source:
        pass

    child = await source.fork(
        SessionForkRequest(mode="fork", replay_inputs=(UserMessage(content="child"),)),
        adapter_factory=lambda *_args: Adapter([]),
    )

    assert child._harness_cache_dir == str(tmp_path / "cache")
    assert child._runtime_manager is manager
    assert child._runtime_invocation_dir == invocation
    assert child._runtime_project_root == project.resolve()


def test_kit_agents_expands_multiple_managed_versions_without_io(
    tmp_path: Path,
) -> None:
    from m3.async_api import AsyncMCPTestKit

    kit = AsyncMCPTestKit(harness_cache_dir=tmp_path / "cache")
    agents = kit.agents(
        [
            {
                "harness": "opencode",
                "runtime": "managed",
                "version": "1.2.3",
                "models": ["m"],
            },
            {
                "harness": "opencode",
                "runtime": "managed",
                "version": "1.2.4",
                "models": ["m"],
            },
        ]
    )
    assert [agent.entry["version"] for agent in agents] == ["1.2.3", "1.2.4"]
    assert not (tmp_path / "cache").exists()
    # Selection expansion is pure; it does not acquire the runtime.
    import asyncio

    asyncio.run(kit.aclose())


@pytest.mark.asyncio
async def test_system_runtime_preserves_explicit_executable() -> None:
    manager = Manager()
    adapter = Adapter([])
    adapter.executable = "system-cli"
    session = AsyncAgentSession(
        _spec(runtime="system"), adapter, runtime_manager=manager
    )
    await session.__aenter__()
    assert adapter.executable == "system-cli"
    assert manager.calls == []
    await session.aclose()


@pytest.mark.asyncio
async def test_managed_custom_adapter_without_executable_is_rejected() -> None:
    class NoExecutableAdapter:
        managed_runtime_supported = True

    session = AsyncAgentSession(
        _spec(runtime="managed", version="1.2.3"),
        NoExecutableAdapter(),
        runtime_manager=Manager(),
    )
    with pytest.raises(Exception, match="session startup unsupported"):
        await session.__aenter__()


def test_public_constructor_cache_directory_parity_and_override(tmp_path: Path) -> None:
    from m3.async_api import AsyncMCPTestKit
    from m3.sync_api import MCPTestKit

    kit_cache = tmp_path / "kit-cache"
    assert AsyncMCPTestKit(harness_cache_dir=kit_cache)._harness_cache_dir == str(
        kit_cache.resolve()
    )
    assert MCPTestKit(harness_cache_dir=kit_cache)._harness_cache_dir == str(
        kit_cache.resolve()
    )


@pytest.mark.asyncio
async def test_public_async_session_override_reaches_runtime_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import m3.runtime as runtime_module
    from m3.async_api import AsyncMCPTestKit

    seen: list[Path | None] = []
    invocations: list[Path | None] = []
    project_roots: list[Path] = []
    lease = Lease()

    class PublicManager(Manager):
        def __init__(self, cache_root=None, **kwargs):
            super().__init__(lease)
            seen.append(None if cache_root is None else Path(cache_root))
            invocations.append(
                None
                if kwargs.get("invocation_dir") is None
                else Path(kwargs["invocation_dir"])
            )
            project_roots.append(Path(kwargs["project_root"]))

    monkeypatch.setattr(runtime_module, "RuntimeManager", PublicManager)
    project = tmp_path / "project"
    project.mkdir()
    kit = AsyncMCPTestKit(cwd=project, harness_cache_dir=tmp_path / "kit")
    session = kit.agent_session(
        _empty_spec(), adapter=Adapter([]), harness_cache_dir=tmp_path / "session"
    )
    async with session:
        pass
    session2 = kit.agent_session(_empty_spec(), adapter=Adapter([]))
    async with session2:
        pass
    await kit.aclose()
    assert seen == [(tmp_path / "session").resolve(), (tmp_path / "kit").resolve()]
    assert project_roots == [project.resolve(), project.resolve()]
    assert (
        len(invocations) == 2
        and invocations[0] is not None
        and invocations[0] == invocations[1]
    )
    assert not invocations[0].exists()


@pytest.mark.asyncio
async def test_sdk_rejects_managed_cache_inside_source_project(tmp_path: Path) -> None:
    from m3.async_api import AsyncMCPTestKit

    project = tmp_path / "project"
    project.mkdir()
    kit = AsyncMCPTestKit(cwd=project, harness_cache_dir=project / "cache")
    session = kit.agent_session(_empty_spec(), adapter=Adapter([]))
    with pytest.raises(Exception, match="agent session startup failed"):
        async with session:
            pass
    assert not (project / "cache").exists()
    await kit.aclose()


@pytest.mark.asyncio
async def test_latest_pin_is_shared_across_managers_and_refreshes_for_new_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from m3.runtime import RuntimeManager

    invocation = tmp_path / "invocation"
    calls = 0

    async def fake_fetch(
        self: Any, kind: str, url: str, version: str
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"version": f"1.2.{calls}", "sha256": "a" * 64}

    monkeypatch.setattr(RuntimeManager, "_fetch_claude_or_json", fake_fetch)
    first = RuntimeManager(
        cache_root=tmp_path / "cache",
        project_root=tmp_path / "project",
        invocation_dir=invocation,
    )
    one = await first._resolve_manifest(
        "opencode", "https://example.test/manifest", "latest", "darwin-arm64"
    )
    second = RuntimeManager(
        cache_root=tmp_path / "cache",
        project_root=tmp_path / "project",
        invocation_dir=invocation,
    )
    two = await second._resolve_manifest(
        "opencode", "https://example.test/manifest", "latest", "darwin-arm64"
    )
    assert calls == 1
    assert one["version"] == two["version"] == "1.2.1"
    fresh = RuntimeManager(
        cache_root=tmp_path / "cache",
        project_root=tmp_path / "project",
        invocation_dir=tmp_path / "new-invocation",
    )
    three = await fresh._resolve_manifest(
        "opencode", "https://example.test/manifest", "latest", "darwin-arm64"
    )
    assert calls == 2
    assert three["version"] == "1.2.2"


def test_public_sync_constructor_uses_same_cache_path(tmp_path: Path) -> None:
    from m3.sync_api import MCPTestKit

    kit = MCPTestKit(harness_cache_dir=tmp_path / "cache")
    assert kit._harness_cache_dir == str((tmp_path / "cache").resolve())
    kit.close()
