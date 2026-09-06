"""Opt-in pytest integration used by ``mcp-pal test``."""

from __future__ import annotations

from pathlib import Path as _Path
import time as _time
from uuid import uuid4 as _uuid4
from typing import Any as _Any

import pytest as _pytest

from ._default_store import (
    install_default_run_id_factory as _install_default_run_id_factory,
    install_default_store_factory as _install_default_store_factory,
    restore_default_run_id_factory as _restore_default_run_id_factory,
    restore_default_store_factory as _restore_default_store_factory,
)

_NATIVE_PROGRESS_UNSET = object()


def pytest_addoption(parser: _Any) -> None:
    parser.getgroup("mcp-pal").addoption(
        "--mcp-pal-results-db", action="store", default=None, metavar="PATH",
        help="internal: persist default MCPTestKit executions in PATH",
    )


def pytest_configure(config: _Any) -> None:
    raw = config.getoption("--mcp-pal-results-db")
    if not raw:
        return
    from .storage import SQLiteExecutionStore

    path = str(_Path(raw).expanduser().resolve())
    from .types import RunId

    run_id = RunId(f"run-{_uuid4().hex}")
    config._mcp_pal_run_id = run_id
    config._mcp_pal_run_id_previous = _install_default_run_id_factory(lambda: run_id)
    config._mcp_pal_store_token = _install_default_store_factory(lambda: SQLiteExecutionStore(path))
    config._mcp_pal_progress = _Progress(config)
    config._mcp_pal_progress.reporter = config.pluginmanager.getplugin("terminalreporter")
    if config._mcp_pal_progress.reporter is not None:
        config._mcp_pal_progress.enabled = config._mcp_pal_progress.enabled and config._mcp_pal_progress._is_tty()
    config._mcp_pal_progress.disable_native_progress()
    config.pluginmanager.register(config._mcp_pal_progress, "mcp-pal-progress")


def pytest_unconfigure(config: _Any) -> None:
    progress = getattr(config, "_mcp_pal_progress", None)
    if progress is not None:
        progress.finish()
        progress.restore_native_progress()
    token = getattr(config, "_mcp_pal_store_token", None)
    if token is not None:
        _restore_default_store_factory(token)
    if hasattr(config, "_mcp_pal_run_id_previous"):
        _restore_default_run_id_factory(config._mcp_pal_run_id_previous)


class _Progress:
    def __init__(self, config: _Any) -> None:
        self.config = config
        self.reporter: _Any = None
        self.total = self.completed = self.passed = self.failed = self.skipped = 0
        self._counted: set[str] = set()
        self._outcomes: dict[str, str] = {}
        self._last_write = 0.0
        self._finished = False
        self._native_progress: object = _NATIVE_PROGRESS_UNSET
        option = config.option
        self.enabled = (
            int(getattr(option, "verbose", 0) or 0) <= 0
            and not bool(getattr(option, "numprocesses", 0))
        )

    @_pytest.hookimpl(trylast=True)
    def pytest_collection_finish(self, session: _Any) -> None:
        self.total = len(session.items)
        if self.reporter is None:
            self.reporter = self.config.pluginmanager.getplugin("terminalreporter")
            self.enabled = self.enabled and self._is_tty()
            self.disable_native_progress()

    @_pytest.hookimpl(trylast=True)
    def pytest_runtest_logreport(self, report: _Any) -> None:
        if not self.enabled or report.when not in {"setup", "call", "teardown"}:
            return
        if report.when == "teardown":
            if report.outcome == "failed" and report.nodeid in self._counted and self._outcomes.get(report.nodeid) != "failed":
                previous = self._outcomes[report.nodeid]
                if previous == "passed":
                    self.passed -= 1
                elif previous == "skipped":
                    self.skipped -= 1
                self.failed += 1
                self._outcomes[report.nodeid] = "failed"
                self._write()
            return
        if report.nodeid in self._counted:
            return
        # Passing setup is intermediate; a setup failure/skip is terminal.
        if report.when == "setup" and report.outcome == "passed":
            return
        if report.when != "call" and report.outcome not in {"failed", "skipped"}:
            return
        self._counted.add(report.nodeid)
        self._outcomes[report.nodeid] = report.outcome
        self.completed += 1
        if report.outcome == "passed":
            self.passed += 1
        elif report.outcome == "failed":
            self.failed += 1
        else:
            self.skipped += 1
        self._write()

    def _write(self, *, force: bool = False) -> None:
        if self.reporter is None:
            return
        now = _time.monotonic()
        if not force and now - self._last_write < 0.05:
            return
        self._last_write = now
        total = max(self.total, self.completed)
        filled = round(24 * self.completed / total) if total else 0
        bar = "=" * filled + " " * (24 - filled)
        percent = self.completed / total * 100 if total else 0
        text = f"[{bar}] {self.completed}/{total} {percent:3.0f}% pass={self.passed} fail={self.failed} skip={self.skipped}"
        if self._is_tty():
            self.reporter.rewrite("\r" + text, flush=True)

    def disable_native_progress(self) -> None:
        if not self.enabled or self.reporter is None:
            return
        if hasattr(self.reporter, "_show_progress_info"):
            self._native_progress = self.reporter._show_progress_info
            self.reporter._show_progress_info = False

    def restore_native_progress(self) -> None:
        if self._native_progress is not _NATIVE_PROGRESS_UNSET and self.reporter is not None:
            self.reporter._show_progress_info = self._native_progress
            self._native_progress = _NATIVE_PROGRESS_UNSET

    def _is_tty(self) -> bool:
        value = getattr(self.reporter, "isatty", None)
        if callable(value):
            return bool(value())
        if value is not None:
            return bool(value)
        writer = getattr(self.reporter, "_tw", None)
        stream = getattr(writer, "_file", None)
        isatty = getattr(stream, "isatty", None)
        if callable(isatty):
            return bool(isatty())
        return bool(getattr(writer, "hasmarkup", False))

    def finish(self) -> None:
        if not self.enabled or self._finished:
            return
        self._finished = True
        if self.reporter is None:
            self.reporter = self.config.pluginmanager.getplugin("terminalreporter")
        if self.reporter is None:
            return
        self._write(force=True)
        if self._is_tty():
            self.reporter.write_line("")

    @_pytest.hookimpl(tryfirst=True)
    def pytest_terminal_summary(self, terminalreporter: _Any, **_: _Any) -> None:
        self.reporter = terminalreporter
        self.finish()


__all__ = ["pytest_addoption", "pytest_configure", "pytest_unconfigure"]
