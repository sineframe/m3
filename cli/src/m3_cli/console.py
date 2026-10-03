"""Interactive terminal helpers for `m3 test` and `m3 ui`.

Everything here is used only when stdout is an interactive terminal; piped
output keeps the plain lines printed by the supervisor.
"""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import signal
import subprocess
import sys
import threading
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
    With ``hide_cursor`` it also hides the cursor while active. The terminal
    mode and cursor are restored on exit, on Ctrl-Z (and re-applied on
    ``fg``), and keys are not read while the job is in the background.
    """

    def __init__(self, *, hide_cursor: bool = False) -> None:
        self.enabled = False
        self._hide_cursor = hide_cursor
        self._cursor_hidden = False
        self._fd: int | None = None
        self._saved: Any = None
        self._pending = b""
        self._previous_handlers: dict[int, Any] = {}

    def __enter__(self) -> KeyReader:
        try:
            interactive = sys.stdout.isatty()
        except (AttributeError, OSError, ValueError):
            interactive = False
        if self._hide_cursor and interactive:
            self._set_cursor(visible=False)
        try:
            if not (interactive and sys.stdin.isatty()):
                self._watch_job_control()
                return self
            fd = sys.stdin.fileno()
        except (AttributeError, OSError, ValueError):
            return self
        if os.name == "nt":
            self.enabled = True
            return self
        self._fd = fd
        if not self._foreground() or not self._apply_mode():
            self._fd = None
            self._watch_job_control()
            return self
        self.enabled = True
        self._watch_job_control()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        for signum, handler in self._previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                pass
        self._previous_handlers = {}
        self._restore_mode()
        self._set_cursor(visible=True)
        self._fd = self._saved = None
        self.enabled = False

    def _foreground(self) -> bool:
        if self._fd is None or os.name == "nt":
            return self._fd is not None
        try:
            return os.getpgrp() == os.tcgetpgrp(self._fd)
        except OSError:
            return False

    def _apply_mode(self) -> bool:
        """Switch to cbreak mode (no echo, keys without Enter)."""

        try:
            import termios
            import tty
        except ImportError:
            return False
        assert self._fd is not None
        try:
            if self._saved is None:
                self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        except (OSError, termios.error):
            return False
        return True

    def _restore_mode(self) -> None:
        if self._fd is None or self._saved is None:
            return
        import termios

        # Restoring from a background job would stop it (SIGTTOU) unless the
        # signal is ignored for the call.
        previous = None
        try:
            previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        except (AttributeError, OSError, ValueError):
            pass
        try:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
        except (OSError, termios.error):
            pass
        finally:
            if previous is not None:
                try:
                    signal.signal(signal.SIGTTOU, previous)
                except (OSError, ValueError):
                    pass

    def _set_cursor(self, *, visible: bool) -> None:
        if not self._hide_cursor or visible != self._cursor_hidden:
            return
        try:
            sys.stdout.write("\x1b[?25h" if visible else "\x1b[?25l")
            sys.stdout.flush()
        except (OSError, ValueError):
            return
        self._cursor_hidden = not visible

    def _watch_job_control(self) -> None:
        """Restore the terminal on Ctrl-Z and re-apply it on ``fg``."""

        if os.name == "nt" or threading.current_thread() is not threading.main_thread():
            return
        for signum, handler in (
            (signal.SIGTSTP, self._on_stop),
            (signal.SIGCONT, self._on_continue),
        ):
            try:
                self._previous_handlers[signum] = signal.signal(signum, handler)
            except (OSError, ValueError):
                pass

    def _on_stop(self, signum: int, _frame: object) -> None:
        self._restore_mode()
        self._set_cursor(visible=True)
        # Stop for real with the default action; SIGCONT re-installs us.
        signal.signal(signal.SIGTSTP, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTSTP)

    def _on_continue(self, signum: int, _frame: object) -> None:
        try:
            signal.signal(signal.SIGTSTP, self._on_stop)
        except (OSError, ValueError):
            pass
        if self.enabled and self._foreground():
            self._apply_mode()
        if self._foreground() or self._fd is None:
            self._set_cursor(visible=False)

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
        if not self._foreground():
            # Reading the terminal from a background job would stop it.
            time.sleep(timeout)
            return None
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
