from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from m3._types.specs import AgentSpec
from m3.async_api import AsyncMCPTestKit
from m3.harness import DeterministicHarnessAdapter
from m3.types import (
    ACPAgent,
    ArtifactPolicy,
    ExecutionId,
    ExecutionOutcome,
    ServerBinding,
    StdioServer,
    WorkspaceKind,
    WorkspacePolicy,
)
from m3.workspace import WorkspaceError, WorkspaceManager


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "temporary", "sqlite"])
async def test_owned_capture_preserves_artifacts_and_redaction(
    tmp_path: Path, backend: str
) -> None:
    from m3.storage import InMemoryArtifactStore, TemporaryArtifactStore
    from m3.storage.sqlite import SQLiteExecutionStore
    from m3.trace.redaction import RedactionConfig
    from m3.types import ExecutionState

    config = RedactionConfig(
        secrets=frozenset({"private-code-ALPHA"}), include_environment=False
    )
    execution_id = ExecutionId("capture-test")
    persistent = None
    if backend == "sqlite":
        persistent = SQLiteExecutionStore(tmp_path / "results.sqlite", config=config)
        persistent.create(ExecutionState(execution_id=execution_id))
        store = persistent.artifacts
    elif backend == "temporary":
        store = TemporaryArtifactStore(tmp_path / "artifacts", config=config)
    else:
        store = InMemoryArtifactStore(config=config)
    manager = WorkspaceManager(
        WorkspacePolicy(),
        execution_id,
        artifact_policy=ArtifactPolicy.ALWAYS,
        declared_artifacts=("output.txt",),
        artifact_store=store,
    )
    root = await manager.create_async(deadline=time.monotonic() + 5)
    (root / "output.txt").write_text("code: private-code-ALPHA")
    try:
        capture = await manager.capture_async(
            ExecutionOutcome.COMPLETED, deadline=time.monotonic() + 5
        )
        assert [entry.path for entry in capture.diff.added] == ["output.txt"]
        assert len(capture.artifacts) == 1
        assert capture.artifacts[0].execution_id == execution_id
        content = store.get(capture.artifacts[0])
        assert b"private-code-ALPHA" not in content
        assert b"code:" in content
        assert (
            await manager.capture_async(
                ExecutionOutcome.COMPLETED, deadline=time.monotonic() + 5
            )
            is capture
        )
    finally:
        await manager.cleanup_async(deadline=time.monotonic() + 5)
        if persistent is not None:
            persistent.close()


@pytest.mark.asyncio
async def test_owned_capture_streams_artifact_larger_than_private_frame_limit(
    tmp_path: Path,
) -> None:
    from m3.storage import InMemoryArtifactStore
    from m3.trace.redaction import RedactionConfig

    store = InMemoryArtifactStore(config=RedactionConfig(include_environment=False))
    manager = _manager(
        WorkspacePolicy(),
        tmp_path,
        artifact_policy=ArtifactPolicy.ALWAYS,
        declared_artifacts=("large.bin",),
        artifact_store=store,
    )
    root = await manager.create_async(deadline=time.monotonic() + 5)
    content = os.urandom(13 * 1024 * 1024)
    (root / "large.bin").write_bytes(content)
    try:
        capture = await manager.capture_async(
            ExecutionOutcome.COMPLETED, deadline=time.monotonic() + 5
        )
        assert store.get(capture.artifacts[0]) == content
    finally:
        await manager.cleanup_async(deadline=time.monotonic() + 5)


@pytest.mark.asyncio
async def test_owned_capture_rejects_custom_artifact_hooks_before_workspace_creation(
    tmp_path: Path,
) -> None:
    from m3.errors import UnsupportedFeature
    from m3.storage import InMemoryArtifactStore

    class CustomStore(InMemoryArtifactStore):
        def put(self, *args: object, **kwargs: object) -> Any:
            pytest.fail("custom artifact hook was invoked")

    manager = _manager(
        WorkspacePolicy(),
        tmp_path,
        artifact_policy=ArtifactPolicy.ALWAYS,
        declared_artifacts=("output.txt",),
        artifact_store=CustomStore(),
    )
    with pytest.raises(UnsupportedFeature, match="built-in artifact store"):
        await manager.create_async(deadline=time.monotonic() + 5)
    assert manager._root is None


@pytest.mark.asyncio
async def test_owned_capture_retains_launch_secret_after_ambient_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from m3.storage import InMemoryArtifactStore
    from m3.trace.redaction import RedactionConfig

    store = InMemoryArtifactStore(config=RedactionConfig(include_environment=False))
    manager = _manager(
        WorkspacePolicy(),
        tmp_path,
        artifact_policy=ArtifactPolicy.ALWAYS,
        declared_artifacts=("output.txt",),
        artifact_store=store,
    )
    manager.add_artifact_secrets(("pinned-launch-secret",))
    root = await manager.create_async(deadline=time.monotonic() + 5)
    monkeypatch.setenv("API_TOKEN", "rotated-ambient-value")
    (root / "output.txt").write_text("pinned-launch-secret and rotated-ambient-value")
    try:
        capture = await manager.capture_async(
            ExecutionOutcome.COMPLETED, deadline=time.monotonic() + 5
        )
        content = store.get(capture.artifacts[0])
        assert b"pinned-launch-secret" not in content
        assert b"rotated-ambient-value" in content
    finally:
        await manager.cleanup_async(deadline=time.monotonic() + 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_blocked_capture_returns_on_deadline_without_publishing_artifacts(
    tmp_path: Path, blocked_helper: Any, cancel: bool
) -> None:
    from m3._bootstrap_operations import PrivateOperationError
    from m3.storage import InMemoryArtifactStore

    _, _, marker, _, _ = blocked_helper("workspace_capture")
    store = InMemoryArtifactStore()
    manager = _manager(
        WorkspacePolicy(),
        tmp_path,
        artifact_policy=ArtifactPolicy.ALWAYS,
        declared_artifacts=("output.txt",),
        artifact_store=store,
    )
    root = manager.create()
    (root / "output.txt").write_text("unaccepted")
    started = time.monotonic()
    pending = asyncio.create_task(
        manager.capture_async(
            ExecutionOutcome.COMPLETED, deadline=started + (5 if cancel else 0.5)
        )
    )
    if cancel:

        async def wait_until_blocked() -> None:
            while not marker.exists():
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_until_blocked(), timeout=1)
        pending.cancel()
    with pytest.raises(
        (asyncio.TimeoutError, PrivateOperationError, asyncio.CancelledError)
    ):
        await pending
    assert time.monotonic() - started < 1.5
    assert marker.exists()
    assert manager.captured is None
    assert tuple(store.iter_refs()) == ()
    assert manager._capture_operations is not None
    await asyncio.to_thread(
        manager._capture_operations.close, deadline=time.monotonic() + 3
    )
    assert manager._capture_operations.cleanup_complete
    assert tuple(store.iter_refs()) == ()
    await manager.cleanup_async(deadline=time.monotonic() + 5)
    assert not root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", [WorkspaceKind.COPY, WorkspaceKind.READ_ONLY, WorkspaceKind.IN_PLACE]
)
async def test_bounded_cleanup_preserves_source_and_removes_only_owned_roots(
    tmp_path: Path, kind: WorkspaceKind
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "input.txt").write_text("source")
    manager = _manager(
        WorkspacePolicy(kind=kind, source=str(source), acknowledge_risk=True), tmp_path
    )
    root = await manager.create_async(deadline=time.monotonic() + 5)
    await manager.cleanup_async(deadline=time.monotonic() + 5)
    assert (source / "input.txt").read_text() == "source"
    assert root.exists() == (kind is WorkspaceKind.IN_PLACE)
    assert manager._preparation_root is not None
    assert not manager._preparation_root.exists()


@pytest.mark.asyncio
async def test_bounded_cleanup_rejects_replaced_root(tmp_path: Path) -> None:
    from m3._bootstrap_operations import PrivateOperationError

    manager = _manager(WorkspacePolicy(), tmp_path)
    root = await manager.create_async(deadline=time.monotonic() + 5)
    original = root.with_name("original")
    root.rename(original)
    root.mkdir()
    (root / "must-survive.txt").write_text("replacement")
    try:
        with pytest.raises(PrivateOperationError):
            await manager.cleanup_async(deadline=time.monotonic() + 5)
        assert (root / "must-survive.txt").read_text() == "replacement"
        with pytest.raises(WorkspaceError, match="quarantined"):
            manager.cleanup()
    finally:
        root.joinpath("must-survive.txt").unlink()
        root.rmdir()
        original.rmdir()
        assert manager._preparation_root is not None
        manager._preparation_root.rmdir()


@pytest.mark.asyncio
async def test_expired_cleanup_never_removes_workspace(tmp_path: Path) -> None:
    manager = _manager(WorkspacePolicy(), tmp_path)
    root = manager.create()
    with pytest.raises(asyncio.TimeoutError):
        await manager.cleanup_async(deadline=time.monotonic() - 1)
    assert root.exists()
    # The expired call cannot start its helper; fixture disposal is safe.
    root.rmdir()


@pytest.mark.asyncio
async def test_bounded_worktree_cleanup_removes_only_its_registration(
    tmp_path: Path,
) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    (source / "input.txt").write_text("source")
    subprocess.run(["git", "add", "input.txt"], cwd=source, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "initial",
        ],
        cwd=source,
        check=True,
        capture_output=True,
    )
    manager = _manager(
        WorkspacePolicy(kind=WorkspaceKind.GIT_WORKTREE, source=str(source)), tmp_path
    )
    root = await manager.create_async(deadline=time.monotonic() + 5)
    await manager.cleanup_async(deadline=time.monotonic() + 5)
    registered = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert str(root) not in registered
    assert (source / "input.txt").read_text() == "source"
    assert not root.exists()


@pytest.mark.asyncio
async def test_blocked_cleanup_is_fenced_without_late_workspace_deletion(
    tmp_path: Path, blocked_helper: Any
) -> None:
    from m3._bootstrap_operations import PrivateOperationError

    _, _, marker, _, _ = blocked_helper("workspace_remove")
    manager = _manager(WorkspacePolicy(), tmp_path)
    root = manager.create()
    (root / "evidence.txt").write_text("keep")
    started = time.monotonic()
    with pytest.raises((asyncio.TimeoutError, PrivateOperationError)):
        await manager.cleanup_async(deadline=started + 0.5)
    assert time.monotonic() - started < 1.5
    assert marker.exists()
    assert (root / "evidence.txt").read_text() == "keep"
    with pytest.raises(WorkspaceError, match="quarantined"):
        manager.cleanup()
    # The retained owner must acknowledge writer death before fixture disposal.
    assert manager._cleanup_operations is not None
    await asyncio.to_thread(
        manager._cleanup_operations.close, deadline=time.monotonic() + 3
    )
    assert manager._cleanup_operations.cleanup_complete
    (root / "evidence.txt").unlink()
    root.rmdir()


def _manager(
    policy: WorkspacePolicy, tmp_path: Path, **kwargs: object
) -> WorkspaceManager:
    return WorkspaceManager(policy, ExecutionId("execution-test"), **kwargs)  # type: ignore[arg-type]


def test_copy_is_filtered_and_cleanup_is_owned(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.txt").write_text("safe")
    (source / ".env.secret").write_text("token")
    (source / "credentials.json").write_text("secret")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("secret")
    (source / ".venv").mkdir()
    (source / ".venv" / "x").write_text("secret")
    (source / "artifacts").mkdir()
    (source / "artifacts" / "secret").write_text("secret")
    manager = _manager(
        WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(source)), tmp_path
    )
    root = manager.create()
    assert (root / "main.txt").read_text() == "safe"
    assert not (root / ".env.secret").exists()
    assert not (root / "credentials.json").exists()
    assert not (root / ".git").exists()
    manager.cleanup()
    assert not root.exists()
    manager.cleanup()
    assert source.exists()


def test_fresh_temporary_workspace_is_empty_and_risky_inclusion_is_explicit(
    tmp_path: Path,
) -> None:
    manager = _manager(WorkspacePolicy(), tmp_path)
    root = manager.create()
    assert list(root.iterdir()) == []
    manager.cleanup()
    with pytest.raises(WorkspaceError):
        _manager(
            WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(tmp_path)),
            tmp_path,
            include_excluded=True,
        )


def test_copy_skips_symlink_outside_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("must not copy")
    try:
        (source / "link.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    manager = _manager(
        WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(source)), tmp_path
    )
    root = manager.create()
    assert not (root / "link.txt").exists()
    manager.cleanup()


def test_read_only_workspace_rejects_mutation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.txt").write_text("safe")
    manager = _manager(
        WorkspacePolicy(kind=WorkspaceKind.READ_ONLY, source=str(source)), tmp_path
    )
    root = manager.create()
    with pytest.raises(PermissionError):
        (root / "main.txt").write_text("changed")
    manager.cleanup()


def test_in_place_requires_acknowledgement_and_does_not_cleanup(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        WorkspacePolicy(kind=WorkspaceKind.IN_PLACE, source=str(tmp_path))
    manager = _manager(
        WorkspacePolicy(
            kind=WorkspaceKind.IN_PLACE, source=str(tmp_path), acknowledge_risk=True
        ),
        tmp_path,
    )
    root = manager.create()
    manager.cleanup()
    assert root.exists()


def test_diff_is_structured_deterministic_and_undeclared_files_are_not_artifacts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "modified.txt").write_text("before")
    (source / "deleted.txt").write_text("gone")
    manager = _manager(
        WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(source)),
        tmp_path,
        artifact_policy=ArtifactPolicy.ALWAYS,
        declared_artifacts=("result.json",),
    )
    root = manager.create()
    (root / "modified.txt").write_text("after")
    (root / "deleted.txt").unlink()
    (root / "undeclared.txt").write_text("not promoted")
    (root / "result.json").write_text('{"ok": true}')
    capture = manager.capture(ExecutionOutcome.COMPLETED)
    assert [entry.path for entry in capture.diff.entries] == [
        "deleted.txt",
        "modified.txt",
        "result.json",
        "undeclared.txt",
    ]
    assert [entry.status for entry in capture.diff.entries] == [
        "deleted",
        "modified",
        "added",
        "added",
    ]
    assert [ref.name for ref in capture.artifacts] == ["result.json"]
    assert all(ref.redacted for ref in capture.artifacts)
    manager.cleanup()


@pytest.mark.parametrize(
    ("artifact_policy", "outcome", "collects"),
    [
        (ArtifactPolicy.ALWAYS, ExecutionOutcome.COMPLETED, True),
        (ArtifactPolicy.FAILED, ExecutionOutcome.COMPLETED, False),
        (ArtifactPolicy.FAILED, ExecutionOutcome.FAILED, True),
        (ArtifactPolicy.NEVER, ExecutionOutcome.FAILED, False),
    ],
)
def test_artifact_policy_applies_to_success_and_failure(
    tmp_path: Path,
    artifact_policy: ArtifactPolicy,
    outcome: ExecutionOutcome,
    collects: bool,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    manager = _manager(
        WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(source)),
        tmp_path,
        artifact_policy=artifact_policy,
        declared_artifacts=("result.txt",),
    )
    root = manager.create()
    (root / "result.txt").write_text("result")
    capture = manager.capture(outcome)
    assert bool(capture.artifacts) is collects
    manager.cleanup()


def test_artifact_symlink_escape_is_not_collected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    try:
        (source / "result.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    manager = _manager(
        WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(source)),
        tmp_path,
        artifact_policy=ArtifactPolicy.ALWAYS,
        declared_artifacts=("result.txt",),
    )
    manager.create()
    capture = manager.capture(ExecutionOutcome.COMPLETED)
    assert capture.artifacts == ()
    assert "artifact_unavailable:result.txt" in capture.limitations
    manager.cleanup()


def test_concurrent_managers_are_isolated(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_text("x")
    first = _manager(
        WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(source)), tmp_path
    )
    second = _manager(
        WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(source)), tmp_path
    )
    assert first.create() != second.create()
    first.cleanup()
    second.cleanup()


def test_git_worktree_creation_and_cleanup(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True
    )
    subprocess.run(["git", "config", "user.name", "M3 Test"], cwd=source, check=True)
    (source / "file.txt").write_text("x")
    subprocess.run(["git", "add", "file.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=source, check=True)
    manager = _manager(
        WorkspacePolicy(kind=WorkspaceKind.GIT_WORKTREE, source=str(source)), tmp_path
    )
    try:
        root = manager.create()
    except WorkspaceError:
        pytest.skip("git worktree unavailable")
    assert (root / "file.txt").read_text() == "x"
    manager.cleanup()
    assert not root.exists()


def test_exclusions_are_case_insensitive_and_cover_worktree_metadata(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / ".ENV.PROD").write_text("secret")
    (source / ".Git").mkdir()
    (source / ".Git" / "config").write_text("secret")
    (source / "VENV").mkdir()
    (source / "VENV" / "secret").write_text("secret")
    (source / "safe.txt").write_text("safe")
    manager = _manager(
        WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(source)), tmp_path
    )
    root = manager.create()
    assert (root / "safe.txt").exists()
    assert not (root / ".ENV.PROD").exists()
    assert not (root / ".Git").exists()
    assert not (root / "VENV").exists()
    manager.cleanup()


def test_declared_artifact_globs_are_contained_and_redacted(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    manager = _manager(
        WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(source)),
        tmp_path,
        artifact_policy=ArtifactPolicy.ALWAYS,
        declared_artifacts=("results/*.json",),
    )
    root = manager.create()
    (root / "results").mkdir()
    (root / "results" / "one.json").write_text('{"token":"secret"}')
    (root / "results" / "two.txt").write_text("not selected")
    capture = manager.capture(ExecutionOutcome.COMPLETED)
    assert [artifact.name for artifact in capture.artifacts] == ["results/one.json"]
    manager.cleanup()


def test_hardlinks_and_special_files_are_not_copied_or_collected(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret")
    hardlink = source / "hardlink.txt"
    try:
        os.link(outside, hardlink)
    except (OSError, NotImplementedError):
        pytest.skip("hardlinks unavailable")
    fifo = source / "pipe"
    if hasattr(os, "mkfifo"):
        os.mkfifo(fifo)
    manager = _manager(
        WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(source)),
        tmp_path,
        artifact_policy=ArtifactPolicy.ALWAYS,
        declared_artifacts=("hardlink.txt", "pipe"),
    )
    root = manager.create()
    assert not (root / "hardlink.txt").exists()
    if hasattr(os, "mkfifo"):
        assert not (root / "pipe").exists()
    capture = manager.capture(ExecutionOutcome.COMPLETED)
    assert capture.artifacts == ()
    assert (
        len(capture.limitations) == 2
        if hasattr(os, "mkfifo")
        else len(capture.limitations) == 1
    )
    manager.cleanup()


def test_excluded_artifact_and_cleanup_failure_are_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / ".git").write_text("gitdir: /private/admin")
    manager = _manager(
        WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(source)),
        tmp_path,
        artifact_policy=ArtifactPolicy.ALWAYS,
        declared_artifacts=(".git",),
    )
    manager.create()
    capture = manager.capture(ExecutionOutcome.COMPLETED)
    assert capture.artifacts == ()
    assert "artifact_unavailable:.git" in capture.limitations

    monkeypatch.setattr(
        "m3.workspace.shutil.rmtree",
        lambda _path: (_ for _ in ()).throw(OSError("no")),
    )
    with pytest.raises(WorkspaceError, match="workspace cleanup failed"):
        manager.cleanup()
    assert manager.cleanup_error is not None
    monkeypatch.undo()
    manager.cleanup()
    assert not manager.root.exists()


@pytest.mark.asyncio
async def test_agent_session_uses_and_cleans_its_workspace_before_terminal_result(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "input.txt").write_text("input")
    spec = AgentSpec(
        harness=ACPAgent(model="workspace-test"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
        workspace=WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(source)),
        artifact_policy=ArtifactPolicy.ALWAYS,
        declared_artifacts=("result-*.json",),
    )
    adapter = DeterministicHarnessAdapter()
    async with AsyncMCPTestKit(env={}, cwd=str(tmp_path)) as kit:
        async with kit.agent_session(spec, adapter=adapter) as session:
            assert adapter.last_launch is not None
            root_value = adapter.last_launch.workspace_root
            assert root_value is not None
            root = Path(root_value)
            assert (root / "input.txt").read_text() == "input"
            (root / "result-1.json").write_text('{"ok":true}')
        result = session.result
    assert not root.exists()
    assert [artifact.name for artifact in result.artifacts] == ["result-1.json"]
