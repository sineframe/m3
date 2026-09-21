"""Regression checks for cache locks surviving long installs and crashes."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from m3.runtime.core import (
    RuntimeValidationError,
    _entry_lock,
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
