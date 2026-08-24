"""Command-line helpers for ACP harness manifests and local probes.

Examples::

    uv run mcp-pal-harness validate harness.json --check-local
    uv run mcp-pal-harness probe harness.json --kind protocol
    uv run mcp-pal-harness probe harness.json --kind full --mode-id default

All output is JSON, making the commands convenient in shell scripts and CI.
Probe commands are intentionally opt-in and launch the executable from the
manifest on the local machine.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from .manifest import ManifestValidationError, load_manifest, validate_manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcp-pal-harness")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate a manifest without launching it")
    validate.add_argument("manifest", help="JSON path, or - for stdin")
    validate.add_argument("--check-local", action="store_true", help="also report executable/environment readiness")
    probe = sub.add_parser("probe", help="run an opt-in ACP protocol or full probe")
    probe.add_argument("manifest", help="JSON path, or - for stdin")
    probe.add_argument("--kind", choices=("protocol", "full"), default="protocol")
    probe.add_argument("--transport", choices=("stdio", "http", "sse"), default="stdio")
    probe.add_argument("--mode-id")
    probe.add_argument("--session-config", default="{}", help="JSON object of ACP config options")
    characterize = sub.add_parser(
        "characterize",
        help="run an explicitly authorized live ACP characterization",
    )
    characterize.add_argument("manifest", help="JSON path, or - for stdin")
    characterize.add_argument(
        "--confirm-live",
        action="store_true",
        required=True,
        help="allow launching the configured ACP executable",
    )
    characterize.add_argument("--transport", choices=("stdio", "http", "sse"), default="stdio")
    characterize.add_argument("--mode-id")
    characterize.add_argument("--session-config", default="{}", help="JSON object of ACP config options")
    return parser


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        if args.command == "validate":
            _print(validate_manifest(manifest, check_local=args.check_local))
            return 0
        try:
            config = json.loads(args.session_config)
        except json.JSONDecodeError as exc:
            raise ManifestValidationError(f"--session-config must be JSON: {exc}") from exc
        if not isinstance(config, dict):
            raise ManifestValidationError("--session-config must be a JSON object")
        from .acp import full_probe, protocol_probe

        if args.command == "characterize":
            from .acp import full_probe

            result = asyncio.run(
                full_probe(
                    manifest,
                    mode_id=args.mode_id,
                    session_config=config,
                    transport=args.transport,
                )
            )
        elif args.kind == "protocol":
            result = asyncio.run(protocol_probe(manifest))
        else:
            result = asyncio.run(
                full_probe(
                    manifest,
                    mode_id=args.mode_id,
                    session_config=config,
                    transport=args.transport,
                )
            )
        _print(result)
        return 0 if result.get("status") in {"verified", "completed"} else 1
    except (ManifestValidationError, OSError) as exc:
        print(f"mcp-pal-harness: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["main"]
