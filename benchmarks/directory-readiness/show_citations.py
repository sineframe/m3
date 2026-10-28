#!/usr/bin/env python3
"""Print every `file:line` a readiness report cites, next to that line's text.

Reports cite code as `server.py:55`, `ledgerly_mcp/store.py:79-82` or a bare
`:60` after an earlier file. This lists each citation with the cited line(s)
from the server so a reviewer can confirm the evidence points at the code it
describes. Lines past the end of a file are flagged.

Usage: python3 benchmarks/directory-readiness/show_citations.py REPORT SERVER_DIR
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

CITATION = re.compile(r"(?:([\w./-]+\.(?:py|json|md)))?:(\d+)(?:-(\d+))?")


# Tool, cache and dependency folders: their files are not the server's own.
# Other dot-folders such as .codex-plugin hold files reports do cite.
VENDORED = {
    ".agents", ".claude", ".git", ".m3", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".venv", "__pycache__", "node_modules",
    "site-packages", "vendor", "venv",
}  # fmt: skip


def vendored(root: Path, path: Path) -> bool:
    """True for a file under a tool, cache or dependency folder, or a venv."""
    folder = root
    for part in path.relative_to(root).parts[:-1]:
        folder = folder / part
        if part in VENDORED or (folder / "pyvenv.cfg").is_file():
            return True
    return False


def resolve(server: Path, files: list[Path], cited: str) -> Path | None:
    """The one project file a citation names: by relative path, else by name."""
    cited_path = Path(cited)
    matches = [
        path
        for path in files
        if path == cited_path
        or path.as_posix().endswith("/" + cited_path.as_posix())
        or (len(cited_path.parts) == 1 and path.name == cited_path.name)
    ]
    return server / matches[0] if len(matches) == 1 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=Path)
    parser.add_argument("server", type=Path)
    args = parser.parse_args()
    files = [
        path.relative_to(args.server)
        for path in args.server.rglob("*")
        if path.is_file() and not vendored(args.server, path)
    ]
    problems = 0
    for number, row in enumerate(args.report.read_text().splitlines(), 1):
        current = None
        for match in CITATION.finditer(row):
            name, start, end = match.group(1), int(match.group(2)), match.group(3)
            if name:
                current = resolve(args.server, files, name)
                if current is None:
                    print(f"report:{number} {name}: no single matching file")
                    problems += 1
            if current is None or (
                not name and match.start() and row[match.start() - 1] not in "`, "
            ):
                continue
            lines = current.read_text().splitlines()
            last = int(end) if end else start
            if last > len(lines) or start < 1:
                print(
                    f"report:{number} {current.name}:{start}-{last} PAST END ({len(lines)} lines)"
                )
                problems += 1
                continue
            text = " | ".join(line.strip() for line in lines[start - 1 : last])
            print(
                f"report:{number} {current.name}:{start}{'-' + end if end else ''}: {text[:150]}"
            )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
