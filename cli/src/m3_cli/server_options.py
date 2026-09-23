"""Parsing and validation for grouped ``m3 test --server`` options."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from .errors import CLIError


class _ServerGroupAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        groups = getattr(namespace, "_server_groups", None)
        if groups is None:
            groups = []
            namespace._server_groups = groups
        if not isinstance(values, str):
            raise CLIError("--server must be http or stdio")
        groups.append({"type": values, "_seen": set(), "_args": []})


class _ServerFieldAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        groups = getattr(namespace, "_server_groups", None)
        if not groups:
            raise CLIError(f"{option_string} requires a preceding --server group")
        if not isinstance(values, str):
            raise CLIError(f"{option_string} requires a value")
        group = groups[-1]
        field = self.dest.removeprefix("server_")
        if field == "arg":
            group["_args"].append(values)
            return
        seen: set[str] = group["_seen"]
        if field in seen:
            raise CLIError(f"{option_string} may appear only once per --server group")
        seen.add(field)
        group[field] = values


def add_server_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the grouped public server flags on the ``test`` parser."""
    parser.add_argument(
        "--server",
        action=_ServerGroupAction,
        metavar="http|stdio",
        help="start an HTTP or stdio server selection group",
    )
    for option, dest in (
        ("--url", "server_url"),
        ("--command", "server_command"),
        ("--arg", "server_arg"),
        ("--name", "server_name"),
        ("--trust", "server_trust"),
    ):
        parser.add_argument(option, action=_ServerFieldAction, dest=dest)


def normalize_server_groups(
    groups: list[dict[str, object]] | None,
) -> list[dict[str, object]]:
    """Validate parsed groups and return the SDK's JSON declaration shape."""
    if not groups:
        return []

    result: list[dict[str, object]] = []
    seen_groups: set[tuple[object, ...]] = set()
    for group in groups:
        transport = group["type"]
        if transport == "http":
            url = group.get("url")
            if not isinstance(url, str) or not _valid_http_url(url):
                raise CLIError(
                    "--server http requires --url with an http:// or https:// MCP endpoint"
                )
            if "command" in group or group.get("_args"):
                raise CLIError("--server http accepts --url, --name, and --trust only")
            trust = group.get("trust")
            if trust is not None and trust not in {
                "untrusted",
                "public",
                "trusted_private",
            }:
                raise CLIError("--trust must be untrusted, public, or trusted_private")
            entry: dict[str, object] = {
                "type": "http",
                "url": url,
                "name": group.get("name", "server"),
            }
            if trust is not None:
                entry["trust"] = trust
            duplicate_key: tuple[object, ...] = ("http", url, entry["name"], trust)
        elif transport == "stdio":
            command = group.get("command")
            if not isinstance(command, str) or not command.strip():
                raise CLIError("--server stdio requires a non-empty --command")
            if "url" in group or "trust" in group:
                raise CLIError(
                    "--server stdio accepts --command, --arg, and --name only"
                )
            raw_args = group.get("_args", [])
            args = list(raw_args) if isinstance(raw_args, list) else []
            if any(not isinstance(arg, str) or not arg for arg in args):
                raise CLIError("--arg values must not be empty")
            entry = {
                "type": "stdio",
                "command": command,
                "args": args,
                "name": group.get("name", "server"),
            }
            duplicate_key = ("stdio", command, tuple(args), entry["name"])
        else:
            raise CLIError("--server must be http or stdio")

        name = entry["name"]
        if not isinstance(name, str) or not name.strip():
            raise CLIError("--name must not be blank")
        if duplicate_key in seen_groups:
            raise CLIError("duplicate --server group")
        seen_groups.add(duplicate_key)
        result.append(entry)
    return result


def _valid_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.port != 0
        )
    except ValueError:
        return False


__all__ = ["add_server_arguments", "normalize_server_groups"]
