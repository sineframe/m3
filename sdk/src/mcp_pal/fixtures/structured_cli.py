"""Deterministic structured non-ACP CLI used by the reference bridge."""

from __future__ import annotations

import json
import sys


def main() -> int:
    line = next((line for line in sys.stdin if line.strip()), "{}")
    request = json.loads(line)
    # A deliberately boring adapter: deterministic output makes protocol and
    # wire assertions stable without an API key or network request.
    print(
        json.dumps(
            {"final_output": request.get("nonce", "reference-echo"), "tool": "echo"}
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
