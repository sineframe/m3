"""Thin adapter for the shared synthetic long-error fixture in _shared/."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared.server import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    anyio.run(run, "large_error", args.nonce, args.log)


if __name__ == "__main__":
    main()
