"""Per-request API timing log (``.m3/logs/api.log``).

Every ``/api/`` request writes one line that splits its time into ``pre``
(middleware, body parsing, validation), ``handler`` (the endpoint) and
``post`` (response validation and encoding), plus time spent in the execution
store. ``M3_LOG_LEVEL=DEBUG`` adds a per-store-method breakdown line.

Lines carry route templates, status codes, method names and durations only.
Never log raw paths, parameters, bodies or exception messages: they can hold
user data.
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Any

import fastapi.routing
from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

LEVEL_ENV = "M3_LOG_LEVEL"
FILE_ENV = "M3_LOG_FILE"
DEFAULT_FILE = Path(".m3") / "logs" / "api.log"
MAX_BYTES = 10 * 1024 * 1024
SLOW_NS = 1_000_000_000
_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}
_MARK = "_m3_api_log"

logger = logging.getLogger("m3_app.api")
_configured = False
_configure_lock = threading.Lock()


@dataclass
class _Request:
    start: int
    handler_start: int | None = None
    handler_end: int | None = None
    response_start: int | None = None
    status: int = 0
    store_depth: int = 0
    store: dict[str, list[int]] = field(default_factory=dict)  # name -> [ns, calls]


_current: contextvars.ContextVar[_Request | None] = contextvars.ContextVar(
    "m3_api_log_request", default=None
)


def install_api_log(application: FastAPI) -> None:
    """Configure the log once per process and instrument this application."""
    _configure()
    _time_endpoints()
    runtime = getattr(application.state, "runtime", None)
    store = getattr(runtime, "store", None)
    if store is not None:
        _instrument_store(store)
    application.add_middleware(_RequestLogMiddleware)


def _configure() -> None:
    global _configured
    with _configure_lock:
        if _configured:
            return
        _configured = True
        raw = os.environ.get(LEVEL_ENV, "").strip().upper()
        logger.setLevel(_LEVELS.get(raw, logging.INFO))
        logger.propagate = False
        path = Path(os.environ.get(FILE_ENV) or DEFAULT_FILE).expanduser()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _roll_over(path)
            handler = logging.FileHandler(path, mode="a", encoding="utf-8")
        except OSError as exc:
            print(
                f"m3: API request log disabled: cannot open {path} ({type(exc).__name__})",
                file=sys.stderr,
            )
            return
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s pid=%(process)d %(message)s")
        )
        setattr(handler, _MARK, True)
        logger.addHandler(handler)
        logger.info(
            "session start app=%s level=%s file=%s",
            _app_version(),
            logging.getLevelName(logger.level),
            path,
        )


def _roll_over(path: Path) -> None:
    """Keep one backup once the log passes ``MAX_BYTES``; checked at start only."""
    try:
        if path.stat().st_size <= MAX_BYTES:
            return
        os.replace(path, path.with_name(path.name + ".1"))
    except OSError:  # missing file, or another process rolled it first
        return


def _app_version() -> str:
    try:
        return distribution_version("sf-m3")
    except Exception:
        return "unknown"


def _reset() -> None:
    """Tests only: drop the handler so the next app re-reads the environment."""
    global _configured
    with _configure_lock:
        for handler in list(logger.handlers):
            if getattr(handler, _MARK, False):
                logger.removeHandler(handler)
                handler.close()
        _configured = False


# Instrumentation -------------------------------------------------------------


def _time_endpoints() -> None:
    # FastAPI calls every endpoint through this module-level function, kept
    # separate upstream to make endpoints easy to profile. Patching it works
    # across FastAPI's routing internals, which have changed between releases.
    original = fastapi.routing.run_endpoint_function
    if getattr(original, _MARK, False):
        return

    @functools.wraps(original)
    async def run_endpoint_function(*args: Any, **kwargs: Any) -> Any:
        record = _current.get()
        if record is None:
            return await original(*args, **kwargs)
        record.handler_start = time.perf_counter_ns()
        try:
            return await original(*args, **kwargs)
        finally:
            record.handler_end = time.perf_counter_ns()

    setattr(run_endpoint_function, _MARK, True)
    fastapi.routing.run_endpoint_function = run_endpoint_function


def _instrument_store(store: object) -> None:
    # Patch the instance, not the SDK class: isinstance checks keep working and
    # other stores in the process are untouched.
    for name in dir(type(store)):
        if name.startswith("_") or not inspect.isfunction(
            inspect.getattr_static(type(store), name)
        ):
            continue
        method = getattr(store, name)
        if not getattr(method, _MARK, False):
            setattr(store, name, _timed_store_method(name, method))


def _timed_store_method(name: str, method: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        record = _current.get()
        # Count only the outermost store call so nested calls aren't added twice.
        if record is None or record.store_depth:
            return method(*args, **kwargs)
        record.store_depth += 1
        t0 = time.perf_counter_ns()
        try:
            return method(*args, **kwargs)
        finally:
            elapsed = time.perf_counter_ns() - t0
            record.store_depth -= 1
            entry = record.store.setdefault(name, [0, 0])
            entry[0] += elapsed
            entry[1] += 1

    setattr(wrapper, _MARK, True)
    return wrapper


class _RequestLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _api_path(scope):
            await self.app(scope, receive, send)
            return
        record = _Request(time.perf_counter_ns())
        token = _current.set(record)

        async def timed_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                record.response_start = time.perf_counter_ns()
                record.status = int(message["status"])
            await send(message)

        error: str | None = None
        try:
            await self.app(scope, receive, timed_send)
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            _current.reset(token)
            _write(scope, record, time.perf_counter_ns(), error)


def _api_path(scope: Scope) -> bool:
    path = str(scope.get("path", ""))
    root = str(scope.get("root_path", ""))
    if root and path.startswith(root):
        path = path[len(root) :]
    return path.startswith("/api/")


# Output ----------------------------------------------------------------------


def _write(scope: Scope, record: _Request, end: int, error: str | None) -> None:
    try:
        total = end - record.start
        if error is not None:
            level = logging.ERROR
        elif total >= SLOW_NS:
            level = logging.WARNING
        else:
            level = logging.INFO
        if not logger.isEnabledFor(level) and not logger.isEnabledFor(logging.DEBUG):
            return
        route = getattr(scope.get("route"), "path", None) or "unmatched"
        label = f"{scope.get('method', '')} {route}"
        if logger.isEnabledFor(level):
            status = record.status or (500 if error is not None else 0)
            logger.log(
                level,
                "%s%s %d %s%s",
                "slow " if level == logging.WARNING else "",
                label,
                status,
                _phases(record, end),
                f" error={error}" if error is not None else "",
            )
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("%s store: %s%s", label, _breakdown(record), _test_id())
    except Exception:  # logging must never fail a request
        return


def _phases(record: _Request, end: int) -> str:
    first = record.handler_start or record.response_start or end
    handler = (
        record.handler_end - record.handler_start
        if record.handler_start is not None and record.handler_end is not None
        else 0
    )
    post = (
        record.response_start - record.handler_end
        if record.response_start is not None and record.handler_end is not None
        else 0
    )
    store_ns = sum(ns for ns, _ in record.store.values())
    calls = sum(n for _, n in record.store.values())
    return (
        f"total={_fmt(end - record.start)} pre={_fmt(first - record.start)} "
        f"handler={_fmt(handler)} post={_fmt(post)} store={_fmt(store_ns)}/{calls}"
    )


def _breakdown(record: _Request) -> str:
    items = sorted(record.store.items(), key=lambda item: -item[1][0])
    return " ".join(f"{name}={_fmt(ns)}/{n}" for name, (ns, n) in items) or "-"


def _test_id() -> str:
    current = os.environ.get("PYTEST_CURRENT_TEST")
    if not current:
        return ""
    return f" test={current.rsplit(' (', 1)[0]}"


def _fmt(ns: int) -> str:
    # Always milliseconds, so ``sort -t= -k2 -rn`` orders lines correctly.
    return f"{ns / 1e6:.1f}ms"


__all__ = ["install_api_log"]
