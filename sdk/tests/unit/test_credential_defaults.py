"""The default credential variable each native harness receives."""

from __future__ import annotations

import pytest

from m3._agent_selection import _credential_refs
from m3._credentials import default_credential_source, default_credential_sources


@pytest.mark.parametrize(
    ("kind", "model", "source"),
    [
        ("claude_code", "claude-model", "ANTHROPIC_API_KEY"),
        ("codex", "codex-model", "OPENAI_API_KEY"),
        ("opencode", "opencode/model", "OPENCODE_API_KEY"),
        ("opencode", "openai/model", "OPENAI_API_KEY"),
        ("opencode", "anthropic/model", "ANTHROPIC_API_KEY"),
        ("opencode", "openai-codex/model", None),
        ("opencode", "other/model", None),
        ("opencode", "model", None),
        ("pi", "openai/model", "OPENAI_API_KEY"),
        ("pi", "anthropic/model", "ANTHROPIC_API_KEY"),
        ("pi", "openai-codex/model", "PI_CODING_AGENT_DIR"),
        ("pi", "model", None),
        ("acp", "openai/model", None),
    ],
)
def test_default_credential_source(kind: str, model: str, source: str | None) -> None:
    assert default_credential_source(kind, model) == source


@pytest.mark.parametrize("kind", ["claude_code", "codex", "opencode", "pi", "acp"])
def test_default_credential_sources_cover_every_model_default(kind: str) -> None:
    models = (
        "model",
        "opencode/model",
        "openai/model",
        "anthropic/model",
        "openai-codex/model",
        "other/model",
    )
    selected = {default_credential_source(kind, model) for model in models} - {None}
    assert set(default_credential_sources(kind)) == selected


def test_agent_selection_forwards_only_a_non_empty_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert _credential_refs("claude_code", "claude-model", None) == {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert _credential_refs("claude_code", "claude-model", None) == {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "credential-canary-value")
    refs = _credential_refs("claude_code", "claude-model", None)
    assert {target: ref.name for target, ref in refs.items()} == {
        "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY"
    }
    # An explicit mapping, even an empty one, replaces the default.
    assert _credential_refs("claude_code", "claude-model", {}) == {}
