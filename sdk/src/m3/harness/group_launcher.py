"""Internal POSIX launcher used when an ACP agent may create child processes."""

from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            "usage: python -m m3.harness.group_launcher COMMAND [ARGS...]",
            file=sys.stderr,
        )
        return 2
    if os.name != "nt":
        os.setsid()
    os.execvp(args[0], args)
    return 127


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
