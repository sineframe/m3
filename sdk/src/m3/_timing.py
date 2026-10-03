"""Opt-in step timings for m3 runs (``M3_TIMINGS=1``).

Privacy rule: span keys, notes and counter names carry names, ids, kinds and
counts only. Never put prompts, tool arguments, user paths, file contents or
credentials into them.

Two kinds of measurement:

* ``span`` / ``timed`` record one line per step (start, duration, parent).
* ``count`` / ``counted`` only feed in-process aggregates (count, total, max,
  log-bucket histogram) for hot paths, written once when the sink stops.

Collection is active only when ``M3_TIMINGS`` was truthy at import *and*
``start()`` has been called. Otherwise every entry point returns a shared no-op
object without allocating, and ``timed``/``counted`` return the decorated
function unchanged. Closing a span never touches the disk: it enqueues one
tuple on a bounded queue drained by a daemon writer thread. Any I/O failure
emits a single warning and switches the sink off; timing never fails a run.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
import itertools
import json
import math
import os
import queue
import threading
import time
import warnings
from collections.abc import Callable
from json.encoder import encode_basestring_ascii as _q
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO, TypeVar

ENV_VAR = "M3_TIMINGS"
QUEUE_MAX = 100_000
_TRUTHY = {"1", "true", "yes", "on"}
_BUCKET_BASE = 1.08
_LOG_BASE = math.log(_BUCKET_BASE)
_NOTE_LIMIT = 200
_BATCH = 500

F = TypeVar("F", bound=Callable[..., Any])
Token = tuple[int | None, str | None]


def _env_enabled() -> bool:
    return os.environ.get(ENV_VAR, "").strip().lower() in _TRUTHY


ENABLED: bool = _env_enabled()

_current_span: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "m3_timing_span", default=None
)
_current_test: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "m3_timing_test", default=None
)
_ids = itertools.count(1)


def bucket_of(ns: int) -> int:
    """Log-bucket index for a duration (about 8% wide buckets)."""
    return int(math.log(ns) / _LOG_BASE) if ns > 1 else 0


def bucket_value(index: int) -> float:
    """Representative duration (geometric middle) of a bucket."""
    return float(_BUCKET_BASE ** (index + 0.5)) if index > 0 else 1.0


class _Ctx:
    """No-op context manager; the shared inactive return value."""

    __slots__ = ()

    def __enter__(self) -> _Ctx:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def note(self, field: str, value: str | int | float | bool) -> None:
        return None


NOOP = _Ctx()


class _Sink:
    def __init__(self, directory: Path, label: str) -> None:
        self.label = label
        self.label_json = _q(label)
        self.pid = os.getpid()
        self.path = directory / f"{label}-{self.pid}.jsonl"
        self.queue: queue.Queue[tuple[Any, ...] | None] = queue.Queue(QUEUE_MAX)
        self.lock = threading.Lock()  # guards counters and dropped only
        self.counters: dict[tuple[str, str | None], list[Any]] = {}
        self.dropped = 0
        self.max_queue = 0
        self.busy_ns = 0
        self.max_lag_ns = 0
        self.closed = False
        self.failed = False
        self.file: TextIO | None = None
        self.thread: threading.Thread | None = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a", encoding="utf-8")
        self.thread = threading.Thread(
            target=self._run, name="m3-timing-writer", daemon=True
        )
        self.thread.start()

    def put(self, row: tuple[Any, ...]) -> None:
        try:
            self.queue.put_nowait(row)
        except queue.Full:
            with self.lock:
                self.dropped += 1

    def add_count(self, name: str, test: str | None, ns: int) -> None:
        bucket = bucket_of(ns)
        with self.lock:
            agg = self.counters.get((name, test))
            if agg is None:
                agg = self.counters[(name, test)] = [0, 0, 0, {}]
            agg[0] += 1
            agg[1] += ns
            if ns > agg[2]:
                agg[2] = ns
            buckets = agg[3]
            buckets[bucket] = buckets.get(bucket, 0) + 1

    # Writer thread -------------------------------------------------------

    def _run(self) -> None:
        q = self.queue
        done = False
        while not done:
            first = q.get()
            began = time.perf_counter_ns()
            depth = q.qsize() + 1
            if depth > self.max_queue:
                self.max_queue = depth
            batch = [first]
            while len(batch) < _BATCH:
                try:
                    batch.append(q.get_nowait())
                except queue.Empty:
                    break
            lines: list[str] = []
            for row in batch:
                if row is None:
                    done = True
                    break
                lag = began - row[10]
                if lag > self.max_lag_ns:
                    self.max_lag_ns = lag
                if not self.failed:
                    lines.append(self._encode(row))
            if lines and not self.failed:
                self._write("\n".join(lines) + "\n")
            self.busy_ns += time.perf_counter_ns() - began
            time.sleep(0.0002)  # hand the GIL back to the event loop
        self._finish()

    def _encode(self, r: tuple[Any, ...]) -> str:
        # Hand-formatted (about 3x faster than json.dumps of a dict).
        key = _q(r[1]) if r[1] is not None else "null"
        test = _q(r[4]) if r[4] is not None else "null"
        notes = json.dumps(r[9], separators=(",", ":")) if r[9] else "null"
        return (
            f'{{"type":"span","name":{_q(r[0])},"key":{key},"id":{r[2]},'
            f'"parent":{"null" if r[3] is None else r[3]},"test":{test},'
            f'"start_ns":{r[5]},"dur_ns":{r[6]},"pid":{self.pid},"tid":{r[7]},'
            f'"process":{self.label_json},"status":"{r[8]}","notes":{notes}}}'
        )

    def _write(self, text: str) -> None:
        if self.file is None:
            return
        try:
            self.file.write(text)
            self.file.flush()
        except Exception as exc:
            self._fail(exc)

    def _fail(self, exc: BaseException) -> None:
        global _sink
        self.failed = True
        self.closed = True
        if _sink is self:
            _sink = None
        with contextlib.suppress(queue.Full):
            self.queue.put_nowait(None)  # let the writer exit
        warnings.warn(
            f"m3 timings disabled: could not write {self.path.name}: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )

    def _finish(self) -> None:
        if not self.failed:
            with self.lock:
                counters = {
                    k: (v[0], v[1], v[2], dict(v[3])) for k, v in self.counters.items()
                }
                dropped = self.dropped
            lines = [
                json.dumps(
                    {
                        "type": "counter",
                        "name": name,
                        "test": test,
                        "count": count,
                        "total_ns": total,
                        "max_ns": biggest,
                        "buckets": buckets,
                        "pid": self.pid,
                        "process": self.label,
                    },
                    separators=(",", ":"),
                )
                for (name, test), (count, total, biggest, buckets) in counters.items()
            ]
            lines.append(
                json.dumps(
                    {
                        "type": "meta",
                        "pid": self.pid,
                        "process": self.label,
                        "dropped": dropped,
                        "max_queue": self.max_queue,
                        "writer_busy_ns": self.busy_ns,
                        "max_lag_ns": self.max_lag_ns,
                    },
                    separators=(",", ":"),
                )
            )
            self._write("\n".join(lines) + "\n")
        if self.file is not None:
            with contextlib.suppress(Exception):
                self.file.close()
            self.file = None


_sink: _Sink | None = None
_start_lock = threading.Lock()


def active() -> bool:
    return _sink is not None


def start(directory: str | os.PathLike[str], process_label: str) -> None:
    """Begin collecting into ``<directory>/<label>-<pid>.jsonl`` (if enabled)."""
    global _sink
    if not ENABLED:
        return
    with _start_lock:
        if _sink is not None:
            return
        sink = _Sink(Path(directory), process_label)
        try:
            sink.open()
        except Exception as exc:
            warnings.warn(
                f"m3 timings disabled: could not open {sink.path}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            return
        _sink = sink


def stop() -> None:
    """Drain the writer, write counters and a meta record, then deactivate."""
    global _sink
    with _start_lock:
        sink, _sink = _sink, None
    if sink is None:
        return
    sink.closed = True
    try:
        sink.queue.put(None, timeout=5)
    except queue.Full:
        return
    if sink.thread is not None:
        sink.thread.join(timeout=5)


def _reset() -> None:
    """Tests only: stop, clear state, re-read the environment."""
    global ENABLED, _ids
    stop()
    ENABLED = _env_enabled()
    _ids = itertools.count(1)
    _current_span.set(None)
    _current_test.set(None)


def _after_fork_in_child() -> None:
    global _sink, _start_lock
    _sink = None
    _start_lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_in_child)


# Spans ---------------------------------------------------------------------


class _LiveSpan(_Ctx):
    __slots__ = (
        "id",
        "key",
        "name",
        "notes",
        "parent",
        "sink",
        "start_ns",
        "status",
        "t0",
        "test",
        "tok",
    )

    def __init__(
        self, sink: _Sink, name: str, key: str | Callable[[], str] | None
    ) -> None:
        self.sink = sink
        self.name = name
        if callable(key):
            try:
                key = key()
            except Exception:
                key = None
        self.key = key
        self.id = next(_ids)
        self.parent = _current_span.get()
        self.test = _current_test.get()
        self.notes: dict[str, str | int | float | bool] | None = None
        self.status = "ok"
        self.start_ns = 0
        self.t0 = 0

    def __enter__(self) -> _LiveSpan:
        self.tok = _current_span.set(self.id)
        self.start_ns = time.time_ns()
        self.t0 = time.perf_counter_ns()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        end = time.perf_counter_ns()
        try:
            _current_span.reset(self.tok)
        except ValueError:  # exited in a different context than entered
            _current_span.set(self.parent)
        if exc_type is not None:
            self.status = "error"
            self.note("error", exc_type.__name__)
        if not self.sink.closed:
            self.sink.put(
                (
                    self.name,
                    self.key,
                    self.id,
                    self.parent,
                    self.test,
                    self.start_ns,
                    end - self.t0,
                    threading.get_ident(),
                    self.status,
                    self.notes,
                    end,
                )
            )

    def note(self, field: str, value: str | int | float | bool) -> None:
        if isinstance(value, str):
            value = value[:_NOTE_LIMIT]
        elif not isinstance(value, (int, float, bool)):
            return
        if self.notes is None:
            self.notes = {}
        self.notes[field] = value


def span(name: str, key: str | Callable[[], str] | None = None) -> _Ctx:
    sink = _sink
    if sink is None:
        return NOOP
    return _LiveSpan(sink, name, key)


class _LiveCount(_Ctx):
    __slots__ = ("name", "sink", "t0")

    def __init__(self, sink: _Sink, name: str) -> None:
        self.sink = sink
        self.name = name
        self.t0 = 0

    def __enter__(self) -> _LiveCount:
        self.t0 = time.perf_counter_ns()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        ns = time.perf_counter_ns() - self.t0
        if not self.sink.closed:
            self.sink.add_count(self.name, _current_test.get(), ns)


def count(name: str) -> _Ctx:
    sink = _sink
    if sink is None:
        return NOOP
    return _LiveCount(sink, name)


def _decorate(fn: F, make: Callable[[], _Ctx]) -> F:
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with make():
                return await fn(*args, **kwargs)

        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with make():
            return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def timed(name: str, key: str | None = None) -> Callable[[F], F]:
    """Decorator form of ``span``; returns ``fn`` itself unless enabled at import."""

    def decorator(fn: F) -> F:
        if not ENABLED:
            return fn
        return _decorate(fn, lambda: span(name, key))

    return decorator


def counted(name: str) -> Callable[[F], F]:
    """Decorator form of ``count``; returns ``fn`` itself unless enabled at import."""

    def decorator(fn: F) -> F:
        if not ENABLED:
            return fn
        return _decorate(fn, lambda: count(name))

    return decorator


# Context propagation ---------------------------------------------------------


def current() -> Token | None:
    """Capture (current span, current test) for use in another thread."""
    if _sink is None:
        return None
    return (_current_span.get(), _current_test.get())


class _Adopt(_Ctx):
    __slots__ = ("token", "toks")

    def __init__(self, token: Token) -> None:
        self.token = token
        self.toks: tuple[Any, Any] | None = None

    def __enter__(self) -> _Adopt:
        self.toks = (
            _current_span.set(self.token[0]),
            _current_test.set(self.token[1]),
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self.toks is not None:
            _current_span.reset(self.toks[0])
            _current_test.reset(self.toks[1])


def adopt(token: Token | None) -> _Ctx:
    if token is None:
        return NOOP
    return _Adopt(token)


class _TestScope(_Ctx):
    __slots__ = ("nodeid", "tok")

    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid

    def __enter__(self) -> _TestScope:
        self.tok = _current_test.set(self.nodeid)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        _current_test.reset(self.tok)


def test_scope(nodeid: str) -> _Ctx:
    if _sink is None:
        return NOOP
    return _TestScope(nodeid)
