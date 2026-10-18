"""`m3 lint`: check an MCP server's metadata against Claude Code and Codex quirks.

The command reads only discovery, initialize and the list methods. It never
calls a tool and never contacts a model.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from ..errors import CLIError


class LintUsageError(CLIError):
    """An invalid `m3 lint` invocation; the message holds no user values."""


class LintCollectError(CLIError):
    """The server could not be read; the message holds no server text."""


def _fail(message: str, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"error": message, "exit_code": 2}))
    else:
        print(f"m3 lint: {message}", file=sys.stderr)
    return 2


# RFC 9110 field-name token.
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", re.ASCII)
_MAX_TIMEOUT = 86400.0


def _parse_headers(values: list[str]) -> list[tuple[str, str]]:
    headers: list[tuple[str, str]] = []
    for value in values:
        name, separator, content = value.partition(":")
        name, content = name.strip(), content.strip()
        if (
            not separator
            or _HEADER_NAME.fullmatch(name) is None
            or any(char in content for char in "\r\n\0")
        ):
            raise LintUsageError("invalid --header (expected NAME:VALUE)")
        headers.append((name, content))
    return headers


def run(args: argparse.Namespace, *, stdio_command: list[str]) -> int:
    from . import report as report_module

    as_json = bool(args.json)
    if args.list_rules:
        report_module.print_rules(as_json=as_json)
        return 0
    try:
        return _run(args, stdio_command, as_json=as_json)
    except (LintUsageError, LintCollectError) as exc:
        return _fail(str(exc), as_json=as_json)


def _run(args: argparse.Namespace, stdio_command: list[str], *, as_json: bool) -> int:
    from ..server_options import _valid_http_url
    from . import report as report_module
    from .collect import collect
    from .rules import RULES, evaluate
    from .versions import parse_targets
    from .wire import HttpWire, StdioWire, Wire

    url: str | None = args.url
    if (url is None) == (not stdio_command):
        raise LintUsageError("provide a URL or -- COMMAND (not both)")
    if url is not None and not _valid_http_url(url):
        raise LintUsageError("URL must be an http(s) URL without credentials")
    if url is None and args.header:
        raise LintUsageError("--header applies only to URL targets")
    headers = _parse_headers(args.header)
    targets = parse_targets(args.harness)
    known = {rule.id for rule in RULES}
    if any(rule_id not in known for rule_id in args.ignore):
        raise LintUsageError("unknown rule id in --ignore")
    if not args.timeout > 0:
        raise LintUsageError("--timeout must be positive")
    if args.timeout > _MAX_TIMEOUT:
        raise LintUsageError("--timeout must be at most 86400 seconds")

    def open_wire() -> Wire:
        if url is not None:
            return HttpWire(url, headers)
        try:
            return StdioWire(stdio_command)
        except OSError:
            raise LintUsageError("could not start the server command") from None

    snapshot = collect(open_wire, timeout=args.timeout, server_name=args.server_name)
    findings = evaluate(snapshot, targets, set(args.ignore))
    target = (
        report_module.http_target(url)
        if url is not None
        else {"kind": "stdio", "command": list(stdio_command)}
    )
    report = report_module.build_report(
        target, snapshot, targets, findings, args.fail_on
    )
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        from ..console import stdout_style

        report_module.print_human(report, stdout_style())
    return int(report["exit_code"])


__all__ = ["LintCollectError", "LintUsageError", "run"]
