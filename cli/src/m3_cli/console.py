"""Interactive terminal helpers for `m3 test` and `m3 ui`.

Everything here is used only when stdout is an interactive terminal; piped
output keeps the plain lines printed by the supervisor.
"""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import Any

from .branding import M3_TAGLINE, render_banner
from .project import project_name
from .terminal import Style, fit, stream_is_utf8

_REVEAL_FRAME_SECONDS = 0.02
_CLIPBOARD_TIMEOUT_SECONDS = 2.0


def terminal_width() -> int:
    # Stay off the last column: some terminals wrap when it is written.
    return max(20, shutil.get_terminal_size((80, 24)).columns - 1)


def stdout_style() -> Style | None:
    """Styling for an interactive stdout, or None to keep the plain output."""

    try:
        interactive = sys.stdout.isatty()
    except (AttributeError, OSError, ValueError):
        return None
    if not interactive or os.environ.get("TERM") == "dumb":
        return None
    return Style("NO_COLOR" not in os.environ, unicode=stream_is_utf8(sys.stdout))


def print_start_banner(
    root: Path,
    run_id: str,
    *,
    ui: bool,
    harnesses: Sequence[str],
    suite: str | None,
    num_processes: str | None,
) -> bool:
    """Print the banner before pytest starts; return whether it was shown."""

    style = stdout_style()
    if style is None:
        return False
    try:
        version = importlib.metadata.version("sf-m3-cli")
    except importlib.metadata.PackageNotFoundError:
        version = ""
    dot = f" {style.dim(style.glyphs.dot)} "

    def row(key: str, value: str) -> str:
        return f"{style.grey(key.ljust(9))}{value}"

    details = []
    if suite is not None:
        details.append(f"suite {suite}")
    if harnesses:
        details.append(", ".join(harnesses))
    if num_processes is not None:
        details.append(f"{num_processes} workers")
    project = project_name(root) or root.name
    short_run = run_id.removeprefix("run-")[:7]
    title = f"{style.bold('m3')} {style.dim(version)}".rstrip()
    info = [
        title,
        style.dim(M3_TAGLINE),
        "",
        row("project", project),
        row("run", short_run),
    ]
    if details:
        info.append(row("with", dot.join(details)))
    if ui:
        info.append(row("report", "opens in your browser when the run ends"))
    start_at_top()
    print(file=sys.stdout)
    print_banner(style, info, dot.join((title, project, short_run)))
    print(flush=True)
    return True


def start_at_top() -> None:
    """Scroll the current screen into scrollback and draw from the top.

    Nothing is erased: the previous output stays one scroll away.
    """

    rows = shutil.get_terminal_size((80, 24)).lines
    sys.stdout.write("\n" * rows + "\x1b[H")
    sys.stdout.flush()


def print_banner(style: Style, info: Sequence[str], summary: str) -> None:
    """Print the banner, sweeping the gradient in when colour is on."""

    width = terminal_width()
    final = render_banner(style, info, width=width, summary=summary)
    height = final.count("\n") + 1
    rows = shutil.get_terminal_size((80, 24)).lines
    if style.color and 1 < height < rows - 2:
        for reveal in range(0, 24, 3):
            frame = render_banner(
                style, info, width=width, summary=summary, reveal=reveal
            )
            sys.stdout.write(frame + f"\n\x1b[{height}A\r")
            sys.stdout.flush()
            time.sleep(_REVEAL_FRAME_SECONDS)
    sys.stdout.write("".join(f"{line}\x1b[K\n" for line in final.split("\n")))
    sys.stdout.flush()


class KeyReader:
    """Single key presses from an interactive stdin, without echo.

    It is inactive (``enabled`` is False) when stdin is not a terminal or the
    process is in the background, so scripted and piped runs are unaffected.
    The terminal mode is always restored on exit.
    """

    def __init__(self) -> None:
        self.enabled = False
        self._fd: int | None = None
        self._saved: Any = None
        self._pending = b""

    def __enter__(self) -> KeyReader:
        try:
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                return self
            fd = sys.stdin.fileno()
        except (AttributeError, OSError, ValueError):
            return self
        if os.name == "nt":
            self.enabled = True
            return self
        try:
            import termios
            import tty

            # Changing the mode from a background job would stop it (SIGTTOU).
            if os.getpgrp() != os.tcgetpgrp(fd):
                return self
            self._saved = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        except (ImportError, OSError):
            return self
        self._fd = fd
        self.enabled = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._fd is not None and self._saved is not None:
            import termios

            try:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            except OSError:
                pass
        self._fd = self._saved = None
        self.enabled = False

    def read(self, timeout: float) -> str | None:
        """Return one lower-case key, or None after ``timeout`` seconds."""

        if not self.enabled:
            time.sleep(timeout)
            return None
        if os.name == "nt":
            import msvcrt

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if msvcrt.kbhit():  # type: ignore[attr-defined,unused-ignore]
                    key = str(msvcrt.getwch())  # type: ignore[attr-defined,unused-ignore]
                    if key in ("\x00", "\xe0"):
                        # Arrow and function keys arrive as a prefix + code.
                        msvcrt.getwch()  # type: ignore[attr-defined,unused-ignore]
                        return None
                    return key.lower() if key.isprintable() else None
                time.sleep(0.02)
            return None
        import select

        assert self._fd is not None
        if not self._pending:
            ready, _, _ = select.select([self._fd], [], [], timeout)
            if not ready:
                return None
            self._pending = os.read(self._fd, 64)
        return self._next_key()

    def _next_key(self) -> str | None:
        """Take one key from buffered input; keys typed together all count."""

        while self._pending:
            data = self._pending
            if data[:1] == b"\x1b":
                # Arrow keys and other escape sequences are not commands:
                # skip ESC [ ... final byte, ESC O x, or a lone ESC.
                end = 1
                if data[1:2] == b"[":
                    end = 2
                    while end < len(data) and not 0x40 <= data[end] <= 0x7E:
                        end += 1
                    end += 1
                elif data[1:2] == b"O":
                    end = 3
                self._pending = data[end:]
                continue
            self._pending = data[1:]
            key = data[:1].decode("ascii", "ignore").lower()
            if key.isprintable() and key:
                return key
        return None


def copy_to_clipboard(text: str) -> bool:
    """Copy ``text`` with the platform clipboard tool; False if none worked."""

    if sys.platform == "darwin":
        commands = [["pbcopy"]]
    elif os.name == "nt":
        commands = [["clip"]]
    else:
        commands = [
            ["wl-copy"],
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ]
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        try:
            result = subprocess.run(
                command,
                input=text.encode(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_CLIPBOARD_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return True
    return False


def status(style: Style, message: str, *, ok: bool = True) -> None:
    """Rewrite the single status line under the key hints."""

    mark = (
        style.green(style.glyphs.passed) if ok else style.yellow(style.glyphs.warning)
    )
    sys.stdout.write(
        "\r" + fit(f"     {mark} {style.dim(message)}", terminal_width()) + "\x1b[K"
    )
    sys.stdout.flush()
