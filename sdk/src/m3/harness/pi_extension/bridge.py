"""Private bounded JSONL bridge used by the bundled Pi extension.

The bridge protocol is intentionally private: Pi receives a dynamic catalog
and forwards calls, while the SDK's capture proxies remain authoritative for
MCP wire evidence.  Transport clients are injected by the adapter in live
launches; this module also provides deterministic framing for fixtures.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from mcp import types as _mcp_types

try:
    from ..._mrtr import (
        input_required as _input_required,
    )
    from ..._mrtr import (
        normalize_requests as _normalize_requests,
    )
    from ..._mrtr import plain_json as _plain_json
    from ..._mrtr import (
        resolve_other as _resolve_other,
    )
    from ..._mrtr import (
        validate_response as _validate_response,
    )
    from ...elicitation import (
        ElicitationPlan,
        ElicitationResponse,
        PendingElicitationRound,
        PlanMatcher,
    )
    from ...errors import ElicitationExpectationError, UnsupportedFeature
except ImportError:  # Executed as the package-owned bridge script.
    from m3._mrtr import (
        input_required as _input_required,
    )
    from m3._mrtr import (
        normalize_requests as _normalize_requests,
    )
    from m3._mrtr import plain_json as _plain_json
    from m3._mrtr import (
        resolve_other as _resolve_other,
    )
    from m3._mrtr import (
        validate_response as _validate_response,
    )
    from m3.elicitation import (
        ElicitationPlan,
        ElicitationResponse,
        PendingElicitationRound,
        PlanMatcher,
    )
    from m3.errors import ElicitationExpectationError, UnsupportedFeature

MAX_FRAME_BYTES = 1024 * 1024
MAX_CHANNEL_BYTES = 512 * 1024
_STATUS_STATES = frozenset(
    {"idle", "awaiting_plan", "in_progress", "completed", "failed"}
)


class BridgeError(RuntimeError):
    pass


class BridgeProtocolError(BridgeError):
    """A bounded, typed failure at the bridge/MCP protocol boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class BridgeActionContext:
    generation: str
    turn_sequence: int
    plan: ElicitationPlan | None
    round_limit: int
    execution_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None

    def __post_init__(self) -> None:
        if not self.generation or self.turn_sequence < 0:
            raise ValueError("action context identity is invalid")
        if self.plan is not None and self.round_limit <= 0:
            raise ValueError("action context round limit must be positive")
        for name in ("execution_id", "session_id", "turn_id"):
            value = getattr(self, name)
            if value is not None and not value:
                raise ValueError(f"action context {name} is invalid")


@dataclass(frozen=True, slots=True)
class BridgeActionStatus:
    generation: str
    turn_sequence: int
    state: str
    rounds: int = 0
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not self.generation or self.turn_sequence < 0 or self.rounds < 0:
            raise ValueError("action status is invalid")
        if self.state not in _STATUS_STATES:
            raise ValueError("action status state is invalid")


@dataclass(slots=True)
class _BridgeActionState:
    """In-memory state for one action generation.

    A failed state is deliberately retained until a newer context is observed.
    This makes a terminal failure sticky without allowing the bridge process to
    accumulate state for every turn in a long-lived Pi session.
    """

    claimed: bool = False
    matcher: PlanMatcher | None = None
    invocation: str | None = None
    rounds: int = 0
    managed_round_index: int = 0
    terminal: str | None = None
    operation_task: asyncio.Task[Any] | None = None
    pending: _BridgePendingCall | None = None


@dataclass(slots=True)
class _BridgePendingCall:
    continuation_id: str
    session: Any
    server: str
    tool: str
    arguments: dict[str, Any]
    context: BridgeActionContext
    invocation: str
    required: Any
    requests: dict[str, Any]
    other: dict[str, Any]


class ActionContextChannel:
    """Small atomic channel owned by one native adapter process tree.

    The adapter and bridge do not share Python memory.  Files are therefore
    used only as a capability channel, never as a transport or proxy control
    plane.  Every write is bounded, private, fsynced, and atomically renamed.
    """

    def __init__(self, context_path: str | Path, status_path: str | Path) -> None:
        self.context_path = Path(context_path)
        self.status_path = Path(status_path)
        if self.context_path.parent != self.status_path.parent:
            raise ValueError("action channel files must share a parent directory")
        self.context_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.context_path.parent, 0o700)

    @staticmethod
    def _write(path: Path, value: Mapping[str, Any]) -> None:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(encoded) > MAX_CHANNEL_BYTES:
            raise BridgeProtocolError(
                "channel_overflow", "action channel frame is too large"
            )
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    @staticmethod
    def _read(path: Path) -> Mapping[str, Any] | None:
        fd = -1
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(path, flags)
            stat = os.fstat(fd)
            if stat.st_mode & 0o077 or stat.st_uid != os.getuid():
                os.close(fd)
                fd = -1
                raise BridgeProtocolError(
                    "channel_permissions", "action channel permissions are invalid"
                )
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                raw = handle.read(MAX_CHANNEL_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise BridgeProtocolError(
                "channel_io", "action channel is unavailable"
            ) from error
        finally:
            if fd >= 0:
                os.close(fd)
        if len(raw) > MAX_CHANNEL_BYTES:
            raise BridgeProtocolError(
                "channel_overflow", "action channel frame is too large"
            )
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BridgeProtocolError(
                "channel_protocol", "action channel frame is invalid"
            ) from error
        if not isinstance(value, Mapping):
            raise BridgeProtocolError(
                "channel_protocol", "action channel frame is invalid"
            )
        return value

    def write_context(
        self,
        *,
        generation: str,
        turn_sequence: int,
        plan: ElicitationPlan | None,
        round_limit: int,
        execution_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        self._write(
            self.context_path,
            {
                "generation": generation,
                "turn_sequence": turn_sequence,
                "plan": plan.model_dump(mode="json") if plan is not None else None,
                "round_limit": round_limit,
                "execution_id": execution_id,
                "session_id": session_id,
                "turn_id": turn_id,
            },
        )

    def read_context(self) -> BridgeActionContext | None:
        value = self._read(self.context_path)
        if value is None:
            return None
        generation = value.get("generation")
        sequence = value.get("turn_sequence")
        limit = value.get("round_limit")
        if (
            not isinstance(generation, str)
            or not generation
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or (value.get("plan") is not None and limit <= 0)
        ):
            raise BridgeProtocolError("channel_protocol", "action context is invalid")
        raw_plan = value.get("plan")
        try:
            plan = (
                ElicitationPlan.model_validate(raw_plan)
                if raw_plan is not None
                else None
            )
        except Exception as error:
            raise BridgeProtocolError(
                "channel_protocol", "action plan is invalid"
            ) from error
        if plan is not None and not plan.is_complete:
            raise BridgeProtocolError("channel_protocol", "action plan is incomplete")
        identities = {
            name: value.get(name) for name in ("execution_id", "session_id", "turn_id")
        }
        if any(
            item is not None and (not isinstance(item, str) or not item)
            for item in identities.values()
        ):
            raise BridgeProtocolError("channel_protocol", "action identity is invalid")
        return BridgeActionContext(generation, sequence, plan, limit, **identities)

    def write_status(self, status: BridgeActionStatus) -> None:
        self._write(
            self.status_path,
            {
                "generation": status.generation,
                "turn_sequence": status.turn_sequence,
                "state": status.state,
                "rounds": status.rounds,
                "error_code": status.error_code,
            },
        )

    def read_status(self) -> BridgeActionStatus | None:
        value = self._read(self.status_path)
        if value is None:
            return None
        generation = value.get("generation")
        sequence = value.get("turn_sequence")
        state = value.get("state")
        rounds = value.get("rounds", 0)
        code = value.get("error_code")
        if (
            not isinstance(generation, str)
            or not generation
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or not isinstance(state, str)
            or isinstance(rounds, bool)
            or not isinstance(rounds, int)
            or (code is not None and not isinstance(code, str))
            or state not in _STATUS_STATES
        ):
            raise BridgeProtocolError("channel_protocol", "action status is invalid")
        return BridgeActionStatus(generation, sequence, state, rounds, code)

    def clear(self) -> None:
        for path in (self.context_path, self.status_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def qualified_tool_name(server: str, tool: str) -> str:
    # Provider names are short ASCII identifiers. Keep readable slugs, with a
    # pair digest to distinguish identical slugs and preserve routing.
    def slug(value: str) -> str:
        value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
        return value or "unnamed"

    digest = hashlib.sha256(f"{server}\0{tool}".encode()).hexdigest()[:12]
    # Keep the MCP names visible to the model while reserving room for a
    # collision-resistant suffix and the required provider-safe prefix.
    prefix = f"mcp_{slug(server)[:20]}_{slug(tool)[:20]}"
    return f"{prefix}_{digest}"[:64]


class MCPBridge:
    """Catalog/call router; MCP sessions are owned by the caller."""

    def __init__(
        self,
        tools: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
        sessions: Mapping[str, Any] | None = None,
        *,
        channel: ActionContextChannel | None = None,
    ) -> None:
        self._tools = {
            str(server): {str(name): dict(value) for name, value in values.items()}
            for server, values in (tools or {}).items()
        }
        self._sessions = dict(sessions or {})
        self._channel = channel
        self._actions: dict[str, _BridgeActionState] = {}

    def list_tools(self) -> list[dict[str, Any]]:
        output = []
        for server, tools in self._tools.items():
            for tool, descriptor in tools.items():
                name = qualified_tool_name(server, tool)
                public = {
                    key: value
                    for key, value in descriptor.items()
                    if key not in {"call", "name", "label", "server", "tool"}
                }
                if "input_schema" in public and "inputSchema" not in public:
                    public["inputSchema"] = public.pop("input_schema")
                if "output_schema" in public and "outputSchema" not in public:
                    public["outputSchema"] = public.pop("output_schema")
                output.append(
                    {
                        "name": name,
                        "label": f"{server}: {tool}",
                        "server": server,
                        "tool": tool,
                        **public,
                    }
                )
        mapping_path = os.environ.get("M3_PI_TOOL_MAP")
        if mapping_path:
            try:
                parent = os.path.dirname(mapping_path) or "."
                temporary: str | None = None
                fd, temporary = tempfile.mkstemp(prefix=".mapping-", dir=parent)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            item["name"]: [item["server"], item["tool"]]
                            for item in output
                        },
                        handle,
                    )
                os.replace(temporary, mapping_path)
            except OSError:
                if temporary is not None:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
        return output

    def _context_for_call(self, generation: str | None) -> BridgeActionContext | None:
        if self._channel is None:
            return None
        context = self._channel.read_context()
        if context is None:
            raise BridgeProtocolError(
                "context_missing", "action context is unavailable"
            )
        # The channel is the source of truth for the current action.  Retain
        # only that generation so failed generations stay sticky while the
        # bounded bridge state cannot grow with session length.
        for stale in tuple(self._actions):
            if stale != context.generation:
                self._actions.pop(stale, None)
        if generation is not None and generation != context.generation:
            raise BridgeProtocolError(
                "stale_generation", "action context generation is stale"
            )
        return context

    def _status(
        self,
        context: BridgeActionContext,
        state: str,
        *,
        rounds: int = 0,
        error_code: str | None = None,
    ) -> None:
        action = self._actions.get(context.generation)
        if action is not None and action.terminal == "failed":
            if state != "failed":
                return
            if self._channel is not None:
                existing = self._channel.read_status()
                if (
                    existing is not None
                    and existing.generation == context.generation
                    and existing.turn_sequence == context.turn_sequence
                    and existing.state == "failed"
                ):
                    return
        if self._channel is not None:
            self._channel.write_status(
                BridgeActionStatus(
                    context.generation,
                    context.turn_sequence,
                    state,
                    rounds,
                    error_code,
                )
            )

    def _ensure_action_active(
        self, context: BridgeActionContext, action: _BridgeActionState
    ) -> None:
        if action.terminal == "failed":
            raise BridgeProtocolError(
                "failed", "the action generation is already terminal"
            )
        self._reject_failed_context(context)

    def _fail_concurrent_operation(
        self, context: BridgeActionContext, action: _BridgeActionState
    ) -> None:
        action.terminal = "failed"
        self._status(
            context,
            "failed",
            rounds=action.rounds,
            error_code="concurrent_elicitation",
        )
        task = action.operation_task
        current = asyncio.current_task()
        if task is not None and task is not current and not task.done():
            task.cancel()

    def _mark_failure_if_current(
        self, context: BridgeActionContext, error_code: str
    ) -> None:
        action = self._actions.setdefault(context.generation, _BridgeActionState())
        action.terminal = "failed"
        if self._channel is None:
            return
        try:
            current = self._channel.read_context()
        except BridgeProtocolError:
            return
        if (
            current is not None
            and current.generation == context.generation
            and current.turn_sequence == context.turn_sequence
        ):
            self._status(context, "failed", error_code=error_code)

    def _reject_failed_context(self, context: BridgeActionContext) -> None:
        if self._channel is None:
            return
        status = self._channel.read_status()
        if (
            status is not None
            and status.generation == context.generation
            and status.turn_sequence == context.turn_sequence
            and status.state == "failed"
        ):
            raise BridgeProtocolError(
                "failed", "the action generation is already terminal"
            )

    @staticmethod
    def _result(value: Any) -> Any:
        return (
            value.model_dump(mode="json", by_alias=True)
            if hasattr(value, "model_dump")
            else value
        )

    async def _call_tool_mrtr(
        self,
        session: Any,
        server: str,
        tool: str,
        arguments: Mapping[str, Any],
        context: BridgeActionContext,
        *,
        initial: Any = None,
        invocation: str | None = None,
    ) -> Any:
        invocation = invocation or os.urandom(16).hex()
        action = self._actions.setdefault(context.generation, _BridgeActionState())
        claimed_operation = False
        if action.terminal == "failed":
            raise BridgeProtocolError(
                "failed", "the action generation is already terminal"
            )
        current = (
            initial
            if initial is not None
            else await session.call_tool(
                tool,
                dict(arguments),
                allow_input_required=True,
            )
        )
        required = _input_required(current)
        rounds = 0
        while required is not None:
            rounds += 1
            elicitation, other = _normalize_requests(
                required,
                session=session,
                operation_kind="tool",
                operation_name=tool,
                server_name=server,
            )
            if elicitation and action.claimed and action.invocation != invocation:
                self._fail_concurrent_operation(context, action)
                raise BridgeProtocolError(
                    "concurrent_elicitation",
                    "more than one logical operation requested elicitation",
                )
            total_rounds = action.rounds + rounds
            if total_rounds > context.round_limit:
                self._status(
                    context, "failed", rounds=total_rounds, error_code="round_limit"
                )
                raise BridgeProtocolError(
                    "round_limit", "elicitation round limit exceeded"
                )
            managed = context.execution_id is not None
            if elicitation and context.plan is None and not managed:
                self._status(
                    context,
                    "failed",
                    rounds=rounds,
                    error_code="unexpected_elicitation",
                )
                raise BridgeProtocolError(
                    "unexpected_elicitation",
                    "MCP tool requested elicitation without a plan",
                )
            if not elicitation and not other and required.request_state is None:
                raise BridgeProtocolError(
                    "empty_round", "input-required result contains no requests"
                )
            plan = context.plan
            matcher: PlanMatcher | None = None
            if elicitation and not action.claimed:
                action.claimed = True
                action.invocation = invocation
                if managed:
                    # Control round indices count managed elicitation frames
                    # within this logical operation.  ``action.rounds`` also
                    # includes sampling and roots rounds for status reporting.
                    action.managed_round_index = 0
                if plan is None and not managed:
                    raise BridgeProtocolError(
                        "channel_protocol", "action plan is unavailable"
                    )
                action.matcher = plan.matcher() if plan is not None else None
                action.operation_task = asyncio.current_task()
                claimed_operation = True
            if elicitation:
                matcher = action.matcher
                if not managed and not isinstance(matcher, PlanMatcher):
                    raise BridgeProtocolError(
                        "channel_protocol", "action matcher is unavailable"
                    )
            responses = matcher.match_round(elicitation) if matcher is not None else {}
            for key, response in responses.items():
                _validate_response(elicitation[key], response)
            # Sampling and roots remain callback-owned.  If the installed
            # MCP session has no callback, _resolve_other raises the
            # typed unsupported error instead of dropping the request.
            resolved = await _resolve_other(session, other)
            for key, response in responses.items():
                resolved[key] = _mcp_types.ElicitResult(
                    action=response.action,
                    content=(
                        cast(dict[str, Any], _plain_json(response.content))
                        if response.content is not None
                        else None
                    ),
                    _meta=(dict(response.meta) if response.meta is not None else None),
                )
            if managed and context.plan is None and elicitation:
                # The bridge owns the operation and retry, while the parent
                # coordinator owns persistence and response validation.
                action.rounds += rounds
                pending_id = uuid4().hex
                action.pending = _BridgePendingCall(
                    pending_id,
                    session,
                    server,
                    tool,
                    dict(arguments),
                    context,
                    invocation,
                    required,
                    dict(elicitation),
                    dict(resolved),
                )
                pending = PendingElicitationRound(
                    round_id=uuid4().hex,
                    execution_id=context.execution_id or "unbound",
                    logical_operation_id=invocation,
                    server=server,
                    operation_kind="tool",
                    operation_name=tool,
                    request_state=required.request_state,
                    requests=elicitation,
                    created_at=datetime.now(timezone.utc),
                    deadline=None,
                )
                pending_frame = pending.model_dump(mode="json")
                pending_frame.update(
                    {
                        "generation": context.generation,
                        "turn_sequence": context.turn_sequence,
                        "round_index": action.managed_round_index,
                        "round_limit": context.round_limit,
                        "operation_parameters": {
                            **dict(arguments),
                            **(
                                {"session_id": context.session_id}
                                if context.session_id is not None
                                else {}
                            ),
                            **(
                                {"turn_id": context.turn_id}
                                if context.turn_id is not None
                                else {}
                            ),
                        },
                    }
                )
                action.managed_round_index += 1
                return {
                    "__m3_pending__": pending_frame,
                    "continuation_id": pending_id,
                }
            before = self._context_for_call(context.generation)
            if (
                before is None
                or before.generation != context.generation
                or before.turn_sequence != context.turn_sequence
            ):
                raise BridgeProtocolError(
                    "stale_generation", "action context changed during elicitation"
                )
            if claimed_operation:
                self._ensure_action_active(context, action)
            current = await session.call_tool(
                tool,
                dict(arguments),
                input_responses=resolved,
                request_state=required.request_state,
                allow_input_required=True,
            )
            required = _input_required(current)
            if claimed_operation:
                self._ensure_action_active(context, action)
        action.rounds += rounds
        if claimed_operation:
            self._ensure_action_active(context, action)
        if context.execution_id is not None and action.pending is None:
            # A completed managed operation releases its claim.  A second
            # eliciting operation may then run sequentially in this action,
            # while concurrent work is still rejected above.
            action.claimed = False
            action.invocation = None
            action.operation_task = None
        self._status(
            context,
            "awaiting_plan" if context.plan and not action.claimed else "in_progress",
            rounds=action.rounds,
        )
        return current

    async def continue_tool(
        self,
        generation: str,
        continuation_id: str,
        responses: Mapping[str, Any],
    ) -> Any:
        context = self._context_for_call(generation)
        if context is None:
            raise BridgeProtocolError(
                "context_missing", "action context is unavailable"
            )
        action = self._actions.get(generation)
        if action is None:
            raise BridgeProtocolError(
                "stale_round", "managed elicitation round is stale"
            )
        pending = action.pending
        if pending is None or pending.continuation_id != continuation_id:
            raise BridgeProtocolError(
                "stale_round", "managed elicitation round is stale"
            )
        if set(responses) != set(pending.requests):
            raise BridgeProtocolError(
                "response_keys", "managed responses do not match the current round"
            )
        resolved = dict(pending.other)
        for key, request in pending.requests.items():
            try:
                response = ElicitationResponse.model_validate(responses[key])
                _validate_response(request, response)
            except Exception as error:
                raise BridgeProtocolError(
                    "elicitation_mismatch", "managed response is invalid"
                ) from error
            resolved[key] = _mcp_types.ElicitResult(
                action=response.action,
                content=(
                    cast(dict[str, Any], _plain_json(response.content))
                    if response.content is not None
                    else None
                ),
                _meta=(dict(response.meta) if response.meta is not None else None),
            )
        if action.terminal == "failed":
            raise BridgeProtocolError("cancelled", "managed elicitation was cancelled")
        action.operation_task = asyncio.current_task()
        try:
            initial = await pending.session.call_tool(
                pending.tool,
                dict(pending.arguments),
                input_responses=resolved,
                request_state=pending.required.request_state,
                allow_input_required=True,
            )
            result = await self._call_tool_mrtr(
                pending.session,
                pending.server,
                pending.tool,
                pending.arguments,
                pending.context,
                initial=initial,
                invocation=pending.invocation,
            )
            if action.pending is pending:
                action.pending = None
                action.claimed = False
                action.invocation = None
            return result
        except BaseException as error:
            action.pending = None
            action.claimed = False
            action.invocation = None
            action.operation_task = None
            action.terminal = "failed"
            self._status(
                pending.context,
                "failed",
                rounds=action.rounds,
                error_code=(
                    "cancelled"
                    if isinstance(error, asyncio.CancelledError)
                    else "mcp_protocol"
                ),
            )
            if isinstance(error, BridgeError):
                raise
            if isinstance(error, asyncio.CancelledError):
                raise
            raise BridgeProtocolError("mcp_protocol", "MCP tool retry failed") from None
        finally:
            if action.operation_task is asyncio.current_task():
                action.operation_task = None

    def cancel_tool(self, generation: str, continuation_id: str) -> None:
        context = self._context_for_call(generation)
        if context is None:
            return
        action = self._actions.get(generation)
        if action is None:
            raise BridgeProtocolError(
                "stale_round", "managed elicitation round is stale"
            )
        pending = action.pending
        if pending is None or pending.continuation_id != continuation_id:
            raise BridgeProtocolError(
                "stale_round", "managed elicitation round is stale"
            )
        action.pending = None
        action.terminal = "failed"
        self._status(context, "failed", rounds=action.rounds, error_code="cancelled")
        task = action.operation_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    def finalize_action(self, generation: str) -> None:
        context = self._context_for_call(generation)
        if context is None:
            return
        action = self._actions.get(generation)
        if action is not None and action.terminal == "failed":
            raise BridgeProtocolError(
                "failed", "the action generation is already terminal"
            )
        if self._channel is not None:
            status = self._channel.read_status()
            if status is not None and status.state == "failed":
                raise BridgeProtocolError(
                    "failed", "the action generation is already terminal"
                )
        try:
            if context.plan is not None:
                matcher = (
                    action.matcher if action is not None else context.plan.matcher()
                )
                if matcher is None:
                    matcher = context.plan.matcher()
                if not isinstance(matcher, PlanMatcher):
                    raise BridgeProtocolError(
                        "channel_protocol", "action matcher is unavailable"
                    )
                matcher.complete()
            self._status(
                context,
                "completed",
                rounds=action.rounds if action else 0,
            )
        except Exception as error:
            if isinstance(error, BridgeProtocolError):
                failure = error
            else:
                failure = BridgeProtocolError(
                    "elicitation_mismatch",
                    "action did not satisfy its elicitation plan",
                )
            # Keep failure terminal even when no tool call created an action
            # state (for example, a required plan was unused).
            action = self._actions.setdefault(generation, _BridgeActionState())
            action.terminal = "failed"
            self._status(
                context,
                "failed",
                rounds=action.rounds,
                error_code=failure.code,
            )
            raise failure from None
        finally:
            if self._actions.get(generation, _BridgeActionState()).terminal != "failed":
                self._actions.pop(generation, None)

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: Any,
        *,
        generation: str | None = None,
    ) -> Any:
        descriptor = self._tools.get(server, {}).get(tool)
        if descriptor is None:
            raise BridgeError("MCP tool is unavailable")
        callback = descriptor.get("call")
        session = self._sessions.get(server)
        context: BridgeActionContext | None = None
        try:
            context = self._context_for_call(generation)
            if not isinstance(arguments, Mapping):
                raise BridgeProtocolError(
                    "arguments", "MCP tool arguments must be a mapping"
                )
            if context is not None:
                self._reject_failed_context(context)
            if callable(callback):
                return (
                    await callback(arguments)
                    if asyncio.iscoroutinefunction(callback)
                    else callback(arguments)
                )
            if session is None:
                raise BridgeError("MCP tool transport is unavailable")
            if context is None:
                result = await session.call_tool(
                    tool,
                    arguments,
                    allow_input_required=True,
                )
                if _input_required(result) is not None:
                    raise BridgeProtocolError(
                        "unexpected_elicitation",
                        "MCP tool requested elicitation without a plan",
                    )
                return self._result(result)
            result = await self._call_tool_mrtr(
                session,
                server,
                tool,
                arguments,
                context,
            )
            return self._result(result)
        except BridgeProtocolError as error:
            if context is not None:
                self._mark_failure_if_current(context, error.code)
            raise
        except ElicitationExpectationError as error:
            if context is not None:
                self._status(context, "failed", error_code="elicitation_mismatch")
                self._mark_failure_if_current(context, "elicitation_mismatch")
            raise BridgeProtocolError("elicitation_mismatch", str(error)) from None
        except UnsupportedFeature as error:
            if context is not None:
                self._status(context, "failed", error_code="unsupported_input")
                self._mark_failure_if_current(context, "unsupported_input")
            raise BridgeProtocolError("unsupported_input", str(error)) from None
        except asyncio.CancelledError:
            if context is not None:
                self._status(context, "failed", error_code="cancelled")
                self._mark_failure_if_current(context, "cancelled")
            raise
        except Exception:
            if context is not None:
                self._status(context, "failed", error_code="mcp_protocol")
                self._mark_failure_if_current(context, "mcp_protocol")
            raise BridgeProtocolError("mcp_protocol", "MCP tool call failed") from None
        finally:
            if context is not None:
                action = self._actions.get(context.generation)
                if (
                    action is not None
                    and action.operation_task is asyncio.current_task()
                ):
                    action.operation_task = None


async def _handle_request(
    bridge: MCPBridge, request: Mapping[str, Any]
) -> dict[str, Any]:
    method = request.get("method")
    if method == "list_tools":
        return {"ok": True, "tools": bridge.list_tools()}
    if method == "call_tool":
        try:
            result = await bridge.call_tool(
                str(request.get("server")),
                str(request.get("tool")),
                request.get("arguments"),
                generation=(
                    request.get("generation")
                    if isinstance(request.get("generation"), str)
                    else None
                ),
            )
            if hasattr(result, "model_dump"):
                result = result.model_dump(mode="json", by_alias=True)
            return {"ok": True, "result": result}
        except BridgeError as error:
            return {
                "ok": False,
                "error": str(error),
                "error_code": getattr(error, "code", "bridge_error"),
            }
    if method == "continue_tool":
        try:
            generation = request.get("generation")
            continuation_id = request.get("continuation_id")
            responses = request.get("responses")
            if not isinstance(generation, str) or not isinstance(continuation_id, str):
                raise BridgeProtocolError(
                    "channel_protocol", "continuation identity is invalid"
                )
            if not isinstance(responses, Mapping):
                raise BridgeProtocolError(
                    "response_keys", "managed responses are invalid"
                )
            result = await bridge.continue_tool(generation, continuation_id, responses)
            if hasattr(result, "model_dump"):
                result = result.model_dump(mode="json", by_alias=True)
            return {"ok": True, "result": result}
        except BridgeError as error:
            return {
                "ok": False,
                "error": str(error),
                "error_code": getattr(error, "code", "bridge_error"),
            }
    if method == "cancel_tool":
        try:
            generation = request.get("generation")
            continuation_id = request.get("continuation_id")
            if not isinstance(generation, str) or not isinstance(continuation_id, str):
                raise BridgeProtocolError(
                    "channel_protocol", "continuation identity is invalid"
                )
            bridge.cancel_tool(generation, continuation_id)
            return {
                "ok": False,
                "error": "managed elicitation cancelled",
                "error_code": "cancelled",
            }
        except BridgeError as error:
            return {
                "ok": False,
                "error": str(error),
                "error_code": getattr(error, "code", "bridge_error"),
            }
    if method == "action_context":
        try:
            context = bridge._context_for_call(None)
            return {
                "ok": True,
                "generation": context.generation if context is not None else None,
                "turn_sequence": context.turn_sequence if context is not None else None,
            }
        except BridgeError as error:
            return {
                "ok": False,
                "error": str(error),
                "error_code": getattr(error, "code", "bridge_error"),
            }
    if method == "finalize_action":
        try:
            generation = request.get("generation")
            if not isinstance(generation, str):
                raise BridgeProtocolError("channel_protocol", "generation is invalid")
            bridge.finalize_action(generation)
            return {"ok": True}
        except BridgeError as error:
            return {
                "ok": False,
                "error": str(error),
                "error_code": getattr(error, "code", "bridge_error"),
            }
    return {"ok": False, "error": "unknown bridge method"}


async def serve(bridge: MCPBridge) -> None:
    """Serve private JSONL frames with concurrent non-eliciting dispatch."""
    output_lock = asyncio.Lock()
    tasks: set[asyncio.Task[None]] = set()

    def reap(task: asyncio.Task[None]) -> None:
        tasks.discard(task)
        if not task.cancelled():
            # Retrieve an unexpected exception before dropping the task.  The
            # request handler normally converts failures into responses, but
            # this also prevents asyncio's "Task exception was never
            # retrieved" warning if cancellation or a future bug escapes it.
            task.exception()

    async def write_response(response: Mapping[str, Any]) -> None:
        async with output_lock:
            print(
                json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                flush=True,
            )

    async def respond(request: Mapping[str, Any]) -> None:
        try:
            response = await _handle_request(bridge, request)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            response = {
                "ok": False,
                "error": "bridge request failed",
                "error_code": getattr(error, "code", "bridge_error"),
            }
        response["id"] = request.get("id")
        await write_response(response)

    while True:
        line = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_FRAME_BYTES + 1)
        if not line:
            break
        if len(line) > MAX_FRAME_BYTES:
            await write_response(
                {
                    "id": None,
                    "ok": False,
                    "error": "bridge frame exceeded safe size",
                }
            )
            continue
        try:
            request = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            await write_response(
                {"id": None, "ok": False, "error": "invalid bridge frame"}
            )
            continue
        if not isinstance(request, Mapping):
            await write_response(
                {"id": None, "ok": False, "error": "invalid bridge frame"}
            )
            continue
        task = asyncio.create_task(respond(request))
        tasks.add(task)
        task.add_done_callback(reap)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def connect_configured_sessions(
    config: Mapping[str, Any], stack: AsyncExitStack
) -> dict[str, Any]:
    """Create official MCP ClientSession connections for configured servers."""
    if not config:
        return {}
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    sessions: dict[str, Any] = {}
    for server, descriptor in config.items():
        transport = descriptor.get("transport", "stdio")
        if transport == "stdio":
            params = StdioServerParameters(
                command=descriptor["command"],
                args=list(descriptor.get("args", ())),
                env=dict(descriptor.get("env", {})) or None,
                cwd=descriptor.get("cwd"),
            )
            read, write = await stack.enter_async_context(stdio_client(params))
        elif transport in {"http", "streamable_http"}:
            import httpx2
            from mcp.client.streamable_http import streamable_http_client

            client = await stack.enter_async_context(
                httpx2.AsyncClient(headers=descriptor.get("headers") or {})
            )
            streams = await stack.enter_async_context(
                streamable_http_client(descriptor["url"], http_client=client)
            )
            if isinstance(streams, tuple):
                read, write = streams[0], streams[1]
            else:
                read, write = streams.read_stream, streams.write_stream
        else:
            raise BridgeError("unsupported MCP transport")
        # The Pi extension has no callback channel for sampling or roots yet.
        # ClientSession's explicit defaults return ErrorData; _mrtr turns that
        # into a typed unsupported-input failure rather than inventing a
        # response.  Do not silently claim these requests are supported.
        session = await stack.enter_async_context(ClientSession(read, write))
        discover = getattr(session, "discover", None)
        if not callable(discover):
            raise BridgeProtocolError(
                "protocol_version", "MCP client discovery is unavailable"
            )
        await discover()
        sessions[str(server)] = session
    return sessions


async def configured_bridge() -> tuple[MCPBridge, AsyncExitStack]:
    stack = AsyncExitStack()
    await stack.__aenter__()
    raw = os.environ.get("M3_MCP_CONFIG", "{}")
    try:
        config = json.loads(raw)
    except json.JSONDecodeError:
        await stack.aclose()
        raise BridgeError("MCP configuration is invalid") from None
    sessions = await connect_configured_sessions(config, stack)
    tools: dict[str, dict[str, Mapping[str, Any]]] = {}
    for server, session in sessions.items():
        result = await session.list_tools()
        tools[server] = {}
        for tool in result.tools:
            descriptor = (
                tool.model_dump(mode="json")
                if hasattr(tool, "model_dump")
                else dict(tool)
            )
            if "input_schema" in descriptor and "inputSchema" not in descriptor:
                descriptor["inputSchema"] = descriptor.pop("input_schema")
            if "output_schema" in descriptor and "outputSchema" not in descriptor:
                descriptor["outputSchema"] = descriptor.pop("output_schema")
            tools[server][str(descriptor.get("name"))] = descriptor
    context_path = os.environ.get("M3_PI_ACTION_CONTEXT")
    status_path = os.environ.get("M3_PI_ACTION_STATUS")
    channel = (
        ActionContextChannel(context_path, status_path)
        if context_path and status_path
        else None
    )
    return MCPBridge(tools, sessions, channel=channel), stack


async def main() -> None:
    bridge, stack = await configured_bridge()
    try:
        await serve(bridge)
    finally:
        await stack.aclose()


if __name__ == "__main__":
    asyncio.run(main())
