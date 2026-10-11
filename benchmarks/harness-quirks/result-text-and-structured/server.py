"""Thin adapter for the shared synthetic combined-result fixture in _shared/."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared.schema_probes import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    anyio.run(run, "combined_result", args.nonce, args.log)


if __name__ == "__main__":
    main()
