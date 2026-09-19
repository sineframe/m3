"""Opt-in live native harness characterization.

This module is intentionally never imported by deterministic execution paths.
Run it explicitly with ``python -m m3.harness.characterize --live``;
missing binaries are failures, not skips.  It performs only version/help
probes and never sends a model prompt or reads ambient credentials.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence

# Capability help is untrusted subprocess output.  Keep a bounded amount so a
# broken binary cannot make characterization retain unbounded data, while
# leaving enough room for real-world help text where a capability may appear
# well after the first few hundred characters.
MAX_PROBE_OUTPUT = 64 * 1024


def _probe(executable: str, args: Sequence[str]) -> str:
    with tempfile.TemporaryDirectory(prefix="m3-characterize-") as home:
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": home,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
        }
        return _run_probe(executable, args, environment)


def _run_probe(
    executable: str, args: Sequence[str], environment: dict[str, str]
) -> str:
    try:
        result = subprocess.run(
            [executable, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("requested harness prerequisite is unavailable") from None
    output = (result.stdout + "\n" + result.stderr).replace("\x00", " ").strip()
    # Normalize only after bounding raw output.  The normalized form can grow
    # slightly or shift whitespace, so apply the same cap to the final value.
    return " ".join(output.split())[:MAX_PROBE_OUTPUT]


def characterize(harness: str, executable: str) -> int:
    if harness == "claude":
        version = _probe(executable, ["--version"])
        help_text = _probe(executable, ["--help"])
        if "stream-json" not in help_text:
            raise RuntimeError("Claude Code stream-json capability is unavailable")
        print(f"claude executable ready: {version}")
        print("stream-json input/output: available")
        return 0
    if harness == "opencode":
        version = _probe(executable, ["--version"])
        help_text = _probe(executable, ["serve", "--help"])
        if "serve" not in help_text.lower():
            raise RuntimeError("OpenCode serve capability is unavailable")
        print(f"opencode executable ready: {version}")
        print("serve/session capability: available")
        return 0
    if harness == "codex":
        version = _probe(executable, ["--version"])
        help_text = _probe(executable, ["app-server", "--help"])
        if "app-server" not in help_text.lower():
            raise RuntimeError("Codex App Server capability is unavailable")
        print(f"codex executable ready: {version}")
        print("App Server JSON-RPC capability: available")
        return 0
    if harness == "pi":
        version = _probe(executable, ["--version"])
        help_text = _probe(executable, ["--help"])
        if "rpc" not in help_text.lower():
            raise RuntimeError("Pi RPC capability is unavailable")
        print(f"pi executable ready: {version}")
        print("RPC capability: available")
        return 0
    raise ValueError("harness must be claude, opencode, codex, or pi")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Opt-in native harness live capability characterization"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="acknowledge live binary, credential, and privacy implications",
    )
    parser.add_argument(
        "--harness", choices=("claude", "opencode", "codex", "pi"), required=True
    )
    parser.add_argument("--executable", default=None)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("--live is required; characterization is never run implicitly")
    executable = args.executable or args.harness
    print(
        "WARNING: live characterization inspects an installed binary; it does not run a model prompt or use credentials.",
        file=sys.stderr,
    )
    try:
        return characterize(args.harness, executable)
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["MAX_PROBE_OUTPUT", "characterize", "main"]
