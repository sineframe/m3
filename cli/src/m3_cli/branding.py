"""Shared M3 CLI branding."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import groupby

from m3._terminal import GRADIENT, Style

M3_ASCII_ART = """\
███╗   ███╗  ███████╗░
████╗ ████║       ██║░
██╔████╔██║  ███████║░
██║╚██╔╝██║       ██║░
██║ ╚═╝ ██║  ███████║░
╚═╝     ╚═╝  ╚══════╝░
 ░░░░░░░░░░░░░░░░░░░░"""

M3_TAGLINE = "Test MCP servers and the agents that use them"


def render_banner(style: Style, info: Sequence[str] = ()) -> str:
    """The wordmark in a top-to-bottom gradient, with ``info`` lines beside it."""

    if style.glyphs.passed != "✓":
        # The block art needs UTF-8; fall back to a plain wordmark.
        return "\n".join(f"  {line}" for line in ("M3", *info))
    art = M3_ASCII_ART.splitlines()
    width = max(len(line) for line in art)
    top, bottom = GRADIENT
    lines = []
    for row, line in enumerate(art):
        ratio = row / (len(art) - 1)
        color = tuple(
            round(a + (b - a) * ratio) for a, b in zip(top, bottom, strict=True)
        )
        painted = "".join(
            style.grey(run) if shade else style.rgb(run, color)
            for shade, run in (
                (key, "".join(group))
                for key, group in groupby(line, key=lambda char: char == "░")
            )
        )
        side = info[row] if row < len(info) else ""
        padding = " " * (width - len(line) + 3)
        lines.append(f"  {painted}{padding}{side}" if side else f"  {painted}")
    return "\n".join(lines)
