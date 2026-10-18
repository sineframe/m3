"""Read a server's discovery, initialize result and listings into a snapshot."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from .._http import cli_version
from . import LintCollectError
from .schema import utf8_size
from .wire import Wire, WireError

DiscoverOutcome = Literal["result", "error", "timeout", "closed"]
ListingError = Literal[None, "method_not_found", "error", "timeout", "invalid"]

MAX_PAGES = 1000
DISCOVER_PROTOCOL = "2026-07-28"
INITIALIZE_PROTOCOL = "2025-11-25"

_INITIALIZE_ERRORS = {
    "http_status": "initialize failed: server returned an error",
    "timeout": "timed out waiting for initialize",
    "closed": "server process exited before initialize",
    "connect": "could not connect to the server",
    "invalid_json": "server returned invalid JSON-RPC",
    "too_large": "server returned invalid JSON-RPC",
}


@dataclass
class ListingResult:
    items: list[Any] = field(default_factory=list)
    pages: int = 0
    repeated_cursor: bool = False
    max_cursor_bytes: int = 0
    error: ListingError = None
    max_result_bytes: int = 0
    total_bytes: int = 0
    # A page held bytes that are not UTF-8 (read as U+FFFD).
    invalid_utf8: bool = False


@dataclass
class Snapshot:
    discover: DiscoverOutcome
    initialize: dict[str, Any]
    protocol_version: str | None
    tools: ListingResult
    prompts: ListingResult | None
    resources: ListingResult | None
    templates: ListingResult | None
    server_name: str
    initialize_invalid_utf8: bool = False


def declared(capabilities: dict[str, Any], key: str) -> bool:
    """Whether a capability is declared; an empty object declares it."""

    return capabilities.get(key) not in (None, False)


def capabilities_of(initialize: dict[str, Any]) -> dict[str, Any]:
    capabilities = initialize.get("capabilities")
    return capabilities if isinstance(capabilities, dict) else {}


def _client_info() -> dict[str, str]:
    return {"name": "m3-lint", "version": cli_version()}


def _discover(wire: Wire, timeout: float) -> DiscoverOutcome:
    params = {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": DISCOVER_PROTOCOL,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": _client_info(),
        }
    }
    wire.set_protocol_version(DISCOVER_PROTOCOL)
    try:
        reply = wire.request("server/discover", params, timeout=min(3.0, timeout))
    except WireError as exc:
        if exc.reason in ("timeout", "closed"):
            return exc.reason
        return "error"
    finally:
        wire.set_protocol_version(None)
    return "error" if reply.error is not None else "result"


class _InitializeRejected(Exception):
    """initialize got a JSON-RPC error response."""


def _initialize(wire: Wire, timeout: float) -> tuple[dict[str, Any], bool]:
    """The initialize result and whether it held invalid UTF-8."""
    params = {
        "protocolVersion": INITIALIZE_PROTOCOL,
        "capabilities": {},
        "clientInfo": _client_info(),
    }
    try:
        reply = wire.request("initialize", params, timeout=timeout)
    except WireError as exc:
        raise LintCollectError(_INITIALIZE_ERRORS[exc.reason]) from None
    if reply.error is not None:
        raise _InitializeRejected
    if not isinstance(reply.result, dict):
        raise LintCollectError(_INITIALIZE_ERRORS["invalid_json"])
    return reply.result, reply.invalid_utf8


def _list(wire: Wire, method: str, key: str, timeout: float) -> ListingResult:
    listing = ListingResult()
    cursor: str | None = None
    seen: set[str] = set()
    while listing.pages < MAX_PAGES:
        params = None if cursor is None else {"cursor": cursor}
        try:
            reply = wire.request(method, params, timeout=timeout)
        except WireError as exc:
            if exc.reason == "timeout":
                listing.error = "timeout"
            elif exc.reason == "invalid_json":
                listing.error = "invalid"
            else:
                listing.error = "error"
            return listing
        if reply.error is not None:
            code = reply.error.get("code")
            not_found = code == -32601 and not isinstance(code, bool)
            listing.error = "method_not_found" if not_found else "error"
            return listing
        result = reply.result
        page = result.get(key) if isinstance(result, dict) else None
        if not isinstance(page, list):
            listing.error = "invalid"
            return listing
        assert isinstance(result, dict)
        listing.pages += 1
        listing.items.extend(page)
        listing.max_result_bytes = max(listing.max_result_bytes, reply.size)
        listing.total_bytes += reply.size
        listing.invalid_utf8 = listing.invalid_utf8 or reply.invalid_utf8
        next_cursor = result.get("nextCursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            return listing
        listing.max_cursor_bytes = max(listing.max_cursor_bytes, utf8_size(next_cursor))
        if next_cursor in seen:
            listing.repeated_cursor = True
            return listing
        seen.add(next_cursor)
        cursor = next_cursor
    return listing


def collect(
    open_wire: Callable[[], Wire], *, timeout: float, server_name: str | None
) -> Snapshot:
    wire = open_wire()
    try:
        discover = _discover(wire, timeout)
        fresh = discover in ("timeout", "closed")
        if fresh:
            # Claude Code respawns a stdio server here; HTTP gets a new session.
            wire.close()
            wire = open_wire()
        try:
            initialize, invalid_utf8 = _initialize(wire, timeout)
        except _InitializeRejected:
            if fresh:
                raise LintCollectError(_INITIALIZE_ERRORS["http_status"]) from None
            # Servers that also speak 2026-07-28 can bind the connection to it
            # after the probe and refuse initialize there; retry on a new one.
            wire.close()
            wire = open_wire()
            try:
                initialize, invalid_utf8 = _initialize(wire, timeout)
            except _InitializeRejected:
                raise LintCollectError(_INITIALIZE_ERRORS["http_status"]) from None
        protocol_version = initialize.get("protocolVersion")
        if not isinstance(protocol_version, str):
            protocol_version = None
        else:
            wire.set_protocol_version(protocol_version)
        try:
            wire.notify("notifications/initialized", None, timeout=timeout)
        except WireError:
            pass  # The listings below report a server that stopped answering.
        capabilities = capabilities_of(initialize)
        tools = _list(wire, "tools/list", "tools", timeout)
        prompts = (
            _list(wire, "prompts/list", "prompts", timeout)
            if declared(capabilities, "prompts")
            else None
        )
        resources = templates = None
        if declared(capabilities, "resources"):
            resources = _list(wire, "resources/list", "resources", timeout)
            templates = _list(
                wire, "resources/templates/list", "resourceTemplates", timeout
            )
    finally:
        wire.close()
    if server_name is None:
        server_info = initialize.get("serverInfo")
        name = server_info.get("name") if isinstance(server_info, dict) else None
        server_name = name if isinstance(name, str) and name else "server"
    return Snapshot(
        discover=discover,
        initialize=initialize,
        protocol_version=protocol_version,
        tools=tools,
        prompts=prompts,
        resources=resources,
        templates=templates,
        server_name=server_name,
        initialize_invalid_utf8=invalid_utf8,
    )
