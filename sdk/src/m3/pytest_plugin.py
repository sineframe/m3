"""Opt-in pytest integration used by ``m3 test``."""

from __future__ import annotations

import hashlib as _hashlib
import inspect as _inspect
import json as _json
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

from . import _timing
from ._check_recording import (
    restore_default_record_checks as _restore_default_record_checks,
)
from ._check_recording import (
    set_default_record_checks as _set_default_record_checks,
)
from ._credentials import (
    validate_credential_environment_names as _validate_reserved_names,
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
from ._terminal import Style as _Style
from ._terminal import fit as _fit
from ._terminal import hyperlink as _hyperlink
from ._terminal import stream_is_utf8 as _stream_is_utf8
from ._terminal import supports_hyperlinks as _supports_hyperlinks
from ._terminal import truncate as _truncate
from ._terminal import visible_len as _visible_len
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
    xfail_phase as _xfail_phase,
)
from ._test_runs import (
    xfail_waives_required_evaluations as _xfail_waives_required_evaluations,
)

_NATIVE_PROGRESS_UNSET = object()
_PLUGIN_CONFIG: _ContextVar[_Any] = _ContextVar("m3_pytest_plugin_config", default=None)
_ENV_NAME = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CREDENTIAL_SCOPES = {"claude_code", "opencode", "codex", "pi", "acp", "judge"}
_CI_METADATA_FIELDS = {
    "provider",
    "branch",
    "repository",
    "commit",
    "ref",
    "pr_number",
    "workflow",
    "job",
    "workflow_run_id",
    "attempt",
    "job_url",
}


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
        try:
            _validate_reserved_names(target, source)
        except ValueError as exc:
            raise _pytest.UsageError(str(exc)) from None
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
    group.addoption(
        "--m3-ci",
        action="store_true",
        default=False,
        help="apply the M3 CI test selection policy",
    )
    group.addoption(
        "--m3-run-id",
        action="store",
        default=None,
        help="internal run identity assigned by the M3 CLI",
    )
    group.addoption(
        "--m3-timings-owner",
        action="store",
        default="pytest",
        choices=("pytest", "cli"),
        help="internal owner of the M3_TIMINGS final report",
    )
    group.addoption(
        "--m3-ci-metadata",
        action="store",
        default=None,
        help="internal JSON CI metadata passed by the M3 CLI",
    )


def pytest_configure(config: _Any) -> None:
    _TimingPlugin.install(config)
    try:
        with _timing.span("pytest.configure"):
            _configure(config)
    except BaseException:
        timing_plugin = getattr(config, "_m3_timing_plugin", None)
        if timing_plugin is not None:
            timing_plugin.close(config)
        raise


def _configure(config: _Any) -> None:
    raw_ci_metadata = config.getoption("--m3-ci-metadata")
    ci_metadata: dict[str, str] = {}
    if raw_ci_metadata is not None:
        if not isinstance(raw_ci_metadata, str) or len(raw_ci_metadata) > 16_384:
            raise _pytest.UsageError("M3 CI metadata is invalid")
        try:
            decoded_ci_metadata = _json.loads(raw_ci_metadata)
        except (TypeError, ValueError) as exc:
            raise _pytest.UsageError("M3 CI metadata is invalid") from exc
        if (
            not isinstance(decoded_ci_metadata, dict)
            or set(decoded_ci_metadata) - _CI_METADATA_FIELDS
        ):
            raise _pytest.UsageError("M3 CI metadata is invalid")
        for key, value in decoded_ci_metadata.items():
            if not isinstance(value, str) or not value or len(value) > 512:
                raise _pytest.UsageError("M3 CI metadata is invalid")
            ci_metadata[key] = value
    config._m3_ci_metadata = ci_metadata
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
    config._m3_suite_validation_failed = False
    judge_limit = config.getoption("--judge-max-requests")
    if judge_limit is not None and judge_limit < 0:
        raise _pytest.UsageError("--judge-max-requests must be nonnegative")
    config._m3_judge_max_requests = judge_limit
    config.addinivalue_line(
        "markers",
        "m3(agents=None, servers=None, trials=None, suite_name=None, ci=None): select agent and server executions; ci=False excludes a test from m3 ci test",
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
        requested_run_id = config.getoption("--m3-run-id")
        run_id = (
            RunId(str(requested_run_id))
            if requested_run_id
            else RunId(
                getattr(config, "_m3_timing_run_id", None) or f"run-{_uuid4().hex}"
            )
        )
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

    with _timing.span("configure.store_open"):
        config._m3_manifest_store = SQLiteExecutionStore(path)
        if config._m3_project_id is not None:
            config._m3_manifest_store.ensure_project(
                config._m3_project_id.root, config._m3_project_name
            )
    config._m3_checks_token = _set_default_record_checks(True)
    if not config._m3_is_worker:
        with _timing.span("configure.manifest_init"):
            _init_manifest(config, run_id, ci_metadata)
    config._m3_run_id_previous = _install_default_run_id_factory(lambda: run_id)
    config._m3_judge_limit_previous = _install_default_judge_limit_factory(
        lambda: config._m3_judge_max_requests
    )
    config._m3_store_token = _install_default_store_factory(
        lambda: SQLiteExecutionStore(path, execution_queue=f"pytest-{_uuid4().hex}")
    )
    config._m3_progress = _Progress(config)
    config._m3_progress.reporter = config.pluginmanager.getplugin("terminalreporter")
    if config._m3_progress.reporter is not None:
        config._m3_progress.enabled = (
            config._m3_progress.enabled and config._m3_progress._is_tty()
        )
    config._m3_progress.disable_native_progress()
    config._m3_progress.restyle_separators()
    config.pluginmanager.register(config._m3_progress, "m3-progress")
    config._m3_manifest_hooks = _ManifestHooks()
    config.pluginmanager.register(config._m3_manifest_hooks, "m3-manifest-hooks")


def _init_manifest(config: _Any, run_id: _Any, ci_metadata: dict[str, str]) -> None:
    run_record = _run_record(
        run_id.root,
        project_root=str(config._m3_project_root),
        selection=tuple(str(value) for value in getattr(config, "args", ()) or ()),
        capture={
            "mode": getattr(config.option, "capture", None),
            "show_capture": bool(getattr(config.option, "showcapture", False)),
            "verbose": int(getattr(config.option, "verbose", 0) or 0),
        },
        project_id=(
            config._m3_project_id.root if config._m3_project_id is not None else None
        ),
        project_name=getattr(config, "_m3_project_name", None),
    )
    if ci_metadata:
        run_record["ci"] = dict(ci_metadata)
    config._m3_manifest_store.save_test_run(
        run_id.root,
        run_record,
    )


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
    callspec = getattr(node, "callspec", None)
    for marker in getattr(callspec, "marks", ()):
        if getattr(marker, "name", None) == "m3":
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
            if config.getoption("--m3-ci"):
                # Parameter-level ci=False marks are not attached to items until
                # after generation. Defer the error so excluded cases can leave.
                metafunc.parametrize(
                    "agent", [None], indirect=True, ids=["missing-harness"]
                )
                return
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


def _parametrizes_server(node: _Any) -> bool:
    """Whether pytest already owns ``server`` through a parametrize marker."""
    for marker in node.iter_markers(name="parametrize"):
        names = marker.args[0] if marker.args else marker.kwargs.get("argnames")
        if names is None:
            continue
        if isinstance(names, str):
            names = names.replace(",", " ").split()
        if "server" in names:
            return True
    return False


def _has_included_ci_parameter(metafunc: _Any) -> bool:
    """Whether a generated parameter case overrides inherited ci=False."""
    for callspec in getattr(metafunc, "_calls", ()):
        marker_kwargs: dict[str, _Any] = {}
        for marker in reversed(list(metafunc.definition.iter_markers(name="m3"))):
            marker_kwargs.update(marker.kwargs)
        for marker in getattr(callspec, "marks", ()):
            if getattr(marker, "name", None) == "m3":
                marker_kwargs.update(marker.kwargs)
        if marker_kwargs.get("ci") is True:
            return True
        if "ci" in marker_kwargs and not isinstance(marker_kwargs["ci"], bool):
            # Let the normal collection validation report malformed values.
            return True
    return False


@_pytest.hookimpl(trylast=True)
@_timing.counted("pytest.generate")
def pytest_generate_tests(metafunc: _Any) -> None:
    marker = metafunc.definition.get_closest_marker("m3")
    if marker is None:
        # Unmarked projects may define their own agent/server fixtures.
        return
    marker_kwargs = _merged_m3_marker(metafunc.definition)
    if (
        metafunc.config.getoption("--m3-ci")
        and marker_kwargs.get("ci") is False
        and not _has_included_ci_parameter(metafunc)
    ):
        # No generated case overrides ci=False, so avoid validating excluded
        # agent and server selections. Parameter overrides take the normal
        # matrix path below.
        if "agent" in metafunc.fixturenames:
            metafunc.parametrize("agent", [], indirect=True)
        fixture_defs = getattr(metafunc, "_arg2fixturedefs", {}).get("server", ())
        if any(
            fixture.func is getattr(server, "__wrapped__", None)
            for fixture in fixture_defs
        ) and not _parametrizes_server(metafunc.definition):
            metafunc.parametrize("server", [], indirect=True)
        return
    if metafunc.config.getoption("--m3-ci") and "ci" in marker_kwargs:
        if not isinstance(marker_kwargs["ci"], bool):
            raise _pytest.UsageError("m3(ci=...) must be a Boolean")
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
    pytest_server_parameter = _parametrizes_server(metafunc.definition)
    selected_servers = _server_choices(metafunc.config, marker_kwargs)
    if "server" in metafunc.fixturenames and selected_servers is not None:
        if project_server_fixture or pytest_server_parameter:
            raise _pytest.UsageError(
                "M3 server selections conflict with a project fixture or pytest parameter named 'server'"
            )
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


@_pytest.hookimpl(tryfirst=True)
@_timing.timed("collection.select")
def pytest_collection_modifyitems(config: _Any, items: list[_Any]) -> None:
    if config.getoption("--m3-ci"):
        ci_kept: list[_Any] = []
        excluded: list[_Any] = []
        for item in items:
            marker = _merged_m3_marker(item)
            if "ci" in marker and not isinstance(marker["ci"], bool):
                raise _pytest.UsageError("m3(ci=...) must be a Boolean")
            if marker.get("ci") is False:
                excluded.append(item)
            else:
                ci_kept.append(item)
        items[:] = ci_kept
        config._m3_ci_excluded_count = getattr(
            config, "_m3_ci_excluded_count", 0
        ) + len(excluded)
        store = getattr(config, "_m3_manifest_store", None)
        run_id = getattr(config, "_m3_run_id", None)
        if (
            store is not None
            and run_id is not None
            and not getattr(config, "_m3_is_worker", False)
        ):
            record = dict(store.get_test_run(run_id.root) or {})
            record["ci_excluded_count"] = int(config._m3_ci_excluded_count)
            store.save_test_run(run_id.root, record)
        if excluded:
            config.hook.pytest_deselected(items=excluded)
    selected = config.getoption("--suite")
    if selected is not None:
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

    for item in items:
        callspec = getattr(item, "callspec", None)
        if (
            config.getoption("--m3-ci")
            and item.get_closest_marker("m3") is not None
            and "agent" in getattr(item, "fixturenames", ())
            and getattr(callspec, "params", {}).get("agent", object()) is None
        ):
            raise _pytest.UsageError(
                "agent test requires --harness or m3(agents=[...])"
            )
        if "server" not in getattr(item, "fixturenames", ()):
            continue
        if item.get_closest_marker("m3") is None:
            continue
        fixture_defs = getattr(item._fixtureinfo, "name2fixturedefs", {}).get(
            "server", ()
        )
        if not fixture_defs or fixture_defs[-1].func is not getattr(
            server, "__wrapped__", None
        ):
            continue
        callspec = getattr(item, "callspec", None)
        if "server" not in getattr(callspec, "params", {}):
            raise _pytest.UsageError(
                "server fixture requires --server selections or m3(servers=[...])"
            )


def pytest_unconfigure(config: _Any) -> None:
    timing_plugin = getattr(config, "_m3_timing_plugin", None)
    if timing_plugin is not None:
        timing_plugin.close(config)
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


@_timing.timed("collection.select")
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
    if config.getoption("--m3-ci"):
        record["ci_excluded_count"] = int(getattr(config, "_m3_ci_excluded_count", 0))
    store.save_test_run(run_id.root, record)


def _record_collected(
    config: _Any,
    node_ids: list[str],
    *,
    worker_id: str | None = None,
    ci_excluded_count: int | None = None,
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
    if ci_excluded_count is not None:
        count = max(int(record.get("ci_excluded_count", 0)), int(ci_excluded_count))
        record["ci_excluded_count"] = count
        config._m3_ci_excluded_count = count
    store.save_test_run(run_id.root, record)


@_timing.timed("collection.manifest")
def _pytest_collection_finish(session: _Any) -> None:
    config = session.config
    items = session.items
    if config.getoption("--results-db") and not config.option.collectonly:
        missing = [
            str(item.nodeid)
            for item in items
            if not _merged_m3_marker(item).get("suite_name")
        ]
        if missing:
            config._m3_suite_validation_failed = True
            message = _missing_suite_message(missing)
            if not config._m3_is_worker:
                raise _pytest.UsageError(message)
            # xdist reports worker collection errors to the controller. Raising
            # UsageError here instead kills the worker without forwarding the
            # diagnostic, leaving an unhelpful "no active workers" error.
            config.hook.pytest_collectreport(
                report=_pytest.CollectReport(
                    nodeid="", outcome="failed", longrepr=message, result=[]
                )
            )
            items[:] = [
                item for item in items if _merged_m3_marker(item).get("suite_name")
            ]
    node_ids = [str(item.nodeid) for item in items]
    suites = {}
    for item in items:
        suite_name = _merged_m3_marker(item).get("suite_name")
        if suite_name:
            suites[str(item.nodeid)] = suite_name
    workerinput = getattr(config, "workerinput", None)
    if isinstance(workerinput, dict):
        workeroutput = getattr(config, "workeroutput", None)
        if isinstance(workeroutput, dict):
            workeroutput["m3_collected_node_ids"] = node_ids
            workeroutput["m3_ci_excluded_count"] = int(
                getattr(config, "_m3_ci_excluded_count", 0)
            )
            workeroutput["m3_collected_suites"] = suites
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
    record["collected_suites"] = suites
    record["collection_count"] = len(node_ids)
    store.save_test_run(run_id.root, record)


def _missing_suite_message(missing: list[str]) -> str:
    examples = ", ".join(missing[:3])
    more = f" (and {len(missing) - 3} more)" if len(missing) > 3 else ""
    return (
        "Persisted pytest tests require a non-empty suite name. "
        "Add @pytest.mark.m3(suite_name='my-suite') to the test, "
        "or set pytestmark = pytest.mark.m3(suite_name='my-suite') "
        f"for the module. Missing: {examples}{more}"
    )


def _pytest_xdist_node_collection_finished(node: _Any, ids: list[str]) -> None:
    config = getattr(node, "config", None) or _PLUGIN_CONFIG.get()
    worker_id = getattr(node, "gateway", None)
    worker_id = (
        getattr(worker_id, "id", None) or getattr(node, "workerid", None) or "worker"
    )
    workeroutput = getattr(node, "workeroutput", None)
    excluded_count = (
        workeroutput.get("m3_ci_excluded_count")
        if isinstance(workeroutput, dict)
        else None
    )
    _record_collected(
        config,
        [str(item) for item in ids],
        worker_id=str(worker_id),
        ci_excluded_count=(
            int(excluded_count)
            if isinstance(excluded_count, int) and not isinstance(excluded_count, bool)
            else None
        ),
    )


def _pytest_testnodedown(node: _Any, error: object | None = None) -> None:
    config = getattr(node, "config", None) or _PLUGIN_CONFIG.get()
    if config is None or getattr(config, "_m3_is_worker", False):
        return
    store = getattr(config, "_m3_manifest_store", None)
    run_id = getattr(config, "_m3_run_id", None)
    if store is None or run_id is None:
        return
    record = dict(store.get_test_run(run_id.root) or {})
    workeroutput = getattr(node, "workeroutput", None)
    excluded_count = (
        workeroutput.get("m3_ci_excluded_count")
        if isinstance(workeroutput, dict)
        else None
    )
    if isinstance(excluded_count, int) and not isinstance(excluded_count, bool):
        count = max(int(record.get("ci_excluded_count", 0)), excluded_count)
        record["ci_excluded_count"] = count
        config._m3_ci_excluded_count = count
    if isinstance(workeroutput, dict):
        suites = workeroutput.get("m3_collected_suites")
        if isinstance(suites, dict):
            record["collected_suites"] = {
                **record.get("collected_suites", {}),
                **suites,
            }
    if error is None:
        store.save_test_run(run_id.root, record)
        return
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


@_timing.counted("pytest.persist_attempt")
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
        call_phase = (state.get("phases") or {}).get("call")
        xfail_found = _xfail_phase(state.get("phases"))
        if isinstance(call_phase, dict) and call_phase.get("wasxfail"):
            state["xfail_reason"] = str(call_phase.get("xfail_reason", ""))
        elif xfail_found is not None:
            state["xfail_reason"] = str(xfail_found[1].get("xfail_reason", ""))
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
    if _xfail_phase(values) is not None:
        return "xfailed"
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
    phase_record: dict[str, object] = {
        "outcome": str(report.outcome),
        "duration_seconds": float(getattr(report, "duration", 0.0) or 0.0),
        "wasxfail": hasattr(report, "wasxfail"),
    }
    if hasattr(report, "wasxfail"):
        phase_record["xfail_reason"] = str(report.wasxfail)
    phases[str(report.when)] = phase_record
    exception_types = state.get("_m3_exception_types")
    exception_type = (
        exception_types.pop(str(report.when), None)
        if isinstance(exception_types, dict)
        else None
    )
    if report.outcome == "failed" or (
        report.outcome == "skipped"
        and report.when in {"setup", "teardown"}
        and hasattr(report, "wasxfail")
    ):
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
    suites = manifest.get("collected_suites", {}) if isinstance(manifest, dict) else {}
    suite_name = suites.get(node_id) if isinstance(suites, dict) else None
    # A worker may die before transmitting its collection metadata. Retain the
    # incomplete attempt under an explicit suite rather than dropping the audit.
    suite_name = suite_name or "Unknown (interrupted collection)"
    return {
        "schema_version": 1,
        "attempt_id": f"{run_id}:manifest-not-run:{digest}",
        "run_id": str(run_id),
        "node_id": str(node_id),
        "description": "",
        "worker_id": "controller",
        "suite_id": None,
        "suite_name": suite_name,
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


@_timing.timed("finish.manifest")
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


@_timing.timed("finish.required_evaluations")
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
        xfail_found = _xfail_phase(phases)
        ignored_diagnostic = (
            f"{xfail_found[0]}:longrepr" if xfail_found is not None else None
        )
        has_phase_error = isinstance(diagnostics, _Mapping) and any(
            str(key).split(":", 1)[0] in {"setup", "teardown"}
            and str(key).endswith(":longrepr")
            and str(key) != ignored_diagnostic
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


@_timing.counted("pytest.manifest_write")
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
        # A genuine xfail is an executed test even though pytest reports its
        # call phase as skipped. Legacy rows stored it as "skipped" with a
        # wasxfail phase. Ordinary skips still do not count.
        executed_attempt = any(
            item.get("outcome") in {"passed", "failed", "error", "xfailed"}
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
        if not_run_node_ids and not config._m3_suite_validation_failed:
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
        from .feedback import build_feedback, export_feedback, load_run_entries
        from .storage import SQLiteExecutionStore

        export_store = SQLiteExecutionStore(path)
        try:
            with _timing.span("finish.timeout_scan"):
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
            baseline_run_id = getattr(config, "_m3_baseline", None)
            with _timing.span("finish.load_entries", "run"):
                run_entries = load_run_entries(export_store, run_id)
            baseline_entries = None
            if baseline_run_id is not None:
                with _timing.span("finish.load_entries", "baseline"):
                    baseline_entries = load_run_entries(export_store, baseline_run_id)
            with _timing.span("feedback.build", "counters"):
                feedback = build_feedback(
                    export_store,
                    run_id,
                    baseline_run_id=baseline_run_id,
                    entries=run_entries,
                    baseline_entries=baseline_entries,
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
            with _timing.span("feedback.build", "final"):
                feedback = build_feedback(
                    export_store,
                    run_id,
                    baseline_run_id=baseline_run_id,
                    entries=run_entries,
                    baseline_entries=baseline_entries,
                )
            with _timing.span("feedback.export"):
                output = export_feedback(
                    feedback,
                    export_store,
                    getattr(config, "_m3_project_root", _Path.cwd())
                    / ".m3"
                    / "reports"
                    / run_id.root,
                    entries=run_entries,
                    baseline_entries=baseline_entries,
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
            ("xfailed", "xfailed"),
        )
        tool_errors = sum(
            test.get("tool_result") == "tool_error" for test in feedback.tests
        )
        completed = sum(
            execution.get("outcome") == "completed" for execution in feedback.executions
        )
        style = None if config.getoption("--m3-ci") else _terminal_style(reporter)
        if style is not None:
            _write_run_panel(
                reporter,
                style,
                run_id.root,
                output,
                [(verdicts.count(kind), kind, label) for kind, label in categories],
                tool_errors,
                completed,
                comparison=feedback.comparison,
                slowest=(
                    config._m3_progress.slowest()
                    if getattr(config, "_m3_progress", None) is not None
                    else []
                ),
            )
        else:
            counts = ", ".join(
                f"{verdicts.count(kind)} {label}"
                for kind, label in categories
                if verdicts.count(kind)
            )
            reporter.write_line("")
            reporter.write_line(f"M3 run {run_id.root}")
            reporter.write_line(f"M3 feedback: {output}")
            reporter.write_line("M3 verdicts: " + (counts or "no test cases recorded"))
            reporter.write_line(
                f"M3 observations: {tool_errors} tool error result(s); "
                f"{completed} completed execution(s)"
            )
        if no_executed_tests:
            reporter.write_line(
                "M3: no tests executed; skipped-only runs fail", red=True
            )
        if style is not None and timeout_summaries:
            _write_timeouts(reporter, style, timeout_summaries)
        for execution_id, stage, elapsed in timeout_summaries if style is None else ():
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


def _write_run_panel(
    reporter: _Any,
    style: _Style,
    run_id: str,
    output: _Any,
    counts: list[tuple[int, str, str]],
    tool_errors: int,
    completed: int,
    *,
    comparison: _Any = None,
    slowest: list[tuple[str, str, float]] | None = None,
) -> None:
    """Terminal rendering of the M3 run summary for interactive sessions."""

    dot = f" {style.dim(style.glyphs.dot)} "
    verdict_parts = []
    for count, kind, label in counts:
        if not count:
            continue
        paint = (
            style.green
            if kind == "passed"
            else style.yellow
            if kind in {"skipped", "xfailed"}
            else style.red
        )
        verdict_parts.append(paint(f"{count} {label}"))
    observations = (
        (style.yellow if tool_errors else str)(
            f"{tool_errors} tool error{'' if tool_errors == 1 else 's'}"
        )
        + dot
        + f"{completed} execution{'' if completed == 1 else 's'}"
    )
    feedback = _Path(output)
    try:
        feedback = feedback.relative_to(_Path.cwd())
    except ValueError:
        pass
    width = getattr(getattr(reporter, "_tw", None), "fullwidth", 80)
    rows = [("verdicts", dot.join(verdict_parts) or "no test cases recorded")]
    if comparison is not None:
        rows.append(("vs baseline", _baseline_delta(style, comparison)))
    rows.append(("observations", observations))
    slowest = slowest or []
    if slowest:
        # The row must fit inside the closed panel. The test name matters
        # most and gets the room left after the bar and the time; the file
        # name is shown only when it fits whole.
        terminal = width if isinstance(width, int) and width > 0 else 80
        key_width = 16  # the panel's key column ("observations" + gap)
        available = terminal - 1 - 2 - 2 - 1 - key_width
        fixed = 1 + 12 + 1 + 6  # " " + bar + " " + "123.4s"
        name_width = max(
            12, min(max(len(name) for name, _, _ in slowest), available - fixed)
        )
        longest = slowest[0][2]
        for index, (name, file, seconds) in enumerate(slowest):
            filled = max(1, round(12 * seconds / longest)) if longest else 1
            show_file = file and name_width + fixed + 2 + len(file) <= available
            rows.append(
                (
                    "slowest" if index == 0 else "",
                    f"{_truncate(name, name_width, style.glyphs.ellipsis, keep='start'):<{name_width}} "
                    f"{style.cyan(style.glyphs.bar_full * filled)}{' ' * (12 - filled)} "
                    f"{style.dim(f'{seconds:>5.1f}s')}"
                    + (f"  {style.dim(file)}" if show_file else ""),
                )
            )
    path_text = style.dim(str(feedback))
    if style.color and _supports_hyperlinks():
        # The text is the path itself, so terminals without links lose nothing.
        path_text = _hyperlink(_Path(output).absolute().as_uri(), path_text)
    rows.append(("feedback", path_text))
    title = style.bold(f"M3 {run_id}")
    reporter.write_line("")
    for line in style.box(title, rows, width if isinstance(width, int) else 80):
        reporter.write_line(line)


def _write_timeouts(
    reporter: _Any, style: _Style, timeouts: list[tuple[str, str, float | None]]
) -> None:
    """Compact timeout lines under the panel; the plain lines stay for pipes."""

    width = getattr(getattr(reporter, "_tw", None), "fullwidth", 80)
    width = (width if isinstance(width, int) and width > 1 else 80) - 1
    shown = timeouts[:3]
    for execution_id, stage, elapsed in shown:
        details = [
            style.dim(_truncate(execution_id, 24, style.glyphs.ellipsis, keep="start"))
        ]
        if stage and stage != "unknown":
            details.append(f"stage {stage}")
        if isinstance(elapsed, float):
            details.append(f"after {elapsed:.1f}s")
        line = (
            f"  {style.yellow(style.glyphs.warning + ' execution timed out')}  "
            + "  ".join(details)
        )
        reporter.write_line(_fit(line, width))
    more = len(timeouts) - len(shown)
    tail = f"{more} more {style.glyphs.dot} " if more else ""
    reporter.write_line(
        _fit(f"    {style.dim(tail + 'details in feedback.json')}", width)
    )


def _baseline_delta(style: _Style, comparison: _Any) -> str:
    """Summarise per-test changes against the baseline run."""

    def state(attempts: _Any) -> str:
        outcomes = {
            str(attempt.get("outcome"))
            for attempt in attempts or ()
            if isinstance(attempt, _Mapping)
        }
        if not outcomes:
            return "absent"
        if outcomes - {"passed", "skipped", "xfailed", "not_run"}:
            return "failed"
        return "passed" if "passed" in outcomes else "skipped"

    tally = {"fixed": 0, "regressed": 0, "new": 0, "removed": 0, "other": 0}
    for change in comparison.test_changes:
        before, after = state(change.get("baseline")), state(change.get("current"))
        if before == "absent":
            tally["new"] += 1
        elif after == "absent":
            tally["removed"] += 1
        elif before == "failed" and after == "passed":
            tally["fixed"] += 1
        elif after == "failed":
            tally["regressed"] += 1
        else:
            tally["other"] += 1
    current = int(comparison.coverage.get("current_tests", 0) or 0)
    changed_now = len(comparison.test_changes) - tally["removed"]
    unchanged = max(0, current - changed_now)
    parts = []
    if tally["fixed"]:
        parts.append(style.green(f"{tally['fixed']} fixed"))
    if tally["regressed"]:
        parts.append(style.red(f"{tally['regressed']} regressed"))
    if tally["new"]:
        parts.append(f"{tally['new']} new")
    if tally["removed"]:
        parts.append(f"{tally['removed']} removed")
    if tally["other"]:
        parts.append(f"{tally['other']} changed")
    parts.append(style.dim(f"{unchanged} unchanged"))
    label = comparison.baseline_run_label or str(comparison.baseline_run_id)
    dot = f" {style.dim(style.glyphs.dot)} "
    return dot.join(parts) + style.dim(f"  vs {label}")


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
        with _timing.span("pytest.finish"):
            _pytest_sessionfinish(session, exitstatus)


def _terminal_style(reporter: _Any) -> _Style | None:
    """Styling for a TTY terminal reporter, or None for plain output."""

    if reporter is None or _os.environ.get("TERM") == "dumb":
        return None
    writer = getattr(reporter, "_tw", None)
    value = getattr(reporter, "isatty", None)
    if callable(value):
        tty = bool(value())
    elif value is not None:
        tty = bool(value)
    else:
        isatty = getattr(getattr(writer, "_file", None), "isatty", None)
        tty = bool(callable(isatty) and isatty())
    if not tty:
        return None
    return _Style(
        bool(getattr(writer, "hasmarkup", False)),
        unicode=_stream_is_utf8(getattr(writer, "_file", None)),
    )


def _frame(text: str) -> str:
    """Wrap one redraw in synchronized-output markers (DEC mode 2026).

    Terminals that support it show the frame in one go; others ignore the
    markers. Together with overwriting in place, this keeps the live block
    from flickering.
    """

    return f"\x1b[?2026h{text}\x1b[?2026l"


_MATRIX_COLUMNS = 40
_MATRIX_ROWS = 6
_MATRIX_ASPECT = 4.0
_MATRIX_SINGLE_ROW = 12


def _matrix_shape(count: int, max_columns: int) -> tuple[int, int]:
    """Rows and columns for ``count`` cells that look like a matrix.

    Up to a dozen cells stay on one row. Otherwise the grid aims for about
    four columns per row, wastes as few cells as possible, fits within
    ``max_columns`` and at most six rows (callers fold larger suites first).
    """

    if count <= 0:
        return 0, 0
    if count <= min(_MATRIX_SINGLE_ROW, max_columns):
        return 1, count
    best: tuple[float, int, int] | None = None
    for rows in range(1, _MATRIX_ROWS + 1):
        columns = -(-count // rows)
        if columns > max_columns:
            continue
        rows = -(-count // columns)  # drop rows that would stay empty
        empty = rows * columns - count
        shape = abs(_math.log((columns / rows) / _MATRIX_ASPECT))
        score = shape + 4 * empty / count
        if best is None or score < best[0]:
            best = (score, rows, columns)
    if best is None:
        columns = max_columns
        return -(-count // columns), columns
    return best[1], best[2]


_CELL_RANK = {
    "failed": 4,
    "running": 3,
    "pending": 2,
    "skipped": 1,
    "xfailed": 1,
    "passed": 0,
}
_NOTIFY_TERMINALS = frozenset({"iTerm.app", "ghostty", "WezTerm"})
_NOTIFY_AFTER_SECONDS = 30.0


class _Progress:
    """Interactive live block: per-file and failure lines flow above it, and a
    test matrix plus progress line are redrawn in place below them."""

    def __init__(self, config: _Any) -> None:
        self.config = config
        self.reporter: _Any = None
        self.total = self.completed = self.passed = self.failed = self.skipped = 0
        self.xfailed = 0
        self._counted: set[str] = set()
        self._outcomes: dict[str, str] = {}
        self._durations: dict[str, float] = {}
        self._order: dict[str, int] = {}
        self._cells: list[str] = []
        self._last_write = 0.0
        self._finished = False
        self._native_progress: object = _NATIVE_PROGRESS_UNSET
        self._native_fspath: object = _NATIVE_PROGRESS_UNSET
        self._started = _time.monotonic()
        self._current = ""
        self._frame = 0
        self._live_height = 0
        self._title_pushed = False
        self._file: str | None = None
        self._file_counts: dict[str, int] = {}
        self._file_started = 0.0
        self._running: set[str] = set()
        self._attached = False
        self._cursor_hidden = False
        self._spaced = False
        self._logged = False
        self._native_write_sep = False
        option = config.option
        # With xdist workers, tests from several files run at once, so the
        # per-file lines are left out; the main process still sees every
        # test start and finish and drives the live block.
        self._parallel = bool(getattr(option, "numprocesses", 0))
        self.enabled = (
            int(getattr(option, "verbose", 0) or 0) <= 0
            # Workers report to the main process; only it draws.
            and not hasattr(config, "workerinput")
            # Uncaptured test output and live logs would interleave with the
            # live block; pytest's native output handles those better.
            and getattr(option, "capture", None) != "no"
            and not self._live_logging()
        )

    def _live_logging(self) -> bool:
        getini = getattr(self.config, "getini", None)
        if not callable(getini):
            return False
        try:
            return bool(getini("log_cli"))
        except (ValueError, KeyError):
            return False

    @_pytest.hookimpl(tryfirst=True)
    def pytest_sessionstart(self, session: _Any) -> None:
        # Before pytest's own session header is written.
        if self.reporter is None:
            self.reporter = self.config.pluginmanager.getplugin("terminalreporter")
        self.restyle_separators()

    def _attach(self) -> None:
        """Bind the terminal reporter and settle ``enabled`` (once).

        Every entry point calls this: under xdist the main process never
        runs pytest_collection_finish, so it cannot be the only place.
        """

        if self._attached:
            return
        self._attached = True
        if self.reporter is None:
            self.reporter = self.config.pluginmanager.getplugin("terminalreporter")
        self.enabled = self.enabled and self.reporter is not None and self._is_tty()
        self.disable_native_progress()
        self.restyle_separators()

    @_pytest.hookimpl(trylast=True)
    def pytest_collection_finish(self, session: _Any) -> None:
        self._started = _time.monotonic()
        self._attach()
        # Under xdist the main process collects nothing; workers report
        # their collection through pytest_xdist_node_collection_finished.
        self._collected(
            [getattr(item, "nodeid", index) for index, item in enumerate(session.items)]
        )

    @_pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node: _Any, ids: _Any) -> None:
        self._attach()
        if not self.total:
            self._started = _time.monotonic()
            self._collected(list(ids))

    def _collected(self, nodeids: list[_Any]) -> None:
        if not nodeids:
            return
        self.total = len(nodeids)
        self._order = {str(nodeid): index for index, nodeid in enumerate(nodeids)}
        self._cells = ["pending"] * self.total
        if self.enabled and not self._title_pushed:
            # Save the window title so finish() can restore it (xterm title
            # stack; terminals without one ignore both sequences).
            self._emit("\x1b[22;0t")
            self._title_pushed = True

    @_pytest.hookimpl(wrapper=True)
    def pytest_report_teststatus(self, report: _Any, config: _Any) -> _Any:
        # The live block replaces pytest's per-test letters, which would
        # otherwise be appended to it.
        result = yield
        self._attach()
        if self.enabled and not self._finished and result and len(result) == 3:
            category, _letter, word = result
            return category, "", word
        return result

    @_pytest.hookimpl
    def pytest_runtest_logstart(self, nodeid: str, location: _Any) -> None:
        self._attach()
        if not self.enabled:
            return
        self._running.add(str(nodeid))
        path = str(nodeid).split("::", 1)[0]
        if not self._parallel and path != self._file:
            self._flush_file()
            self._file, self._file_counts = path, {}
            self._file_started = _time.monotonic()
        # Parameter values may contain secrets; show the test id only.
        self._current = str(nodeid).split("[", 1)[0].split("::", 1)[-1]
        self._set_cell(nodeid, "running")
        self._write(force=True)

    @_pytest.hookimpl(trylast=True)
    def pytest_runtest_logreport(self, report: _Any) -> None:
        if report.when in {"setup", "call", "teardown"}:
            duration = getattr(report, "duration", 0.0)
            if isinstance(duration, (int, float)) and _math.isfinite(duration):
                self._durations[report.nodeid] = self._durations.get(
                    report.nodeid, 0.0
                ) + float(duration)
        self._attach()
        if not self.enabled or report.when not in {"setup", "call", "teardown"}:
            return
        if report.when == "teardown":
            self._running.discard(str(report.nodeid))
            if report.nodeid not in self._counted:
                return
            previous = self._outcomes.get(report.nodeid)
            crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
            crash = getattr(crash, "message", None)
            teardown_xfail = (
                report.outcome == "skipped"
                and hasattr(report, "wasxfail")
                and not (
                    isinstance(crash, str) and "XFailed" not in crash.split(":", 1)[0]
                )
            )
            if (report.outcome == "failed" and previous != "failed") or (
                teardown_xfail and previous in {"passed", "skipped"}
            ):
                if previous == "passed":
                    self.passed -= 1
                elif previous == "skipped":
                    self.skipped -= 1
                elif previous == "xfailed":
                    self.xfailed -= 1
                if previous is not None:
                    self._count_file(previous, -1)
                if teardown_xfail:
                    self.xfailed += 1
                    self._outcomes[report.nodeid] = "xfailed"
                    self._count_file("xfailed", 1)
                    self._set_cell(report.nodeid, "xfailed")
                else:
                    self.failed += 1
                    self._outcomes[report.nodeid] = "failed"
                    self._count_file("failed", 1)
                    self._set_cell(report.nodeid, "failed")
                    self._report_failure(report)
                self._write(force=True)
            return
        if report.nodeid in self._counted:
            return
        # Passing setup is intermediate; a setup failure/skip is terminal.
        if report.when == "setup" and report.outcome == "passed":
            return
        if report.when != "call" and report.outcome not in {"failed", "skipped"}:
            return
        self._counted.add(report.nodeid)
        outcome = report.outcome
        crash_message = getattr(getattr(report, "longrepr", None), "reprcrash", None)
        crash_message = getattr(crash_message, "message", None)
        is_xfail = (
            outcome == "skipped"
            and report.when in {"setup", "call"}
            and hasattr(report, "wasxfail")
            # A setup *error* under an xfail marker also carries ``wasxfail``;
            # when pytest exposes the exception, only ``XFailed`` counts.
            and not (
                report.when == "setup"
                and isinstance(crash_message, str)
                and "XFailed" not in crash_message.split(":", 1)[0]
            )
        )
        self._outcomes[report.nodeid] = "xfailed" if is_xfail else outcome
        self.completed += 1
        if outcome == "passed":
            self.passed += 1
        elif outcome == "failed":
            self.failed += 1
        elif is_xfail:
            self.xfailed += 1
        else:
            self.skipped += 1
        cell = (
            "xfailed"
            if is_xfail
            else report.outcome
            if report.outcome in _CELL_RANK
            else "failed"
        )
        self._count_file(cell, 1)
        self._set_cell(report.nodeid, cell)
        if report.outcome == "failed":
            self._report_failure(report)
        self._write(force=report.outcome == "failed")

    def slowest(self, count: int = 3) -> list[tuple[str, str, float]]:
        """The slowest tests (setup + call + teardown) that took 0.5s or more.

        Returns (test name, file name, seconds). Parametrized cases are
        grouped under their test name, since parameter values may contain
        secrets; the slowest case represents the group.
        """

        groups: dict[tuple[str, str], tuple[float, int]] = {}
        for nodeid, seconds in self._durations.items():
            path, _, test = nodeid.split("[", 1)[0].partition("::")
            key = (test or path, path.rsplit("/", 1)[-1] if test else "")
            longest, cases = groups.get(key, (0.0, 0))
            groups[key] = (max(longest, seconds), cases + 1)
        ranked = sorted(groups.items(), key=lambda item: -item[1][0])
        return [
            (test if cases == 1 else f"{test} ({cases} cases)", file, seconds)
            for (test, file), (seconds, cases) in ranked[:count]
            if seconds >= 0.5
        ]

    def _set_cell(self, nodeid: str, state: str) -> None:
        index = self._order.get(nodeid)
        if index is not None and index < len(self._cells):
            self._cells[index] = state

    def _count_file(self, outcome: str, delta: int) -> None:
        self._file_counts[outcome] = self._file_counts.get(outcome, 0) + delta

    def _style(self) -> _Style:
        return _terminal_style(self.reporter) or _Style(False, unicode=False)

    def _width(self) -> int:
        width = getattr(getattr(self.reporter, "_tw", None), "fullwidth", 80)
        # Stay off the last column: some terminals wrap when it is written.
        return (width if isinstance(width, int) and width > 1 else 80) - 1

    def _emit(self, text: str) -> None:
        if self.reporter is not None and self._is_tty():
            if self.enabled and not self._cursor_hidden and not self._finished:
                # A blinking cursor at the end of the live block is noise.
                text = "\x1b[?25l" + text
                self._cursor_hidden = True
            self.reporter.rewrite(text, flush=True)

    def _clear_live(self) -> str:
        if not self._live_height:
            if self._spaced:
                return "\r"
            # The first draw: end whatever line pytest or xdist left open,
            # then leave exactly one blank line above the block.
            self._spaced = True
            column = getattr(
                getattr(self.reporter, "_tw", None), "width_of_current_line", 0
            )
            return ("\n" if isinstance(column, int) and column > 0 else "") + "\n\r"
        # Move back to the block's first line without erasing anything:
        # the new frame overwrites it line by line, and only what is left
        # below the new frame is cleared afterwards (see _block). Erasing
        # first would let the terminal paint an empty frame (a flicker).
        up = f"\x1b[{self._live_height - 1}A" if self._live_height > 1 else ""
        self._live_height = 0
        return f"\r{up}"

    def _log(self, lines: list[str]) -> None:
        """Print lines above the live block, then redraw the block."""

        if self.reporter is None or not self._is_tty():
            return
        width = self._width()
        text = "".join(_fit(line, width) + "\x1b[K\n" for line in lines)
        clear = self._clear_live()
        self._logged = True
        self._emit(_frame(clear + text + self._block()))

    def _flush_file(self) -> None:
        if self._file is None or not sum(self._file_counts.values()):
            return
        style = self._style()
        counts = self._file_counts
        parts = [
            paint(f"{counts[key]} {key}")
            for key, paint in (
                ("passed", style.green),
                ("failed", style.red),
                ("skipped", style.yellow),
                ("xfailed", style.yellow),
            )
            if counts.get(key)
        ]
        mark = (
            style.red(style.glyphs.failed)
            if counts.get("failed")
            else style.green(style.glyphs.passed)
        )
        dot = f" {style.dim(style.glyphs.dot)} "
        elapsed = style.dim(f"{_time.monotonic() - self._file_started:.1f}s")
        self._log([f"  {mark} {style.bold(self._file)}  {dot.join(parts)}  {elapsed}"])
        self._file_counts = {}

    def _report_failure(self, report: _Any) -> None:
        """Keep each failure, and the line that failed, above the live block."""

        style = self._style()
        lines = [
            f"  {style.red(style.glyphs.failed)} {style.red(str(report.nodeid).split('[', 1)[0])}"
        ]
        crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
        message = str(getattr(crash, "message", "") or "").strip().splitlines()
        if message:
            where = ""
            path, lineno = getattr(crash, "path", None), getattr(crash, "lineno", None)
            if path and isinstance(lineno, int):
                where = style.dim(
                    f"  {style.glyphs.dot} {_Path(str(path)).name}:{lineno}"
                )
            lines.append(f"      {message[0]}{where}")
        self._log(lines)

    def _matrix(self, style: _Style, width: int) -> list[str]:
        if not self._cells or not style.color:
            # Without colour every state would look the same.
            return []
        # Each cell is a glyph and a space, so the grid reads as a matrix.
        max_columns = max(1, min(_MATRIX_COLUMNS, (width - 2) // 2))
        capacity = max_columns * _MATRIX_ROWS
        # Large suites: one cell stands for several tests and shows the most
        # important state among them.
        size = max(1, -(-len(self._cells) // capacity))
        buckets = [
            max(self._cells[start : start + size], key=_CELL_RANK.__getitem__)
            for start in range(0, len(self._cells), size)
        ]
        _rows, columns = _matrix_shape(len(buckets), max_columns)
        full = "■" if style.glyphs.passed == "✓" else "#"
        paint = {
            "passed": style.green(full),
            "failed": style.red(full),
            "skipped": style.yellow(full),
            "xfailed": style.yellow(full),
            "running": style.cyan(full),
            "pending": style.grey("·" if full == "■" else "."),
        }
        return [
            "  " + " ".join(paint[cell] for cell in buckets[start : start + columns])
            for start in range(0, len(buckets), columns)
        ]

    def _block(self, *, done: bool = False) -> str:
        style = self._style()
        width = self._width()
        matrix = self._matrix(style, width)
        # A blank line between the matrix and the progress line, and one
        # between the block and any file or failure lines printed above it.
        lines = (
            [*matrix, "", self._line(done=done)] if matrix else [self._line(done=done)]
        )
        if self._logged:
            lines.insert(0, "")
        self._live_height = len(lines)
        # Each line clears only its own tail; the final erase removes rows
        # the previous, taller frame left below this one.
        return "\n".join(_fit(line, width) + "\x1b[K" for line in lines) + "\x1b[J"

    def _line(self, *, done: bool = False) -> str:
        style = self._style()
        glyphs = style.glyphs
        total = max(self.total, self.completed)
        if done:
            lead = (
                style.red(glyphs.failed)
                if self.failed
                # An interrupted run did not pass, even with no failures.
                else style.yellow(glyphs.warning)
                if self.completed < total
                else style.green(glyphs.passed)
            )
        else:
            lead = style.cyan(glyphs.spinner[self._frame % len(glyphs.spinner)])
            self._frame += 1
        width = self._width()
        # Pad numbers to the total's width so the line does not jitter.
        digits = len(str(total))
        counts = (
            f"   {style.green(f'{glyphs.passed} {self.passed:<{digits}}')}"
            f"  {(style.red if self.failed else style.grey)(f'{glyphs.failed} {self.failed:<{digits}}')}"
            f"  {(style.yellow if self.skipped else style.grey)(f'{glyphs.skipped} {self.skipped:<{digits}}')}"
            + (
                f"  {style.yellow(f'xfail {self.xfailed:<{digits}}')}"
                if self.xfailed
                else ""
            )
        )
        elapsed = f" {style.dim(f'{_time.monotonic() - self._started:5.1f}s')}"
        fraction = (
            f"  {style.bold(f'{self.completed:>{digits}}')}{style.dim(f'/{total}')}"
        )
        # Drop parts in reverse priority until the line fits: test name,
        # elapsed time, bar, counts.
        room = width - 4 - _visible_len(fraction)
        show_counts = _visible_len(counts) <= room
        room -= _visible_len(counts) if show_counts else 0
        bar_width = min(28, room - 2 - _visible_len(elapsed) - 14)
        show_bar = bar_width >= 8
        room -= bar_width + 1 if show_bar else 0
        show_elapsed = _visible_len(elapsed) <= room
        room -= _visible_len(elapsed) if show_elapsed else 0
        line = f"  {lead} "
        if show_bar:
            line += style.bar(self.completed, total, bar_width)
        line += fraction + (counts if show_counts else "")
        line += elapsed if show_elapsed else ""
        current = self._current
        if self._parallel and len(self._running) > 1:
            current = f"{len(self._running)} running"
        if not done and current and room >= 12:
            line += "  " + style.dim(
                _truncate(current, room - 2, glyphs.ellipsis, keep="start")
            )
        return line

    def _write(self, *, force: bool = False, done: bool = False) -> None:
        if self.reporter is None:
            return
        now = _time.monotonic()
        if not force and now - self._last_write < 0.05:
            return
        self._last_write = now
        if not self._is_tty():
            return
        text = _frame(self._clear_live() + self._block(done=done))
        if self._title_pushed:
            failed = (
                f" {self._style().glyphs.dot} {self._style().glyphs.failed} {self.failed}"
                if self.failed
                else ""
            )
            text += f"\x1b]2;m3 {self._style().glyphs.dot} {self.completed}/{max(self.total, self.completed)}{failed}\x1b\\"
        self._emit(text)

    def disable_native_progress(self) -> None:
        if not self.enabled or self.reporter is None:
            return
        # Save the reporter's own settings only once: a second call would
        # record the values this method already replaced.
        if (
            hasattr(self.reporter, "_show_progress_info")
            and self._native_progress is _NATIVE_PROGRESS_UNSET
        ):
            self._native_progress = self.reporter._show_progress_info
            self.reporter._show_progress_info = False
        if (
            hasattr(self.reporter, "_showfspath")
            and self._native_fspath is _NATIVE_PROGRESS_UNSET
        ):
            # File names would also land on the live block.
            self._native_fspath = self.reporter._showfspath
            self.reporter._showfspath = False

    def restyle_separators(self) -> None:
        """Draw pytest's section rules as thin dim lines in a colour terminal.

        Pytest draws every rule (session start, FAILURES, captured output,
        warnings, the final counts) through its terminal writer's ``sep``;
        only that method is replaced, and only on an interactive colour
        terminal outside --m3-ci and xdist workers.
        """

        reporter = self.reporter
        writer: _Any = getattr(reporter, "_tw", None)
        if (
            reporter is None
            or writer is None
            or self._native_write_sep
            or hasattr(self.config, "workerinput")
            or not callable(getattr(writer, "sep", None))
        ):
            return
        getoption = getattr(self.config, "getoption", None)
        if callable(getoption) and getoption("--m3-ci", default=False):
            return
        style = _terminal_style(reporter)
        if style is None or not style.color:
            return
        # The M3 banner replaces the session header (the CLI passes
        # --no-header when it printed one).
        skip_header = bool(getattr(self.config.option, "no_header", False))
        rule = "─" if style.glyphs.passed == "✓" else "-"

        def sep(
            sepchar: str,
            title: str | None = None,
            fullwidth: int | None = None,
            **markup: bool,
        ) -> None:
            if skip_header and title == "test session starts":
                return
            width = max(20, int(getattr(writer, "fullwidth", 80)) - 1)
            if not title:
                line = style.grey(rule * width)
            else:
                head = f"{rule * 2} "
                text = writer.markup(title, **markup) if markup else title
                tail = width - len(head) - _visible_len(text) - 1
                line = style.grey(head) + text + " " + style.grey(rule * max(2, tail))
            writer.line(line)

        writer.sep = sep
        self._native_write_sep = True

    def restore_native_progress(self) -> None:
        if self.reporter is None:
            return
        if self._native_write_sep:
            # Drop the instance attribute so the class method applies again.
            vars(getattr(self.reporter, "_tw", object())).pop("sep", None)
            self._native_write_sep = False
        if self._native_progress is not _NATIVE_PROGRESS_UNSET:
            self.reporter._show_progress_info = self._native_progress
            self._native_progress = _NATIVE_PROGRESS_UNSET
        if self._native_fspath is not _NATIVE_PROGRESS_UNSET:
            self.reporter._showfspath = self._native_fspath
            self._native_fspath = _NATIVE_PROGRESS_UNSET

    def _is_tty(self) -> bool:
        if _os.environ.get("TERM") == "dumb":
            return False
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
        if self.total == 0 and self.completed == 0:
            return
        self._flush_file()
        self._current = ""
        self._write(force=True, done=True)
        self._live_height = 0
        if self._title_pushed:
            self._emit("\x1b[23;0t")
            self._title_pushed = False
        if self._cursor_hidden:
            self._emit("\x1b[?25h")
            self._cursor_hidden = False
        self._notify()
        if self._is_tty():
            self.reporter.write_line("")

    def _notify(self) -> None:
        """Desktop notification for long runs, on terminals that support OSC 9."""

        if (
            _os.environ.get("TERM_PROGRAM") not in _NOTIFY_TERMINALS
            or _time.monotonic() - self._started < _NOTIFY_AFTER_SECONDS
        ):
            return
        parts = [f"{self.passed} passed"]
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        self._emit(f"\x1b]9;m3: {', '.join(parts)}\x1b\\")

    @_pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self, session: _Any, exitstatus: int) -> None:
        # Close the live block before the M3 summary is written below it.
        self.finish()

    @_pytest.hookimpl(tryfirst=True)
    def pytest_terminal_summary(self, terminalreporter: _Any, **_: _Any) -> None:
        self.reporter = terminalreporter
        excluded = int(getattr(self.config, "_m3_ci_excluded_count", 0))
        if self.config.getoption("--m3-ci"):
            terminalreporter.write_line(f"M3 CI excluded {excluded} test(s)")
        self.finish()


def _safe_nodeid(item: _Any) -> str:
    """Node id without parameter values, which may contain secrets."""

    nodeid = str(item.nodeid).split("[", 1)[0]
    callspec = getattr(item, "callspec", None)
    if callspec is None:
        return nodeid
    indices = getattr(callspec, "indices", None)
    if not indices:
        return f"{nodeid}[param]"
    return nodeid + "[" + "-".join(str(i) for i in indices.values()) + "]"


def _fail_on_exception(span: _Any, outcome: _Any) -> None:
    excinfo = getattr(outcome, "excinfo", None)
    if excinfo is not None:
        span.fail(type(excinfo[1]).__name__)


class _TimingPlugin:
    """Opt-in M3_TIMINGS spans for pytest; registered only when enabled."""

    def __init__(self, directory: str, owner: str, is_worker: bool) -> None:
        self.directory = directory
        self.owner = owner
        self.is_worker = is_worker
        self.reported = False
        self.test_span: _Any = None

    @classmethod
    def install(cls, config: _Any) -> None:
        if not _timing.ENABLED:
            return
        workerinput = getattr(config, "workerinput", None)
        if isinstance(workerinput, dict):
            directory = workerinput.get("m3_timings_dir")
            if not directory:
                return
            owner = str(workerinput.get("m3_timings_owner") or "pytest")
            label = f"worker-{workerinput.get('workerid', 'worker')}"
        else:
            requested = config.getoption("--m3-run-id")
            run_id = str(requested) if requested else f"run-{_uuid4().hex}"
            config._m3_timing_run_id = run_id
            root = (
                _Path(
                    config.getoption("--project-root")
                    or getattr(config, "rootpath", _Path.cwd())
                )
                .expanduser()
                .resolve()
            )
            directory = str(root / ".m3" / "reports" / run_id / "timings")
            owner = str(config.getoption("--m3-timings-owner") or "pytest")
            label = "controller"
        _timing.start(directory, label)
        plugin = cls(str(directory), owner, isinstance(workerinput, dict))
        config._m3_timing_plugin = plugin
        config.pluginmanager.register(plugin, "m3-timings")

    def close(self, config: _Any) -> None:
        _timing.stop()
        if not self.is_worker and self.owner == "pytest" and not self.reported:
            self.reported = True
            from . import _timing_report

            _timing_report.finish(self.directory)
        config.pluginmanager.unregister(self)
        config._m3_timing_plugin = None

    @_pytest.hookimpl(optionalhook=True)
    def pytest_configure_node(self, node: _Any) -> None:
        workerinput = getattr(node, "workerinput", None)
        if isinstance(workerinput, dict):
            workerinput["m3_timings_dir"] = self.directory
            workerinput["m3_timings_owner"] = self.owner

    @_pytest.hookimpl(hookwrapper=True)
    def pytest_collection(self) -> _Iterator[None]:
        with _timing.span("pytest.collection"):
            yield

    @_pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item: _Any, nextitem: _Any) -> _Iterator[None]:
        del nextitem
        nodeid = _safe_nodeid(item)
        with _timing.test_scope(nodeid), _timing.span("test", nodeid) as span:
            self.test_span = span
            try:
                outcome = yield
                _fail_on_exception(span, outcome)
            finally:
                self.test_span = None

    def pytest_runtest_logreport(self, report: _Any) -> None:
        if report.failed and self.test_span is not None:
            self.test_span.fail(str(report.when))

    @_pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_setup(self, item: _Any) -> _Iterator[None]:
        del item
        with _timing.span("test.setup") as span:
            _fail_on_exception(span, (yield))

    @_pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item: _Any) -> _Iterator[None]:
        del item
        with _timing.span("test.call") as span:
            _fail_on_exception(span, (yield))

    @_pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_teardown(self, item: _Any, nextitem: _Any) -> _Iterator[None]:
        del item, nextitem
        with _timing.span("test.teardown") as span:
            _fail_on_exception(span, (yield))

    @_pytest.hookimpl(hookwrapper=True)
    def pytest_fixture_setup(self, fixturedef: _Any, request: _Any) -> _Iterator[None]:
        del request
        with _timing.span("fixture.setup", str(fixturedef.argname)) as span:
            _fail_on_exception(span, (yield))

    @_pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: _Any, exitstatus: int) -> None:
        del session, exitstatus
        if self.is_worker:
            _timing.stop()

    @_pytest.hookimpl(trylast=True)
    def pytest_terminal_summary(self, terminalreporter: _Any, **_: _Any) -> None:
        if self.is_worker:
            return
        _timing.stop()
        if self.owner != "pytest" or self.reported:
            return
        self.reported = True
        from . import _timing_report

        class _Out:
            def write(self, text: str) -> int:
                terminalreporter.write(text)
                return len(text)

            def flush(self) -> None:
                pass

        terminalreporter.write_line("")
        out: _Any = _Out()
        _timing_report.finish(self.directory, print_to=out)


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
