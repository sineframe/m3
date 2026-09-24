"""Transparent stdio MCP relay that records newline-delimited JSON-RPC frames."""

from __future__ import annotations

import argparse
import json
import os
import queue
import socket
import stat
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from m3.trace.capture import CaptureWriter, parse_json_payload
from m3.transport.tool_policy import ProxyToolPolicy

_MAX_OBSERVATION_FRAME_BYTES = 8 * 1024 * 1024
_MAX_OBSERVATION_QUEUE_BYTES = 16 * 1024 * 1024


def _read_handoff(path: Path) -> Any:
    """Read one regular, owner-only handoff without following replacements."""

    flags = os.O_RDONLY
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags | no_follow)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ValueError
        with os.fdopen(descriptor, "r", encoding="utf-8") as input_file:
            descriptor = -1
            return json.load(input_file)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_policy(path: Path) -> Any:
    """Read an owner-only policy handoff without following symlinks."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ValueError
        with os.fdopen(descriptor, "r", encoding="utf-8") as input_file:
            descriptor = -1
            return json.load(input_file)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


class _ObservationChannel:
    """Authenticated, bounded sender for the manager's loopback TCP socket."""

    def __init__(self, *, host: str, port: str, connection_id: str, token: str) -> None:
        if host != "127.0.0.1" or not connection_id or not token:
            raise ValueError
        port_number = int(port)
        if not 1 <= port_number <= 65535:
            raise ValueError
        self._socket = socket.create_connection((host, port_number), timeout=2.0)
        self._socket.sendall(
            (
                json.dumps(
                    {"connection_id": connection_id, "token": token},
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        )
        self._socket.settimeout(2.0)
        self._lock = threading.Lock()
        self._failed = False
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=128)
        self._queued_bytes = 0
        self._sender = threading.Thread(target=self._send_pending, daemon=True)
        self._sender.start()

    def observe(self, direction: str, payload: Any) -> None:
        if self._failed:
            return
        try:
            frame = (
                json.dumps(
                    {"type": "event", "direction": direction, "payload": payload},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                + b"\n"
            )
            if len(frame) > _MAX_OBSERVATION_FRAME_BYTES:
                raise ValueError("observation frame exceeds limit")
            with self._lock:
                if self._failed:
                    return
                if self._queued_bytes + len(frame) > _MAX_OBSERVATION_QUEUE_BYTES:
                    raise queue.Full
                self._queue.put_nowait(frame)
                self._queued_bytes += len(frame)
        except (OSError, TypeError, ValueError, UnicodeEncodeError):
            self._abort()
        except queue.Full:
            self._abort()

    def _send_pending(self) -> None:
        while True:
            frame = self._queue.get()
            if frame is None:
                return
            try:
                self._socket.sendall(frame)
            except OSError:
                self._abort()
                return
            finally:
                with self._lock:
                    self._queued_bytes = max(0, self._queued_bytes - len(frame))

    def _abort(self) -> None:
        with self._lock:
            if self._failed:
                return
            self._failed = True
            self._queued_bytes = 0
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._socket.close()

    def close(self) -> None:
        with self._lock:
            if self._failed:
                return
            self._failed = True
        self._queue.put(None)
        self._sender.join(timeout=2)
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._socket.close()


def _relay(
    source: Any,
    destination: Any,
    writer: CaptureWriter,
    direction: str,
    policy: ProxyToolPolicy | None = None,
    observer: _ObservationChannel | None = None,
) -> None:
    try:
        while True:
            line = source.readline()
            if not line:
                break
            payload = parse_json_payload(line)
            if policy is not None and direction == "server_to_client":
                policy.observe(payload)
            writer.write(transport="stdio", direction=direction, payload=payload)
            destination.write(line)
            destination.flush()
            if observer is not None:
                observer.observe(direction, payload)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            destination.close()
        except OSError:
            pass


def _relay_policy(
    source: Any,
    destination: Any,
    writer: CaptureWriter,
    direction: str,
    policy: ProxyToolPolicy | None,
    observer: _ObservationChannel | None = None,
) -> None:
    """Relay stdin while denying tools/call before writing to the child."""

    try:
        while True:
            line = source.readline()
            if not line:
                break
            payload = parse_json_payload(line)
            if policy is not None:
                if isinstance(payload, list):
                    for item in payload:
                        policy.observe_request(item)
                else:
                    policy.observe_request(payload)
            denied: tuple[bool, str] | None = None
            denied_batch: list[dict[str, Any]] | None = None
            if (
                policy is not None
                and isinstance(payload, dict)
                and payload.get("method") == "tools/call"
            ):
                params = payload.get("params")
                name = params.get("name") if isinstance(params, dict) else None
                allowed, reason = policy.decide(name)
                if not allowed:
                    denied = (allowed, reason)
            elif policy is not None and isinstance(payload, list):
                denied_batch = []
                for item in payload:
                    if not isinstance(item, dict) or item.get("method") != "tools/call":
                        continue
                    params = item.get("params")
                    name = params.get("name") if isinstance(params, dict) else None
                    allowed, reason = policy.decide(name)
                    if not allowed:
                        denied_batch.append({"id": item.get("id"), "reason": reason})
                if not denied_batch:
                    denied_batch = None
            if denied is None:
                if denied_batch is not None:
                    safe_batch = [
                        {
                            "jsonrpc": item.get("jsonrpc", "2.0"),
                            "id": item.get("id"),
                            "method": "tools/call",
                            "params": {
                                "name": item.get("params", {}).get("name")
                                if isinstance(item.get("params"), dict)
                                else None
                            },
                        }
                        if isinstance(item, dict) and item.get("method") == "tools/call"
                        else {"policy_batch_item": "redacted"}
                        for item in payload
                    ]
                    writer.write(
                        transport="stdio",
                        direction=direction,
                        payload=safe_batch,
                        kind="policy_denied",
                        metadata={"policy_denied": True},
                    )
                    response_batch = [
                        {
                            "jsonrpc": "2.0",
                            "id": item.get("id"),
                            "error": {
                                "code": -32001,
                                "message": "MCP batch denied by policy",
                            },
                        }
                        for item in payload
                        if isinstance(item, dict) and "id" in item
                    ]
                    if response_batch:
                        writer.write(
                            transport="stdio",
                            direction="server_to_client",
                            payload=response_batch,
                            kind="policy_denied",
                        )
                        destination_out = sys.stdout.buffer
                        destination_out.write(
                            (
                                json.dumps(response_batch, separators=(",", ":")) + "\n"
                            ).encode("utf-8")
                        )
                        destination_out.flush()
                    continue
                writer.write(transport="stdio", direction=direction, payload=payload)
                destination.write(line)
                destination.flush()
                if observer is not None:
                    observer.observe(direction, payload)
                continue
            params = payload.get("params")
            safe = {
                "jsonrpc": payload.get("jsonrpc", "2.0"),
                "id": payload.get("id"),
                "method": "tools/call",
                "params": {
                    "name": params.get("name") if isinstance(params, dict) else None
                },
            }
            writer.write(
                transport="stdio",
                direction=direction,
                payload=safe,
                kind="policy_denied",
                metadata={"policy_denied": True, "policy_reason": denied[1]},
            )
            response = {
                "jsonrpc": safe["jsonrpc"],
                "id": safe["id"],
                "error": {"code": -32001, "message": "MCP tool call denied by policy"},
            }
            if "id" not in payload:
                continue
            writer.write(
                transport="stdio",
                direction="server_to_client",
                payload=response,
                kind="policy_denied",
            )
            destination_out = sys.stdout.buffer
            destination_out.write(
                (json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8")
            )
            destination_out.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            destination.close()
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True)
    parser.add_argument("--baseline", required=True, type=int)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--cwd")
    parser.add_argument("--policy-file")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("server command is required after --")

    # The handoff is the complete child environment. In particular, do not
    # inherit ambient provider credentials into a legacy MCP process.
    environment: dict[str, str] = {}
    canaries: set[str] = set()
    observation: dict[str, str] | None = None
    if args.env_file:
        try:
            handoff = Path(args.env_file)
            payload = _read_handoff(handoff)
            if not isinstance(payload, dict):
                raise ValueError
            if set(payload) not in (
                {"environment", "canaries"},
                {"environment", "canaries", "observer"},
            ):
                raise ValueError
            values = payload.get("environment")
            raw_canaries = payload.get("canaries")
            if not isinstance(values, dict) or not isinstance(raw_canaries, list):
                raise ValueError
            if any(not isinstance(value, str) for value in raw_canaries):
                raise ValueError
            canaries.update(raw_canaries)
            if "observer" in payload:
                raw_observer = payload.get("observer")
                if (
                    not isinstance(raw_observer, dict)
                    or set(raw_observer) != {"host", "port", "connection_id", "token"}
                    or any(
                        not isinstance(value, str) for value in raw_observer.values()
                    )
                ):
                    raise ValueError
                observation = raw_observer
            if any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in values.items()
            ):
                raise ValueError
            environment.update(values)
        except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            return 78
        finally:
            # The file is a one-shot launch handoff.  Remove it before the MCP
            # child starts so an explicitly supplied capture root cannot keep
            # resolved credentials at rest after startup.
            try:
                Path(args.env_file).unlink(missing_ok=True)
            except OSError:
                return 78
    writer = CaptureWriter(
        args.capture,
        args.baseline,
        secrets=canaries if canaries else None,
    )
    policy: ProxyToolPolicy | None = None
    if args.policy_file:
        try:
            payload = _read_policy(Path(args.policy_file))
            if not isinstance(payload, dict):
                raise ValueError
            policy = ProxyToolPolicy.from_payload(payload)
        except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            return 78
        finally:
            try:
                Path(args.policy_file).unlink(missing_ok=True)
            except OSError:
                return 78
    observer: _ObservationChannel | None = None
    if observation is not None:
        try:
            observer = _ObservationChannel(
                host=observation["host"],
                port=observation["port"],
                connection_id=observation["connection_id"],
                token=observation["token"],
            )
        except (OSError, ValueError):
            return 78
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        shell=False,
        bufsize=0,
        cwd=args.cwd,
        env=environment,
    )
    assert process.stdin is not None and process.stdout is not None
    inbound = threading.Thread(
        target=_relay_policy,
        args=(
            sys.stdin.buffer,
            process.stdin,
            writer,
            "client_to_server",
            policy,
            observer,
        ),
        daemon=True,
    )
    outbound = threading.Thread(
        target=_relay,
        args=(
            process.stdout,
            sys.stdout.buffer,
            writer,
            "server_to_client",
            policy,
            observer,
        ),
        daemon=True,
    )
    inbound.start()
    outbound.start()
    return_code = process.wait()
    outbound.join(timeout=1)
    if observer is not None:
        observer.close()
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
