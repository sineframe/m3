"""Managed-input bridge for observed Codex MCP elicitation rounds.

This coordinator does not send MCP retries or decide whether Codex may invoke a
tool. It exposes an observed, keyed ``input_required`` round to M3's durable
managed-input runtime and returns the validated responses to the app-server
action that owns the native response and retry observation.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .._mrtr import plain_json
from ..elicitation import (
    ElicitationRequest,
    ElicitationResponse,
    PendingElicitationRound,
)
from ..errors import ElicitationExpectationError
from ._codex_mrtr import _ObservedCall, canonical_json


class CodexManagedMRTRCoordinator:
    """Persist managed rounds and bind them to one observed MCP tool call."""

    _ROUND_MUTABLE_PARAMS = frozenset({"requestState", "inputResponses"})

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        self._operation_identity: tuple[str, str, str, str] | None = None
        self._logical_operation_id: str | None = None
        self._active_round_id: str | None = None

    async def handle_round(
        self,
        call: _ObservedCall,
        requests: Mapping[str, ElicitationRequest],
        request_state: str | None,
        request_state_present: bool,
    ) -> Mapping[str, ElicitationResponse]:
        """Wait for one managed response set for the exact observed call."""

        if not requests:
            raise ElicitationExpectationError(
                "Codex state-only input-required round has no managed responses",
                details={"reason": "empty_managed_round"},
            )
        if self._active_round_id is not None:
            raise ElicitationExpectationError(
                "Codex began a managed round before resolving the previous one",
                details={"reason": "managed_round_overlap"},
            )

        signature = self._call_signature(call)
        if self._operation_identity is None:
            self._operation_identity = signature
            self._logical_operation_id = uuid4().hex
        elif self._operation_identity != signature:
            raise ElicitationExpectationError(
                "Codex managed round belongs to a different logical tool call",
                details={"reason": "managed_operation_mismatch"},
            )

        identity = getattr(self._runtime, "bound_identity", None)
        if (
            not isinstance(identity, tuple)
            or len(identity) != 3
            or not all(isinstance(value, str) and value for value in identity)
        ):
            raise ElicitationExpectationError(
                "Codex managed elicitation identity is unavailable",
                details={"reason": "managed_identity_unavailable"},
            )
        harness_session_id, m3_session_id, m3_turn_id = identity
        execution_id = getattr(self._runtime, "execution_id", None)
        if execution_id is None:
            raise ElicitationExpectationError(
                "Codex managed execution identity is unavailable",
                details={"reason": "managed_identity_unavailable"},
            )
        if call.server_name == "" or call.name == "":
            raise ElicitationExpectationError(
                "Codex managed tool call has no server or operation name",
                details={"reason": "invalid_managed_operation"},
            )

        round_id = uuid4().hex
        pending = PendingElicitationRound(
            round_id=round_id,
            execution_id=str(execution_id),
            logical_operation_id=self._logical_operation_id or uuid4().hex,
            server=call.server_name,
            operation_kind="tool",
            operation_name=call.name,
            request_state=request_state if request_state_present else None,
            requests=requests,
            created_at=datetime.now(timezone.utc),
        )
        operation_parameters = self._operation_parameters(
            call.arguments,
            session_id=m3_session_id or harness_session_id,
            turn_id=m3_turn_id,
        )
        self._active_round_id = round_id
        try:
            responses = await self._runtime.await_round(pending, operation_parameters)
        except BaseException:
            # await_round commits/fails its own durable record on exceptions.
            self._active_round_id = None
            raise
        return dict(responses)

    async def round_completed(self, *, operation_complete: bool) -> None:
        """Resolve the managed record only after Codex's exact retry result."""

        round_id = self._active_round_id
        if round_id is not None:
            try:
                await self._runtime.resolve_round(
                    round_id, operation_complete=operation_complete
                )
            finally:
                self._active_round_id = None
        if operation_complete:
            self._operation_identity = None
            self._logical_operation_id = None

    async def abort(self, error: BaseException) -> None:
        """Fail an unresolved managed round when the Codex action is lost."""

        if self._active_round_id is None:
            return
        try:
            await self._runtime.fail_round(error)
        finally:
            self._active_round_id = None
            self._operation_identity = None
            self._logical_operation_id = None

    @classmethod
    def _call_signature(cls, call: _ObservedCall) -> tuple[str, str, str, str]:
        params = {
            str(key): plain_json(value)
            for key, value in call.params.items()
            if key not in cls._ROUND_MUTABLE_PARAMS
        }
        metadata = params.get("_meta")
        if isinstance(metadata, Mapping) and "progressToken" in metadata:
            # MCP progress tokens identify individual requests. Codex advances
            # this token on each exact retry, while preserving the server,
            # tool arguments, callId, and session metadata for the logical
            # operation.
            params["_meta"] = {
                str(key): value
                for key, value in metadata.items()
                if key != "progressToken"
            }
        return (
            call.connection_id,
            call.server_name,
            call.name,
            canonical_json(params),
        )

    @staticmethod
    def _operation_parameters(
        arguments: object,
        *,
        session_id: str,
        turn_id: str,
    ) -> dict[str, object]:
        if arguments is None:
            values: dict[str, object] = {}
        elif isinstance(arguments, Mapping):
            values = {str(key): plain_json(value) for key, value in arguments.items()}
        else:
            raise ElicitationExpectationError(
                "Codex managed tool arguments are not a JSON object",
                details={"reason": "invalid_managed_operation"},
            )
        values["session_id"] = session_id
        values["turn_id"] = turn_id
        return values


__all__ = ["CodexManagedMRTRCoordinator"]
