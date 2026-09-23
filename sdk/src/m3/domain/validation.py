import json
import os
import re
from typing import Any

ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ProfileValidationError(ValueError):
    def __init__(self, errors: list[str], warnings: list[str] | None = None):
        self.errors, self.warnings = errors, warnings or []
        super().__init__("; ".join(errors))


def referenced_environment_variables(value: Any) -> list[str]:
    text = (
        json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    )
    return sorted(set(ENV_RE.findall(text)))


def _check_refs(value: Any, missing: list[str]) -> None:
    for n in referenced_environment_variables(value):
        if n not in os.environ and n not in missing:
            missing.append(n)


def validate_mcp_config(
    config: Any, *, check_environment: bool = False
) -> dict[str, Any]:
    """Validate MCP profile shape without consulting the host by default.

    Profile validation is used while storing and listing revisions, so it must
    be deterministic and independent of the process environment.  Callers
    performing an explicit local/readiness check may opt into environment
    inspection with ``check_environment=True``.
    """
    errors: list[str] = []
    warnings: list[str] = []
    missing: list[str] = []
    if (
        not isinstance(config, dict)
        or not isinstance(config.get("mcpServers"), dict)
        or not config["mcpServers"]
    ):
        raise ProfileValidationError(["mcpServers must be a non-empty object"])
    try:
        size = len(json.dumps(config, ensure_ascii=False).encode())
    except Exception:
        size = 100001
    if size > 100 * 1024:
        errors.append("MCP JSON exceeds maximum size of 100 KB")
    names = list(config["mcpServers"])
    if len(names) != len(set(names)):
        errors.append("server names must be unique")
    for name, server in config["mcpServers"].items():
        if not isinstance(name, str) or not SAFE_NAME.match(name):
            errors.append(f"invalid server name: {name!r}")
        if not isinstance(server, dict):
            errors.append(f"server {name} must be an object")
            continue
        typ = server.get("type", "stdio")
        if typ == "stdio":
            if not isinstance(server.get("command"), str) or not server["command"]:
                errors.append(f"server {name}: command is required")
            if "args" in server and (
                not isinstance(server["args"], list)
                or not all(isinstance(x, str) for x in server["args"])
            ):
                errors.append(f"server {name}: args must be a string array")
            if "env" in server and (
                not isinstance(server["env"], dict)
                or not all(
                    isinstance(k, str) and isinstance(v, str)
                    for k, v in server["env"].items()
                )
            ):
                errors.append(f"server {name}: env must be a string object")
        elif typ == "http":
            if not isinstance(server.get("url"), str) or not server["url"].startswith(
                ("http://", "https://")
            ):
                errors.append(f"server {name}: HTTP url is required")
            if "headers" in server and (
                not isinstance(server["headers"], dict)
                or not all(
                    isinstance(k, str) and isinstance(v, str)
                    for k, v in server["headers"].items()
                )
            ):
                errors.append(f"server {name}: headers must be a string object")
        else:
            errors.append(f"server {name}: unsupported type {typ!r}")
        if "oauth" in server or server.get("auth") == "oauth":
            errors.append(f"server {name}: interactive OAuth is not supported")
        if check_environment:
            _check_refs(server, missing)
    if check_environment and missing:
        warnings.append("Missing environment variables: " + ", ".join(missing))
    if not errors:
        warnings.append(
            "Profile JSON is stored unredacted in SQLite; use environment references for secrets"
        )
    if errors:
        raise ProfileValidationError(errors, warnings)
    return {
        "valid": True,
        "warnings": warnings,
        "missing_environment_variables": missing,
        "size_bytes": size,
    }


def selected_server_config(config: dict[str, Any], server: str) -> dict[str, Any]:
    validate_mcp_config(config)
    if server not in config["mcpServers"]:
        raise ProfileValidationError([f"selected server not found: {server}"])
    return {"mcpServers": {server: config["mcpServers"][server]}}
