"""Shared contract for deterministic and real agent-harness adapters.

The contract is deliberately independent from the execution/session
controllers.  Adapters own one harness conversation; controllers decide how
that conversation is attached to an execution and how terminal evidence is
persisted.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import uuid
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias

from ..agent_session import HarnessAdapter as HarnessAdapter
from ..errors import MCPError
from ..interaction_handlers import Interactions
from ..policy import ToolDescriptor, ToolPolicyEvaluator, ToolPolicyEvidence
from ..types import (
    AgentSpec,
    Capability,
    CapabilityStatus,
    ErrorCode,
    ErrorInfo,
    HarnessSpec,
    Readiness,
    TextContent,
    ToolPolicy,
    TurnResponse,
    UserMessage,
)

if TYPE_CHECKING:
    from ..server_group import HarnessServerConfig, ServerGroupSnapshot
    from .observations import HarnessSessionEvidence, TurnEvidence


class HarnessAdapterError(MCPError):
    """Sanitized adapter failure; messages never contain harness output."""


class HarnessStartupError(HarnessAdapterError):
    """The harness could not be opened."""


class HarnessCleanupError(HarnessAdapterError):
    """Owned harness resources could not be cleaned up."""


class UnsupportedHarnessFeature(HarnessAdapterError):
    """The requested message, policy, or lifecycle feature is unsupported."""


@dataclass(frozen=True, slots=True)
class HarnessAdapterCapabilities:
    """Features an adapter can truthfully enforce and observe."""

    name: str
    supports_multiturn: bool = True
    supports_cancellation: bool = True
    supports_timeout: bool = True
    supports_tool_policy: bool = True
    supports_streaming: bool = False
    supported_content_kinds: frozenset[str] = frozenset({"text"})

    def readiness(self, *, ready: bool = True, reason: str | None = None) -> Readiness:
        status = CapabilityStatus.READY if ready else CapabilityStatus.UNAVAILABLE
        capability = Capability(
            name=f"harness:{self.name}",
            status=status,
            reason=reason,
        )
        return Readiness(ready=ready, capabilities=(capability,), reason=reason)


@dataclass(frozen=True, slots=True)
class HarnessTurnRequest:
    """One immutable turn request delivered to an adapter-owned session."""

    message: UserMessage
    timeout_seconds: float | None = None
    metadata: Mapping[str, str | int | float | bool | None] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and (
            not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
        ):
            raise ValueError("harness turn timeout must be positive and finite")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @classmethod
    def from_message(
        cls,
        message: str | UserMessage,
        *,
        timeout_seconds: float | None = None,
        metadata: Mapping[str, str | int | float | bool | None] | None = None,
    ) -> HarnessTurnRequest:
        user_message = (
            message
            if isinstance(message, UserMessage)
            else UserMessage(content=(TextContent(text=message),))
        )
        return cls(user_message, timeout_seconds, metadata or {})


@dataclass(frozen=True, slots=True)
class HarnessTurnResult:
    """Safe adapter result for one completed, failed, or cancelled turn."""

    sequence: int
    status: Literal["completed", "failed", "timed_out", "cancelled", "interrupted"]
    response: TurnResponse | None = None
    error: ErrorInfo | None = None
    tool_calls: tuple[Mapping[str, Any], ...] = ()
    evidence: Mapping[str, str | int | float | bool | None] = field(
        default_factory=dict
    )
    trace_limitations: tuple[str, ...] = ()
    # Typed evidence is the preferred shared boundary.  ``evidence`` remains
    # as a compatibility receipt for existing adapters until R6-R8 migrate.
    turn_evidence: TurnEvidence | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise TypeError("harness turn sequence must be a non-negative integer")
        if self.status not in {
            "completed",
            "failed",
            "timed_out",
            "cancelled",
            "interrupted",
        }:
            raise ValueError("harness turn status is invalid")
        if self.turn_evidence is not None and (
            self.turn_evidence.sequence != self.sequence
            or self.turn_evidence.status != self.status
        ):
            raise ValueError("turn evidence does not match the harness turn result")
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))
        object.__setattr__(
            self,
            "tool_calls",
            tuple(MappingProxyType(dict(call)) for call in self.tool_calls),
        )
        object.__setattr__(
            self,
            "trace_limitations",
            tuple(str(item) for item in self.trace_limitations),
        )


@dataclass(frozen=True, slots=True)
class HarnessSessionSnapshot:
    """Immutable adapter-owned conversation evidence."""

    session_id: str
    turns: int
    server_configuration_count: int
    closed: bool
    evidence: Mapping[str, str | int | float | bool | None] = field(
        default_factory=dict
    )
    session_evidence: HarnessSessionEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ValueError("harness session ID must be non-empty")
        if (
            isinstance(self.turns, bool)
            or not isinstance(self.turns, int)
            or self.turns < 0
        ):
            raise TypeError("harness session turn count must be a non-negative integer")
        if self.session_evidence is not None and (
            self.session_evidence.session_id != self.session_id
            or len(self.session_evidence.turns) != self.turns
            or self.session_evidence.closed != self.closed
        ):
            raise ValueError("session evidence does not match the harness snapshot")
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))


@dataclass(frozen=True, slots=True)
class HarnessLaunch:
    """Resolved, non-secret launch inputs supplied by a controller."""

    spec: AgentSpec
    servers: ServerGroupSnapshot
    configurations: tuple[HarnessServerConfig, ...]
    tool_policy: ToolPolicy
    interactions: Interactions | None = None
    workspace_root: str | None = None
    tool_policy_evidence: ToolPolicyEvidence | None = None
    # Runtime-owned, read-only capture context.  The adapter may query a
    # structured snapshot but never owns transport framing or proxy cleanup.
    capture: Any | None = None

    def with_tool_policy_evidence(self, evidence: ToolPolicyEvidence) -> HarnessLaunch:
        """Return this immutable launch with its preflight evidence attached."""

        return HarnessLaunch(
            self.spec,
            self.servers,
            self.configurations,
            self.tool_policy,
            self.interactions,
            self.workspace_root,
            evidence,
            self.capture,
        )


class HarnessSession(Protocol):
    """One adapter-owned conversation, preserved across sequential turns."""

    @property
    def session_id(self) -> str: ...

    @property
    def capabilities(self) -> HarnessAdapterCapabilities: ...

    async def send(self, request: HarnessTurnRequest) -> HarnessTurnResult: ...

    async def cancel(self) -> None: ...

    def snapshot(self) -> HarnessSessionSnapshot: ...

    async def close(self) -> None: ...


class HarnessAdapterContract(Protocol):
    """Common adapter contract used by all execution controllers."""

    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> HarnessAdapterCapabilities: ...

    async def preflight(self, launch: HarnessLaunch) -> Readiness: ...

    async def open(self, launch: HarnessLaunch) -> HarnessSession: ...


HarnessAdapterFactory: TypeAlias = Callable[[HarnessSpec], HarnessAdapter]


class HarnessAdapterRegistry:
    """Resolve a typed harness value to one fresh conversation adapter.

    The registry is intentionally keyed by the discriminated ``kind`` in the
    public harness value.  It never executes an executable from that value;
    real process-backed adapters can be registered explicitly by an
    application, while the SDK default deliberately has no concrete fallback.
    """

    def __init__(
        self, factories: Mapping[str, HarnessAdapterFactory] | None = None
    ) -> None:
        self._factories: dict[str, HarnessAdapterFactory] = dict(factories or {})

    def register(self, kind: str, factory: HarnessAdapterFactory) -> None:
        if not kind or len(kind) > 64:
            raise ValueError("harness kind must be non-empty and bounded")
        self._factories[kind] = factory

    def resolve(self, spec: AgentSpec) -> HarnessAdapter:
        harness = spec.harness
        if harness is None:
            raise HarnessStartupError("harness profile resolution is unavailable")
        try:
            factory = self._factories[harness.kind]
        except KeyError:
            raise HarnessStartupError("requested harness is unavailable") from None
        return factory(harness)


def default_adapters() -> HarnessAdapterRegistry:
    """Return the production registry with explicit real adapters.

    Each registered kind resolves only to its own process-backed adapter. ACP,
    Claude, OpenCode, Codex, and Pi never fall back to deterministic adapters or to one
    another when a selected executable is unavailable.
    """

    from .acp import AcpHarnessAdapter
    from .claude import ClaudeCodeHarnessAdapter
    from .codex import CodexHarnessAdapter
    from .opencode import OpenCodeHarnessAdapter
    from .pi import PiHarnessAdapter

    registry = HarnessAdapterRegistry()

    def claude_factory(harness: HarnessSpec) -> HarnessAdapter:
        executable = harness.executable if hasattr(harness, "executable") else None
        return ClaudeCodeHarnessAdapter(executable=executable or "claude")

    def opencode_factory(harness: HarnessSpec) -> HarnessAdapter:
        executable = harness.executable if hasattr(harness, "executable") else None
        return OpenCodeHarnessAdapter(executable=executable or "opencode")

    def acp_factory(harness: HarnessSpec) -> HarnessAdapter:
        manifest = getattr(harness, "manifest", {})
        if not manifest or not manifest.get("command"):
            # A typed ACP value without an executable is not a runnable
            # profile. Resolve-time failure preserves the no-implicit-fake
            # invariant; a supplied but unavailable command is reported by
            # the adapter's typed readiness path.
            raise HarnessStartupError("ACP harness manifest is unavailable")
        return AcpHarnessAdapter(manifest=manifest)

    def codex_factory(harness: HarnessSpec) -> HarnessAdapter:
        executable = harness.executable if hasattr(harness, "executable") else None
        return CodexHarnessAdapter(executable=executable or "codex")

    def pi_factory(harness: HarnessSpec) -> HarnessAdapter:
        executable = harness.executable if hasattr(harness, "executable") else None
        return PiHarnessAdapter(executable=executable or "pi")

    registry.register("claude_code", claude_factory)
    registry.register("opencode", opencode_factory)
    registry.register("acp", acp_factory)
    registry.register("codex", codex_factory)
    registry.register("pi", pi_factory)
    return registry


TurnHandler: TypeAlias = Callable[
    [HarnessTurnRequest, MutableMapping[str, Any]],
    HarnessTurnResult
    | TurnResponse
    | str
    | Awaitable[HarnessTurnResult | TurnResponse | str],
]


def _safe_error(code: ErrorCode, message: str) -> ErrorInfo:
    return ErrorInfo(code=code, message=message)


def _response_text(response: TurnResponse) -> str:
    return "".join(
        block.text for block in response.content if isinstance(block, TextContent)
    )


class DeterministicHarnessAdapter:
    """In-process fake adapter for the shared harness contract.

    The handler is an explicitly registered Python callable.  It is never
    imported from user input or executed through a shell.  The adapter keeps a
    single session ID, launch configuration, and mutable test state for the
    entire conversation so tests can prove true multi-turn preservation.
    """

    def __init__(
        self,
        *,
        name: str = "deterministic",
        handler: TurnHandler | None = None,
        capabilities: HarnessAdapterCapabilities | None = None,
        startup_error: bool = False,
        cleanup_error: bool = False,
    ) -> None:
        if not name or len(name) > 128:
            raise ValueError("adapter name must be non-empty and bounded")
        self._name = name
        self._capabilities = capabilities or HarnessAdapterCapabilities(name=name)
        self._handler = handler or self._default_handler
        self._startup_error = startup_error
        self._cleanup_error = cleanup_error
        self.open_count = 0
        self.last_launch: HarnessLaunch | None = None
        self.last_policy_evidence: ToolPolicyEvidence | None = None
        self._active_session: _DeterministicHarnessSession | None = None

    @property
    def supported_content_kinds(self) -> frozenset[str]:
        """Compatibility hook consumed by the shared AgentSession controller."""

        return self._capabilities.supported_content_kinds

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> HarnessAdapterCapabilities:
        return self._capabilities

    async def preflight(self, launch: HarnessLaunch) -> Readiness:
        if self._startup_error:
            return self._capabilities.readiness(
                ready=False, reason="startup_unavailable"
            )
        try:
            descriptors = tuple(
                ToolDescriptor(server=record.key, name=tool)
                for record in launch.servers.records
                if record.available
                for tool in record.tools
            )
            evidence = ToolPolicyEvaluator(descriptors).preflight(
                launch.tool_policy,
                harness_name=self._name,
                supports_enforcement=self._capabilities.supports_tool_policy,
            )
            self.last_policy_evidence = evidence
        except Exception:
            return self._capabilities.readiness(
                ready=False, reason="tool_policy_unsupported"
            )
        unsupported = {
            block.kind
            for block in (
                launch.spec.message.content if launch.spec.message is not None else ()
            )
            if block.kind not in self._capabilities.supported_content_kinds
        }
        if unsupported:
            return self._capabilities.readiness(
                ready=False, reason="attachment_unsupported"
            )
        return self._capabilities.readiness()

    async def open(self, launch: HarnessLaunch) -> HarnessSession:
        readiness = await self.preflight(launch)
        if not readiness.ready:
            raise HarnessStartupError("harness preflight failed")
        self.open_count += 1
        effective_launch = launch
        if (
            launch.tool_policy_evidence is None
            and self.last_policy_evidence is not None
        ):
            effective_launch = launch.with_tool_policy_evidence(
                self.last_policy_evidence
            )
        self.last_launch = effective_launch
        session = _DeterministicHarnessSession(self, effective_launch)
        self._active_session = session
        return session

    async def start(self, spec: AgentSpec) -> None:
        """Open the existing session-controller adapter contract."""

        from ..server_group import ServerGroupSnapshot

        launch = HarnessLaunch(spec, ServerGroupSnapshot(), (), spec.tool_policy)
        await self.open(launch)

    async def send(
        self,
        message: UserMessage,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> TurnResponse:
        session = self._active_session
        if session is None:
            raise HarnessAdapterError("harness session is not started")
        request = HarnessTurnRequest.from_message(
            message,
            timeout_seconds=timeout,
            metadata={
                str(key): value
                for key, value in (metadata or {}).items()
                if isinstance(value, (str, int, float, bool)) or value is None
            },
        )
        result = await session.send(request)
        if result.response is not None:
            return result.response
        raise HarnessAdapterError("harness turn failed")

    async def close(self) -> None:
        session = self._active_session
        if session is not None:
            await session.close()
            self._active_session = None

    async def _default_handler(
        self,
        request: HarnessTurnRequest,
        state: MutableMapping[str, Any],
    ) -> TurnResponse:
        del state
        return TurnResponse(content=tuple(request.message.content))


class _DeterministicHarnessSession:
    def __init__(
        self, adapter: DeterministicHarnessAdapter, launch: HarnessLaunch
    ) -> None:
        self._adapter = adapter
        self._launch = launch
        self._session_id = "fake-" + uuid.uuid4().hex
        self._process_scope = "in-process-" + uuid.uuid4().hex
        self._connection_scope = "connection-" + uuid.uuid4().hex
        self._turns = 0
        self._closed = False
        self._cancel_requested = False
        self._active: asyncio.Task[Any] | None = None
        self._state: dict[str, Any] = {
            "turns": [],
            "servers": launch.configurations,
            # Deterministic handlers use this explicit injection point to
            # exercise the same policy-gated interaction callbacks that a
            # concrete harness adapter receives from HarnessLaunch.
            "interactions": launch.interactions,
        }

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def capabilities(self) -> HarnessAdapterCapabilities:
        return self._adapter.capabilities

    async def send(self, request: HarnessTurnRequest) -> HarnessTurnResult:
        if self._closed:
            raise HarnessAdapterError("harness session is closed")
        if self._cancel_requested:
            return HarnessTurnResult(
                sequence=self._turns + 1,
                status="cancelled",
                error=_safe_error(ErrorCode.CANCELLED, "harness turn cancelled"),
            )
        unsupported = {
            block.kind
            for block in request.message.content
            if block.kind not in self.capabilities.supported_content_kinds
        }
        if unsupported:
            raise UnsupportedHarnessFeature("harness attachment is unsupported")
        self._turns += 1
        sequence = self._turns
        self._state["turns"].append(request.message)
        self._cancel_requested = False

        async def invoke() -> HarnessTurnResult:
            value = self._adapter._handler(request, self._state)
            if inspect.isawaitable(value):
                value = await value
            if isinstance(value, HarnessTurnResult):
                evidence = {
                    "process_capture": "in_process",
                    "transport_capture": "in_process",
                    "mcp_capture": "normalized",
                    "tool_capture": "adapter_reported",
                    "content_capture": "structured",
                    "usage_provenance": "unavailable",
                    **dict(value.evidence),
                }
                if value.sequence != sequence:
                    return HarnessTurnResult(
                        sequence=sequence,
                        status=value.status,
                        response=value.response,
                        error=value.error,
                        tool_calls=value.tool_calls,
                        evidence=evidence,
                        turn_evidence=value.turn_evidence,
                    )
                return HarnessTurnResult(
                    sequence=value.sequence,
                    status=value.status,
                    response=value.response,
                    error=value.error,
                    tool_calls=value.tool_calls,
                    evidence=evidence,
                    turn_evidence=value.turn_evidence,
                )
            response = (
                value
                if isinstance(value, TurnResponse)
                else TurnResponse(content=(TextContent(text=str(value)),))
            )
            return HarnessTurnResult(
                sequence=sequence,
                status="completed",
                response=response,
                evidence={
                    "process_capture": "in_process",
                    "transport_capture": "in_process",
                    "mcp_capture": "normalized",
                    "tool_capture": "adapter_reported",
                    "content_capture": "structured",
                    "usage_provenance": "unavailable",
                },
            )

        task = asyncio.create_task(invoke())
        self._active = task
        try:
            if request.timeout_seconds is None:
                return await task
            return await asyncio.wait_for(asyncio.shield(task), request.timeout_seconds)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return HarnessTurnResult(
                sequence=sequence,
                status="timed_out",
                error=_safe_error(ErrorCode.TIMEOUT, "harness turn timed out"),
                evidence={"usage_provenance": "unavailable"},
            )
        except asyncio.CancelledError:
            if self._cancel_requested:
                return HarnessTurnResult(
                    sequence=sequence,
                    status="cancelled",
                    error=_safe_error(ErrorCode.CANCELLED, "harness turn cancelled"),
                    evidence={"usage_provenance": "unavailable"},
                )
            raise
        except Exception:
            return HarnessTurnResult(
                sequence=sequence,
                status="failed",
                error=_safe_error(ErrorCode.PROTOCOL_ERROR, "harness turn failed"),
                evidence={"usage_provenance": "unavailable"},
            )
        finally:
            self._active = None

    async def cancel(self) -> None:
        if self._closed:
            return
        self._cancel_requested = True
        task = self._active
        if task is not None and not task.done():
            task.cancel()

    def snapshot(self) -> HarnessSessionSnapshot:
        evidence: dict[str, str | int | float | bool | None] = {
            "adapter": self._adapter.name,
            "process_scope": self._process_scope,
            "connection_scope": self._connection_scope,
            "capture_provenance": "deterministic_fixture",
            "usage_provenance": "unavailable",
        }
        policy_evidence = self._launch.tool_policy_evidence
        if policy_evidence is not None:
            evidence.update(
                {
                    "policy_requested": policy_evidence.requested,
                    "policy_enforced": policy_evidence.enforced,
                    "policy_observed": policy_evidence.observed,
                    "policy_portable": policy_evidence.portable,
                }
            )
        return HarnessSessionSnapshot(
            session_id=self._session_id,
            turns=self._turns,
            server_configuration_count=len(self._launch.configurations),
            closed=self._closed,
            evidence=evidence,
        )

    async def close(self) -> None:
        if self._closed:
            return
        await self.cancel()
        if self._adapter._cleanup_error:
            self._closed = True
            raise HarnessCleanupError("harness cleanup failed")
        self._closed = True


__all__ = [
    "DeterministicHarnessAdapter",
    "HarnessAdapter",
    "HarnessAdapterCapabilities",
    "HarnessAdapterContract",
    "HarnessAdapterError",
    "HarnessAdapterFactory",
    "HarnessAdapterRegistry",
    "HarnessCleanupError",
    "HarnessLaunch",
    "HarnessSession",
    "HarnessSessionSnapshot",
    "HarnessStartupError",
    "HarnessTurnRequest",
    "HarnessTurnResult",
    "UnsupportedHarnessFeature",
    "default_adapters",
]
