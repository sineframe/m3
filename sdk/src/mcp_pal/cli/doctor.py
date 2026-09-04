"""Implementation of the safe ``mcp-pal doctor`` command."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from ..configuration import ConfigurationError, resolve_config
from ..services.probes import CapabilityProbeService, ProbeKind, ProbeRequest
from .errors import CLIError

_TRANSPORT_MODULES = {
    "stdio": "mcp.client.stdio",
    "streamable_http": "mcp.client.streamable_http",
    "sse": "mcp.client.sse",
}
_STORAGE_MODULES = {"sqlite": "sqlalchemy"}
_ENV_PREFIX = "MCP_PAL_"


class DoctorCLIError(CLIError):
    """A user-facing doctor argument or configuration error."""


class DoctorArgumentError(DoctorCLIError):
    """A safe diagnostic for an invalid doctor argument combination."""


class DoctorConfigurationError(DoctorCLIError):
    """A value-free configuration diagnostic at the CLI boundary."""

    def __init__(self, error: ConfigurationError) -> None:
        self.configuration_error = error
        super().__init__("invalid configuration")


def _configuration_error_payload(error: ConfigurationError) -> dict[str, str]:
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
        # explicit source, while ambient MCP_PAL_* values override it below.
        values = dotenv_values(path, interpolate=False)
        selected = {
            key: value
            for key, value in values.items()
            if key.startswith(_ENV_PREFIX) and isinstance(value, str)
        }
        selected.update(
            {key: value for key, value in os.environ.items() if key.startswith(_ENV_PREFIX)}
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
    if not target or kind not in {"binary", "harness", "protocol", "transport", "storage"}:
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
        return {key: _redact_requirement_targets(item, targets) for key, item in value.items()}
    return value


def _safe_probe_results(results: list[Any], requirements: list[tuple[str, str | None]]) -> list[dict[str, Any]]:
    """Project probe results without exposing executable paths or arguments."""

    targets = {target for kind, target in requirements if kind in {"binary", "harness", "protocol"} and target}
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
    include_config = any(kind == "config" for kind, _ in requirements)
    if include_config:
        try:
            config = resolve_config(env=environment, cwd=args.project_root)
        except ConfigurationError as error:
            raise DoctorConfigurationError(error) from None
        except (OSError, TypeError, ValueError):
            raise DoctorCLIError("invalid configuration") from None
        configuration = {"status": "ready", "settings": _json_value(config)}
    probe_report = CapabilityProbeService().probe_requested(_requests(requirements))
    results = _safe_probe_results(list(probe_report.results), requirements)
    ready = (configuration is None or configuration["status"] == "ready") and probe_report.readiness.ready
    report = {
        "ready": ready,
        "requirements": [_requirement_label(kind, target) for kind, target in requirements],
        "configuration": configuration,
        "results": results,
    }
    return (0 if ready else 1), report


def print_human(report: dict[str, Any]) -> None:
    print(f"mcp-pal doctor: {'ready' if report['ready'] else 'not ready'}")
    if report.get("configuration") is not None:
        print(f"config: {report['configuration']['status']}")
    for result in report["results"]:
        capability = result["capability"]
        reason = capability.get("reason")
        print(f"{capability['name']}: {capability['status']}" + (f" ({reason})" if reason else ""))


def print_configuration_error(error: ConfigurationError) -> None:
    details = _configuration_error_payload(error)
    print(
        "mcp-pal doctor: configuration error "
        f"(code={details['code']} field={details['field']} "
        f"origin={details['origin']} reason={details['reason']})",
        file=sys.stderr,
    )


__all__ = [
    "DoctorCLIError",
    "DoctorArgumentError",
    "DoctorConfigurationError",
    "run",
    "print_human",
    "print_configuration_error",
    "_read_selected_environment",
]
