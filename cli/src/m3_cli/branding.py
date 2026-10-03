"""Shared M3 CLI branding."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import groupby

from .terminal import GRADIENT, Style, fit

M3_ASCII_ART = """\
███╗   ███╗  ███████╗░
████╗ ████║       ██║░
██╔████╔██║  ███████║░
██║╚██╔╝██║       ██║░
██║ ╚═╝ ██║  ███████║░
╚═╝     ╚═╝  ╚══════╝░
 ░░░░░░░░░░░░░░░░░░░░"""

M3_TAGLINE = "Test MCP servers and the agents that use them"


def render_banner(
    style: Style,
    info: Sequence[str] = (),
    *,
    width: int | None = None,
    summary: str | None = None,
    reveal: int | None = None,
) -> str:
    """The wordmark in a top-to-bottom gradient, with ``info`` lines beside it.

    ``width`` adapts the layout: info beside the art when it fits, otherwise
    the art with the one-line ``summary`` under it, and on very narrow
    terminals only the summary. ``reveal`` draws just the first columns of
    the art, for the opening animation.
    """

    art = M3_ASCII_ART.splitlines()
    art_width = max(len(line) for line in art)
    available = width if width is not None else 1000
    one_line = summary if summary is not None else " ".join(filter(None, info[:1]))
    if style.glyphs.passed != "✓" or available < art_width + 4:
        # The block art needs UTF-8 and room; fall back to a plain wordmark.
        word = style.bold(style.rgb("M", GRADIENT[0]) + style.rgb("3", GRADIENT[1]))
        return fit(f"  {word}  {one_line}", available)
    beside = available >= art_width + 5 + 30
    shown = art_width if reveal is None else max(0, min(reveal, art_width))
    top, bottom = GRADIENT
    lines = []
    for row, line in enumerate(art):
        ratio = row / (len(art) - 1)
        color = tuple(
            round(a + (b - a) * ratio) for a, b in zip(top, bottom, strict=True)
        )
        visible = line[:shown]
        painted = "".join(
            style.grey(run) if shade else style.rgb(run, color)
            for shade, run in (
                (key, "".join(group))
                for key, group in groupby(visible, key=lambda char: char == "░")
            )
        )
        side = info[row] if beside and reveal is None and row < len(info) else ""
        padding = " " * (art_width - len(visible) + 3)
        text = f"  {painted}{padding}{side}" if side else f"  {painted}"
        lines.append(fit(text, available))
    if not beside:
        lines.append(fit(f"  {one_line}" if reveal is None else "", available))
    return "\n".join(lines)
