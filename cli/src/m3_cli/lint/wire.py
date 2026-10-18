"""Raw JSON-RPC clients for stdio and streamable HTTP.

The official MCP client validates results and rejects the malformed entries
the lint must report, so these clients hand back the raw JSON instead.
"""

from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from http.client import HTTPException, HTTPResponse
from typing import IO, Any, Literal, Protocol
from urllib import request
from urllib.error import HTTPError, URLError

from .._http import user_agent

MAX_MESSAGE_BYTES = 64 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024
# The first MCP revision whose HTTP transport requires the Mcp-Method header.
_METHOD_HEADER_PROTOCOL = "2026-07-28"

WireErrorReason = Literal[
    "connect", "timeout", "closed", "invalid_json", "http_status", "too_large"
]
_MESSAGES: dict[str, str] = {
    "connect": "could not connect to the server",
    "timeout": "timed out waiting for the server",
    "closed": "the server closed the connection",
    "invalid_json": "server returned invalid JSON-RPC",
    "http_status": "server returned an HTTP error",
    "too_large": "server message is too large",
}


class WireError(Exception):
    def __init__(self, reason: WireErrorReason) -> None:
        super().__init__(_MESSAGES[reason])
        self.reason: WireErrorReason = reason


@dataclass
class Reply:
    result: Any | None
    error: dict[str, Any] | None
    size: int
    # The message held bytes that are not UTF-8; they were decoded as U+FFFD,
    # as Claude Code (Node) does.
    invalid_utf8: bool = False


class Wire(Protocol):
    def request(
        self, method: str, params: dict[str, Any] | None, *, timeout: float
    ) -> Reply: ...

    def notify(
        self, method: str, params: dict[str, Any] | None, *, timeout: float
    ) -> None: ...

    def set_protocol_version(self, version: str | None) -> None: ...

    def close(self) -> None: ...


def _message(method: str, params: dict[str, Any] | None, id: int | None) -> bytes:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if id is not None:
        message["id"] = id
    if params is not None:
        message["params"] = params
    return json.dumps(message, separators=(",", ":")).encode()


def _bounded_int(text: str) -> int:
    # int() refuses strings over 4300 digits, and exact conversion of a huge
    # literal costs quadratic time. Rules only compare integers with 2**53, so
    # a same-sign stand-in far beyond that keeps them reported.
    if len(text) <= 4000:
        return int(text)
    return -(10**4000) if text.startswith("-") else 10**4000


def _reject_constant(name: str) -> Any:
    # Python's json accepts NaN and Infinity; JSON, Claude Code and Codex
    # do not, so a message carrying one is not JSON.
    raise ValueError(f"{name} is not JSON")


_DECODER = json.JSONDecoder(parse_int=_bounded_int, parse_constant=_reject_constant)


def _decode(data: bytes) -> tuple[str, bool]:
    """Text of a message and whether invalid UTF-8 had to be replaced."""

    # As json.loads does for bytes: detect UTF-8/16/32 and drop a BOM.
    try:
        return data.decode(json.detect_encoding(data), "surrogatepass"), False
    except UnicodeDecodeError:
        return data.decode("utf-8", "replace"), True


def _loads(text: str) -> Any:
    """Parse JSON text like json.loads; raises ValueError for non-JSON."""

    try:
        value, end = _DECODER.raw_decode(text, _skip_whitespace(text, 0))
    except RecursionError:
        raise WireError("invalid_json") from None
    if _skip_whitespace(text, end) != len(text):
        raise ValueError("extra data")
    return value


def _parse(data: bytes) -> tuple[Any, bool]:
    """Decode and parse one message; the flag marks replaced invalid UTF-8."""

    text, replaced = _decode(data)
    return _loads(text), replaced


def _reply(message: Any, id: int, size: int) -> Reply | None:
    """The reply in ``message`` when it answers request ``id``."""

    if not isinstance(message, dict) or "method" in message:
        return None
    if message.get("id") != id or isinstance(message.get("id"), bool):
        return None
    if "error" in message:
        error = message["error"]
        return Reply(None, error if isinstance(error, dict) else {}, size)
    if "result" in message:
        return Reply(message["result"], None, size)
    return None


_WHITESPACE = " \t\n\r"


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index] in _WHITESPACE:
        index += 1
    return index


def _batch_elements(text: str) -> Iterator[tuple[Any, int]]:
    """Each element of a JSON array with the UTF-8 size of its own text.

    The caller has already parsed ``text`` as an array.
    """

    index = _skip_whitespace(text, 0) + 1
    while True:
        index = _skip_whitespace(text, index)
        if text[index] == "]":
            return
        element, end = _DECODER.raw_decode(text, index)
        yield element, len(text[index:end].encode("utf-8", "surrogatepass"))
        index = _skip_whitespace(text, end)
        if text[index] == ",":
            index += 1


_EOF = object()
_TOO_LARGE = object()
_POSIX = os.name == "posix"


class StdioWire:
    """Newline-delimited JSON-RPC over a child process's stdin and stdout."""

    def __init__(self, command: list[str]) -> None:
        # A process group lets close() stop wrappers such as `uv run` together
        # with the server they start, which inherits the stdout pipe.
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=_POSIX,
        )
        self._lines: queue.Queue[object] = queue.Queue()
        self._next_id = 1
        self._closed = False
        stdout = self._process.stdout
        assert stdout is not None
        threading.Thread(target=self._read, args=(stdout,), daemon=True).start()
        stdin = self._process.stdin
        assert stdin is not None
        self._outgoing: queue.Queue[
            tuple[bytes, threading.Event, list[bool]] | None
        ] = queue.Queue()
        self._write_stalled = False
        self._write_failed = False
        threading.Thread(target=self._write_loop, args=(stdin,), daemon=True).start()

    def _read(self, stdout: IO[bytes]) -> None:
        try:
            while True:
                line = stdout.readline(MAX_MESSAGE_BYTES + 2)
                if not line:
                    break
                # The limit applies to the message, not its line delimiter.
                payload = line[:-1] if line.endswith(b"\n") else line
                if payload.endswith(b"\r"):
                    payload = payload[:-1]
                if len(payload) > MAX_MESSAGE_BYTES:
                    self._lines.put(_TOO_LARGE)
                    return
                self._lines.put(payload)
        except (OSError, ValueError):
            pass
        self._lines.put(_EOF)

    def _write_loop(self, stdin: IO[bytes]) -> None:
        while True:
            item = self._outgoing.get()
            if item is None:  # close(): EOF lets the server exit on its own
                break
            data, done, failed = item
            try:
                stdin.write(data + b"\n")
                stdin.flush()
            except (OSError, ValueError):
                failed.append(True)
            done.set()
            if failed:
                break
        try:
            stdin.close()
        except (OSError, ValueError):
            pass

    def _write(self, data: bytes, deadline: float) -> None:
        # Writes go through one thread so a server that stops reading stdin
        # (a full pipe) costs at most the request deadline, not a hang.
        if self._closed or self._write_failed:
            raise WireError("closed")
        if self._write_stalled:
            raise WireError("timeout")
        done = threading.Event()
        failed: list[bool] = []
        self._outgoing.put((data, done, failed))
        if not done.wait(max(0.0, deadline - time.monotonic())):
            self._write_stalled = True
            raise WireError("timeout")
        if failed:
            self._write_failed = True
            raise WireError("closed")

    def request(
        self, method: str, params: dict[str, Any] | None, *, timeout: float
    ) -> Reply:
        id = self._next_id
        self._next_id += 1
        deadline = time.monotonic() + timeout
        self._write(_message(method, params, id), deadline)
        malformed = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WireError("invalid_json" if malformed else "timeout")
            try:
                item = self._lines.get(timeout=remaining)
            except queue.Empty:
                raise WireError("invalid_json" if malformed else "timeout") from None
            if item is _EOF:
                self._lines.put(_EOF)
                raise WireError("closed")
            if item is _TOO_LARGE:
                self._lines.put(_TOO_LARGE)
                raise WireError("too_large")
            assert isinstance(item, bytes)
            line = item.rstrip(b"\r\n")
            try:
                message, replaced = _parse(line)
            except ValueError:
                # Claude Code skips lines that are not JSON too; but a broken
                # reply to this request ends it rather than timing out.
                if line.startswith(b"{"):
                    if re.search(rb'"id"\s*:\s*%d(?![0-9])' % id, line):
                        raise WireError("invalid_json") from None
                    malformed = True
                continue
            if isinstance(message, dict) and "method" in message and "id" in message:
                self._decline(message["id"], deadline)
                continue
            reply = _reply(message, id, len(line))
            if reply is not None:
                reply.invalid_utf8 = replaced
                return reply

    def _decline(self, id: Any, deadline: float) -> None:
        response = {
            "jsonrpc": "2.0",
            "id": id,
            "error": {"code": -32601, "message": "Method not found"},
        }
        try:
            self._write(json.dumps(response, separators=(",", ":")).encode(), deadline)
        except WireError:
            pass

    def notify(
        self, method: str, params: dict[str, Any] | None, *, timeout: float
    ) -> None:
        self._write(_message(method, params, None), time.monotonic() + timeout)

    def set_protocol_version(self, version: str | None) -> None:
        pass

    def _signal(self, number: int) -> None:
        try:
            if _POSIX:
                os.killpg(self._process.pid, number)
            elif number == signal.SIGTERM:
                self._process.terminate()
            else:
                self._process.kill()
        except OSError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        # stdin is closed by the writer thread after any pending write; if a
        # write is stuck on a full pipe, the signals below end it instead of
        # this thread waiting on the writer's lock.
        self._outgoing.put(None)
        # stdout is left to the daemon reader: closing it while the reader is
        # blocked in readline() would wait on the reader's lock.
        for number in (None, signal.SIGTERM, getattr(signal, "SIGKILL", None)):
            if number is not None:
                self._signal(number)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                continue
            break
        if _POSIX:
            # Children that outlived the server (wrappers, helpers) go too.
            self._signal(getattr(signal, "SIGKILL", signal.SIGTERM))


class _DeadlineReader:
    """An HTTP response body read in chunks, every read bounded by one
    request deadline (the socket timeout alone resets on each byte)."""

    def __init__(self, response: HTTPResponse, deadline: float) -> None:
        self._response = response
        self._deadline = deadline
        self._buffer = bytearray()
        self._eof = False
        raw = getattr(response.fp, "raw", None)
        self._socket: Any = getattr(raw, "_sock", None)

    def _fill(self) -> bool:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise WireError("timeout")
        if self._socket is not None:
            self._socket.settimeout(remaining)
        chunk = self._response.read1(_CHUNK_BYTES)
        if not chunk:
            if self._response.length:  # Content-Length promised more bytes
                raise WireError("closed")
            self._eof = True
            return False
        self._buffer += chunk
        return True

    def read_all(self) -> bytes:
        while not self._eof and self._fill():
            if len(self._buffer) > MAX_MESSAGE_BYTES:
                raise WireError("too_large")
        data = bytes(self._buffer)
        self._buffer.clear()
        return data

    def readline(self) -> bytes:
        """The next line with its newline, the unterminated rest at the end
        of the body, or b"" once the body is exhausted."""
        searched = 0
        while True:
            index = self._buffer.find(b"\n", searched)
            if index >= 0:
                line = bytes(self._buffer[: index + 1])
                del self._buffer[: index + 1]
                return line
            searched = len(self._buffer)
            if searched > MAX_MESSAGE_BYTES + 2:
                raise WireError("too_large")
            if self._eof or not self._fill():
                line = bytes(self._buffer)
                self._buffer.clear()
                return line


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise WireError("http_status")


class HttpWire:
    """MCP streamable HTTP: one POST per message, JSON or SSE responses."""

    def __init__(self, url: str, headers: list[tuple[str, str]]) -> None:
        self._url = url
        self._headers = headers
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._next_id = 1
        self._opener = request.build_opener(_NoRedirect())

    def _request_headers(self, rpc_method: str | None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": user_agent(),
        }
        for name, value in self._headers:
            headers[name] = value
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        version = self._protocol_version
        if version is not None:
            headers["MCP-Protocol-Version"] = version
            # ISO dates compare as strings.
            if rpc_method is not None and version >= _METHOD_HEADER_PROTOCOL:
                headers["Mcp-Method"] = rpc_method
        return headers

    def _open(
        self,
        method: str,
        data: bytes | None,
        timeout: float,
        rpc_method: str | None = None,
    ) -> HTTPResponse:
        req = request.Request(
            self._url,
            data=data,
            method=method,
            headers=self._request_headers(rpc_method),
        )
        try:
            response = self._opener.open(req, timeout=timeout)
        except HTTPError as exc:
            exc.close()
            raise WireError("http_status") from None
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise WireError("timeout") from None
            raise WireError("connect") from None
        except TimeoutError:
            raise WireError("timeout") from None
        except (HTTPException, OSError, ValueError):
            raise WireError("connect") from None
        assert isinstance(response, HTTPResponse)
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id
        return response

    def request(
        self, method: str, params: dict[str, Any] | None, *, timeout: float
    ) -> Reply:
        id = self._next_id
        self._next_id += 1
        deadline = time.monotonic() + timeout
        response = self._open("POST", _message(method, params, id), timeout, method)
        try:
            content_type = response.headers.get_content_type()
            body = _DeadlineReader(response, deadline)
            if content_type == "text/event-stream":
                return self._read_events(body, id)
            if content_type == "application/json":
                return self._read_json(body, id)
            if response.status in (202, 204):
                raise WireError("closed")
            raise WireError("invalid_json")
        except TimeoutError:
            raise WireError("timeout") from None
        except (HTTPException, OSError):
            raise WireError("closed") from None
        finally:
            response.close()

    @staticmethod
    def _read_json(body: _DeadlineReader, id: int) -> Reply:
        data = body.read_all()
        try:
            message, replaced = _parse(data)
        except ValueError:
            raise WireError("invalid_json") from None
        reply: Reply | None = None
        if isinstance(message, list):
            for element, size in _batch_elements(_decode(data)[0]):
                reply = _reply(element, id, size)
                if reply is not None:
                    break
        else:
            reply = _reply(message, id, len(data))
        if reply is None:
            raise WireError("invalid_json")
        reply.invalid_utf8 = replaced
        return reply

    @staticmethod
    def _read_events(body: _DeadlineReader, id: int) -> Reply:
        data: list[bytes] = []
        pending = 0

        def dispatch() -> Reply | None:
            nonlocal pending
            if not data:
                return None
            text = b"\n".join(data)
            data.clear()
            pending = 0
            try:
                message, replaced = _parse(text)
            except ValueError:
                return None
            reply = _reply(message, id, len(text))
            if reply is not None:
                reply.invalid_utf8 = replaced
            return reply

        while True:
            raw = body.readline()
            if not raw:
                reply = dispatch()
                if reply is not None:
                    return reply
                raise WireError("closed")
            line = raw.rstrip(b"\r\n")
            if not line:
                reply = dispatch()
                if reply is not None:
                    return reply
                continue
            if line.startswith(b"data:"):
                value = line[5:]
                if value.startswith(b" "):
                    value = value[1:]
                # Data lines are joined with "\n"; count those bytes too.
                pending += len(value) + (1 if data else 0)
                if pending > MAX_MESSAGE_BYTES:
                    raise WireError("too_large")
                data.append(value)

    def notify(
        self, method: str, params: dict[str, Any] | None, *, timeout: float
    ) -> None:
        self._open("POST", _message(method, params, None), timeout, method).close()

    def set_protocol_version(self, version: str | None) -> None:
        self._protocol_version = version

    def close(self) -> None:
        if self._session_id is None:
            return
        try:
            self._open("DELETE", None, 5.0).close()
        except WireError:
            pass
