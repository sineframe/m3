"""Opt-in pytest integration used by ``mcp-pal test``."""

from __future__ import annotations

from pathlib import Path as _Path
from contextvars import ContextVar as _ContextVar
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
from ._test_runs import (
    activate_test as _activate_test,
    active_test as _active_test,
    now_iso as _now_iso,
    reset_test as _reset_test,
    run_record as _run_record,
    test_attempt as _test_attempt,
)
from ._check_recording import (
    restore_default_record_checks as _restore_default_record_checks,
    set_default_record_checks as _set_default_record_checks,
)

_NATIVE_PROGRESS_UNSET = object()
_PLUGIN_CONFIG: _ContextVar[_Any] = _ContextVar("mcp_pal_pytest_plugin_config", default=None)


def pytest_addoption(parser: _Any) -> None:
    parser.getgroup("mcp-pal").addoption(
        "--mcp-pal-results-db", action="store", default=None, metavar="PATH",
        help="internal: persist default MCPTestKit executions in PATH",
    )
    parser.getgroup("mcp-pal").addoption(
        "--mcp-pal-baseline", action="store", default=None, metavar="RUN_ID",
        help="internal: compare the exported feedback with RUN_ID",
    )
    parser.getgroup("mcp-pal").addoption(
        "--mcp-pal-project-root", action="store", default=None, metavar="PATH",
        help="internal: project root used for feedback output",
    )


def pytest_configure(config: _Any) -> None:
    config._mcp_pal_config_token = _PLUGIN_CONFIG.set(config)
    raw = config.getoption("--mcp-pal-results-db")
    if not raw:
        return
    from .storage import SQLiteExecutionStore

    path = str(_Path(raw).expanduser().resolve())
    from .types import RunId

    workerinput = getattr(config, "workerinput", None)
    if isinstance(workerinput, dict) and workerinput.get("mcp_pal_run_id"):
        run_id = RunId(str(workerinput["mcp_pal_run_id"]))
        worker_id = str(workerinput.get("workerid", "worker"))
    else:
        run_id = RunId(f"run-{_uuid4().hex}")
        worker_id = "master"
    config._mcp_pal_run_id = run_id
    config._mcp_pal_database = path
    config._mcp_pal_baseline = config.getoption("--mcp-pal-baseline")
    config._mcp_pal_worker_id = worker_id
    config._mcp_pal_is_worker = isinstance(workerinput, dict)
    config._mcp_pal_project_root = _Path(
        config.getoption("--mcp-pal-project-root") or getattr(config, "rootpath", _Path.cwd())
    ).expanduser().resolve()
    from .storage import SQLiteExecutionStore
    config._mcp_pal_manifest_store = SQLiteExecutionStore(path)
    config._mcp_pal_checks_token = _set_default_record_checks(True)
    if not config._mcp_pal_is_worker:
        config._mcp_pal_manifest_store.save_test_run(
            run_id.root,
            _run_record(
                run_id.root,
                project_root=str(config._mcp_pal_project_root),
                selection=tuple(str(value) for value in getattr(config, "args", ()) or ()),
                capture={
                    "mode": getattr(config.option, "capture", None),
                    "show_capture": bool(getattr(config.option, "showcapture", False)),
                    "verbose": int(getattr(config.option, "verbose", 0) or 0),
                },
            ),
        )
    config._mcp_pal_run_id_previous = _install_default_run_id_factory(lambda: run_id)
    config._mcp_pal_store_token = _install_default_store_factory(lambda: SQLiteExecutionStore(path))
    config._mcp_pal_progress = _Progress(config)
    config._mcp_pal_progress.reporter = config.pluginmanager.getplugin("terminalreporter")
    if config._mcp_pal_progress.reporter is not None:
        config._mcp_pal_progress.enabled = config._mcp_pal_progress.enabled and config._mcp_pal_progress._is_tty()
    config._mcp_pal_progress.disable_native_progress()
    config.pluginmanager.register(config._mcp_pal_progress, "mcp-pal-progress")
    config._mcp_pal_manifest_hooks = _ManifestHooks()
    config.pluginmanager.register(config._mcp_pal_manifest_hooks, "mcp-pal-manifest-hooks")


def pytest_unconfigure(config: _Any) -> None:
    hooks = getattr(config, "_mcp_pal_manifest_hooks", None)
    if hooks is not None:
        config.pluginmanager.unregister(hooks)
    progress = getattr(config, "_mcp_pal_progress", None)
    if progress is not None:
        progress.finish()
        progress.restore_native_progress()
    token = getattr(config, "_mcp_pal_store_token", None)
    if token is not None:
        _restore_default_store_factory(token)
    if hasattr(config, "_mcp_pal_run_id_previous"):
        _restore_default_run_id_factory(config._mcp_pal_run_id_previous)
    check_token = getattr(config, "_mcp_pal_checks_token", None)
    if check_token is not None:
        _restore_default_record_checks(check_token)
    store = getattr(config, "_mcp_pal_manifest_store", None)
    if store is not None:
        try:
            store.close()
        except Exception:
            pass
    config_token = getattr(config, "_mcp_pal_config_token", None)
    if config_token is not None:
        _PLUGIN_CONFIG.reset(config_token)


def _pytest_configure_node(node: _Any) -> None:
    """Pass controller-owned identity to optional xdist workers."""
    config = getattr(node, "config", None)
    run_id = getattr(config, "_mcp_pal_run_id", None)
    if run_id is None:
        return
    workerinput = getattr(node, "workerinput", None)
    if isinstance(workerinput, dict):
        workerinput["mcp_pal_run_id"] = run_id.root


def _pytest_collection_modifyitems(session: _Any, config: _Any, items: list[_Any]) -> None:
    if getattr(config, "_mcp_pal_is_worker", False):
        return
    store = getattr(config, "_mcp_pal_manifest_store", None)
    run_id = getattr(config, "_mcp_pal_run_id", None)
    if store is None or run_id is None:
        return
    record = dict(store.get_test_run(run_id.root) or {})
    record["collected_node_ids"] = [str(item.nodeid) for item in items]
    record["collection_count"] = len(items)
    store.save_test_run(run_id.root, record)


def _record_collected(config: _Any, node_ids: list[str], *, worker_id: str | None = None) -> None:
    if getattr(config, "_mcp_pal_is_worker", False):
        return
    store = getattr(config, "_mcp_pal_manifest_store", None)
    run_id = getattr(config, "_mcp_pal_run_id", None)
    if store is None or run_id is None:
        return
    record = dict(store.get_test_run(run_id.root) or {})
    existing = {str(item) for item in record.get("collected_node_ids", ())}
    existing.update(str(item) for item in node_ids)
    record["collected_node_ids"] = sorted(existing)
    record["collection_count"] = len(existing)
    if worker_id is not None:
        workers = dict(record.get("worker_collections", {}))
        workers[str(worker_id)] = sorted(str(item) for item in node_ids)
        record["worker_collections"] = workers
    store.save_test_run(run_id.root, record)


def _pytest_collection_finish(session: _Any) -> None:
    config = session.config
    node_ids = [str(item.nodeid) for item in getattr(session, "items", ())]
    workerinput = getattr(config, "workerinput", None)
    if isinstance(workerinput, dict):
        workeroutput = getattr(config, "workeroutput", None)
        if isinstance(workeroutput, dict):
            workeroutput["mcp_pal_collected_node_ids"] = node_ids
        return
    _record_collected(config, node_ids)


def _pytest_xdist_node_collection_finished(node: _Any, ids: list[str]) -> None:
    config = getattr(node, "config", None) or _PLUGIN_CONFIG.get()
    worker_id = getattr(node, "gateway", None)
    worker_id = getattr(worker_id, "id", None) or getattr(node, "workerid", None) or "worker"
    _record_collected(config, [str(item) for item in ids], worker_id=str(worker_id))


def _pytest_testnodedown(node: _Any, error: object | None = None) -> None:
    if error is None:
        return
    config = getattr(node, "config", None) or _PLUGIN_CONFIG.get()
    if config is None or getattr(config, "_mcp_pal_is_worker", False):
        return
    store = getattr(config, "_mcp_pal_manifest_store", None)
    run_id = getattr(config, "_mcp_pal_run_id", None)
    if store is None or run_id is None:
        return
    record = dict(store.get_test_run(run_id.root) or {})
    errors = list(record.get("worker_errors", ()))
    errors.append({"worker": str(getattr(node, "gateway", None) or getattr(node, "workerid", "worker")), "error": _diagnostic(error)})
    record["worker_errors"] = errors
    store.save_test_run(run_id.root, record)


def _pytest_collectreport(report: _Any) -> None:
    """Retain collection failures, including when no test item exists."""
    config = getattr(report, "config", None) or _PLUGIN_CONFIG.get()
    if config is None or getattr(config, "_mcp_pal_is_worker", False):
        return
    store = getattr(config, "_mcp_pal_manifest_store", None)
    run_id = getattr(config, "_mcp_pal_run_id", None)
    if store is None or run_id is None or getattr(report, "outcome", "passed") == "passed":
        return
    record = dict(store.get_test_run(run_id.root) or {})
    reports = list(record.get("collection_reports", ()))
    reports.append(
        {
            "node_id": str(getattr(report, "nodeid", "")),
            "outcome": str(getattr(report, "outcome", "error")),
            "longrepr": _diagnostic(getattr(report, "longrepr", None)),
        }
    )
    record["collection_reports"] = reports
    store.save_test_run(run_id.root, record)


def _diagnostic(value: object, *, limit: int = 20_000) -> dict[str, object] | str:
    if value is None:
        return ""
    text = str(value)
    if len(text) <= limit:
        return text
    return {"text": text[:limit], "truncated": True, "total_characters": len(text)}


def _save_attempt(config: _Any, state: dict[str, object]) -> None:
    store = getattr(config, "_mcp_pal_manifest_store", None)
    run_id = getattr(config, "_mcp_pal_run_id", None)
    if store is None or run_id is None:
        return
    try:
        store.save_test_result(run_id.root, str(state["attempt_id"]), state)
    except Exception:
        # Test execution remains authoritative; export reports persistence
        # errors after the session rather than failing individual tests.
        config._mcp_pal_manifest_write_error = True


def _pytest_runtest_protocol(item: _Any, nextitem: _Any) -> object:
    del nextitem
    config = item.config
    run_id = getattr(config, "_mcp_pal_run_id", None)
    if run_id is None:
        yield
        return
    state = _test_attempt(
        run_id.root,
        str(item.nodeid),
        worker_id=str(getattr(config, "_mcp_pal_worker_id", "master")),
    )
    token = _activate_test(state)
    try:
        outcome = yield
        del outcome
    finally:
        state["finished_at"] = _now_iso()
        state["outcome"] = _attempt_outcome(state)
        _save_attempt(config, state)
        _reset_test(token)


def _attempt_outcome(state: dict[str, object]) -> str:
    phases = state.get("phases", {})
    if not isinstance(phases, dict) or not phases:
        return "not_run"
    values = {str(key): value for key, value in phases.items() if isinstance(value, dict)}
    if values.get("call", {}).get("outcome") == "failed":
        return "failed"
    if any(
        value.get("outcome") == "failed"
        for phase, value in values.items()
        if phase != "call"
    ):
        return "error"
    if any(value.get("outcome") == "skipped" for value in values.values()):
        return "skipped"
    if values.get("call", {}).get("outcome") == "passed":
        return "passed"
    return "error"


def _pytest_runtest_logreport(report: _Any) -> None:
    state = _active_test()
    if state is None or str(state.get("node_id")) != str(getattr(report, "nodeid", "")):
        return
    phases = state.setdefault("phases", {})
    if not isinstance(phases, dict):
        return
    phases[str(report.when)] = {
        "outcome": str(report.outcome),
        "duration_seconds": float(getattr(report, "duration", 0.0) or 0.0),
        "wasxfail": bool(getattr(report, "wasxfail", False)),
    }
    state["duration_seconds"] = sum(
        float(value.get("duration_seconds", 0.0) or 0.0)
        for value in phases.values()
        if isinstance(value, dict)
    )
    sections = getattr(report, "sections", ()) or ()
    diagnostics = state.setdefault("diagnostics", {})
    if isinstance(diagnostics, dict):
        for name, content in sections:
            diagnostics[f"{report.when}:{name}"] = _diagnostic(content)
        if getattr(report, "longrepr", None) is not None and report.outcome in {"failed", "skipped"}:
            diagnostics[f"{report.when}:longrepr"] = _diagnostic(report.longrepr)
    _save_attempt(report.config, state) if hasattr(report, "config") else None


def _pytest_sessionfinish(session: _Any, exitstatus: int) -> None:
    """Export deterministic feedback after pytest has finished collecting results."""
    config = session.config
    path = getattr(config, "_mcp_pal_database", None)
    run_id = getattr(config, "_mcp_pal_run_id", None)
    if path is None or run_id is None or getattr(config, "_mcp_pal_is_worker", False):
        return
    manifest_error = bool(getattr(config, "_mcp_pal_manifest_write_error", False))
    effective_exitstatus = 2 if manifest_error and int(exitstatus) == 0 else int(exitstatus)
    store = getattr(config, "_mcp_pal_manifest_store", None)
    if store is not None:
        record = dict(store.get_test_run(run_id.root) or {})
        collected = {str(item) for item in record.get("collected_node_ids", ())}
        recorded = {str(item.get("node_id")) for item in store.list_test_results(run_id.root)}
        worker_errors = list(record.get("worker_errors", ()))
        incomplete_workers = bool(worker_errors)
        if incomplete_workers and effective_exitstatus == 0:
            effective_exitstatus = 2
        record.update(
            {
                "status": "incomplete" if incomplete_workers else ("finished" if effective_exitstatus not in {2, 3, 4} else "interrupted"),
                "exit_status": effective_exitstatus,
                "finished_at": _now_iso(),
                "persistence_error": bool(getattr(config, "_mcp_pal_manifest_write_error", False)),
                "not_run_node_ids": sorted(collected - recorded),
            }
        )
        try:
            store.save_test_run(run_id.root, record)
        except Exception:
            config._mcp_pal_manifest_write_error = True
            if effective_exitstatus == 0:
                effective_exitstatus = 2
    manifest_error = manifest_error or bool(getattr(config, "_mcp_pal_manifest_write_error", False))
    if effective_exitstatus != int(exitstatus):
        session.exitstatus = effective_exitstatus
    if manifest_error:
        # The feedback bundle may still be useful, but it must not look like a
        # complete successful run when one or more attempts were not persisted.
        if int(exitstatus) == 0:
            session.exitstatus = 2
    try:
        from .feedback import build_feedback, export_feedback
        from .storage import SQLiteExecutionStore

        export_store = SQLiteExecutionStore(path)
        try:
            feedback = build_feedback(
                export_store,
                run_id,
                baseline_run_id=getattr(config, "_mcp_pal_baseline", None),
            )
            output = export_feedback(
                feedback,
                export_store,
                getattr(config, "_mcp_pal_project_root", _Path.cwd()) / ".mcp-pal" / "reports" / run_id.root,
            )
        finally:
            close = getattr(export_store, "close", None)
            if callable(close):
                close()
    except Exception as error:
        config._mcp_pal_feedback_error = "feedback export failed"
        if int(exitstatus) == 0:
            session.exitstatus = 2
        reporter = config.pluginmanager.getplugin("terminalreporter")
        if reporter is not None:
            reporter.write_line("MCP Pal feedback export failed", red=True)
        return
    config._mcp_pal_feedback_path = str(output)
    reporter = config.pluginmanager.getplugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("")
        reporter.write_line(f"MCP Pal run {run_id.root}")
        reporter.write_line(f"MCP Pal feedback: {output}")
        if manifest_error:
            reporter.write_line("MCP Pal test manifest persistence was incomplete", red=True)


class _ManifestHooks:
    """Internal hook object kept out of the public plugin module surface."""

    @_pytest.hookimpl(optionalhook=True)
    def pytest_configure_node(self, node: _Any) -> None:
        _pytest_configure_node(node)

    @_pytest.hookimpl
    def pytest_collection_modifyitems(self, session: _Any, config: _Any, items: list[_Any]) -> None:
        _pytest_collection_modifyitems(session, config, items)

    @_pytest.hookimpl
    def pytest_collection_finish(self, session: _Any) -> None:
        _pytest_collection_finish(session)

    @_pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node: _Any, ids: list[str]) -> None:
        _pytest_xdist_node_collection_finished(node, ids)

    @_pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: _Any, error: object | None = None) -> None:
        _pytest_testnodedown(node, error)

    @_pytest.hookimpl
    def pytest_collectreport(self, report: _Any) -> None:
        _pytest_collectreport(report)

    @_pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item: _Any, nextitem: _Any) -> object:
        yield from _pytest_runtest_protocol(item, nextitem)

    @_pytest.hookimpl
    def pytest_runtest_logreport(self, report: _Any) -> None:
        _pytest_runtest_logreport(report)

    @_pytest.hookimpl
    def pytest_sessionfinish(self, session: _Any, exitstatus: int) -> None:
        _pytest_sessionfinish(session, exitstatus)


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
