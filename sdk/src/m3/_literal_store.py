"""Volatile invocation literals and authenticated local worker bootstrap.

Only handles and bootstrap paths belong in ordinary worker metadata. Values
are transferred once over private local IPC, never through pickle or files.
This module does not resolve named credentials or read project configuration.
"""

from __future__ import annotations

import json
import math
import os
import secrets
import socket
import struct
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ._bootstrap_operations import (
    OwnedBootstrapOperations,
    PrivateBytes,
    PrivateOperationError,
)

_VERSION = 1
_MAX_FRAME = 8 * 1024 * 1024


class LiteralTransferError(RuntimeError):
    """Value-free failure at the private bootstrap boundary."""


@dataclass(frozen=True, slots=True)
class LiteralHandle:
    run_id: str
    generation: str
    slot: str

    def metadata(self) -> dict[str, str]:
        return {"run_id": self.run_id, "generation": self.generation, "slot": self.slot}


class RunLiteralStore:
    """Run-owned memory; entries remain available after individual kits close."""

    def __init__(self, run_id: str, *, generation: str | None = None) -> None:
        if not isinstance(run_id, str) or not run_id:
            raise LiteralTransferError("invalid literal registry identity")
        if generation is not None and (
            not isinstance(generation, str) or not generation
        ):
            raise LiteralTransferError("invalid literal registry identity")
        self._run_id = run_id
        self._generation = generation or secrets.token_hex(16)
        self._values: dict[str, str] = {}
        self._lock = threading.Lock()
        self._closed = False

    def __repr__(self) -> str:
        return "RunLiteralStore(<private>)"

    def __getstate__(self) -> None:
        raise TypeError("literal registry cannot be serialized")

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def generation(self) -> str:
        return self._generation

    def freeze(self, value: str) -> LiteralHandle:
        if not isinstance(value, str):
            raise LiteralTransferError("literal must be text")
        with self._lock:
            self._ensure_open()
            slot = secrets.token_hex(16)
            self._values[slot] = value
        return LiteralHandle(self.run_id, self.generation, slot)

    def resolve(self, handle: LiteralHandle) -> str:
        with self._lock:
            self._ensure_open()
            if (handle.run_id, handle.generation) != (self.run_id, self.generation):
                raise LiteralTransferError("literal registry identity mismatch")
            try:
                return self._values[handle.slot]
            except KeyError:
                raise LiteralTransferError("literal unavailable") from None

    def export(self, handles: Sequence[LiteralHandle]) -> Mapping[str, str]:
        # Resolve under one lock so shutdown cannot produce a partial bundle.
        with self._lock:
            self._ensure_open()
            slots: dict[str, str] = {}
            for handle in handles:
                if (handle.run_id, handle.generation) != (self.run_id, self.generation):
                    raise LiteralTransferError("literal registry identity mismatch")
                if handle.slot in slots or handle.slot not in self._values:
                    raise LiteralTransferError("invalid literal manifest")
                slots[handle.slot] = self._values[handle.slot]
            return MappingProxyType(slots)

    @classmethod
    def _from_bundle(
        cls, run_id: str, generation: str, values: Mapping[str, str]
    ) -> RunLiteralStore:
        store = cls(run_id, generation=generation)
        store._values.update(values)
        return store

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._values.clear()

    def _ensure_open(self) -> None:
        if self._closed:
            raise LiteralTransferError("literal registry closed")


@dataclass(frozen=True, slots=True)
class WorkerBootstrap:
    """Public descriptor: no authentication bytes or literal values."""

    endpoint: str
    capsule: str
    run_id: str
    generation: str
    slots: tuple[str, ...]

    def metadata(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "capsule": self.capsule,
            "run_id": self.run_id,
            "generation": self.generation,
            "slots": list(self.slots),
        }


def _deadline(timeout_seconds: float) -> float:
    if (
        isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise LiteralTransferError("bootstrap timeout must be finite and positive")
    return time.monotonic() + timeout_seconds


def _bootstrap_deadline(timeout_seconds: float, deadline: float | None) -> float:
    phase_deadline = _deadline(timeout_seconds)
    if deadline is not None:
        if isinstance(deadline, bool) or not math.isfinite(deadline):
            raise LiteralTransferError("invalid worker bootstrap deadline")
        phase_deadline = min(phase_deadline, deadline)
    _remaining(phase_deadline)
    return phase_deadline


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LiteralTransferError("worker bootstrap deadline exceeded")
    return remaining


def _receive_exact(channel: socket.socket, size: int, deadline: float) -> bytes:
    output = bytearray()
    while len(output) < size:
        channel.settimeout(_remaining(deadline))
        data = channel.recv(size - len(output))
        if not data:
            raise LiteralTransferError("worker bootstrap transport ended")
        output.extend(data)
    return bytes(output)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LiteralTransferError("invalid worker bootstrap frame")
        result[key] = value
    return result


def _receive(channel: socket.socket, deadline: float) -> dict[str, Any]:
    size = struct.unpack("!I", _receive_exact(channel, 4, deadline))[0]
    if not 0 < size <= _MAX_FRAME:
        raise LiteralTransferError("invalid worker bootstrap frame size")
    try:
        value = json.loads(
            _receive_exact(channel, size, deadline), object_pairs_hook=_unique_object
        )
    except (UnicodeError, ValueError, RecursionError):
        raise LiteralTransferError("invalid worker bootstrap frame") from None
    if not isinstance(value, dict):
        raise LiteralTransferError("invalid worker bootstrap frame")
    return value


def _send(channel: socket.socket, value: Mapping[str, Any], deadline: float) -> None:
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8")
    if not 0 < len(encoded) <= _MAX_FRAME:
        raise LiteralTransferError("worker bootstrap bundle too large")
    channel.settimeout(_remaining(deadline))
    channel.sendall(struct.pack("!I", len(encoded)) + encoded)


class LiteralBootstrapBroker:
    """Invocation-owned POSIX broker; capabilities are scoped and single-use.

    A Windows named-pipe backend is required before exposing this transport
    there. Unsupported platforms fail before creating secret-bearing files.
    """

    def __init__(
        self,
        store: RunLiteralStore,
        directory: Path | None = None,
        *,
        timeout_seconds: float = 30,
    ) -> None:
        deadline = _deadline(timeout_seconds)
        if os.name != "posix":
            raise LiteralTransferError("local literal bootstrap backend unsupported")
        self._store = store
        self._timeout = timeout_seconds
        self._capabilities: dict[str, Mapping[str, str]] = {}
        self._capsules: set[Path] = set()
        self._pending_capsules: set[str] = set()
        self._lock = threading.Lock()
        self._shutdown_lock = threading.Lock()
        self._finished = False
        self._closed = threading.Event()
        self._transfers: dict[threading.Thread, socket.socket] = {}
        self._operations = OwnedBootstrapOperations()
        thread: threading.Thread | None = None
        try:
            endpoint = self._operations.create_endpoint(directory, deadline=deadline)
            self._socket_directory = endpoint.resource.root
            self._endpoint = endpoint.resource.path
            self._listener = endpoint.channel
            self._listener.settimeout(0.1)
            thread = threading.Thread(target=self._serve, daemon=True)
            self._thread = thread
            thread.start()
        except BaseException as error:
            self._closed.set()
            if hasattr(self, "_listener"):
                self._listener.close()
            if thread is not None and thread.is_alive():
                thread.join(timeout=max(0, deadline - time.monotonic()))
            self._operations.close(deadline=deadline)
            if isinstance(error, (OSError, RuntimeError)):
                raise LiteralTransferError(
                    "worker bootstrap listener unavailable"
                ) from None
            raise

    @property
    def cleanup_complete(self) -> bool:
        """Registry owners retain values until pending owned writes settle."""
        with self._lock:
            return (
                self._finished
                and not self._pending_capsules
                and not self._transfers
                and self._operations.cleanup_complete
            )

    def issue(
        self, handles: Sequence[LiteralHandle], *, deadline: float | None = None
    ) -> WorkerBootstrap:
        deadline = _bootstrap_deadline(self._timeout, deadline)
        values = self._store.export(handles)
        # Reject before creating a capsule whose promised bundle cannot fit.
        bundle = {
            "version": _VERSION,
            "run_id": self._store.run_id,
            "generation": self._store.generation,
            "values": dict(values),
        }
        if len(json.dumps(bundle, ensure_ascii=True).encode("utf-8")) > _MAX_FRAME:
            raise LiteralTransferError("worker bootstrap bundle too large")
        capability = secrets.token_hex(32)
        pending = secrets.token_hex(16)
        payload = {
            "version": _VERSION,
            "capability": capability,
            "run_id": self._store.run_id,
            "generation": self._store.generation,
            "slots": list(values),
        }
        with self._lock:
            if self._closed.is_set():
                raise LiteralTransferError("worker bootstrap broker closed")
            self._pending_capsules.add(pending)
        try:
            resource = self._operations.create_capsule(
                PrivateBytes(json.dumps(payload, ensure_ascii=True).encode("utf-8")),
                deadline=deadline,
            )
            capsule = resource.path
            with self._lock:
                if self._closed.is_set():
                    raise LiteralTransferError("worker bootstrap broker closed")
                _remaining(deadline)
                self._capabilities[capability] = values
                self._capsules.add(capsule)
        except PrivateOperationError:
            raise LiteralTransferError("worker bootstrap capsule unavailable") from None
        finally:
            with self._lock:
                self._pending_capsules.discard(pending)
        return WorkerBootstrap(
            str(self._endpoint),
            str(capsule),
            self._store.run_id,
            self._store.generation,
            tuple(values),
        )

    def _serve(self) -> None:
        while not self._closed.is_set():
            try:
                channel, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with self._lock:
                if self._closed.is_set():
                    channel.close()
                    return
                # Bounded concurrency isolates slow clients without an
                # unbounded thread/8-MiB-frame allocation surface.
                if len(self._transfers) >= 64:
                    channel.close()
                    continue
                thread = threading.Thread(
                    target=self._transfer_owned, args=(channel,), daemon=True
                )
                self._transfers[thread] = channel
                try:
                    thread.start()
                except RuntimeError:
                    self._transfers.pop(thread)
                    channel.close()

    def _transfer_owned(self, channel: socket.socket) -> None:
        with channel:
            try:
                self._transfer(channel)
            except (LiteralTransferError, OSError, ValueError, TypeError):
                # Neither invalid requests nor secret-bearing exceptions
                # cross an observation boundary.
                pass
            finally:
                with self._lock:
                    self._transfers.pop(threading.current_thread(), None)

    def _transfer(self, channel: socket.socket) -> None:
        deadline = _deadline(self._timeout)
        request = _receive(channel, deadline)
        capability = request.get("capability")
        if not isinstance(capability, str):
            raise LiteralTransferError("invalid worker bootstrap authentication")
        with self._lock:
            values = self._capabilities.get(capability)
            if values is None or self._closed.is_set():
                raise LiteralTransferError("invalid worker bootstrap authentication")
            expected = {
                "version": _VERSION,
                "capability": capability,
                "run_id": self._store.run_id,
                "generation": self._store.generation,
                "slots": list(values),
            }
            if request != expected or type(request.get("version")) is not int:
                raise LiteralTransferError("worker bootstrap manifest mismatch")
            # Reserve once before sending. A partial transfer cannot replay;
            # the controller can issue a fresh replacement-worker capsule.
            del self._capabilities[capability]
        _send(
            channel,
            {
                "version": _VERSION,
                "run_id": self._store.run_id,
                "generation": self._store.generation,
                "values": dict(values),
            },
            deadline,
        )

    def close(self) -> None:
        with self._shutdown_lock:
            if self._finished:
                self._operations.close(deadline=time.monotonic() + 1)
                return
            self._closed.set()
            self._listener.close()
            with self._lock:
                transfers = tuple(self._transfers.items())
                for _, channel in transfers:
                    try:
                        channel.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            deadline = time.monotonic() + 1
            for thread in (self._thread, *(thread for thread, _ in transfers)):
                thread.join(timeout=max(0, deadline - time.monotonic()))
            if self._thread.is_alive() or any(
                thread.is_alive() for thread, _ in transfers
            ):
                # Keep resources owned rather than deleting a live broker's paths.
                raise LiteralTransferError("worker bootstrap broker cleanup incomplete")
            with self._lock:
                self._capabilities.clear()
                capsules = tuple(self._capsules)
                self._capsules.clear()
            del capsules
            self._operations.close(deadline=deadline)
            self._finished = True


def bootstrap_worker(
    descriptor: WorkerBootstrap,
    *,
    timeout_seconds: float = 30,
    deadline: float | None = None,
) -> RunLiteralStore:
    """Accept a complete bundle atomically; future sessions use local memory."""
    deadline = _bootstrap_deadline(timeout_seconds, deadline)
    if os.name != "posix":
        raise LiteralTransferError("local literal bootstrap backend unsupported")
    operations = OwnedBootstrapOperations()
    try:
        raw = operations.read_and_unlink(
            Path(descriptor.capsule), deadline=deadline
        ).consume()
        capsule = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(capsule, dict):
            raise LiteralTransferError("invalid worker bootstrap capsule")
        expected = {
            "version": _VERSION,
            "run_id": descriptor.run_id,
            "generation": descriptor.generation,
            "slots": list(descriptor.slots),
        }
        if (
            set(capsule) != set(expected) | {"capability"}
            or type(capsule.get("version")) is not int
            or any(capsule.get(key) != value for key, value in expected.items())
            or not isinstance(capsule.get("capability"), str)
        ):
            raise LiteralTransferError("worker bootstrap manifest mismatch")
        with operations.connect(descriptor.endpoint, deadline=deadline) as channel:
            _send(channel, capsule, deadline)
            response = _receive(channel, deadline)
        values = response.get("values")
        if (
            set(response) != {"version", "run_id", "generation", "values"}
            or type(response.get("version")) is not int
            or any(
                response.get(key) != expected[key]
                for key in ("version", "run_id", "generation")
            )
            or not isinstance(values, dict)
            or set(values) != set(descriptor.slots)
            or not all(isinstance(value, str) for value in values.values())
        ):
            raise LiteralTransferError("worker bootstrap manifest mismatch")
        _remaining(deadline)
        store = RunLiteralStore._from_bundle(
            descriptor.run_id, descriptor.generation, values
        )
        try:
            _remaining(deadline)
        except LiteralTransferError:
            store.close()
            raise
        return store
    except PrivateOperationError as error:
        raise LiteralTransferError(str(error)) from None
    except (OSError, UnicodeError, ValueError):
        raise LiteralTransferError("worker bootstrap unavailable") from None
    finally:
        operations.close(deadline=deadline)
