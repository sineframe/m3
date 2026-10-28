#!/usr/bin/env python3
"""Check the readiness skill's policy excerpts against the live pages.

Every blockquote in skills/preparing-directory-submissions/references/policy-*.md
must appear verbatim on the page its section links to. A paragraph the
reference joins from a table ("field: cell") is accepted when each side
appears in order. Update the excerpts and their "Last checked" dates together.

Usage: python3 benchmarks/directory-readiness/check_policy_excerpts.py
"""

from __future__ import annotations

import html
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

REFERENCES = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "preparing-directory-submissions"
    / "references"
)
URL = re.compile(r"^(https://\S+)$")


class _Text(HTMLParser):
    SKIP = ("script", "style", "svg", "template")

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in self.SKIP:
            self.skip += 1
        elif tag in ("p", "li", "br", "div", "tr", "td", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def squash(text: str) -> str:
    return " ".join(text.split())


def plain(markdown: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", markdown)
    text = re.sub(r"^\s*(?:[*-]|\d+\.)\s+", "", text, flags=re.MULTILINE)
    return html.unescape(text.replace("`", "").replace("**", ""))


def fetch(url: str) -> str | None:
    page = url.split("#", 1)[0]
    markdown = urlsplit(page).hostname == "claude.com"
    if markdown:
        page += ".md"
    request = urllib.request.Request(
        page, headers={"User-Agent": "Mozilla/5.0 m3-readiness"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return None
    if markdown:
        return squash(plain(body))
    parser = _Text()
    parser.feed(body)
    return squash("".join(parser.parts))


def excerpts(path: Path) -> list[tuple[str, str, list[str]]]:
    """(section id, url, paragraphs) for every section with a blockquote."""
    found: list[tuple[str, str, list[str]]] = []
    section, url, quote = "", "", []

    def flush() -> None:
        paragraphs = [p for p in "\n".join(quote).split("\n\n") if p.strip()]
        if section and paragraphs:
            # A section without a URL is reported by main(), not skipped.
            found.append((section, url, paragraphs))

    for raw in [*path.read_text(encoding="utf-8").splitlines(), ""]:
        if raw.startswith("### "):
            flush()
            section, url, quote = raw[4:].split()[0], "", []
        elif URL.match(raw) and not url:
            url = raw
        elif raw.startswith(">"):
            quote.append(raw[1:].lstrip(" ") if raw != ">" else "")
        elif quote and quote[-1] != "":
            quote.append("")
    return found


def piece_found(page: str, paragraph: str) -> bool:
    """Every piece of the paragraph is on the page; "…" marks left-out text."""
    for item in re.split(r"\n(?=\s*[-*] )", paragraph):
        for piece in squash(plain(item)).split("…"):
            text = piece.strip()
            if not text or text in page:
                continue
            position = 0
            for side in text.split(": ", 1):
                at = page.find(side, position)
                if at < 0:
                    return False
                position = at + len(side)
    return True


def main() -> int:
    pages: dict[str, str | None] = {}
    missing = 0
    total = 0
    for path in sorted(REFERENCES.glob("policy-*.md")):
        for section, url, paragraphs in excerpts(path):
            if not url:
                print(f"NO URL {path.name} {section}: the excerpt can't be checked")
                total += len(paragraphs)
                missing += len(paragraphs)
                continue
            page_url = url.split("#", 1)[0]
            if page_url not in pages:
                pages[page_url] = fetch(url)
            page = pages[page_url]
            for paragraph in paragraphs:
                total += 1
                if page is None:
                    print(f"UNREACHABLE {path.name} {section}: {page_url}")
                    missing += 1
                elif not piece_found(page, paragraph):
                    print(
                        f"CHANGED {path.name} {section}: {squash(plain(paragraph))[:120]}"
                    )
                    missing += 1
    print(f"{total - missing} of {total} excerpt paragraphs found verbatim")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
