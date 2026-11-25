"""Internal helpers for credential environment names.

Holds the reserved upload-token name and the credential variable each native
harness receives by default, so agent selection and ``m3 doctor`` agree.
"""

from __future__ import annotations

RESERVED_CREDENTIAL_ENV = "M3_ACCESS_TOKEN"

# Model prefix -> variable forwarded to OpenCode and Pi for that provider.
_PROVIDER_CREDENTIAL_SOURCES = {
    "opencode": "OPENCODE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
_PI_CODEX_SOURCE = "PI_CODING_AGENT_DIR"


def default_credential_source(kind: str, model: str) -> str | None:
    """Name the variable forwarded by default for one harness and model.

    Applies only when a selection has no ``credential_env``. ``None`` means
    the harness and model have no default credential variable.
    """
    if kind == "claude_code":
        return "ANTHROPIC_API_KEY"
    if kind == "codex":
        return "OPENAI_API_KEY"
    if kind in {"opencode", "pi"} and "/" in model:
        if kind == "pi" and model.startswith("openai-codex/"):
            return _PI_CODEX_SOURCE
        return _PROVIDER_CREDENTIAL_SOURCES.get(model.split("/", 1)[0])
    return None


def default_credential_sources(kind: str) -> dict[str, str]:
    """Map each default credential variable of a harness to what selects it.

    Claude Code and Codex have one variable for every model. OpenCode and Pi
    choose by model prefix, so the value names that prefix.
    """
    if kind == "claude_code":
        return {"ANTHROPIC_API_KEY": "any model"}
    if kind == "codex":
        return {"OPENAI_API_KEY": "any model"}
    if kind in {"opencode", "pi"}:
        sources = {
            source: f"{prefix}/ models"
            for prefix, source in _PROVIDER_CREDENTIAL_SOURCES.items()
        }
        if kind == "pi":
            sources[_PI_CODEX_SOURCE] = "openai-codex/ models"
        return sources
    return {}


def validate_credential_environment_names(target: str, source: str) -> None:
    """Reject the upload token name in supported agent and judge mappings."""
    if target == RESERVED_CREDENTIAL_ENV or source == RESERVED_CREDENTIAL_ENV:
        raise ValueError("M3_ACCESS_TOKEN cannot be mapped to a test credential")
