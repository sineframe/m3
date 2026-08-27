"""Ownership and race handling for native process-group cleanup."""

import errno
import os
import signal
import subprocess
from typing import Any

import pytest

from mcp_pal.harness.process_group import terminate_process_group


def test_group_id_alone_cannot_be_signalled(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []
    monkeypatch.setattr(os, "killpg", lambda *args: called.append(args))

    assert terminate_process_group(pgid=4242) is False
    assert called == []


def test_mismatched_live_child_group_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []
    monkeypatch.setattr(os, "getpgid", lambda _pid: 100)
    monkeypatch.setattr(os, "killpg", lambda *args: called.append(args))

    assert terminate_process_group(pid=200, pgid=300) is False
    assert called == []


def test_reaped_leader_only_allows_its_pid_rooted_group(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []

    def gone(_pid: int) -> int:
        raise ProcessLookupError(errno.ESRCH, "gone")

    monkeypatch.setattr(os, "getpgid", gone)
    monkeypatch.setattr(os, "killpg", lambda *args: called.append(args))

    assert terminate_process_group(pid=200, pgid=300) is False
    assert called == []


def test_matching_owned_child_receives_bounded_term_then_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "getpgrp", lambda: 1)
    monkeypatch.setattr(os, "killpg", lambda *args: calls.append(args))

    assert terminate_process_group(pid=200, pgid=200, grace_seconds=0) is True
    assert calls == [(200, signal.SIGTERM), (200, signal.SIGKILL)]


def test_eperm_does_not_retry_an_unowned_group(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "getpgrp", lambda: 1)

    def denied(*args: Any) -> None:
        calls.append(args)
        raise PermissionError("not owner")

    monkeypatch.setattr(os, "killpg", denied)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: type("R", (), {"stdout": ""})())

    assert terminate_process_group(pid=200, pgid=200, grace_seconds=0) is False
    assert calls == [(200, signal.SIGTERM)]


def test_reaped_owned_leader_falls_back_to_verified_group_members(monkeypatch: pytest.MonkeyPatch) -> None:
    signals = []

    def gone(_pid: int) -> int:
        raise ProcessLookupError("leader reaped")

    def no_group(*args: Any) -> None:
        raise ProcessLookupError("group gone")

    monkeypatch.setattr(os, "getpgid", gone)
    monkeypatch.setattr(os, "getpgrp", lambda: 1)
    monkeypatch.setattr(os, "killpg", no_group)
    monkeypatch.setattr(os, "kill", lambda *args: signals.append(args))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: type("R", (), {"stdout": "123 200\n"})(),
    )

    assert terminate_process_group(pid=200, pgid=200, grace_seconds=0) is True
    assert (123, signal.SIGTERM) in signals and (123, signal.SIGKILL) in signals


def test_owned_process_group_refuses_backend_group(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "getpgrp", lambda: 200)
    monkeypatch.setattr(os, "killpg", lambda *args: calls.append(args))

    assert terminate_process_group(pid=200, pgid=200, grace_seconds=0) is False
    assert calls == []
