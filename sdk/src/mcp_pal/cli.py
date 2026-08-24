"""Canonical MCP Pal command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, NoReturn

from dotenv import dotenv_values

from .configuration import ConfigurationError, resolve_config
from .services.probes import CapabilityProbeService, ProbeKind, ProbeRequest

_TRANSPORT_MODULES = {
    "stdio": "mcp.client.stdio",
    "streamable_http": "mcp.client.streamable_http",
    "sse": "mcp.client.sse",
}
_STORAGE_MODULES = {"sqlite": "sqlalchemy"}
_ENV_PREFIX = "MCP_PAL_"


class DoctorCLIError(ValueError):
    """A user-facing doctor argument or configuration error."""


class DoctorArgumentError(DoctorCLIError):
    """A safe, fixed diagnostic for a semantically invalid CLI combination."""


class DoctorConfigurationError(DoctorCLIError):
    """A value-free configuration diagnostic preserved for the CLI boundary."""

    def __init__(self, error: ConfigurationError) -> None:
        self.configuration_error = error
        super().__init__("invalid configuration")


class _RedactingArgumentParser(argparse.ArgumentParser):
    """Argument parser that never echoes untrusted option text on errors."""

    def error(self, message: str) -> NoReturn:  # noqa: ARG002 - message is intentionally discarded
        raise DoctorCLIError("invalid command or configuration")


def _parser() -> argparse.ArgumentParser:
    parser = _RedactingArgumentParser(prog="mcp-pal")
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=_RedactingArgumentParser)
    doctor = subparsers.add_parser("doctor", help="check explicitly requested SDK capabilities")
    doctor.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="KIND:TARGET",
        help=(
            "require config, binary:<exe>, harness:<exe>, protocol:<exe>, "
            "transport:<stdio|streamable_http|sse>, or storage:<memory|sqlite>"
        ),
    )
    doctor.add_argument("--project-root", type=Path, help="project root used for pyproject discovery")
    doctor.add_argument("--env-file", type=Path, help="explicit dotenv file; cwd .env is never searched")
    doctor.add_argument("--json", action="store_true", help="emit a machine-readable report")
    return parser


def _error_message(_: BaseException) -> str:
    return "mcp-pal doctor: invalid command or configuration"


def _argument_error_message(error: DoctorArgumentError) -> str:
    return f"mcp-pal doctor: {error}"


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
        selected.update({key: value for key, value in os.environ.items() if key.startswith(_ENV_PREFIX)})
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
            requests.append(ProbeRequest(getattr(ProbeKind, kind.upper()), _probe_label(kind, target), executable=target))
    return requests


def _requirement_label(kind: str, target: str | None) -> str:
    """Return a fixed or allowlisted label that never contains an executable target."""

    if target is None:
        return kind
    if kind in {"binary", "harness", "protocol"}:
        return kind
    return f"{kind}:{target}"


def _probe_label(kind: str, target: str) -> str:
    """Use a useful fixed/allowlisted result name without arbitrary targets."""

    if kind in {"binary", "harness", "protocol"}:
        return kind
    return target


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _redact_requirement_targets(value: Any, targets: set[str]) -> Any:
    """Remove raw executable targets from probe evidence returned by the CLI."""

    if isinstance(value, str):
        result = value
        for target in targets:
            if target:
                result = result.replace(target, "[REDACTED]")
        return result
    if isinstance(value, list):
        return [_redact_requirement_targets(item, targets) for item in value]
    if isinstance(value, dict):
        return {key: _redact_requirement_targets(item, targets) for key, item in value.items()}
    return value


def _safe_probe_results(
    results: list[Any],
    requirements: list[tuple[str, str | None]],
) -> list[dict[str, Any]]:
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
            # The CLI reports readiness and versions, while the executable
            # path remains private to the process that performed the probe.
            evidence["resolved_executable"] = None
            details = evidence.get("details")
            if isinstance(details, dict):
                details.pop("resolved_executable", None)
        safe_results.append(safe)
    return safe_results


def _doctor(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    requirements = [_parse_requirement(value) for value in args.require]
    if not requirements:
        requirements = [("config", None), ("storage", "memory")]
    if args.env_file is not None and not any(kind == "config" for kind, _ in requirements):
        raise DoctorArgumentError("--env-file requires a config requirement")
    environment = _read_selected_environment(args.env_file)
    if args.project_root is not None:
        try:
            if not args.project_root.is_dir():
                raise DoctorCLIError("project root unavailable")
        except OSError:
            raise DoctorCLIError("project root unavailable") from None

    include_config = any(kind == "config" for kind, _ in requirements)
    configuration: dict[str, Any] | None = None
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


def _print_human(report: dict[str, Any]) -> None:
    print(f"mcp-pal doctor: {'ready' if report['ready'] else 'not ready'}")
    configuration = report.get("configuration")
    if configuration is not None:
        print(f"config: {configuration['status']}")
    for result in report["results"]:
        capability = result["capability"]
        status = capability["status"]
        reason = capability.get("reason")
        print(f"{capability['name']}: {status}" + (f" ({reason})" if reason else ""))


def _print_configuration_error(error: ConfigurationError) -> None:
    details = _configuration_error_payload(error)
    print(
        "mcp-pal doctor: configuration error "
        f"(code={details['code']} field={details['field']} "
        f"origin={details['origin']} reason={details['reason']})",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        try:
            args = _parser().parse_args(effective_argv)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 2
        if args.command != "doctor":
            return 2
        code, report = _doctor(args)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_human(report)
        return code
    except DoctorConfigurationError as exc:
        if "--json" in effective_argv:
            print(json.dumps({"ready": False, "error": _configuration_error_payload(exc.configuration_error)}))
        else:
            _print_configuration_error(exc.configuration_error)
        return 2
    except DoctorArgumentError as exc:
        if "--json" in effective_argv:
            print(json.dumps({"ready": False, "error": str(exc)}))
        else:
            print(_argument_error_message(exc), file=sys.stderr)
        return 2
    except DoctorCLIError as exc:
        if "--json" in effective_argv:
            print(json.dumps({"ready": False, "error": "invalid command or configuration"}))
        else:
            print(_error_message(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - final redacted CLI boundary
        if "--json" in effective_argv:
            print(json.dumps({"ready": False, "error": "invalid command or configuration"}))
        else:
            print(_error_message(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["main"]
