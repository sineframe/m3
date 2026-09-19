from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from m3.async_api import AsyncMCPTestKit
from m3.harness import DeterministicHarnessAdapter
from m3.types import (
    ACPAgent,
    AgentSpec,
    ArtifactPolicy,
    ExecutionId,
    ExecutionOutcome,
    ServerBinding,
    StdioServer,
    WorkspaceKind,
    WorkspacePolicy,
)
from m3.workspace import WorkspaceError, WorkspaceManager


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
