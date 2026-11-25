"""Implementation of the safe ``m3 doctor`` command."""

from __future__ import annotations

import importlib.metadata
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from m3._credentials import (
    default_credential_sources,
    validate_credential_environment_names,
)
from m3.configuration import (
    ConfigError,
    configuration_digest,
    load_config,
)
from m3.services.probes import ProbeKind, ProbeRequest, Probes

from .canary import canary_build
from .errors import CLIError

_TRANSPORT_MODULES = {
    "stdio": "mcp.client.stdio",
    "streamable_http": "mcp.client.streamable_http",
}
_STORAGE_MODULES = {"sqlite": "sqlalchemy"}
_ENV_PREFIX = "M3_"

# Executable name -> harness kind. Any other executable is a harness M3 does
# not know the credentials of, so its credentials are reported as not checked.
_HARNESS_EXECUTABLES = {
    "claude": "claude_code",
    "codex": "codex",
    "opencode": "opencode",
    "pi": "pi",
}
_HARNESS_NAMES = {
    "claude_code": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
    "pi": "Pi",
}
_CREDENTIAL_SCOPES = {"claude_code", "codex", "opencode", "pi", "acp", "judge"}
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
# Variable names doctor may print. A mapping is typed by hand, so any other
# name could be a pasted secret and is described without being shown.
_PRINTABLE_TARGETS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CODEX_API_KEY",
        "OPENAI_API_KEY",
        "OPENCODE_API_KEY",
        "PI_CODING_AGENT_DIR",
    }
)
_CREDENTIAL_FIXES = {
    "claude_code": (
        "set ANTHROPIC_API_KEY, or export a token from `claude setup-token` "
        "as MY_CLAUDE_TOKEN and pass --credential-env "
        "claude_code:CLAUDE_CODE_OAUTH_TOKEN=MY_CLAUDE_TOKEN to m3 doctor and "
        "m3 test"
    ),
    "codex": (
        "set OPENAI_API_KEY, sign in with `codex login`, or export a key as "
        "MY_OPENAI_KEY and pass --credential-env "
        "codex:OPENAI_API_KEY=MY_OPENAI_KEY to m3 doctor and m3 test"
    ),
}


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


def _credential_environment(path: Path | None) -> tuple[dict[str, str], set[str]]:
    """Return the environment `m3 test` gives pytest, and the file-only names.

    Ambient variables win over the dotenv file, including empty ones, and file
    values are not interpolated. The values are used for presence checks only.
    """

    environment = dict(os.environ)
    from_file: set[str] = set()
    if path is None:
        return environment, from_file
    try:
        if not path.is_file():
            raise DoctorCLIError("environment file unavailable")
        values = dotenv_values(path, interpolate=False)
    except (OSError, TypeError, ValueError):
        raise DoctorCLIError("environment file unavailable") from None
    for key, value in values.items():
        if key and isinstance(value, str) and key not in environment:
            environment[key] = value
            from_file.add(key)
    return environment, from_file


def _parse_credential_mappings(values: list[str]) -> dict[str | None, dict[str, str]]:
    """Group ``[KIND:]TARGET=SOURCE`` mappings by scope without echoing them."""

    mappings: dict[str | None, dict[str, str]] = {}
    for raw in values:
        if "=" not in raw:
            raise DoctorArgumentError("--credential-env requires TARGET=SOURCE")
        target, source = raw.split("=", 1)
        scope: str | None = None
        if ":" in target:
            scope, target = target.split(":", 1)
            scope = scope.strip().lower().replace("-", "_")
            scope = {"claude": "claude_code"}.get(scope, scope)
            if scope not in _CREDENTIAL_SCOPES:
                raise DoctorArgumentError("unknown credential scope")
        target, source = target.strip(), source.strip()
        if not _ENV_NAME.match(target) or not _ENV_NAME.match(source):
            raise DoctorArgumentError(
                "credential environment names must be Python identifiers"
            )
        try:
            validate_credential_environment_names(target, source)
        except ValueError as error:
            raise DoctorArgumentError(str(error)) from None
        scoped = mappings.setdefault(scope, {})
        if target in scoped:
            raise DoctorArgumentError("duplicate credential target")
        scoped[target] = source
    return mappings


def _variable_state(
    name: str, environment: dict[str, str], from_file: set[str]
) -> dict[str, str]:
    value = environment.get(name)
    state = {"status": "not set" if value is None else "set" if value else "empty"}
    if value is not None:
        state["origin"] = "env_file" if name in from_file else "environment"
    return state


def _harness_kind(target: str) -> str | None:
    name = Path(target).name.lower()
    for suffix in (".exe", ".cmd"):
        name = name.removesuffix(suffix)
    return _HARNESS_EXECUTABLES.get(name)


def _harness_credentials(
    kind: str | None,
    mappings: dict[str | None, dict[str, str]],
    environment: dict[str, str],
    from_file: set[str],
) -> dict[str, Any]:
    """Report which credential sources M3 would use, never their values.

    ``status`` is ``ready`` when a credential M3 forwards is present,
    ``missing`` when M3 can tell that none is, and ``not checked`` when the
    answer depends on something doctor does not know.
    """

    if kind is None:
        return {
            "status": "not checked",
            "harness": None,
            "reason": "the executable is not a harness doctor knows credentials for",
            "sources": [],
            "fix": None,
        }
    label = _HARNESS_NAMES[kind]
    mapped = dict(mappings.get(None, {}))
    mapped.update(mappings.get(kind, {}))
    sources: list[dict[str, Any]] = []
    if mapped:
        # An explicit mapping is the complete set the agent receives, and a
        # launch needs every mapped source to hold a non-empty value.
        for target, source in mapped.items():
            sources.append(
                {
                    "name": target if target in _PRINTABLE_TARGETS else None,
                    "kind": "mapped",
                    **_variable_state(source, environment, from_file),
                }
            )
        absent = sum(item["status"] != "set" for item in sources)
        return {
            "status": "missing" if absent else "ready",
            "harness": kind,
            "reason": (
                f"{absent} mapped credential source"
                f"{' is' if absent == 1 else 's are'} not set or empty"
                if absent
                else "every mapped credential source is set"
            ),
            "sources": sources,
            "fix": (
                "set the source variable of each --credential-env mapping to "
                "a non-empty value"
                if absent
                else None
            ),
        }
    for name, used_for in default_credential_sources(kind).items():
        sources.append(
            {
                "name": name,
                "kind": "environment",
                "used_for": used_for,
                **_variable_state(name, environment, from_file),
            }
        )
    present = [item["name"] for item in sources if item["status"] == "set"]
    if kind == "codex":
        # M3 copies this file into the temporary Codex home when no key is
        # forwarded. Its presence is checked; its content is never read.
        home = environment.get("CODEX_HOME") or str(Path.home() / ".codex")
        login = Path(home) / "auth.json"
        try:
            signed_in = login.is_file() and not login.is_symlink()
        except OSError:
            signed_in = False
        sources.append(
            {
                "name": "Codex login file",
                "kind": "login",
                "status": "present" if signed_in else "not found",
            }
        )
        if signed_in:
            present.append("Codex login file")
    elif kind == "claude_code":
        sources.append(
            {
                "name": "Claude Code login",
                "kind": "login",
                "status": "not used",
            }
        )
    if kind in {"opencode", "pi"}:
        # The model prefix selects the variable, and doctor has no model.
        return {
            "status": "not checked",
            "harness": kind,
            "reason": (
                f"{label} needs the variable matching the model prefix; "
                + (f"set: {', '.join(present)}" if present else "none is set")
            ),
            "sources": sources,
            "fix": None,
        }
    if present:
        return {
            "status": "ready",
            "harness": kind,
            "reason": f"{present[0]} is present; its value is not validated",
            "sources": sources,
            "fix": None,
        }
    return {
        "status": "missing",
        "harness": kind,
        "reason": f"no credential for {label}",
        "sources": sources,
        "fix": _CREDENTIAL_FIXES[kind],
    }


def _parse_requirement(value: str) -> tuple[str, str | None]:
    if value == "config":
        return "config", None
    if value.startswith("config:"):
        field = value.partition(":")[2]
        if field in {"artifact_policy", "protocol_revision"}:
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
    harness_required = any(kind == "harness" for kind, _ in requirements)
    if (
        args.env_file is not None
        and not harness_required
        and not any(kind == "config" for kind, _ in requirements)
    ):
        raise DoctorArgumentError("--env-file requires a config or harness requirement")
    credential_mappings = _parse_credential_mappings(
        list(getattr(args, "credential_env", None) or [])
    )
    if credential_mappings and not harness_required:
        raise DoctorArgumentError("--credential-env requires a harness requirement")
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
        discover_env_file,
        resolve_project_python,
        resolve_project_root,
        validate_project_python,
    )

    root = resolve_project_root(args.project_root)
    selected_env_file = (
        discover_env_file(args.env_file, root)
        if harness_required or any(kind == "config" for kind, _ in requirements)
        else None
    )
    environment = (
        _read_selected_environment(selected_env_file)
        if any(kind == "config" for kind, _ in requirements)
        else None
    )
    try:
        cli_version = importlib.metadata.version("sf-m3-cli")
        bundled_sdk_version = importlib.metadata.version("sf-m3")
    except importlib.metadata.PackageNotFoundError:
        raise DoctorCLIError(
            "the CLI installation is incomplete; reinstall sf-m3-cli"
        ) from None
    cli: dict[str, Any] = {
        "status": "ready" if cli_version == bundled_sdk_version else "not ready",
        "version": cli_version,
        "sdk_version": bundled_sdk_version,
    }
    canary = canary_build()
    if canary is not None:
        cli["canary"] = {
            "release_tag": canary.release_tag,
            "source_commit": canary.source_commit,
        }
    cli_ready = cli["status"] == "ready"
    source = None
    selected_path = None

    include_config = any(kind == "config" for kind, _ in requirements)
    try:
        config = load_config(
            env=environment if include_config else {},
            cwd=args.project_root,
            config_file=getattr(args, "m3_config", None),
        )
    except ConfigError as error:
        raise DoctorConfigurationError(error) from None
    except (OSError, TypeError, ValueError):
        raise DoctorCLIError("invalid configuration") from None
    if include_config:
        configuration = {
            "status": "ready",
            "settings": _json_value(config),
            "digest": configuration_digest(config),
        }
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
            timeout_seconds=config.cli.project_python_timeout_seconds,
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
    probe_report = Probes().probe_requested(_requests(requirements))
    results = _safe_probe_results(list(probe_report.results), requirements)
    credentials_ready = True
    if harness_required:
        credential_environment, from_file = _credential_environment(selected_env_file)
        probed = [(kind, target) for kind, target in requirements if kind != "config"]
        for (kind, target), result in zip(probed, results, strict=True):
            if kind != "harness" or target is None:
                continue
            credentials = _harness_credentials(
                _harness_kind(target),
                credential_mappings,
                credential_environment,
                from_file,
            )
            result["credentials"] = credentials
            if credentials["status"] != "missing":
                continue
            credentials_ready = False
            capability = result["capability"]
            if capability.get("status") == "ready":
                # The executable runs, but a test cannot: do not call it ready.
                capability["status"] = "unavailable"
                capability["reason"] = credentials["reason"]
    ready = (
        cli_ready
        and project_python["status"] == "ready"
        and (configuration is None or configuration["status"] == "ready")
        and probe_report.readiness.ready
        and credentials_ready
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


def _source_line(source: dict[str, Any]) -> str:
    """Describe one credential source by name and state, never by value."""

    status = source["status"]
    if source["kind"] == "mapped":
        name = (
            f"source mapped to {source['name']}"
            if source.get("name")
            else "mapped source"
        )
    else:
        name = source["name"]
    detail = ""
    if source["kind"] == "login" and status == "not used":
        detail = " (M3 does not copy it into the agent's temporary home)"
    elif source["kind"] == "login" and status == "present":
        detail = " (content not checked)"
    elif source.get("origin") == "env_file":
        detail = " (from the env file, which m3 test loads and plain pytest does not)"
    elif source.get("used_for") and source["used_for"] != "any model":
        detail = f" ({source['used_for']})"
    return f"{name}: {status}{detail}"


def print_human(report: dict[str, Any]) -> None:
    print(f"m3 doctor: {'ready' if report['ready'] else 'not ready'}")
    cli = report.get("cli")
    if cli is not None:
        canary = cli.get("canary")
        suffix = (
            f" ({canary['release_tag']}, {canary['source_commit'][:7]})"
            if isinstance(canary, dict)
            else ""
        )
        print(f"M3 CLI {cli.get('version')}{suffix}: {cli.get('status')}")
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
    configuration = report.get("configuration")
    if configuration is not None:
        print(f"config: {configuration['status']} ({configuration['digest']})")
        for name, origin in sorted(configuration["settings"]["sources"].items()):
            if origin["source"] != "default":
                print(f"  {name} from {origin['origin']}")
    for result in report["results"]:
        capability = result["capability"]
        reason = capability.get("reason")
        print(
            f"{capability['name']}: {capability['status']}"
            + (f" ({reason})" if reason else "")
        )
        credentials = result.get("credentials")
        if credentials is not None:
            print(f"  credentials: {credentials['status']} ({credentials['reason']})")
            for source in credentials["sources"]:
                print(f"  {_source_line(source)}")
    if not report.get("ready"):
        if cli := report.get("cli"):
            if cli.get("status") != "ready":
                print("Next: reinstall sf-m3-cli")
                return
        fixes = [
            result["credentials"]["fix"]
            for result in report["results"]
            if (result.get("credentials") or {}).get("fix")
        ]
        if project_python is not None and project_python.get("status") != "ready":
            print("Next: m3 setup")
        elif fixes:
            for fix in dict.fromkeys(fixes):
                print(f"Next: {fix}")
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
