"""Spec-driven stdio MCP server for `m3 lint` tests and the harness oracle.

Standard library only. The spec is the JSON file named by ``LINT_SPEC``; see
``cli/tests/fixtures/lint_cases`` for examples. When ``LINT_LOG`` is set, the
server appends ``start <pid>`` at startup and ``method <name>`` for every
message it receives.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from typing import Any

_UNSET = object()


def _replace_index(value: Any, index: int) -> Any:
    if isinstance(value, str):
        return value.replace("{i}", str(index))
    if isinstance(value, list):
        return [_replace_index(item, index) for item in value]
    if isinstance(value, dict):
        return {
            _replace_index(key, index): _replace_index(item, index)
            for key, item in value.items()
        }
    return value


def _nest(depth: int) -> Any:
    node: Any = {"type": "string"}
    for _ in range(depth):
        node = {"type": "object", "properties": {"c": node}}
    return node


def _expand(value: Any) -> Any:
    if isinstance(value, list):
        result: list[Any] = []
        for item in value:
            if isinstance(item, dict) and item.get("$gen") == "tools":
                template = _expand(item["template"])
                result.extend(
                    _replace_index(copy.deepcopy(template), index)
                    for index in range(item["n"])
                )
            else:
                result.append(_expand(item))
        return result
    if isinstance(value, dict):
        generator = value.get("$gen")
        if generator == "str":
            return value["char"] * value["n"]
        if generator == "nest":
            return _nest(value["depth"])
        if generator is not None:
            raise ValueError(f"unknown generator {generator!r}")
        return {key: _expand(item) for key, item in value.items()}
    return value


class Server:
    def __init__(self, spec: dict[str, Any], log_path: str | None) -> None:
        self.spec = spec
        self.log_path = log_path
        self.noise = bool(spec.get("noise", False))
        self.sent_roots_request = False
        self.bound = False

    def log(self, line: str) -> None:
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def send(self, message: Any) -> None:
        data = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        corrupt = self.spec.get("corrupt")
        if corrupt:
            # Replace each occurrence of this text with a byte that is never
            # valid UTF-8, to test how clients decode the stream.
            data = data.replace(corrupt.encode(), b"\xff")
        sys.stdout.flush()
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    def reply(self, id: Any, result: Any = _UNSET, error: Any = None) -> None:
        if self.noise:
            sys.stdout.write("not json\n")
            self.send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/message",
                    "params": {"level": "info", "data": "x"},
                }
            )
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result
        self.send(message)

    def capabilities(self) -> Any:
        return self.spec.get("capabilities", {"tools": {}})

    def initialize(self, params: Any) -> dict[str, Any]:
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        result: dict[str, Any] = {
            "protocolVersion": self.spec.get("protocolVersion", requested),
            "capabilities": self.capabilities(),
            "serverInfo": self.spec.get(
                "serverInfo", {"name": "quirk", "version": "1.0.0"}
            ),
        }
        if "instructions" in self.spec:
            result["instructions"] = self.spec["instructions"]
        return {key: value for key, value in result.items() if value is not None}

    def page(self, key: str, params: Any) -> dict[str, Any]:
        items = self.spec.get(key, [])
        # "paginate" limits the paging settings to the named listings.
        paged = key in self.spec.get("paginate", _LISTS.values())
        size = self.spec.get("page_size") if paged else None
        mode = self.spec.get("cursor_mode", "offset") if paged else "offset"
        pad = int(self.spec.get("cursor_pad", 0))
        cursor = params.get("cursor") if isinstance(params, dict) else None
        if mode == "repeat":
            start = 0 if cursor is None else (size or len(items))
        else:
            # Strip the padding first: int() refuses strings over 4300 digits.
            start = int(cursor.lstrip("0") or "0") if cursor else 0
        if size is None:
            chunk, end = items[start:], len(items)
        else:
            chunk, end = items[start : start + size], start + size
        result: dict[str, Any] = {key: chunk}
        if mode == "repeat":
            result["nextCursor"] = "c"
        elif end < len(items):
            result["nextCursor"] = str(end).rjust(pad, "0")
        return result

    def handle(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method is None:
            error = message.get("error")
            code = error.get("code") if isinstance(error, dict) else "ok"
            self.log(f"reply {message.get('id')} {code}")
            return
        self.log(f"method {method}")
        if "id" not in message:
            return
        id = message["id"]
        params = message.get("params")
        if method == "server/discover":
            mode = self.spec.get("discover", "error")
            if mode == "hang":
                return
            if mode in ("result", "bind"):
                # "bind" mirrors dual-protocol SDKs: after answering the probe
                # the connection serves 2026-07-28 only and refuses initialize.
                self.bound = mode == "bind"
                self.reply(
                    id,
                    {
                        "supportedVersions": ["2026-07-28"],
                        "capabilities": self.capabilities(),
                    },
                )
                return
            self.reply(id, error={"code": -32601, "message": "Method not found"})
        elif method == "initialize":
            if self.bound:
                error = {"code": -32022, "message": "initialize not accepted"}
                self.reply(id, error=error)
                return
            self.reply(id, self.initialize(params))
        elif method in _LISTS:
            if method == "tools/list" and self.noise and not self.sent_roots_request:
                self.sent_roots_request = True
                self.send({"jsonrpc": "2.0", "id": "s1", "method": "roots/list"})
            self.reply(id, self.page(_LISTS[method], params))
        elif method == "ping":
            self.reply(id, {})
        elif method == "tools/call":
            self.reply(id, {"content": [{"type": "text", "text": "ok"}]})
        else:
            self.reply(id, error={"code": -32601, "message": "Method not found"})


_LISTS = {
    "tools/list": "tools",
    "prompts/list": "prompts",
    "resources/list": "resources",
    "resources/templates/list": "resourceTemplates",
}


def main() -> None:
    spec_path = os.environ.get("LINT_SPEC")
    spec: dict[str, Any] = {}
    if spec_path:
        with open(spec_path, encoding="utf-8") as handle:
            spec = _expand(json.load(handle))
    server = Server(spec, os.environ.get("LINT_LOG"))
    server.log(f"start {os.getpid()}")
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict):
            server.handle(message)


if __name__ == "__main__":
    main()
