"""Deterministic fake harness adapters for contract and integration tests.

These adapters intentionally model the public capability shape of the named
harnesses without invoking their binaries, reading user configuration, or
falling back between harnesses.  They inherit the in-process stateful session
implementation from :class:`DeterministicHarnessAdapter`; the explicit class
names and capability declarations make a test's selected harness observable.
"""

from __future__ import annotations

from .contracts import (
    DeterministicHarnessAdapter,
    HarnessAdapterCapabilities,
    TurnHandler,
)


_CLAUDE_CONTENT = frozenset({"text", "file", "image", "audio", "resource_link", "opaque"})
_OPENCODE_CONTENT = frozenset({"text", "file", "image", "resource_link", "opaque"})
_ACP_CONTENT = frozenset({"text", "file", "image", "audio", "resource_link", "opaque"})


class DeterministicACPAdapter(DeterministicHarnessAdapter):
    """Local ACP-shaped adapter with one persistent deterministic session."""

    def __init__(
        self,
        *,
        handler: TurnHandler | None = None,
        startup_error: bool = False,
        cleanup_error: bool = False,
    ) -> None:
        super().__init__(
            name="deterministic-acp",
            handler=handler,
            capabilities=HarnessAdapterCapabilities(
                name="deterministic-acp",
                supports_multiturn=True,
                supports_cancellation=True,
                supports_timeout=True,
                supports_tool_policy=True,
                supports_streaming=True,
                supported_content_kinds=_ACP_CONTENT,
            ),
            startup_error=startup_error,
            cleanup_error=cleanup_error,
        )


class FakeClaudeCodeAdapter(DeterministicHarnessAdapter):
    """Explicit fake with Claude Code's stream-oriented test capabilities."""

    def __init__(
        self,
        *,
        handler: TurnHandler | None = None,
        startup_error: bool = False,
        cleanup_error: bool = False,
    ) -> None:
        super().__init__(
            name="fake-claude-code",
            handler=handler,
            capabilities=HarnessAdapterCapabilities(
                name="fake-claude-code",
                supports_multiturn=True,
                supports_cancellation=True,
                supports_timeout=True,
                supports_tool_policy=True,
                supports_streaming=True,
                supported_content_kinds=_CLAUDE_CONTENT,
            ),
            startup_error=startup_error,
            cleanup_error=cleanup_error,
        )


class FakeOpenCodeAdapter(DeterministicHarnessAdapter):
    """Explicit fake with OpenCode's stream-oriented test capabilities."""

    def __init__(
        self,
        *,
        handler: TurnHandler | None = None,
        startup_error: bool = False,
        cleanup_error: bool = False,
    ) -> None:
        super().__init__(
            name="fake-opencode",
            handler=handler,
            capabilities=HarnessAdapterCapabilities(
                name="fake-opencode",
                supports_multiturn=True,
                supports_cancellation=True,
                supports_timeout=True,
                supports_tool_policy=True,
                supports_streaming=True,
                supported_content_kinds=_OPENCODE_CONTENT,
            ),
            startup_error=startup_error,
            cleanup_error=cleanup_error,
        )


__all__ = ["DeterministicACPAdapter", "FakeClaudeCodeAdapter", "FakeOpenCodeAdapter"]
