"""Vendor release recipe metadata used by callers constructing selectors.

URLs remain caller supplied so tests and installations can use mirrors.  The
recipe names document the expected upstream transport and executable shape.
"""

from __future__ import annotations

RECIPES = {
    "claude": {"transport": "direct-manifest", "executable": "claude"},
    "opencode": {
        "transport": "github-release-assets",
        "repository": "anomalyco/opencode",
        "executable": "opencode",
    },
    "codex": {
        "transport": "github-release-assets",
        "repository": "openai/codex",
        "executable": "codex",
    },
    "pi": {
        "transport": "github-release-assets",
        "repository": "earendil-works/pi",
        "executable": "pi",
    },
}


def default_manifest_url(kind: str, version: str = "latest") -> str | None:
    key = {"claude_code": "claude", "open-code": "opencode"}.get(
        kind.lower(), kind.lower()
    )
    if key == "opencode":
        suffix = "latest" if version == "latest" else f"tags/v{version}"
        return f"https://api.github.com/repos/anomalyco/opencode/releases/{suffix}"
    if key == "codex":
        suffix = "latest" if version == "latest" else f"tags/rust-v{version}"
        return f"https://api.github.com/repos/openai/codex/releases/{suffix}"
    if key == "pi":
        suffix = "latest" if version == "latest" else f"tags/v{version}"
        return f"https://api.github.com/repos/earendil-works/pi/releases/{suffix}"
    if key == "claude":
        base = "https://downloads.claude.ai/claude-code-releases"
        return (
            f"{base}/latest"
            if version == "latest"
            else f"{base}/{version}/manifest.json"
        )
    return None


def default_url(kind: str, version: str, target: str) -> str | None:
    """Compatibility hook for callers that used the old URL recipe API."""
    return None


def recipe(kind: str) -> dict[str, str]:
    """Return immutable-ish metadata for a supported vendor runtime."""
    key = {"claude_code": "claude", "open-code": "opencode"}.get(
        kind.lower(), kind.lower()
    )
    try:
        return dict(RECIPES[key])
    except KeyError as exc:
        raise ValueError(f"unsupported runtime kind: {kind}") from exc


__all__ = ["RECIPES", "default_manifest_url", "default_url", "recipe"]
