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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=Path)
    parser.add_argument("server", type=Path)
    args = parser.parse_args()
    files = {path.name: path for path in args.server.rglob("*") if path.is_file()}
    problems = 0
    for number, row in enumerate(args.report.read_text().splitlines(), 1):
        current = None
        for match in CITATION.finditer(row):
            name, start, end = match.group(1), int(match.group(2)), match.group(3)
            if name:
                current = files.get(Path(name).name)
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
