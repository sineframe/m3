from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import MutableMapping
from pathlib import Path

import pytest

from m3._types.specs import AgentSpec
from m3.async_api import AsyncMCPTestKit
from m3.harness import DeterministicHarnessAdapter, HarnessTurnRequest
from m3.interaction_handlers import (
    AllowedCommands,
    FilesystemRequest,
    InteractionHandlers,
    Interactions,
    PermissionRequest,
    SamplingRequest,
    TerminalRequest,
    WorkspaceFiles,
)
from m3.types import (
    ACPAgent,
    FilesystemPolicy,
    PermissionPolicy,
    SamplingPolicy,
    ServerBinding,
    StdioServer,
    TerminalPolicy,
)


def _spec() -> AgentSpec:
    return AgentSpec(
        harness=ACPAgent(model="interaction-test"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
        permission_policy=PermissionPolicy(mode="prompt"),
        sampling_policy=SamplingPolicy(mode="allow"),
        filesystem_policy=FilesystemPolicy(mode="read_write"),
        terminal_policy=TerminalPolicy(mode="allow"),
    )


@pytest.mark.asyncio
async def test_default_deny_is_typed_and_receipted() -> None:
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        session = kit.agent_session(_spec(), adapter=DeterministicHarnessAdapter())
        permission = await session.interactions.permission(
            PermissionRequest("write", "secret")
        )
        sampling = await session.interactions.sample(SamplingRequest("prompt"))
        filesystem = await session.interactions.filesystem(
            FilesystemRequest("read", "/tmp/no")
        )
        terminal = await session.interactions.terminal(
            TerminalRequest((sys.executable, "-c", "print(1)"))
        )

    assert permission.allowed is False
    assert sampling.accepted is False
    assert filesystem.allowed is False
    assert terminal.allowed is False
    assert all(item.decision == "deny" for item in session.interactions.receipts())
    assert "secret" not in repr(session.interactions.receipts())


@pytest.mark.asyncio
async def test_explicit_handlers_enforce_policy_and_record_safe_receipts() -> None:
    async def permission(_request: PermissionRequest) -> bool:
        return True

    async def sampling(_request: SamplingRequest) -> str:
        return "active-process-sample"

    handlers = InteractionHandlers(permission=permission, sampling=sampling)
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        session = kit.agent_session(
            _spec(),
            adapter=DeterministicHarnessAdapter(),
            interaction_handlers=handlers,
        )
        permission_result = await session.interactions.permission(
            PermissionRequest("write", "private", destructive=True)
        )
        sampling_result = await session.interactions.sample(SamplingRequest("prompt"))

    assert permission_result.allowed is True
    assert permission_result.confirmation_required is True
    assert sampling_result.content == "active-process-sample"
    assert "active-process" not in repr(session.interactions.receipts())


@pytest.mark.asyncio
async def test_workspace_filesystem_handler_contains_paths_and_honors_read_only(
    tmp_path: Path,
) -> None:
    (tmp_path / "inside.txt").write_bytes(b"inside")
    readonly = WorkspaceFiles(tmp_path)
    read = await readonly(FilesystemRequest("read", "inside.txt"))
    write = await readonly(FilesystemRequest("write", "new.txt", b"no"))
    escape = await readonly(FilesystemRequest("read", "../outside"))
    assert read.allowed is True and read.data == b"inside"
    assert write.allowed is False
    assert escape.allowed is False

    writable = WorkspaceFiles(tmp_path, mode="read_write")
    created = await writable(FilesystemRequest("write", "new.txt", b"yes"))
    assert created.allowed is True
    assert (tmp_path / "new.txt").read_bytes() == b"yes"


@pytest.mark.asyncio
async def test_concurrent_workspace_handlers_remain_isolated(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = WorkspaceFiles(first_root, mode="read_write")
    second = WorkspaceFiles(second_root, mode="read_write")
    await asyncio.gather(
        first(FilesystemRequest("write", "value.txt", b"first")),
        second(FilesystemRequest("write", "value.txt", b"second")),
    )
    assert (first_root / "value.txt").read_bytes() == b"first"
    assert (second_root / "value.txt").read_bytes() == b"second"


@pytest.mark.asyncio
async def test_terminal_handler_is_argv_only_allowlisted_and_bounded(
    tmp_path: Path,
) -> None:
    handler = AllowedCommands(allowed_executables=(sys.executable,), root=tmp_path)
    result = await handler(TerminalRequest((sys.executable, "-c", "print('ok')")))
    denied = await handler(TerminalRequest(("/bin/sh", "-c", "echo unsafe")))
    timeout = await handler(
        TerminalRequest(
            (sys.executable, "-c", "import time; time.sleep(2)"), timeout_seconds=0.01
        )
    )
    bounded = await handler(
        TerminalRequest(
            (sys.executable, "-c", "print('x' * 10000)"), max_output_bytes=32
        )
    )
    assert result.allowed is True and result.stdout.strip() == b"ok"
    assert denied.allowed is False
    assert timeout.allowed is True and timeout.timed_out is True
    assert (
        bounded.allowed is True
        and bounded.truncated is True
        and len(bounded.stdout) <= 32
    )


@pytest.mark.asyncio
async def test_handler_receipts_are_race_safe_and_launch_is_wired() -> None:
    adapter = DeterministicHarnessAdapter()

    async def permission(_request: PermissionRequest) -> bool:
        await asyncio.sleep(0)
        return True

    handlers = InteractionHandlers(permission=permission)
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        session = kit.agent_session(
            _spec(), adapter=adapter, interaction_handlers=handlers
        )
        results = await asyncio.gather(
            *(
                session.interactions.permission(PermissionRequest("read"))
                for _ in range(32)
            )
        )
        async with session:
            pass
    assert all(result.allowed for result in results)
    assert len(session.interactions.receipts()) == 32
    assert adapter.last_launch is not None
    assert adapter.last_launch.interactions is session.interactions


@pytest.mark.asyncio
async def test_deterministic_adapter_handler_receives_policy_controller() -> None:
    seen: dict[str, object] = {}

    async def turn(
        _request: HarnessTurnRequest, state: MutableMapping[str, object]
    ) -> str:
        seen["interactions"] = state["interactions"]
        return "ok"

    adapter = DeterministicHarnessAdapter(handler=turn)
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        session = kit.agent_session(_spec(), adapter=adapter)
        async with session:
            result = await session.send("run")
    assert result.response is not None and result.response.text == "ok"
    assert seen["interactions"] is session.interactions


@pytest.mark.asyncio
async def test_handler_cancellation_is_not_converted_to_a_deny() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def permission(_request: PermissionRequest) -> bool:
        started.set()
        await release.wait()
        return True

    controller = InteractionHandlers(permission=permission)
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        session = kit.agent_session(
            _spec(),
            adapter=DeterministicHarnessAdapter(),
            interaction_handlers=controller,
        )
        request = asyncio.create_task(
            session.interactions.permission(PermissionRequest("wait"))
        )
        await started.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request


@pytest.mark.asyncio
async def test_interaction_configuration_and_requests_are_immutable() -> None:
    controller = Interactions(
        permission_policy=PermissionPolicy(mode="prompt"),
    )
    with pytest.raises(AttributeError):
        controller.permission_policy = PermissionPolicy(mode="allow")  # type: ignore[misc]


@pytest.mark.asyncio
async def test_workspace_rejects_symlink_hardlink_and_special_file(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_bytes(b"secret")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside)
    (root / "hardlink").hardlink_to(outside)
    fifo = root / "pipe"
    os.mkfifo(fifo)
    handler = WorkspaceFiles(root, mode="read_write")

    for path in ("link", "hardlink", "pipe"):
        result = await asyncio.wait_for(handler(FilesystemRequest("read", path)), 1)
        assert result.allowed is False
    escaped = await handler(FilesystemRequest("write", "link", b"overwrite"))
    assert escaped.allowed is False
    assert outside.read_bytes() == b"secret"


@pytest.mark.asyncio
async def test_terminal_resolves_allowlisted_executables_and_does_not_inherit_environment(
    tmp_path: Path,
) -> None:
    handler = AllowedCommands(allowed_executables=(sys.executable,), root=tmp_path)
    result = await handler(
        TerminalRequest(
            (
                sys.executable,
                "-c",
                "import os; print(os.environ.get('M3_CANARY', 'missing'))",
            ),
        )
    )
    substituted = await handler(
        TerminalRequest((str(tmp_path / Path(sys.executable).name), "-c", "print(1)"))
    )
    assert result.allowed is True and result.stdout.strip() == b"missing"
    assert substituted.allowed is False


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_terminal_timeout_kills_process_group_and_descendants(
    tmp_path: Path,
) -> None:
    script = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(5)']); "
        "time.sleep(5)"
    )
    handler = AllowedCommands(allowed_executables=(sys.executable,), root=tmp_path)
    result = await handler(
        TerminalRequest((sys.executable, "-c", script), timeout_seconds=0.1)
    )
    assert result.timed_out is True
    assert result.returncode is not None
