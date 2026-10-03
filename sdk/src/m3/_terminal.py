"""Terminal styling shared by the pytest plugin and the CLI.

Styling is decided once per stream: colours only when the caller says the
stream supports them, Unicode glyphs only when the stream encodes UTF-8.
Plain (non-TTY) output never goes through here, so logs and parsers keep
seeing the historical text.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import Any, NamedTuple

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Banner gradient, top to bottom: teal to violet. Status colours follow the
# Tailwind 400 shades, which stay readable on both dark and light terminals.
GRADIENT = ((94, 234, 212), (167, 139, 250))
GREEN = (74, 222, 128)
RED = (248, 113, 113)
YELLOW = (250, 204, 21)
CYAN = (94, 234, 212)
GREY = (113, 113, 122)


class Glyphs(NamedTuple):
    passed: str
    failed: str
    skipped: str
    warning: str
    arrow: str
    dot: str
    ellipsis: str
    bar_full: str
    bar_half: str
    bar_empty: str
    spinner: str
    box: str  # top-left, top-right, bottom-left, bottom-right, horizontal, vertical


UNICODE = Glyphs(
    "✓", "✗", "○", "⚠", "➜", "·", "…", "━", "╸", "━", "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", "╭╮╰╯─│"
)
ASCII = Glyphs("+", "x", "s", "!", ">", "-", "...", "#", "#", "-", "|/-\\", "++++-|")


def visible_len(text: str) -> int:
    return len(_ANSI.sub("", text))


def truncate(text: str, width: int, ellipsis: str = "…", *, keep: str = "end") -> str:
    """Trim plain text to ``width`` cells, keeping its ``start`` or ``end``."""

    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= len(ellipsis):
        return text[:width] if keep == "start" else text[-width:]
    room = width - len(ellipsis)
    return text[:room] + ellipsis if keep == "start" else ellipsis + text[-room:]


def stream_is_utf8(stream: Any) -> bool:
    encoding = str(getattr(stream, "encoding", "") or "")
    return encoding.lower().replace("-", "").replace("_", "") == "utf8"


class Style:
    """ANSI styling that collapses to plain text when colour is disabled."""

    def __init__(self, color: bool, *, unicode: bool = True) -> None:
        self.color = color
        self.glyphs = UNICODE if unicode else ASCII
        self.truecolor = os.environ.get("COLORTERM", "").lower() in {
            "truecolor",
            "24bit",
        }

    def _sgr(self, code: str, text: str) -> str:
        if not self.color or not text:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"

    def rgb(self, text: str, color: Sequence[int]) -> str:
        red, green, blue = color
        if self.truecolor:
            return self._sgr(f"38;2;{red};{green};{blue}", text)
        cube = [round(value / 255 * 5) for value in (red, green, blue)]
        return self._sgr(f"38;5;{16 + 36 * cube[0] + 6 * cube[1] + cube[2]}", text)

    def bold(self, text: str) -> str:
        return self._sgr("1", text)

    def dim(self, text: str) -> str:
        return self._sgr("2", text)

    def underline(self, text: str) -> str:
        return self._sgr("4", text)

    def green(self, text: str) -> str:
        return self.rgb(text, GREEN)

    def red(self, text: str) -> str:
        return self.rgb(text, RED)

    def yellow(self, text: str) -> str:
        return self.rgb(text, YELLOW)

    def cyan(self, text: str) -> str:
        return self.rgb(text, CYAN)

    def grey(self, text: str) -> str:
        return self.rgb(text, GREY)

    def bar(self, done: int, total: int, width: int) -> str:
        glyphs = self.glyphs
        exact = width * done / total if total else 0.0
        full = min(int(exact), width)
        rest = width - full
        filled = glyphs.bar_full * full
        if rest == 0:
            return self.cyan(filled)
        if not self.color:
            # Without colour the filled and empty glyphs must differ.
            empty = "-" if glyphs is ASCII else "─"
            return filled + empty * rest
        head = glyphs.bar_half if exact - full >= 0.5 else ""
        return self.cyan(filled + head) + self.grey(
            glyphs.bar_empty * (rest - len(head))
        )

    def box(self, title: str, rows: Sequence[tuple[str, str]], width: int) -> list[str]:
        """Rounded key/value panel; drops the right edge when it would wrap."""

        tl, tr, bl, br, horizontal, vertical = tuple(self.glyphs.box)
        key_width = max((len(key) for key, _ in rows), default=0) + 2
        cells = [f" {self.grey(key.ljust(key_width))}{value}" for key, value in rows]
        inner = max(
            [visible_len(cell) + 1 for cell in cells] + [visible_len(title) + 4]
        )
        closed = inner + 4 <= width
        head = f"{horizontal} {title} "
        lines = [
            "  "
            + self.grey(tl)
            + self.grey(horizontal)
            + f" {title} "
            + (
                self.grey(horizontal * (inner - visible_len(head)) + tr)
                if closed
                else ""
            )
        ]
        for cell in cells:
            pad = (
                " " * (inner - visible_len(cell)) + self.grey(vertical)
                if closed
                else ""
            )
            lines.append("  " + self.grey(vertical) + cell + pad)
        lines.append(
            "  "
            + self.grey(
                bl + horizontal * (inner if closed else 2) + (br if closed else "")
            )
        )
        return lines
