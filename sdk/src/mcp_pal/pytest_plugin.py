"""Opt-in pytest integration used by ``mcp-pal test``."""

from __future__ import annotations

import math as _math
import time as _time
from collections.abc import Iterator as _Iterator
from contextvars import ContextVar as _ContextVar
from pathlib import Path as _Path
from typing import Any as _Any
from uuid import uuid4 as _uuid4

import pytest as _pytest

from ._check_recording import (
    restore_default_record_checks as _restore_default_record_checks,
)
from ._check_recording import (
    set_default_record_checks as _set_default_record_checks,
)
from ._default_store import (
    install_default_run_id_factory as _install_default_run_id_factory,
)
from ._default_store import (
    install_default_store_factory as _install_default_store_factory,
)
from ._default_store import (
    restore_default_run_id_factory as _restore_default_run_id_factory,
)
from ._default_store import (
    restore_default_store_factory as _restore_default_store_factory,
)
from ._test_runs import (
    activate_test as _activate_test,
)
from ._test_runs import (
    active_test as _active_test,
)
from ._test_runs import (
    now_iso as _now_iso,
)
from ._test_runs import (
    reset_test as _reset_test,
)
from ._test_runs import (
    run_record as _run_record,
)
from ._test_runs import (
    test_attempt as _test_attempt,
)

_NATIVE_PROGRESS_UNSET = object()
_PLUGIN_CONFIG: _ContextVar[_Any] = _ContextVar(
    "mcp_pal_pytest_plugin_config", default=None
)


def pytest_addoption(parser: _Any) -> None:
    group = parser.getgroup("mcp-pal")
    group.addoption(
        "--mcp-pal-results-db",
        action="store",
        default=None,
        metavar="PATH",
        help="internal: persist default MCPTestKit executions in PATH",
    )
    group.addoption(
        "--mcp-pal-baseline",
        action="store",
        default=None,
        metavar="RUN_ID",
        help="internal: compare the exported feedback with RUN_ID",
    )
    group.addoption(
        "--mcp-pal-project-root",
        action="store",
        default=None,
        metavar="PATH",
        help="internal: project root used for feedback output",
    )
    group.addoption(
        "--mcp-pal-harness",
        action="append",
        default=[],
        metavar="KIND=MODEL[,MODEL...]",
    )
    group.addoption(
        "--mcp-pal-credential-env", action="append", default=[], metavar="TARGET=SOURCE"
    )
    group.addoption("--mcp-pal-trials", action="store", default=None, type=int)
    group.addoption("--mcp-pal-suite", action="store", default=None, metavar="NAME")
    group.addoption(
        "--mcp-pal-execution-timeout", action="store", default=None, type=float
    )


def pytest_configure(config: _Any) -> None:
    config._mcp_pal_config_token = _PLUGIN_CONFIG.set(config)
    config.addinivalue_line(
        "markers",
        "mcp_pal(agents=None, trials=None, suite_name=None): select agent executions",
    )
    suite = config.getoption("--mcp-pal-suite")
    if suite is not None and not str(suite).strip():
        raise _pytest.UsageError("--suite must not be blank")
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
    config._mcp_pal_project_root = (
        _Path(
            config.getoption("--mcp-pal-project-root")
            or getattr(config, "rootpath", _Path.cwd())
        )
        .expanduser()
        .resolve()
    )

    config._mcp_pal_manifest_store = SQLiteExecutionStore(path)
    config._mcp_pal_checks_token = _set_default_record_checks(True)
    if not config._mcp_pal_is_worker:
        config._mcp_pal_manifest_store.save_test_run(
            run_id.root,
            _run_record(
                run_id.root,
                project_root=str(config._mcp_pal_project_root),
                selection=tuple(
                    str(value) for value in getattr(config, "args", ()) or ()
                ),
                capture={
                    "mode": getattr(config.option, "capture", None),
                    "show_capture": bool(getattr(config.option, "showcapture", False)),
                    "verbose": int(getattr(config.option, "verbose", 0) or 0),
                },
            ),
        )
    config._mcp_pal_run_id_previous = _install_default_run_id_factory(lambda: run_id)
    config._mcp_pal_store_token = _install_default_store_factory(
        lambda: SQLiteExecutionStore(path)
    )
    config._mcp_pal_progress = _Progress(config)
    config._mcp_pal_progress.reporter = config.pluginmanager.getplugin(
        "terminalreporter"
    )
    if config._mcp_pal_progress.reporter is not None:
        config._mcp_pal_progress.enabled = (
            config._mcp_pal_progress.enabled and config._mcp_pal_progress._is_tty()
        )
    config._mcp_pal_progress.disable_native_progress()
    config.pluginmanager.register(config._mcp_pal_progress, "mcp-pal-progress")
    config._mcp_pal_manifest_hooks = _ManifestHooks()
    config.pluginmanager.register(
        config._mcp_pal_manifest_hooks, "mcp-pal-manifest-hooks"
    )


@_pytest.fixture
def mcp_pal_kit(request: _Any) -> _Any:
    from .sync_api import MCPTestKit

    marker = _merged_mcp_pal_marker(request.node)
    suite_name = marker.get("suite_name")
    kit = MCPTestKit(suite_name=str(suite_name) if suite_name else None)
    try:
        yield kit
    finally:
        kit.close()


@_pytest.fixture
def agent(request: _Any, mcp_pal_kit: _Any) -> _Any:
    selected = request.param
    # The generated logical case ID is stable across agent choices and trials.
    callspec = getattr(request.node, "callspec", None)
    import hashlib

    agent_id = f"{selected.harness}-{selected.model or 'profile'}-{selected.name}-trial-{selected.trial}"
    parameter_ids = list(getattr(callspec, "_idlist", ()))
    # Pytest's callspec.indices counts the Cartesian product, so a ToolMatrix
    # case gets a different index for each agent.  The per-parameter IDs retain
    # the ordinary case identity across those products.
    if agent_id not in parameter_ids:
        raise _pytest.UsageError("agent parameter ID is missing from pytest collection")
    parameter_ids.remove(agent_id)
    base = request.node.nodeid.split("[", 1)[0] + repr(parameter_ids)
    digest = hashlib.sha256(base.encode()).hexdigest()[:48]
    case_id = "case-" + digest
    from dataclasses import replace

    entry = dict(selected.entry)
    entry["_case_id"] = case_id
    entry["_matrix_id"] = request.node.nodeid.split("[", 1)[0]
    entry["_cell_id"] = "cell-" + digest
    return replace(selected, kit=mcp_pal_kit, entry=entry)


def _merged_mcp_pal_marker(node: _Any) -> dict[str, _Any]:
    """Merge inherited markers, with the closest marker taking precedence."""
    merged: dict[str, _Any] = {}
    markers = list(node.iter_markers(name="mcp_pal"))
    for marker in reversed(markers):
        merged.update(marker.kwargs)
    if merged.get("suite_name") is not None:
        merged["suite_name"] = str(merged["suite_name"]).strip()
    return merged


def _parse_cli_harnesses(config: _Any) -> list[dict[str, _Any]]:
    result: list[dict[str, _Any]] = []
    for raw in config.getoption("--mcp-pal-harness") or []:
        if "=" not in raw:
            raise _pytest.UsageError("--harness requires KIND=MODEL[,MODEL...]")
        kind, values = raw.split("=", 1)
        kind = kind.strip()
        models = [item.strip() for item in values.split(",")]
        if not kind or any(not item for item in models):
            raise _pytest.UsageError("--harness contains an empty kind or model")
        result.append({"harness": kind, "models": models})
    mappings: dict[str, str] = {}
    scoped: dict[str, dict[str, str]] = {}
    for raw in config.getoption("--mcp-pal-credential-env") or []:
        if "=" not in raw:
            raise _pytest.UsageError("--credential-env requires TARGET=SOURCE")
        target, source = raw.split("=", 1)
        scope = None
        if ":" in target:
            scope, target = target.split(":", 1)
            scope = scope.strip().lower().replace("-", "_")
            scope = {"claude": "claude_code"}.get(scope, scope)
        target = target.strip()
        if (scope, target) in {(None, key) for key in mappings} or (
            scope is not None and target in scoped.get(scope, {})
        ):
            raise _pytest.UsageError(f"duplicate credential target {target!r}")
        if scope is None:
            mappings[target] = source.strip()
        else:
            scoped.setdefault(scope, {})[target] = source.strip()
    config._mcp_pal_cli_credentials = (mappings, scoped)
    for entry in result:
        kind = str(entry["harness"]).lower().replace("-", "_")
        kind = {"claude": "claude_code"}.get(kind, kind)
        values = dict(mappings)
        values.update(scoped.get(kind, {}))
        if values and kind != "acp":
            entry["credential_env"] = values
    return result


def pytest_generate_tests(metafunc: _Any) -> None:
    if "agent" not in metafunc.fixturenames:
        return
    marker = metafunc.definition.get_closest_marker("mcp_pal")
    if marker is None:
        # A project may already provide an unrelated fixture named ``agent``.
        # Leave those tests to pytest's normal fixture resolution.
        return
    config = metafunc.config
    marker_kwargs = _merged_mcp_pal_marker(metafunc.definition)
    selected_suite = config.getoption("--mcp-pal-suite")
    if (
        selected_suite is not None
        and marker_kwargs.get("suite_name") != str(selected_suite).strip()
    ):
        metafunc.parametrize("agent", [], indirect=True)
        return
    selections = _parse_cli_harnesses(config)
    if not selections:
        marked = marker_kwargs.get("agents")
        if marked is None:
            # Bare marker is valid only when CLI supplies a selection.
            raise _pytest.UsageError(
                "agent test requires --harness or mcp_pal(agents=[...])"
            )
        selections = list(marked)
        credential_config: tuple[dict[str, str], dict[str, dict[str, str]]] = getattr(
            config, "_mcp_pal_cli_credentials", ({}, {})
        )
        mappings, scoped = credential_config
        if mappings or scoped:
            updated = []
            for raw in selections:
                value: dict[str, _Any] = dict(raw)
                kind = str(value.get("harness", "")).lower().replace("-", "_")
                kind = {"claude": "claude_code"}.get(kind, kind)
                credentials = dict(value.get("credential_env") or {})
                credentials.update(mappings)
                if kind in scoped:
                    credentials.update(scoped[kind])
                if credentials and kind != "acp":
                    value["credential_env"] = credentials
                updated.append(value)
            selections = updated
    else:
        marked = marker_kwargs.get("agents")
        if marked:
            by_kind: dict[str, list[_Any]] = {}
            for item in marked:
                if isinstance(item, dict) and "harness" in item:
                    key = str(item["harness"]).lower().replace("-", "_")
                    key = {"claude": "claude_code"}.get(key, key)
                    by_kind.setdefault(key, []).append(item)
            merged: list[dict[str, _Any]] = []
            for choice in selections:
                key = str(choice["harness"]).lower().replace("-", "_")
                key = {"claude": "claude_code"}.get(key, key)
                matches = by_kind.get(key, [])
                if len(matches) > 1:
                    raise _pytest.UsageError(
                        f"multiple marked agent configurations match harness {key!r}"
                    )
                value = dict(matches[0]) if matches else {}
                marked_credentials = dict(value.get("credential_env") or {})
                value.update(choice)
                if marked_credentials and key != "acp":
                    merged_credentials = dict(marked_credentials)
                    merged_credentials.update(choice.get("credential_env") or {})
                    value["credential_env"] = merged_credentials
                merged.append(value)
            selections = merged
    trials = config.getoption("--mcp-pal-trials")
    if trials is None:
        trials = marker_kwargs.get("trials", 1)
    execution_timeout = config.getoption("--mcp-pal-execution-timeout")
    if execution_timeout is not None:
        if not _math.isfinite(execution_timeout) or execution_timeout <= 0:
            raise _pytest.UsageError(
                "--execution-timeout must be a positive finite number"
            )
        selections = [
            dict(selection, _execution_timeout=execution_timeout)
            for selection in selections
        ]
    from ._agent_selection import expand

    # Expansion is pure and safe during collection.
    expanded = expand(None, selections, trials)
    ids = tuple(
        f"{item.harness}-{item.model or 'profile'}-{item.name}-trial-{item.trial}"
        for item in expanded
    )
    metafunc.parametrize("agent", expanded, indirect=True, ids=ids)


def pytest_collection_modifyitems(config: _Any, items: list[_Any]) -> None:
    selected = config.getoption("--mcp-pal-suite")
    if selected is None:
        return
    selected = str(selected).strip()
    kept: list[_Any] = []
    deselected: list[_Any] = []
    for item in items:
        marker = _merged_mcp_pal_marker(item)
        if marker.get("suite_name") == selected:
            kept.append(item)
        else:
            deselected.append(item)
    items[:] = kept
    if deselected:
        config.hook.pytest_deselected(items=deselected)


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


def _pytest_collection_modifyitems(
    session: _Any, config: _Any, items: list[_Any]
) -> None:
    # Legacy HarnessMatrix parameter values are generated independently of the
    # new ``agent`` fixture.  Apply the CLI filter to those values while they
    # are still collection items, preserving their own trial semantics.
    cli_choices = _parse_cli_harnesses(config)
    if cli_choices:
        allowed = {
            (
                str(choice["harness"]).lower().replace("-", "_"),
                model,
            )
            for choice in cli_choices
            for model in choice["models"]
        }
        allowed = {
            ({"claude": "claude_code"}.get(kind, kind), model)
            for kind, model in allowed
        }
        kept: list[_Any] = []
        deselected: list[_Any] = []
        for item in items:
            callspec = getattr(item, "callspec", None)
            value = next(
                (
                    value
                    for value in getattr(callspec, "params", {}).values()
                    if value.__class__.__name__ == "HarnessMatrixCase"
                ),
                None,
            )
            if value is None:
                kept.append(item)
                continue
            harness = getattr(getattr(value, "harness", None), "harness", None)
            kind = str(getattr(harness, "kind", "")).lower().replace("-", "_")
            kind = {"claude": "claude_code"}.get(kind, kind)
            model = getattr(harness, "model", None)
            if (kind, model) in allowed:
                kept.append(item)
            else:
                deselected.append(item)
        if deselected:
            items[:] = kept
            config.hook.pytest_deselected(items=deselected)
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


def _record_collected(
    config: _Any, node_ids: list[str], *, worker_id: str | None = None
) -> None:
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
    worker_id = (
        getattr(worker_id, "id", None) or getattr(node, "workerid", None) or "worker"
    )
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
    errors.append(
        {
            "worker": str(
                getattr(node, "gateway", None) or getattr(node, "workerid", "worker")
            ),
            "error": _diagnostic(error),
        }
    )
    record["worker_errors"] = errors
    store.save_test_run(run_id.root, record)


def _pytest_collectreport(report: _Any) -> None:
    """Retain collection failures, including when no test item exists."""
    config = getattr(report, "config", None) or _PLUGIN_CONFIG.get()
    if config is None or getattr(config, "_mcp_pal_is_worker", False):
        return
    store = getattr(config, "_mcp_pal_manifest_store", None)
    run_id = getattr(config, "_mcp_pal_run_id", None)
    if (
        store is None
        or run_id is None
        or getattr(report, "outcome", "passed") == "passed"
    ):
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


def _pytest_runtest_protocol(item: _Any, nextitem: _Any) -> _Iterator[_Any]:
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
        suite_name=_merged_mcp_pal_marker(item).get("suite_name"),
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
    values = {
        str(key): value for key, value in phases.items() if isinstance(value, dict)
    }
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
        if getattr(report, "longrepr", None) is not None and report.outcome in {
            "failed",
            "skipped",
        }:
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
    effective_exitstatus = (
        2 if manifest_error and int(exitstatus) == 0 else int(exitstatus)
    )
    store = getattr(config, "_mcp_pal_manifest_store", None)
    if store is not None:
        record = dict(store.get_test_run(run_id.root) or {})
        collected = {str(item) for item in record.get("collected_node_ids", ())}
        recorded = {
            str(item.get("node_id")) for item in store.list_test_results(run_id.root)
        }
        worker_errors = list(record.get("worker_errors", ()))
        incomplete_workers = bool(worker_errors)
        if incomplete_workers and effective_exitstatus == 0:
            effective_exitstatus = 2
        record.update(
            {
                "status": "incomplete"
                if incomplete_workers
                else (
                    "finished"
                    if effective_exitstatus not in {2, 3, 4}
                    else "interrupted"
                ),
                "exit_status": effective_exitstatus,
                "finished_at": _now_iso(),
                "persistence_error": bool(
                    getattr(config, "_mcp_pal_manifest_write_error", False)
                ),
                "not_run_node_ids": sorted(collected - recorded),
            }
        )
        try:
            store.save_test_run(run_id.root, record)
        except Exception:
            config._mcp_pal_manifest_write_error = True
            if effective_exitstatus == 0:
                effective_exitstatus = 2
    manifest_error = manifest_error or bool(
        getattr(config, "_mcp_pal_manifest_write_error", False)
    )
    if effective_exitstatus != int(exitstatus):
        session.exitstatus = effective_exitstatus
    if manifest_error:
        # The feedback bundle may still be useful, but it must not look like a
        # complete successful run when one or more attempts were not persisted.
        if int(exitstatus) == 0:
            session.exitstatus = 2
    timeout_summaries: list[tuple[str, str, float | None]] = []
    try:
        from .feedback import build_feedback, export_feedback
        from .storage import SQLiteExecutionStore

        export_store = SQLiteExecutionStore(path)
        try:
            timed_out_snapshots = []
            timeout_offset = 0
            while True:
                timeout_page = export_store.list_executions(
                    run_id=run_id.root,
                    outcome="timed_out",
                    limit=100,
                    offset=timeout_offset,
                )
                timed_out_snapshots.extend(timeout_page.items)
                timeout_offset += len(timeout_page.items)
                if not timeout_page.items or timeout_offset >= timeout_page.total:
                    break
            for snapshot in timed_out_snapshots:
                operation_timeout: dict[str, _Any] = next(
                    (
                        event.payload
                        for event in reversed(
                            tuple(export_store.iter_events(snapshot.execution_id))
                        )
                        if event.kind.value == "diagnostic"
                        and event.payload.get("code") == "operation_timeout"
                    ),
                    {},
                )
                timeout_summaries.append(
                    (
                        snapshot.execution_id.root,
                        str(operation_timeout.get("stage", "unknown")),
                        operation_timeout.get("elapsed_seconds"),
                    )
                )
            feedback = build_feedback(
                export_store,
                run_id,
                baseline_run_id=getattr(config, "_mcp_pal_baseline", None),
            )
            output = export_feedback(
                feedback,
                export_store,
                getattr(config, "_mcp_pal_project_root", _Path.cwd())
                / ".mcp-pal"
                / "reports"
                / run_id.root,
            )
        finally:
            close = getattr(export_store, "close", None)
            if callable(close):
                close()
    except Exception:
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
        for execution_id, stage, elapsed in timeout_summaries:
            elapsed_text = (
                f"{elapsed:.3f}s" if isinstance(elapsed, float) else "unknown"
            )
            reporter.write_line(
                "MCP Pal execution timeout: "
                f"id={execution_id} stage={stage} elapsed={elapsed_text} "
                f"feedback={output}"
            )
        if manifest_error:
            reporter.write_line(
                "MCP Pal test manifest persistence was incomplete", red=True
            )


class _ManifestHooks:
    """Internal hook object kept out of the public plugin module surface."""

    @_pytest.hookimpl(optionalhook=True)
    def pytest_configure_node(self, node: _Any) -> None:
        _pytest_configure_node(node)

    @_pytest.hookimpl
    def pytest_collection_modifyitems(
        self, session: _Any, config: _Any, items: list[_Any]
    ) -> None:
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
        self.enabled = int(getattr(option, "verbose", 0) or 0) <= 0 and not bool(
            getattr(option, "numprocesses", 0)
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
            if (
                report.outcome == "failed"
                and report.nodeid in self._counted
                and self._outcomes.get(report.nodeid) != "failed"
            ):
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
        if (
            self._native_progress is not _NATIVE_PROGRESS_UNSET
            and self.reporter is not None
        ):
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


# This order is a compatibility contract for the focused plugin surface.
__all__ = [  # noqa: RUF022
    "pytest_addoption",
    "pytest_configure",
    "pytest_unconfigure",
    "pytest_generate_tests",
    "mcp_pal_kit",
    "agent",
    "pytest_collection_modifyitems",
]
