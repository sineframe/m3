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
