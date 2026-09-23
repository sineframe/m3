"""Implementation of the safe ``m3 doctor`` command."""

from __future__ import annotations

import importlib.metadata
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from m3.configuration import ConfigError, load_config
from m3.services.probes import ProbeKind, ProbeRequest, Probes

from .errors import CLIError

_TRANSPORT_MODULES = {
    "stdio": "mcp.client.stdio",
    "streamable_http": "mcp.client.streamable_http",
}
_STORAGE_MODULES = {"sqlite": "sqlalchemy"}
_ENV_PREFIX = "M3_"


class DoctorCLIError(CLIError):
    """A user-facing doctor argument or configuration error."""


class DoctorArgumentError(DoctorCLIError):
    """A safe diagnostic for an invalid doctor argument combination."""


class DoctorProjectPythonError(DoctorCLIError):
    """A safe diagnostic for an unavailable or mismatched project Python."""


class DoctorConfigurationError(DoctorCLIError):
    """A value-free configuration diagnostic at the CLI boundary."""

    def __init__(self, error: ConfigError) -> None:
        self.configuration_error = error
        super().__init__("invalid configuration")


def _configuration_error_payload(error: ConfigError) -> dict[str, str]:
    return {
        "code": str(error.code),
        "field": str(error.field),
        "origin": str(error.origin),
        "reason": str(error.reason),
    }


def _read_selected_environment(path: Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    try:
        if not path.is_file():
            raise DoctorCLIError("environment file unavailable")
        # No interpolation through ambient variables: the selected file is an
        # explicit source, while ambient M3_* values override it below.
        values = dotenv_values(path, interpolate=False)
        selected = {
            key: value
            for key, value in values.items()
            if key.startswith(_ENV_PREFIX) and isinstance(value, str)
        }
        selected.update(
            {
                key: value
                for key, value in os.environ.items()
                if key.startswith(_ENV_PREFIX)
            }
        )
        return selected
    except (OSError, TypeError, ValueError):
        raise DoctorCLIError("environment file unavailable") from None


def _parse_requirement(value: str) -> tuple[str, str | None]:
    if value == "config":
        return "config", None
    if value.startswith("config:"):
        field = value.partition(":")[2]
        if field in {"artifact_policy", "protocol_revision", "telemetry_enabled"}:
            return "config", field
        raise DoctorCLIError("invalid requirement")
    try:
        kind, target = value.split(":", 1)
    except ValueError:
        raise DoctorCLIError("invalid requirement") from None
    if not target or kind not in {
        "binary",
        "harness",
        "protocol",
        "transport",
        "storage",
    }:
        raise DoctorCLIError("invalid requirement")
    if kind == "transport" and target not in _TRANSPORT_MODULES:
        raise DoctorCLIError("invalid requirement")
    if kind == "storage" and target not in {"memory", "sqlite"}:
        raise DoctorCLIError("invalid requirement")
    return kind, target


def _probe_label(kind: str, target: str) -> str:
    """Use a useful fixed/allowlisted result name without arbitrary targets."""

    if kind in {"binary", "harness", "protocol"}:
        return kind
    return target


def _requests(requirements: list[tuple[str, str | None]]) -> list[ProbeRequest]:
    requests: list[ProbeRequest] = []
    for kind, target in requirements:
        if kind == "config":
            continue
        assert target is not None
        if kind == "transport":
            requests.append(
                ProbeRequest(
                    ProbeKind.TRANSPORT,
                    _probe_label(kind, target),
                    transport=target,
                    module=_TRANSPORT_MODULES[target],
                )
            )
        elif kind == "storage":
            requests.append(
                ProbeRequest(
                    ProbeKind.STORAGE,
                    _probe_label(kind, target),
                    module=_STORAGE_MODULES.get(target),
                )
            )
        else:
            requests.append(
                ProbeRequest(
                    getattr(ProbeKind, kind.upper()),
                    _probe_label(kind, target),
                    executable=target,
                )
            )
    return requests


def _requirement_label(kind: str, target: str | None) -> str:
    """Return a fixed or allowlisted label that never contains executable targets."""

    if target is None:
        return kind
    return kind if kind in {"binary", "harness", "protocol"} else f"{kind}:{target}"


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _redact_requirement_targets(value: Any, targets: set[str]) -> Any:
    if isinstance(value, str):
        for target in targets:
            if target:
                value = value.replace(target, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [_redact_requirement_targets(item, targets) for item in value]
    if isinstance(value, dict):
        return {
            key: _redact_requirement_targets(item, targets)
            for key, item in value.items()
        }
    return value


def _safe_probe_results(
    results: list[Any], requirements: list[tuple[str, str | None]]
) -> list[dict[str, Any]]:
    """Project probe results without exposing executable paths or arguments."""

    targets = {
        target
        for kind, target in requirements
        if kind in {"binary", "harness", "protocol"} and target
    }
    safe_results: list[dict[str, Any]] = []
    for result in results:
        safe = _redact_requirement_targets(_json_value(result), targets)
        if not isinstance(safe, dict):
            safe_results.append(safe)
            continue
        evidence = safe.get("evidence")
        if isinstance(evidence, dict):
            evidence["resolved_executable"] = None
            details = evidence.get("details")
            if isinstance(details, dict):
                details.pop("resolved_executable", None)
        safe_results.append(safe)
    return safe_results


def run(args: Any) -> tuple[int, dict[str, Any]]:
    requirements = [_parse_requirement(value) for value in args.require]
    if not requirements:
        requirements = [("config", None), ("storage", "memory")]
    if args.env_file is not None and not any(
        kind == "config" for kind, _ in requirements
    ):
        raise DoctorArgumentError("--env-file requires a config requirement")
    environment = _read_selected_environment(args.env_file)
    if args.project_root is not None:
        try:
            if not args.project_root.is_dir():
                raise DoctorCLIError("project root unavailable")
        except OSError:
            raise DoctorCLIError("project root unavailable") from None
    configuration: dict[str, Any] | None = None
    project_python: dict[str, Any] | None = None
    from .supervisor import (
        ProjectPythonError,
        resolve_project_python,
        validate_project_python,
    )

    root = (args.project_root or Path.cwd()).resolve()
    try:
        cli_version = importlib.metadata.version("m3-cli")
        bundled_sdk_version = importlib.metadata.version("m3")
    except importlib.metadata.PackageNotFoundError:
        raise DoctorCLIError(
            "the CLI installation is incomplete; reinstall m3-cli"
        ) from None
    cli = {
        "status": "ready" if cli_version == bundled_sdk_version else "not ready",
        "version": cli_version,
        "sdk_version": bundled_sdk_version,
    }
    cli_ready = cli["status"] == "ready"
    source = None
    selected_path = None

    try:
        selected = resolve_project_python(
            getattr(args, "python", None),
            project_root=root,
            fallback_to_system=False,
        )
        selected_path = selected
        if getattr(args, "python", None) is not None:
            source = "--python"
        elif os.environ.get("VIRTUAL_ENV"):
            source = "VIRTUAL_ENV"
        elif os.environ.get("CONDA_PREFIX"):
            source = "CONDA_PREFIX"
        else:
            source = "project .venv"
        version = validate_project_python(
            selected,
            cli_sdk_version=bundled_sdk_version,
            project_root=root,
        )
    except ProjectPythonError as error:
        reason = str(error)
        if reason.startswith("no project environment is configured"):
            project_python = {"status": "not ready", "reason": reason}
        elif (
            "missing required M3 packages" in reason
            or "does not match CLI SDK" in reason
            or "distribution version could not be determined" in reason
        ):
            project_python = {"status": "not ready", "reason": reason, "source": source}
        else:
            raise DoctorProjectPythonError(reason) from None
    else:
        project_python = {
            "status": "ready",
            "version": version,
            "source": source,
            "executable": str(selected_path) if selected_path is not None else None,
        }
    include_config = any(kind == "config" for kind, _ in requirements)
    if include_config:
        try:
            config = load_config(env=environment, cwd=args.project_root)
        except ConfigError as error:
            raise DoctorConfigurationError(error) from None
        except (OSError, TypeError, ValueError):
            raise DoctorCLIError("invalid configuration") from None
        configuration = {"status": "ready", "settings": _json_value(config)}
    probe_report = Probes().probe_requested(_requests(requirements))
    results = _safe_probe_results(list(probe_report.results), requirements)
    ready = (
        cli_ready
        and project_python["status"] == "ready"
        and (configuration is None or configuration["status"] == "ready")
        and probe_report.readiness.ready
    )
    report = {
        "ready": ready,
        "cli": cli,
        "project_root": str(root),
        "requirements": [
            _requirement_label(kind, target) for kind, target in requirements
        ],
        "configuration": configuration,
        "project_python": project_python,
        "results": results,
    }
    return (0 if ready else 1), report


def print_human(report: dict[str, Any]) -> None:
    print(f"m3 doctor: {'ready' if report['ready'] else 'not ready'}")
    cli = report.get("cli")
    if cli is not None:
        print(f"M3 CLI {cli.get('version')}: {cli.get('status')}")
        if cli.get("sdk_version") != cli.get("version"):
            print(f"bundled SDK: {cli.get('sdk_version')}")
    project_python = report.get("project_python")
    if project_python is not None:
        detail = (
            f" (m3 {project_python['version']})"
            if project_python.get("version")
            else ""
        )
        print(f"project environment: {project_python['status']}{detail}")
        if project_python.get("source"):
            print(f"project environment source: {project_python['source']}")
        if project_python.get("executable"):
            print(f"project Python: {project_python['executable']}")
        if project_python.get("reason"):
            print(f"reason: {project_python['reason']}")
    if report.get("configuration") is not None:
        print(f"config: {report['configuration']['status']}")
    for result in report["results"]:
        capability = result["capability"]
        reason = capability.get("reason")
        print(
            f"{capability['name']}: {capability['status']}"
            + (f" ({reason})" if reason else "")
        )
    if not report.get("ready"):
        if cli := report.get("cli"):
            if cli.get("status") != "ready":
                print("Next: reinstall m3-cli")
                return
        if project_python is not None and project_python.get("status") != "ready":
            print("Next: m3 setup")
        else:
            print("Next: m3 doctor")


def print_configuration_error(error: ConfigError) -> None:
    details = _configuration_error_payload(error)
    print(
        "m3 doctor: configuration error "
        f"(code={details['code']} field={details['field']} "
        f"origin={details['origin']} reason={details['reason']})",
        file=sys.stderr,
    )


__all__ = [
    "DoctorArgumentError",
    "DoctorCLIError",
    "DoctorConfigurationError",
    "DoctorProjectPythonError",
    "_read_selected_environment",
    "print_configuration_error",
    "print_human",
    "run",
]
