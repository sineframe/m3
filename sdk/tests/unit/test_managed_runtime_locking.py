"""Regression checks for cache locks surviving long installs and crashes."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from m3.runtime.core import (
    RuntimeValidationError,
    _entry_lock,
    _kernel_entry_lock,
    _lease_is_active,
    resolve_cache_root,
)


def test_cache_root_accepts_resolved_parent_alias_but_not_root_link(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    assert resolve_cache_root(project, alias / "cache") == actual / "cache"
    with pytest.raises(RuntimeValidationError, match="symlink"):
        resolve_cache_root(project, alias)


def test_active_install_lock_is_not_stolen_when_old(tmp_path: Path) -> None:
    destination = tmp_path / "sha256-entry"
    lock = tmp_path / "sha256-entry.lock"
    with _entry_lock(destination, timeout=0.2):
        old = time.time() - 3601
        os.utime(lock, (old, old))
        with pytest.raises(RuntimeValidationError, match="busy"):
            with _entry_lock(destination, timeout=0.1):
                pytest.fail("a live install lock was stolen")
        assert lock.read_text(encoding="ascii").strip() == str(os.getpid())


def test_dead_install_owner_is_recovered(tmp_path: Path) -> None:
    destination = tmp_path / "sha256-entry"
    lock = tmp_path / "sha256-entry.lock"
    lock.write_text("999999999\n", encoding="ascii")
    with _entry_lock(destination, timeout=1):
        assert lock.read_text(encoding="ascii").strip() == str(os.getpid())
    assert not lock.exists()


def test_dead_lease_owner_is_not_active(tmp_path: Path) -> None:
    lease = tmp_path / "lease-crashed-worker"
    lease.write_text("999999999\n", encoding="ascii")
    assert not _lease_is_active(lease)


def test_legacy_empty_lease_uses_pid_in_filename(tmp_path: Path) -> None:
    live = tmp_path / f"lease-{os.getpid()}-1"
    live.touch()
    dead = tmp_path / "lease-999999999-1"
    dead.touch()
    assert _lease_is_active(live)
    assert not _lease_is_active(dead)


def test_unknown_lease_marker_is_preserved(tmp_path: Path) -> None:
    lease = tmp_path / "lease-unknown-format"
    lease.touch()
    assert _lease_is_active(lease)


@pytest.mark.skipif(os.name != "posix", reason="POSIX kernel cache custody")
def test_default_entry_reader_respects_owned_kernel_guard(tmp_path: Path) -> None:
    destination = tmp_path / "sha256-entry"
    # The legacy marker belongs to an exited preparation child. Kernel custody
    # remains authoritative and cannot be stolen based on that child PID.
    marker = destination.with_name(destination.name + ".lock")
    marker.write_text("999999999\n", encoding="ascii")
    with _kernel_entry_lock(destination, deadline=time.monotonic() + 1):
        with pytest.raises(RuntimeValidationError, match="busy"):
            with _entry_lock(destination, timeout=0.05):
                pytest.fail("default reader bypassed owned publication custody")
        assert marker.exists()
    with _entry_lock(destination, timeout=1):
        assert marker.read_text().strip() == str(os.getpid())


@pytest.mark.skipif(os.name != "posix", reason="POSIX kernel cache custody")
def test_kernel_guard_rejects_symlink_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "must-not-write"
    target.write_text("original")
    destination = tmp_path / "entry"
    destination.with_name("entry.guard").symlink_to(target)
    with pytest.raises(OSError):
        with _kernel_entry_lock(destination, deadline=time.monotonic() + 1):
            pytest.fail("kernel guard followed a symlink")
    assert target.read_text() == "original"


@pytest.mark.skipif(os.name != "posix", reason="POSIX kernel runtime leases")
def test_kernel_runtime_lease_tracks_open_custody_not_child_pid(tmp_path: Path) -> None:
    import fcntl

    lease = tmp_path / "lease-999999999-owned"
    lease.write_text("kernel-v1\n")
    descriptor = os.open(lease, os.O_RDWR)
    transferred = -1
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        transferred = os.dup(descriptor)
        os.close(descriptor)
        descriptor = -1
        assert _lease_is_active(lease)
        os.close(transferred)
        transferred = -1
        assert not _lease_is_active(lease)
    finally:
        for owned in (descriptor, transferred):
            if owned >= 0:
                os.close(owned)


@pytest.mark.asyncio
async def test_owned_lease_retries_marker_cleanup_after_descriptor_close(
    tmp_path: Path,
) -> None:
    from m3._startup_deadlines import StartupBudget
    from m3.runtime.owned import OwnedRuntimeLease
    from m3.types import HarnessTimeouts

    marker = tmp_path / "lease-owned"
    marker.write_text("kernel-v1\n")
    descriptor = os.open(marker, os.O_RDWR)
    identity = os.fstat(descriptor)
    clock = StartupBudget(HarnessTimeouts())
    deadline = clock.begin_cleanup()

    class Owner:
        cleanup_complete = False
        attempts = 0
        deadlines: list[float] = []

        def release_runtime(
            self, path: str, device: int, inode: int, *, deadline: float
        ) -> None:
            self.deadlines.append(deadline)
            self.attempts += 1
            assert (device, inode) == (identity.st_dev, identity.st_ino)
            assert Path(path) == marker
            if self.attempts == 1:
                raise RuntimeError("controlled cleanup interruption")
            marker.unlink()

        def close(self, *, deadline: float) -> None:
            self.deadlines.append(deadline)
            self.cleanup_complete = True

    owner = Owner()
    lease = OwnedRuntimeLease(descriptor, marker, owner, clock)
    with pytest.raises(RuntimeError, match="controlled cleanup interruption"):
        await lease.release()
    assert lease.descriptors == ()
    assert marker.exists()
    await lease.release()
    assert not marker.exists()
    assert owner.attempts == 2
    assert owner.deadlines == [deadline] * 4
