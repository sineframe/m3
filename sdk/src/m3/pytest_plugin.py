"""Opt-in pytest integration used by ``m3 test``."""

from __future__ import annotations

import hashlib as _hashlib
import inspect as _inspect
import math as _math
import os as _os
import re as _re
import time as _time
from collections.abc import Iterator as _Iterator
from collections.abc import Mapping as _Mapping
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
    install_default_judge_limit_factory as _install_default_judge_limit_factory,
)
from ._default_store import (
    install_default_run_id_factory as _install_default_run_id_factory,
)
from ._default_store import (
    install_default_store_factory as _install_default_store_factory,
)
from ._default_store import (
    restore_default_judge_limit_factory as _restore_default_judge_limit_factory,
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
    evaluation_lineage as _evaluation_lineage,
)
from ._test_runs import (
    latest_evaluations as _latest_evaluations,
)
from ._test_runs import (
    now_iso as _now_iso,
)
from ._test_runs import (
    required_status_blocks as _required_status_blocks,
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
from ._test_runs import (
    xfail_waives_required_evaluations as _xfail_waives_required_evaluations,
)

_NATIVE_PROGRESS_UNSET = object()
_PLUGIN_CONFIG: _ContextVar[_Any] = _ContextVar("m3_pytest_plugin_config", default=None)
_ENV_NAME = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CREDENTIAL_SCOPES = {"claude_code", "opencode", "codex", "pi", "acp", "judge"}


def _items(value: object) -> tuple[object, ...]:
    """Return JSON-style collection values without assuming object iterability."""
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return ()


def _parse_credential_mappings(
    config: _Any,
) -> tuple[dict[str, str], dict[str, dict[str, str]], dict[str, str]]:
    harness: dict[str, str] = {}
    scoped: dict[str, dict[str, str]] = {}
    judge: dict[str, str] = {}
    seen: set[tuple[str | None, str]] = set()
    for raw in config.getoption("--credential-env") or []:
        if "=" not in raw:
            raise _pytest.UsageError("--credential-env requires TARGET=SOURCE")
        target, source = raw.split("=", 1)
        scope: str | None = None
        if ":" in target:
            scope, target = target.split(":", 1)
            scope = scope.strip().lower().replace("-", "_")
            scope = {"claude": "claude_code"}.get(scope, scope)
            if scope not in _CREDENTIAL_SCOPES:
                raise _pytest.UsageError("unknown credential scope")
        target, source = target.strip(), source.strip()
        if not _ENV_NAME.fullmatch(target) or not _ENV_NAME.fullmatch(source):
            raise _pytest.UsageError(
                "credential environment names must be Python identifiers"
            )
        if (scope, target) in seen:
            raise _pytest.UsageError(f"duplicate credential target {target!r}")
        seen.add((scope, target))
        if scope == "judge":
            judge[target] = source
        elif scope is None:
            harness[target] = source
        else:
            scoped.setdefault(scope, {})[target] = source
    return harness, scoped, judge


def pytest_addoption(parser: _Any) -> None:
    group = parser.getgroup("m3")
    group.addoption(
        "--results-db",
        action="store",
        default=None,
        metavar="PATH",
        help="persist default MCPTestKit executions in PATH",
    )
    group.addoption(
        "--baseline",
        action="store",
        default=None,
        metavar="RUN_ID",
        help="compare the exported feedback with RUN_ID",
    )
    group.addoption(
        "--project-root",
        action="store",
        default=None,
        metavar="PATH",
        help="project root used for persistence and feedback output",
    )
    group.addoption(
        "--harness",
        action="append",
        default=[],
        metavar="KIND=MODEL[,MODEL...]",
    )
    group.addoption("--runtime", action="store", default=None, metavar="RUNTIME")
    group.addoption(
        "--harness-runtime", dest="runtime", action="store", metavar="RUNTIME"
    )
    group.addoption(
        "--harness-version",
        dest="harness_version",
        action="store",
        default=None,
        metavar="VERSION",
    )
    group.addoption(
        "--credential-env", action="append", default=[], metavar="TARGET=SOURCE"
    )
    group.addoption("--trials", action="store", default=None, type=int)
    group.addoption(
        "--m3-server-selections",
        action="store",
        default=None,
        help="internal JSON server selection passed by m3 test",
    )
    group.addoption("--suite", action="store", default=None, metavar="NAME")
    group.addoption("--execution-timeout", action="store", default=None, type=float)
    group.addoption("--judge-max-requests", action="store", default=None, type=int)


def pytest_configure(config: _Any) -> None:
    mappings, scoped, judge = _parse_credential_mappings(config)
    environment = dict(_os.environ)
    judge_values: dict[str, str] = {}
    for target, source in judge.items():
        value = environment.get(source)
        if not value:
            raise _pytest.UsageError("judge credential source is unavailable")
        judge_values[target] = value
    config._m3_cli_credentials = (mappings, scoped)
    config._m3_judge_env_previous = {
        target: environment.get(target) for target in judge_values
    }
    _os.environ.update(judge_values)
    config._m3_config_token = _PLUGIN_CONFIG.set(config)
    judge_limit = config.getoption("--judge-max-requests")
    if judge_limit is not None and judge_limit < 0:
        raise _pytest.UsageError("--judge-max-requests must be nonnegative")
    config._m3_judge_max_requests = judge_limit
    config.addinivalue_line(
        "markers",
        "m3(agents=None, servers=None, trials=None, suite_name=None): select agent and server executions",
    )
    suite = config.getoption("--suite")
    if suite is not None and not str(suite).strip():
        raise _pytest.UsageError("--suite must not be blank")
    raw = config.getoption("--results-db")
    if not raw:
        return
    from .storage import SQLiteExecutionStore

    path = str(_Path(raw).expanduser().resolve())
    from .types import RunId

    workerinput = getattr(config, "workerinput", None)
    if isinstance(workerinput, dict) and workerinput.get("m3_run_id"):
        run_id = RunId(str(workerinput["m3_run_id"]))
        worker_id = str(workerinput.get("workerid", "worker"))
    else:
        run_id = RunId(f"run-{_uuid4().hex}")
        worker_id = "master"
    config._m3_run_id = run_id
    config._m3_database = path
    config._m3_baseline = config.getoption("--baseline")
    config._m3_worker_id = worker_id
    config._m3_is_worker = isinstance(workerinput, dict)
    config._m3_project_root = (
        _Path(
            config.getoption("--project-root")
            or getattr(config, "rootpath", _Path.cwd())
        )
        .expanduser()
        .resolve()
    )
    config._m3_project_id = None
    config._m3_project_name = None
    project_file = config._m3_project_root / "m3.toml"
    if project_file.is_file():
        try:
            try:
                from importlib import import_module

                toml_parser = import_module("tomllib")
            except ModuleNotFoundError:
                from importlib import import_module

                toml_parser = import_module("tomli")
            identity = toml_parser.loads(project_file.read_text(encoding="utf-8"))
            from .types import ProjectId

            if identity.get("schema_version") != 1:
                raise ValueError("unsupported schema_version")
            name = identity.get("project_name")
            if not isinstance(name, str) or not name.strip() or len(name) > 256:
                raise ValueError(
                    "project_name must be non-empty and at most 256 characters"
                )
            config._m3_project_id = ProjectId(str(identity["project_id"]))
            config._m3_project_name = name.strip()
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise _pytest.UsageError(
                "m3.toml must contain a valid project_id and project_name"
            ) from exc

    config._m3_manifest_store = SQLiteExecutionStore(path)
    if config._m3_project_id is not None:
        config._m3_manifest_store.ensure_project(
            config._m3_project_id.root, config._m3_project_name
        )
    config._m3_checks_token = _set_default_record_checks(True)
    if not config._m3_is_worker:
        config._m3_manifest_store.save_test_run(
            run_id.root,
            _run_record(
                run_id.root,
                project_root=str(config._m3_project_root),
                selection=tuple(
                    str(value) for value in getattr(config, "args", ()) or ()
                ),
                capture={
                    "mode": getattr(config.option, "capture", None),
                    "show_capture": bool(getattr(config.option, "showcapture", False)),
                    "verbose": int(getattr(config.option, "verbose", 0) or 0),
                },
                project_id=(
                    config._m3_project_id.root
                    if config._m3_project_id is not None
                    else None
                ),
                project_name=getattr(config, "_m3_project_name", None),
            ),
        )
    config._m3_run_id_previous = _install_default_run_id_factory(lambda: run_id)
    config._m3_judge_limit_previous = _install_default_judge_limit_factory(
        lambda: config._m3_judge_max_requests
    )
    config._m3_store_token = _install_default_store_factory(
        lambda: SQLiteExecutionStore(path)
    )
    config._m3_progress = _Progress(config)
    config._m3_progress.reporter = config.pluginmanager.getplugin("terminalreporter")
    if config._m3_progress.reporter is not None:
        config._m3_progress.enabled = (
            config._m3_progress.enabled and config._m3_progress._is_tty()
        )
    config._m3_progress.disable_native_progress()
    config.pluginmanager.register(config._m3_progress, "m3-progress")
    config._m3_manifest_hooks = _ManifestHooks()
    config.pluginmanager.register(config._m3_manifest_hooks, "m3-manifest-hooks")


@_pytest.fixture
def m3_kit(request: _Any) -> _Any:
    from .sync_api import MCPTestKit

    marker = _merged_m3_marker(request.node)
    suite_name = marker.get("suite_name")
    kit = MCPTestKit(
        suite_name=str(suite_name) if suite_name else None,
        project_id=getattr(request.config, "_m3_project_id", None),
        max_judge_requests=getattr(request.config, "_m3_judge_max_requests", None),
    )
    try:
        yield kit
    finally:
        kit.close()


def _agent_parameter_id(selected: _Any) -> str:
    identity = f"{selected.harness}-{selected.model or 'profile'}-{selected.name}"
    runtime = selected.entry.get("runtime", "system")
    if runtime == "managed":
        identity += f"-managed-{selected.entry.get('version') or 'latest'}"
    return f"{identity}-trial-{selected.trial}"


@_pytest.fixture
def agent(request: _Any, m3_kit: _Any) -> _Any:
    selected = request.param
    # The generated logical case ID is stable across agent choices and trials.
    callspec = getattr(request.node, "callspec", None)
    import hashlib

    agent_id = _agent_parameter_id(selected)
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
    return replace(selected, kit=m3_kit, entry=entry)


@_pytest.fixture
def server(request: _Any) -> _Any:
    """Selected typed server for an M3 marked test."""
    return request.param


def _merged_m3_marker(node: _Any) -> dict[str, _Any]:
    """Merge inherited markers, with the closest marker taking precedence."""
    merged: dict[str, _Any] = {}
    markers = list(node.iter_markers(name="m3"))
    for marker in reversed(markers):
        merged.update(marker.kwargs)
    if merged.get("suite_name") is not None:
        merged["suite_name"] = str(merged["suite_name"]).strip()
    return merged


def _parse_cli_harnesses(config: _Any) -> list[dict[str, _Any]]:
    result: list[dict[str, _Any]] = []
    for raw in config.getoption("--harness") or []:
        if "=" not in raw:
            raise _pytest.UsageError("--harness requires KIND=MODEL[,MODEL...]")
        kind, values = raw.split("=", 1)
        kind = kind.strip()
        requested_version: str | None = None
        if "@" in kind:
            kind, requested_version = (part.strip() for part in kind.split("@", 1))
            if not requested_version:
                raise _pytest.UsageError("--harness contains an empty version")
        models = [item.strip() for item in values.split(",")]
        if not kind or any(not item for item in models):
            raise _pytest.UsageError("--harness contains an empty kind or model")
        entry: dict[str, _Any] = {"harness": kind, "models": models}
        if requested_version:
            entry["version"] = requested_version
            entry["runtime"] = "managed"
        result.append(entry)
    mappings, scoped = config._m3_cli_credentials
    runtime = config.getoption("--runtime")
    version = config.getoption("--harness-version")
    for entry in result:
        kind = str(entry["harness"]).lower().replace("-", "_")
        kind = {"claude": "claude_code"}.get(kind, kind)
        values = dict(mappings)
        values.update(scoped.get(kind, {}))
        if values and kind != "acp":
            entry["credential_env"] = values
        if kind != "acp":
            if runtime and "runtime" not in entry:
                entry["runtime"] = runtime
            if version and "version" not in entry:
                entry["version"] = version
    return result


def _parameterize_agent(metafunc: _Any) -> None:
    if "agent" not in metafunc.fixturenames:
        return
    config = metafunc.config
    marker_kwargs = _merged_m3_marker(metafunc.definition)
    selected_suite = config.getoption("--suite")
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
                "agent test requires --harness or m3(agents=[...])"
            )
        selections = list(marked)
        credential_config: tuple[dict[str, str], dict[str, dict[str, str]]] = getattr(
            config, "_m3_cli_credentials", ({}, {})
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
    selected_runtime = config.getoption("--runtime")
    selected_version = config.getoption("--harness-version")
    if selected_runtime or selected_version:
        updated_selections = []
        for item in selections:
            if not isinstance(item, _Mapping):
                updated_selections.append(item)
                continue
            value = dict(item)
            kind = str(value.get("harness", "")).lower().replace("-", "_")
            if kind != "acp":
                if selected_runtime and "runtime" not in value:
                    value["runtime"] = selected_runtime
                effective_runtime = value.get("runtime", selected_runtime or "system")
                if (
                    selected_version
                    and effective_runtime == "managed"
                    and "version" not in value
                ):
                    value["version"] = selected_version
            updated_selections.append(value)
        selections = updated_selections
    trials = config.getoption("--trials")
    if trials is None:
        trials = marker_kwargs.get("trials", 1)
    execution_timeout = config.getoption("--execution-timeout")
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
    ids = tuple(_agent_parameter_id(item) for item in expanded)
    metafunc.parametrize("agent", expanded, indirect=True, ids=ids)


def _server_choices(
    config: _Any, marker_kwargs: _Mapping[str, _Any]
) -> tuple[_Any, ...] | None:
    raw_cli = config.getoption("--m3-server-selections")
    if raw_cli is not None:
        import json

        try:
            raw = json.loads(raw_cli)
        except (TypeError, ValueError) as exc:
            raise _pytest.UsageError(
                "--m3-server-selections must be a JSON array"
            ) from exc
        if not isinstance(raw, list) or not raw:
            raise _pytest.UsageError(
                "--m3-server-selections must be a non-empty JSON array"
            )
    else:
        raw = marker_kwargs.get("servers")
        if raw is None:
            return None
    try:
        from ._server_selection import normalize_servers

        return normalize_servers(raw)
    except (TypeError, ValueError) as exc:
        raise _pytest.UsageError(str(exc)) from exc


def pytest_generate_tests(metafunc: _Any) -> None:
    marker = metafunc.definition.get_closest_marker("m3")
    if marker is None:
        # Unmarked projects may define their own agent/server fixtures.
        return
    marker_kwargs = _merged_m3_marker(metafunc.definition)
    selected_suite = metafunc.config.getoption("--suite")
    if (
        selected_suite is not None
        and marker_kwargs.get("suite_name") != str(selected_suite).strip()
    ):
        # Skip validation for a suite that collection will deselect. Agent
        # parametrization still needs an empty selection to avoid expansion.
        _parameterize_agent(metafunc)
        return

    fixture_defs = getattr(metafunc, "_arg2fixturedefs", {}).get("server", ())
    project_server_fixture = bool(
        fixture_defs
        and fixture_defs[-1].func is not getattr(server, "__wrapped__", None)
    )
    selected_servers = _server_choices(metafunc.config, marker_kwargs)
    if "server" in metafunc.fixturenames:
        if selected_servers is None:
            if not project_server_fixture:
                raise _pytest.UsageError(
                    "server fixture requires --server selections or m3(servers=[...])"
                )
        elif project_server_fixture:
            raise _pytest.UsageError(
                "M3 server selections conflict with a user fixture named 'server'"
            )
        else:
            ids = tuple(
                f"server-{('http' if item.kind == 'streamable_http' else 'stdio')}-{i + 1}"
                for i, item in enumerate(selected_servers)
            )
            if "agent" in metafunc.fixturenames:
                from ._types.base import TrustLevel
                from ._types.specs import HTTPServer

                if any(
                    isinstance(item, HTTPServer) and item.trust is TrustLevel.UNTRUSTED
                    for item in selected_servers
                ):
                    raise _pytest.UsageError(
                        "agent HTTP server has trust=untrusted; set trust='public' (or --trust public) for a public endpoint, or trust='trusted_private' for a private endpoint you own"
                    )
            metafunc.parametrize("server", selected_servers, indirect=True, ids=ids)
    _parameterize_agent(metafunc)


def pytest_collection_modifyitems(config: _Any, items: list[_Any]) -> None:
    selected = config.getoption("--suite")
    if selected is None:
        return
    selected = str(selected).strip()
    kept: list[_Any] = []
    deselected: list[_Any] = []
    for item in items:
        marker = _merged_m3_marker(item)
        if marker.get("suite_name") == selected:
            kept.append(item)
        else:
            deselected.append(item)
    items[:] = kept
    if deselected:
        config.hook.pytest_deselected(items=deselected)


def pytest_unconfigure(config: _Any) -> None:
    for target, previous in getattr(config, "_m3_judge_env_previous", {}).items():
        if previous is None:
            _os.environ.pop(target, None)
        else:
            _os.environ[target] = previous
    hooks = getattr(config, "_m3_manifest_hooks", None)
    if hooks is not None:
        config.pluginmanager.unregister(hooks)
    progress = getattr(config, "_m3_progress", None)
    if progress is not None:
        progress.finish()
        progress.restore_native_progress()
    token = getattr(config, "_m3_store_token", None)
    if token is not None:
        _restore_default_store_factory(token)
    if hasattr(config, "_m3_run_id_previous"):
        _restore_default_run_id_factory(config._m3_run_id_previous)
    if hasattr(config, "_m3_judge_limit_previous"):
        _restore_default_judge_limit_factory(config._m3_judge_limit_previous)
    check_token = getattr(config, "_m3_checks_token", None)
    if check_token is not None:
        _restore_default_record_checks(check_token)
    store = getattr(config, "_m3_manifest_store", None)
    if store is not None:
        try:
            store.close()
        except Exception:
            pass
    config_token = getattr(config, "_m3_config_token", None)
    if config_token is not None:
        _PLUGIN_CONFIG.reset(config_token)


def _pytest_configure_node(node: _Any) -> None:
    """Pass controller-owned identity to optional xdist workers."""
    config = getattr(node, "config", None)
    run_id = getattr(config, "_m3_run_id", None)
    if run_id is None:
        return
    workerinput = getattr(node, "workerinput", None)
    if isinstance(workerinput, dict):
        workerinput["m3_run_id"] = run_id.root


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
    if getattr(config, "_m3_is_worker", False):
        return
    store = getattr(config, "_m3_manifest_store", None)
    run_id = getattr(config, "_m3_run_id", None)
    if store is None or run_id is None:
        return
    record = dict(store.get_test_run(run_id.root) or {})
    record["collected_node_ids"] = [str(item.nodeid) for item in items]
    record["collection_count"] = len(items)
    store.save_test_run(run_id.root, record)


def _record_collected(
    config: _Any, node_ids: list[str], *, worker_id: str | None = None
) -> None:
    if getattr(config, "_m3_is_worker", False):
        return
    store = getattr(config, "_m3_manifest_store", None)
    run_id = getattr(config, "_m3_run_id", None)
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
            workeroutput["m3_collected_node_ids"] = node_ids
        return
    # At collection-finish this is the controller's final, post-filter item
    # list.  Earlier collection hooks may have observed deselected items; do
    # not retain those as manifest-only not-run requirements.
    store = getattr(config, "_m3_manifest_store", None)
    run_id = getattr(config, "_m3_run_id", None)
    if store is None or run_id is None:
        return
    record = dict(store.get_test_run(run_id.root) or {})
    record["collected_node_ids"] = sorted(set(node_ids))
    record["collection_count"] = len(node_ids)
    store.save_test_run(run_id.root, record)


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
    if config is None or getattr(config, "_m3_is_worker", False):
        return
    store = getattr(config, "_m3_manifest_store", None)
    run_id = getattr(config, "_m3_run_id", None)
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
    if config is None or getattr(config, "_m3_is_worker", False):
        return
    store = getattr(config, "_m3_manifest_store", None)
    run_id = getattr(config, "_m3_run_id", None)
    if (
        store is None
        or run_id is None
        or getattr(report, "outcome", "passed") != "failed"
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
    store = getattr(config, "_m3_manifest_store", None)
    run_id = getattr(config, "_m3_run_id", None)
    if store is None or run_id is None:
        return
    try:
        store.save_test_result(run_id.root, str(state["attempt_id"]), state)
    except Exception:
        # Test execution remains authoritative; export reports persistence
        # errors after the session rather than failing individual tests.
        config._m3_manifest_write_error = True


def _pytest_runtest_protocol(item: _Any, nextitem: _Any) -> _Iterator[_Any]:
    del nextitem
    config = item.config
    run_id = getattr(config, "_m3_run_id", None)
    if run_id is None:
        yield
        return
    function = item if isinstance(item, _pytest.Function) else None
    raw_description = getattr(getattr(function, "function", None), "__doc__", "")
    description = raw_description if isinstance(raw_description, str) else ""
    state = _test_attempt(
        run_id.root,
        str(item.nodeid),
        worker_id=str(getattr(config, "_m3_worker_id", "master")),
        suite_name=_merged_m3_marker(item).get("suite_name"),
        description=_inspect.cleandoc(description).strip(),
        project_id=(
            project.root
            if (project := getattr(config, "_m3_project_id", None)) is not None
            else None
        ),
        project_name=getattr(config, "_m3_project_name", None),
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
    exception_types = state.get("_m3_exception_types")
    exception_type = (
        exception_types.pop(str(report.when), None)
        if isinstance(exception_types, dict)
        else None
    )
    if report.outcome == "failed":
        if isinstance(exception_type, str):
            phases[str(report.when)]["exception_type"] = exception_type
        else:
            crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
            message = getattr(crash, "message", None)
            if isinstance(message, str):
                phases[str(report.when)]["exception_type"] = message.split(":", 1)[0]
    if not state.get("_m3_exception_types"):
        state.pop("_m3_exception_types", None)
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


def _pytest_runtest_makereport(item: _Any, call: _Any) -> None:
    state = _active_test()
    if state is None or str(state.get("node_id")) != str(getattr(item, "nodeid", "")):
        return
    exception = getattr(getattr(call, "excinfo", None), "type", None)
    if not isinstance(exception, type):
        return
    name = exception.__qualname__
    if exception.__module__ != "builtins":
        name = f"{exception.__module__}.{name}"
    exception_types = state.setdefault("_m3_exception_types", {})
    if isinstance(exception_types, dict):
        exception_types[str(call.when)] = name


def _manifest_not_run_attempt(
    run_id: str, node_id: str, manifest: _Any
) -> dict[str, object]:
    """Build the stable, auditable attempt for a collected-but-never-run test."""

    digest = _hashlib.sha256(str(node_id).encode("utf-8")).hexdigest()[:32]
    finished_at = manifest.get("finished_at") if isinstance(manifest, dict) else None
    project_id = manifest.get("project_id") if isinstance(manifest, dict) else None
    project_name = manifest.get("project_name") if isinstance(manifest, dict) else None
    return {
        "schema_version": 1,
        "attempt_id": f"{run_id}:manifest-not-run:{digest}",
        "run_id": str(run_id),
        "node_id": str(node_id),
        "description": "",
        "worker_id": "controller",
        "suite_id": None,
        "suite_name": None,
        "project_id": project_id,
        "project_name": project_name,
        "phases": {},
        "outcome": "not_run",
        "verdict": "not_run",
        "duration": None,
        "duration_seconds": None,
        "execution_ids": [],
        "diagnostics": {"session": "not_run"},
        "diagnostic": "session:not_run",
        "started_at": None,
        "finished_at": finished_at,
        "record_source": "manifest_not_run",
    }


def _persist_manifest_not_run(
    config: _Any, store: _Any, run_id: str, manifest: dict[str, object]
) -> bool:
    """Persist manifest-only attempts, retaining the manifest audit list."""

    collected = {str(item) for item in _items(manifest.get("collected_node_ids", ()))}
    listed = {str(item) for item in _items(manifest.get("not_run_node_ids", ()))}
    try:
        recorded = {
            str(item.get("node_id")) for item in store.list_test_results(run_id)
        }
    except Exception:
        config._m3_manifest_write_error = True
        return False
    missing = sorted((listed or (collected - recorded)) - recorded)
    failed = False
    for node_id in missing:
        attempt = _manifest_not_run_attempt(run_id, node_id, manifest)
        try:
            store.save_test_result(run_id, str(attempt["attempt_id"]), attempt)
        except Exception:
            failed = True
    if failed:
        config._m3_manifest_write_error = True
    return not failed


def _required_evaluation_issues(
    store: _Any,
    run_id: str,
    attempts: tuple[dict[str, object], ...],
    *,
    persistence_error: bool = False,
    collection_error: bool = False,
) -> tuple[str, ...]:
    """Resolve required evaluation verdicts without depending on feedback."""

    issues: list[str] = []
    for attempt in attempts:
        if str(attempt.get("outcome")) in {"not_run", "error"}:
            issues.append(
                f"incomplete pytest attempt {attempt.get('node_id', '<unknown>')}"
            )
        detached = attempt.get("detached_evaluations")
        if (
            isinstance(detached, _Mapping)
            or not isinstance(detached, (list, tuple))
            or _xfail_waives_required_evaluations(attempt)
        ):
            detached = ()
        for evaluation in detached:
            if not isinstance(evaluation, _Mapping) or not bool(
                evaluation.get("required", False)
            ):
                continue
            status = evaluation.get("status")
            if _required_status_blocks(status):
                issues.append(
                    "required evaluation did not pass "
                    f"{attempt.get('node_id', '<unknown>')}:{evaluation.get('name', '<unknown>')}"
                )
        phases = attempt.get("phases")
        diagnostics = attempt.get("diagnostics")
        has_phase_error = isinstance(diagnostics, _Mapping) and any(
            str(key).split(":", 1)[0] in {"setup", "teardown"}
            and str(key).endswith(":longrepr")
            for key in diagnostics
        )
        has_xfail_phase = isinstance(phases, _Mapping) and any(
            isinstance(value, _Mapping) and value.get("wasxfail")
            for value in phases.values()
        )
        if has_phase_error and has_xfail_phase:
            issues.append(
                f"incomplete pytest attempt {attempt.get('node_id', '<unknown>')}"
            )
    if persistence_error:
        issues.append("test manifest persistence is incomplete")
    if collection_error:
        issues.append("pytest collection reported an error")
    linked_attempts: dict[str, list[bool]] = {}
    for attempt in attempts:
        linked = [str(item) for item in _items(attempt.get("execution_ids", ()))]
        for execution_id in linked:
            linked_attempts.setdefault(execution_id, []).append(
                _xfail_waives_required_evaluations(attempt)
            )
    waivers = {
        execution_id: all(values)
        for execution_id, values in linked_attempts.items()
        if values
    }
    running_attempt_execution_ids = {
        str(item)
        for attempt in attempts
        if str(attempt.get("outcome")) == "running"
        for item in _items(attempt.get("execution_ids", ()))
    }
    offset = 0
    executions: list[_Any] = []
    while True:
        page = store.list_executions(limit=100, offset=offset, run_id=run_id)
        executions.extend(page.items)
        offset += len(page.items)
        if not page.items or offset >= page.total:
            break
    for entry in executions:
        raw_execution_id = getattr(entry, "execution_id", "")
        execution_id = str(getattr(raw_execution_id, "root", raw_execution_id))
        if not execution_id:
            continue
        spec = store.get_execution_spec(execution_id)
        spec_required_names = {
            str(item.name)
            for item in (getattr(spec, "evaluations", ()) if spec else ())
            if bool(getattr(item, "required", False))
        }
        declared = set(spec_required_names)
        records = tuple(store.evaluations(execution_id))
        dynamic_names = {str(record.name) for record in records if record.required}
        declared.update(dynamic_names)
        if not declared:
            continue
        by_name: dict[str, list[_Any]] = {}
        for record in records:
            by_name.setdefault(str(record.name), []).append(record)
        lifecycle = getattr(getattr(entry, "lifecycle", None), "value", None)
        terminal = lifecycle == "finished"
        for name in sorted(declared):
            relevant = by_name.get(name, ())
            if not relevant:
                if (
                    terminal
                    and execution_id not in running_attempt_execution_ids
                    and not waivers.get(execution_id)
                ):
                    issues.append(f"missing required evaluation {execution_id}:{name}")
                continue
            if waivers.get(execution_id):
                continue
            by_lineage: dict[
                tuple[str, str | None, str, str | None, str | None], list[_Any]
            ] = {}
            for record in relevant:
                key = _evaluation_lineage(record)
                by_lineage.setdefault(key, []).append(record)
            for lineage_records in by_lineage.values():
                if name not in spec_required_names and not any(
                    record.required for record in lineage_records
                ):
                    continue
                latest = next(iter(_latest_evaluations(lineage_records).values()))
                status = str(getattr(latest.status, "value", latest.status))
                if _required_status_blocks(status):
                    issues.append(
                        f"required evaluation did not pass {execution_id}:{name}"
                    )
    return tuple(dict.fromkeys(issues))


def _manifest_status(manifest: _Mapping[str, object], exit_status: int) -> str:
    if manifest.get("worker_errors"):
        return "incomplete"
    return "interrupted" if exit_status in {2, 3, 4} else "finished"


def _save_manifest(
    config: _Any,
    store: _Any,
    run_id: str,
    updates: _Mapping[str, object],
    *,
    terminal_status: int | None = None,
    session: _Any | None = None,
    fail_closed: bool = False,
) -> bool:
    """Merge and persist terminal manifest fields with consistent failure handling."""

    try:
        manifest = dict(store.get_test_run(run_id) or {})
        manifest.update(updates)
        if terminal_status is not None:
            manifest["exit_status"] = terminal_status
            manifest["status"] = _manifest_status(manifest, terminal_status)
        store.save_test_run(run_id, manifest)
    except Exception:
        config._m3_manifest_write_error = True
        if (
            fail_closed
            and session is not None
            and int(getattr(session, "exitstatus", 0)) == 0
        ):
            session.exitstatus = 1
        return False
    return True


def _save_feedback_counters(
    config: _Any,
    store: _Any,
    run_id: str,
    feedback: _Any,
    session: _Any,
    exitstatus: int,
) -> bool:
    """Persist counters before exporting the manifest-backed feedback bundle."""

    try:
        outcome_counts: dict[str, int] = {}
        for attempt in store.list_test_results(run_id):
            outcome = str(attempt.get("outcome", "unknown"))
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        verdict_counts: dict[str, int] = {}
        for test in feedback.tests:
            verdict = str(test.get("effective_verdict", test.get("verdict", "unknown")))
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        return not _save_manifest(
            config,
            store,
            run_id,
            {
                "test_outcome_counts": outcome_counts,
                "effective_verdict_counts": verdict_counts,
            },
            terminal_status=int(getattr(session, "exitstatus", exitstatus)),
            session=session,
            fail_closed=True,
        )
    except Exception:
        config._m3_manifest_write_error = True
        if int(exitstatus) == 0:
            session.exitstatus = 1
        return True


def _pytest_sessionfinish(session: _Any, exitstatus: int) -> None:
    """Export deterministic feedback after pytest has finished collecting results."""
    config = session.config
    path = getattr(config, "_m3_database", None)
    run_id = getattr(config, "_m3_run_id", None)
    if path is None or run_id is None or getattr(config, "_m3_is_worker", False):
        return
    # Collection-only runs enumerate parametrized cases but intentionally do
    # not execute them.  They are not terminal test sessions: finalizing their
    # manifest would turn every collected node into a false not_run failure.
    if bool(getattr(getattr(config, "option", None), "collectonly", False)):
        return
    manifest_error = bool(getattr(config, "_m3_manifest_write_error", False))
    effective_exitstatus = (
        1 if manifest_error and int(exitstatus) == 0 else int(exitstatus)
    )
    store = getattr(config, "_m3_manifest_store", None)
    no_executed_tests = False
    if store is not None:
        record = dict(store.get_test_run(run_id.root) or {})
        collected = {str(item) for item in record.get("collected_node_ids", ())}
        attempts = store.list_test_results(run_id.root)
        recorded = {str(item.get("node_id")) for item in attempts}
        # A genuine xfail is represented as a skipped call phase by pytest,
        # but the test did execute. Ordinary skips still do not count.
        executed_attempt = any(
            item.get("outcome") in {"passed", "failed", "error"}
            or (
                item.get("outcome") == "skipped"
                and isinstance(item.get("phases"), _Mapping)
                and any(
                    isinstance(phase, _Mapping) and phase.get("wasxfail")
                    for phase in item["phases"].values()
                )
            )
            for item in attempts
        )
        no_executed_tests = effective_exitstatus == 0 and not executed_attempt
        if no_executed_tests:
            effective_exitstatus = 1
        worker_errors = list(record.get("worker_errors", ()))
        incomplete_workers = bool(worker_errors)
        if incomplete_workers and effective_exitstatus == 0:
            effective_exitstatus = 1
        not_run_node_ids = sorted(collected - recorded)
        finished_at = _now_iso()
        record["not_run_node_ids"] = not_run_node_ids
        record["finished_at"] = finished_at
        # Persist real manifest-only attempts before feedback is built.  The
        # manifest list remains an audit trail even after successful upsert.
        if not_run_node_ids:
            if not _persist_manifest_not_run(config, store, run_id.root, record):
                if effective_exitstatus == 0:
                    effective_exitstatus = 1
        record.update(
            {
                "finished_at": finished_at,
                "persistence_error": bool(
                    getattr(config, "_m3_manifest_write_error", False)
                ),
                "not_run_node_ids": not_run_node_ids,
            }
        )
        if (
            not _save_manifest(
                config,
                store,
                run_id.root,
                record,
                terminal_status=effective_exitstatus,
                session=session,
                fail_closed=True,
            )
            and effective_exitstatus == 0
        ):
            effective_exitstatus = 1
    manifest_error = manifest_error or bool(
        getattr(config, "_m3_manifest_write_error", False)
    )
    if effective_exitstatus != int(exitstatus):
        session.exitstatus = effective_exitstatus
    if manifest_error:
        # The feedback bundle may still be useful, but it must not look like a
        # complete successful run when one or more attempts were not persisted.
        if int(exitstatus) == 0:
            session.exitstatus = 1
    if store is not None:
        latest_manifest = dict(store.get_test_run(run_id.root) or {})
        attempts = tuple(dict(value) for value in store.list_test_results(run_id.root))
        collection_error = any(
            isinstance(report, dict) and report.get("outcome") == "failed"
            for report in latest_manifest.get("collection_reports", ()) or ()
        )
        required_issues = _required_evaluation_issues(
            store,
            run_id.root,
            attempts,
            persistence_error=manifest_error,
            collection_error=collection_error,
        )
        config._m3_required_evaluation_issues = required_issues
        if required_issues and int(exitstatus) == 0:
            session.exitstatus = 1
            effective_exitstatus = 1
        elif required_issues and session.exitstatus == 0:
            session.exitstatus = 1
        final_status = int(getattr(session, "exitstatus", effective_exitstatus))
        _save_manifest(
            config,
            store,
            run_id.root,
            {},
            terminal_status=final_status,
            session=session,
            fail_closed=True,
        )
        effective_exitstatus = int(getattr(session, "exitstatus", final_status))
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
                baseline_run_id=getattr(config, "_m3_baseline", None),
            )
            counter_save_failed = _save_feedback_counters(
                config,
                store,
                run_id.root,
                feedback,
                session,
                exitstatus,
            )
            if counter_save_failed:
                _save_manifest(
                    config,
                    store,
                    run_id.root,
                    {},
                    terminal_status=int(getattr(session, "exitstatus", exitstatus)),
                    session=session,
                    fail_closed=True,
                )
            # Rebuild after the final manifest write so the exported feedback
            # and its diagnostic manifest describe the same terminal state.
            feedback = build_feedback(
                export_store,
                run_id,
                baseline_run_id=getattr(config, "_m3_baseline", None),
            )
            output = export_feedback(
                feedback,
                export_store,
                getattr(config, "_m3_project_root", _Path.cwd())
                / ".m3"
                / "reports"
                / run_id.root,
            )
        finally:
            close = getattr(export_store, "close", None)
            if callable(close):
                close()
    except Exception:
        config._m3_feedback_error = "feedback export failed"
        if int(exitstatus) == 0:
            session.exitstatus = 1
        if store is not None:
            final_status = int(getattr(session, "exitstatus", exitstatus))
            _save_manifest(
                config,
                store,
                run_id.root,
                {},
                terminal_status=final_status,
            )
        reporter = config.pluginmanager.getplugin("terminalreporter")
        if reporter is not None:
            reporter.write_line("M3 feedback export failed", red=True)
        return
    config._m3_feedback_path = str(output)
    reporter = config.pluginmanager.getplugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("")
        reporter.write_line(f"M3 run {run_id.root}")
        reporter.write_line(f"M3 feedback: {output}")
        verdicts = [
            str(test.get("verdict", test.get("outcome", "unknown")))
            for test in feedback.tests
        ]
        categories = (
            ("passed", "passed"),
            ("failed_assertion", "failed assertion"),
            ("protocol_error", "protocol error"),
            ("setup_error", "setup error"),
            ("teardown_error", "teardown error"),
            ("pytest_error", "pytest error"),
            ("skipped", "skipped"),
        )
        counts = ", ".join(
            f"{verdicts.count(kind)} {label}"
            for kind, label in categories
            if verdicts.count(kind)
        )
        reporter.write_line("M3 verdicts: " + (counts or "no test cases recorded"))
        tool_errors = sum(
            test.get("tool_result") == "tool_error" for test in feedback.tests
        )
        completed = sum(
            execution.get("outcome") == "completed" for execution in feedback.executions
        )
        reporter.write_line(
            f"M3 observations: {tool_errors} tool error result(s); "
            f"{completed} completed execution(s)"
        )
        if no_executed_tests:
            reporter.write_line(
                "M3: no tests executed; skipped-only runs fail", red=True
            )
        for execution_id, stage, elapsed in timeout_summaries:
            elapsed_text = (
                f"{elapsed:.3f}s" if isinstance(elapsed, float) else "unknown"
            )
            reporter.write_line(
                "M3 execution timeout: "
                f"id={execution_id} stage={stage} elapsed={elapsed_text} "
                f"feedback={output}"
            )
        if manifest_error:
            reporter.write_line("M3 test manifest persistence was incomplete", red=True)
        if getattr(config, "_m3_required_evaluation_issues", ()):
            reporter.write_line(
                "M3 required evaluations blocked finalization: "
                + "; ".join(config._m3_required_evaluation_issues),
                red=True,
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
    def pytest_runtest_makereport(self, item: _Any, call: _Any) -> None:
        _pytest_runtest_makereport(item, call)

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
    "m3_kit",
    "agent",
    "server",
    "pytest_collection_modifyitems",
]
