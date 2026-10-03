"""Interactive console helpers: key reading on a real pseudo-terminal."""

from __future__ import annotations

import os
import sys
import time

import pytest

from m3_cli import console

pytestmark = pytest.mark.skipif(os.name != "posix", reason="needs a POSIX pty")

_CHILD = r"""
import sys, termios
from m3_cli import console
fd = sys.stdin.fileno()
before = termios.tcgetattr(fd)
with console.KeyReader() as keys:
    print("enabled", keys.enabled, flush=True)
    seen = []
    while len(seen) < 3:
        key = keys.read(5)
        if key is None:
            break
        seen.append(key)
    print("keys", "".join(seen), flush=True)
after = termios.tcgetattr(fd)
# macOS sets PENDIN itself after a mode change; it is not a setting we own.
pendin = getattr(termios, "PENDIN", 0)
before[3] &= ~pendin
after[3] &= ~pendin
print("restored", after == before, flush=True)
"""


def _run_in_pty(stdin_bytes: bytes) -> str:
    import pty

    pid, master = pty.fork()
    if pid == 0:  # pragma: no cover - child process
        os.execv(sys.executable, [sys.executable, "-c", _CHILD])
    output = b""
    deadline = time.monotonic() + 20
    sent = False
    while time.monotonic() < deadline:
        try:
            chunk = os.read(master, 1024)
        except OSError:
            break
        if not chunk:
            break
        output += chunk
        if b"enabled" in output and not sent:
            # Arrow key first: escape sequences are not commands.
            os.write(master, stdin_bytes)
            sent = True
    os.waitpid(pid, 0)
    return output.decode(errors="replace")


def test_key_reader_reads_single_keys_without_echo_and_restores_the_terminal() -> None:
    output = _run_in_pty(b"\x1b[AObq")
    assert "enabled True" in output
    assert "keys obq" in output
    # cbreak mode turns echo off, so the typed keys never reach the screen.
    assert "Obq" not in output
    assert "restored True" in output


def test_key_reader_is_inactive_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(console.time, "sleep", lambda _seconds: None)
    with console.KeyReader() as keys:  # pytest captures stdin: not a terminal
        assert keys.enabled is False
        assert keys.read(0.1) is None


_STOP_CHILD = r"""
import os, signal, sys, termios
from m3_cli import console
fd = sys.stdin.fileno()
echo = lambda: bool(termios.tcgetattr(fd)[3] & termios.ECHO)
os.kill = lambda pid, sig: None  # a pty session leader would not stop anyway
with console.KeyReader(hide_cursor=True) as keys:
    print("|active", keys.enabled, echo(), flush=True)
    keys._on_stop(signal.SIGTSTP, None)  # Ctrl-Z
    print("|stopped", echo(), flush=True)
    keys._on_continue(signal.SIGCONT, None)  # fg
    print("|continued", echo(), flush=True)
print("|done", echo(), flush=True)
"""


def test_ctrl_z_restores_the_terminal_and_fg_reapplies_it() -> None:
    text = _run_child(_STOP_CHILD)
    hide, show = "\x1b[?25l", "\x1b[?25h"
    assert "|active True False" in text  # cbreak: no echo
    assert "|stopped True" in text  # Ctrl-Z: echo back for the shell
    assert "|continued False" in text  # fg: cbreak again
    assert "|done True" in text
    active, stopped = text.index("|active"), text.index("|stopped")
    continued, done = text.index("|continued"), text.index("|done")
    assert text.rindex(hide, 0, active) >= 0
    assert active < text.index(show, active) < stopped
    assert stopped < text.index(hide, stopped) < continued
    assert continued < text.index(show, continued) < done


def _run_child(code: str) -> str:
    import pty

    pid, master = pty.fork()
    if pid == 0:  # pragma: no cover - child process
        os.execv(sys.executable, [sys.executable, "-c", code])
    output = b""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            chunk = os.read(master, 1024)
        except OSError:
            break
        if not chunk:
            break
        output += chunk
    os.waitpid(pid, 0)
    return output.decode(errors="replace")


def test_key_reader_does_not_read_or_stop_in_the_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[int] = []
    keys = console.KeyReader()
    keys.enabled, keys._fd = True, 0
    monkeypatch.setattr(console.os, "tcgetpgrp", lambda _fd: -1)  # not foreground
    monkeypatch.setattr(console.os, "read", lambda *_args: reads.append(1) or b"q")
    monkeypatch.setattr(console.time, "sleep", lambda _seconds: None)
    assert keys.read(0.1) is None
    assert reads == []


def test_terminal_errors_on_restore_are_contained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import termios

    def gone(*_args: object) -> None:
        raise termios.error(5, "Input/output error")  # e.g. the window closed

    monkeypatch.setattr(termios, "tcsetattr", gone)
    keys = console.KeyReader()
    keys._fd, keys._saved = 0, [0, 0, 0, 0, 0, 0, []]
    keys.__exit__(None, None, None)  # must not raise
