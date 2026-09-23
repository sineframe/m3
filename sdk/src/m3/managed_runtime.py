"""Private same-worker managed-input runtime.

The public execution handle owns persistence and validation.  This module is
the deliberately small bridge between that handle and an adapter session:
SQLite remains the source of truth, while an in-process future provides the
prompt wakeup without polling.
"""

from __future__ import annotations

import asyncio
import copy
import json
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Protocol

from .elicitation import ElicitationResponse, PendingElicitationRound
from .errors import (
    ElicitationRoundLimitError,
    ManagedInputConflict,
    ManagedInputRecoveryError,
    ManagedInputStateError,
    MCPError,
    OperationCancelled,
    OperationTimeout,
)
from .execution_trace import ExecutionTraceRecorder
from .storage.managed_input import ManagedInputRecord, ManagedInputStore
from .types import EventKind, ExecutionStatus, LifecyclePhase


class ManagedRuntimeMismatch(MCPError):
    """The adapter returned a round for another execution or operation."""

    terminal = True
    code = "managed_input_identity_mismatch"


class ManagedRuntimeStateError(MCPError):
    """Durable managed-input state cannot be reconciled with the waiter."""

    terminal = True
    code = "managed_input_state"


class ManagedInputRuntime(Protocol):
    """Private adapter-session contract; intentionally not a public API."""

    @property
    def execution_id(self) -> str: ...

    async def await_round(
        self,
        pending: PendingElicitationRound,
        operation_parameters: Mapping[str, object] | None = None,
    ) -> Mapping[str, ElicitationResponse]: ...

    async def resolve_round(
        self, round_id: str, *, operation_complete: bool = False
    ) -> None: ...

    async def fail_round(self, error: BaseException) -> None: ...

    def notify_response(self, round_id: str) -> None: ...

    async def cancel(self, error: BaseException) -> None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _ManagedInputCoordinator:
    """Own one managed execution's active elicitation and lease."""

    _LEASE_SECONDS = 30.0
    _RESPONSE_POLL_SECONDS = 0.05
    _ROUND_MUTABLE_PARAMETER_KEYS = frozenset(
        {"requestState", "request_state", "inputResponses", "input_responses"}
    )

    def __init__(
        self,
        store: ManagedInputStore,
        execution_id: str,
        recorder: ExecutionTraceRecorder,
        *,
        round_limit: int = 10,
    ) -> None:
        self._store = store
        self._execution_id = execution_id
        self._recorder = recorder
        self._round_limit = round_limit
        self._owner_id = f"managed-runtime:{execution_id}"
        self._lock = asyncio.Lock()
        self._active_round: str | None = None
        self._active_record: ManagedInputRecord | None = None
        self._operation_identity: tuple[str, str, str, str, str] | None = None
        self._rounds = 0
        self._renew_task: asyncio.Task[None] | None = None
        self._closed = False
        self._session_identity: str | None = None
        self._m3_session_identity: str | None = None
        self._turn_identity: tuple[str, str] | None = None
        self._waiters: dict[
            str, tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]
        ] = {}
        self._waiter_lock = threading.Lock()

    @property
    def execution_id(self) -> str:
        return self._execution_id

    @property
    def bound_identity(self) -> tuple[str, str, str] | None:
        """Current harness session, M3 session, and turn identities."""

        if self._turn_identity is None:
            return None
        return (
            self._turn_identity[0],
            self._m3_session_identity or self._turn_identity[0],
            self._turn_identity[1],
        )

    def bind_session(self, session_id: str) -> None:
        if not isinstance(session_id, str) or not session_id:
            raise ManagedRuntimeMismatch("managed-input session identity is missing")
        self._session_identity = session_id

    def bind_turn(
        self, session_id: str, turn_id: str, *, m3_session_id: str | None = None
    ) -> None:
        # Turn identity is supplied by the adapter envelope and retained in
        # the durable record.  The method is intentionally private by
        # convention: only the internal adapter hook may call it.
        if self._session_identity != session_id or not turn_id:
            raise ManagedRuntimeMismatch("managed-input turn identity is invalid")
        self._turn_identity = (session_id, turn_id)
        self._m3_session_identity = m3_session_id or session_id

    async def await_round(
        self,
        pending: PendingElicitationRound,
        operation_parameters: Mapping[str, object] | None = None,
    ) -> Mapping[str, ElicitationResponse]:
        if self._closed:
            raise OperationCancelled("managed input coordinator is closed")
        if pending.execution_id != self._execution_id:
            raise ManagedRuntimeMismatch("elicitation execution identity mismatched")
        if self._rounds >= self._round_limit:
            raise ElicitationRoundLimitError("managed elicitation round limit exceeded")
        async with self._lock:
            if self._session_identity is None or self._turn_identity is None:
                raise ManagedRuntimeMismatch("managed-input identity is not bound")
            parameters = _copy_operation_parameters(operation_parameters)
            if parameters.get("session_id") not in {
                None,
                self._session_identity,
                self._m3_session_identity,
            }:
                raise ManagedRuntimeMismatch("elicitation session identity mismatched")
            if parameters.get("turn_id") not in {None, self._turn_identity[1]}:
                raise ManagedRuntimeMismatch("elicitation turn identity mismatched")
            if self._active_round is not None:
                raise ManagedRuntimeMismatch(
                    "more than one eliciting operation is active"
                )
            identity = (
                pending.server,
                pending.operation_kind,
                pending.operation_name,
                pending.logical_operation_id,
                _canonical_operation_parameters(parameters),
            )
            if self._operation_identity is None:
                self._operation_identity = identity
            elif self._operation_identity != identity:
                raise ManagedRuntimeMismatch(
                    "elicitation operation identity mismatched"
                )
            # The future is registered before persistence, closing the
            # response-before-wait race.
            loop = asyncio.get_running_loop()
            waiter: asyncio.Future[None] = loop.create_future()
            with self._waiter_lock:
                if pending.round_id in self._waiters:
                    raise ManagedRuntimeStateError(
                        "managed-input round waiter already exists"
                    )
                self._waiters[pending.round_id] = (loop, waiter)
            self._active_round = pending.round_id
            try:
                record = await asyncio.to_thread(
                    self._store.create_round,
                    pending,
                    round_index=self._rounds,
                    round_limit=self._round_limit,
                    owner_id=self._owner_id,
                    lease_seconds=self._LEASE_SECONDS,
                    harness_session_id=self._session_identity,
                    session_id=self._m3_session_identity or self._turn_identity[0],
                    turn_id=self._turn_identity[1],
                    operation_parameters={
                        **parameters,
                        "logical_operation_id": pending.logical_operation_id,
                        "operation_kind": pending.operation_kind,
                        "operation_name": pending.operation_name,
                    },
                )
                self._active_record = record
                self._renew_task = asyncio.create_task(self._renew(record.round_id))
                await self._emit_request(record)
                await self._transition_waiting()
            except BaseException:
                self._remove_waiter(pending.round_id)
                if self._active_record is not None:
                    await self._fail(
                        ManagedRuntimeStateError("managed-input setup failed")
                    )
                self._operation_identity = None
                self._active_round = None
                self._active_record = None
                await self._stop_renewal()
                raise
        try:
            timeout = None
            if pending.deadline is not None:
                timeout = max(0.0, (pending.deadline - _now()).total_seconds())
            await self._wait_for_response(
                waiter,
                pending.round_id,
                timeout=timeout,
                deadline=pending.deadline,
            )
            loaded = await asyncio.to_thread(
                self._store.get_round, self._execution_id, pending.round_id
            )
            if (
                loaded is None
                or loaded.status != "response_validated"
                or loaded.responses is None
            ):
                raise ManagedRuntimeStateError(
                    "managed-input response commit is missing"
                )
            if (
                pending.deadline is not None
                and loaded.response_validated_at is not None
                and loaded.response_validated_at > pending.deadline
            ):
                raise asyncio.TimeoutError
            record = loaded
            responses = loaded.responses
            redacted = await asyncio.to_thread(
                self._store.redacted_responses, self._execution_id, pending.round_id
            )
            await self._emit_response(record, redacted or {})
            record = await asyncio.to_thread(
                self._store.start_delivery,
                self._execution_id,
                pending.round_id,
                owner_id=record.owner_id,
                lease_token=record.lease_token,
            )
            await self._transition_running()
            return dict(responses)
        except asyncio.TimeoutError as exc:
            timeout_error = OperationTimeout("managed elicitation timed out")
            await self._fail(timeout_error)
            raise timeout_error from exc
        except asyncio.CancelledError:
            cancelled_error = OperationCancelled("managed elicitation cancelled")
            await self._fail(cancelled_error)
            raise
        except BaseException as exc:
            await self._fail(exc)
            raise
        finally:
            self._remove_waiter(pending.round_id)

    async def _wait_for_response(
        self,
        waiter: asyncio.Future[None],
        round_id: str,
        *,
        timeout: float | None,
        deadline: datetime | None,
    ) -> None:
        """Wait for local notification while observing durable response state."""

        loop = asyncio.get_running_loop()
        monotonic_deadline = loop.time() + timeout if timeout is not None else None
        while True:
            interval = self._RESPONSE_POLL_SECONDS
            if monotonic_deadline is not None:
                interval = min(interval, max(0.0, monotonic_deadline - loop.time()))
            try:
                # A process-local notification completes this immediately.
                # Shielding keeps the future registered for durable polling
                # and prevents an interval timeout from cancelling it.
                await asyncio.wait_for(asyncio.shield(waiter), interval)
                return
            except asyncio.TimeoutError:
                loaded = await asyncio.to_thread(
                    self._store.get_round, self._execution_id, round_id
                )
                if loaded is not None and loaded.status == "response_validated":
                    if (
                        deadline is not None
                        and loaded.response_validated_at is not None
                        and loaded.response_validated_at > deadline
                    ):
                        raise asyncio.TimeoutError from None
                    return
                if monotonic_deadline is not None and loop.time() >= monotonic_deadline:
                    raise asyncio.TimeoutError from None

    async def resolve_round(
        self, round_id: str, *, operation_complete: bool = False
    ) -> None:
        record = self._active_record
        if record is None or self._active_round != round_id:
            raise ManagedRuntimeStateError("managed-input round identity mismatched")
        try:
            try:
                record = await asyncio.to_thread(
                    self._store.mark_delivered,
                    self._execution_id,
                    round_id,
                    owner_id=record.owner_id,
                    lease_token=record.lease_token,
                )
                await asyncio.to_thread(
                    self._store.resolve,
                    self._execution_id,
                    round_id,
                    owner_id=record.owner_id,
                    lease_token=record.lease_token,
                )
            except BaseException as exc:
                failure = ManagedRuntimeStateError("managed-input resolution failed")
                await self._fail(failure)
                raise failure from exc
            self._rounds += 1
            if operation_complete:
                self._operation_identity = None
        finally:
            self._active_round = None
            self._active_record = None
            await self._stop_renewal()

    async def fail_round(self, error: BaseException) -> None:
        async with self._lock:
            await self._fail(error)

    def notify_response(self, round_id: str) -> None:
        self._notify(round_id)

    async def cancel(self, error: BaseException) -> None:
        self._closed = True
        if self._active_record is not None:
            await self._fail(error)
        await self._stop_renewal()

    async def _fail(self, error: BaseException) -> None:
        record = self._active_record
        if record is None:
            self._operation_identity = None
            return
        failure = (
            error
            if isinstance(error, MCPError)
            else ManagedRuntimeStateError("managed-input lifecycle failed")
        )
        if isinstance(failure, ManagedInputRecoveryError):
            await self._fail_recovery(record, failure)
            return
        try:
            code = str(getattr(failure, "code", "managed_input_error"))
            message = (
                "managed elicitation cancelled"
                if isinstance(failure, OperationCancelled)
                else "managed elicitation timed out"
                if isinstance(failure, OperationTimeout)
                else "managed-input lifecycle failed"
            )
            await asyncio.to_thread(
                self._store.fail,
                self._execution_id,
                record.round_id,
                owner_id=record.owner_id,
                lease_token=record.lease_token,
                code=code,
                message=message,
            )
        except BaseException as exc:
            failure = ManagedRuntimeStateError("managed-input failure could not commit")
            await self._release_failed_round()
            self._notify_error(record.round_id, failure)
            raise failure from exc
        await self._release_failed_round()
        # External cancellation and adapter failure must complete the
        # execution-loop waiter with the typed cause; a result wakeup
        # would make it incorrectly report a missing response commit.
        self._notify_error(record.round_id, failure)

    async def _release_failed_round(self) -> None:
        self._rounds += 1
        try:
            await self._restore_running_after_failure()
        finally:
            self._active_round = None
            self._active_record = None
            self._operation_identity = None
            await self._stop_renewal()

    async def _renew(self, round_id: str) -> None:
        while True:
            await asyncio.sleep(self._LEASE_SECONDS / 3)
            record = self._active_record
            if record is None or record.round_id != round_id:
                return
            renew = getattr(self._store, "renew", None)
            if not callable(renew):
                return
            try:
                renewed = await asyncio.to_thread(
                    renew,
                    self._execution_id,
                    round_id,
                    owner_id=record.owner_id,
                    lease_token=record.lease_token,
                    lease_seconds=self._LEASE_SECONDS,
                )
                self._active_record = renewed
                if (
                    renewed.status == "failed"
                    and renewed.failure_code == "recovery_unavailable"
                ):
                    await self._fail_recovery(
                        record,
                        ManagedInputRecoveryError(
                            "managed interaction cannot be resumed safely after lease loss"
                        ),
                    )
                    return
            except ManagedInputStateError:
                # Delivery may have advanced the round while this renewal
                # was already in SQLite.  The terminal transition owns
                # shutdown; a stale renewal must not turn it into failure.
                current = await asyncio.to_thread(
                    self._store.get_round, self._execution_id, round_id
                )
                if (
                    current is not None
                    and current.status == "failed"
                    and current.failure_code == "recovery_unavailable"
                ):
                    await self._fail_recovery(
                        record,
                        ManagedInputRecoveryError(
                            "managed interaction cannot be resumed safely after lease loss"
                        ),
                    )
                return
            except ManagedInputConflict:
                error = ManagedInputRecoveryError(
                    "managed interaction cannot be resumed safely after lease loss"
                )
                await self._fail_recovery(record, error)
                return
            except Exception:
                recovery_error = ManagedInputRecoveryError(
                    "managed interaction cannot be resumed safely after lease renewal failure"
                )
                await self._fail_recovery(record, recovery_error)
                return

    async def _fail_recovery(
        self, record: ManagedInputRecord, error: ManagedInputRecoveryError
    ) -> None:
        try:
            await asyncio.to_thread(
                self._store.fail_recovery,
                self._execution_id,
                record.round_id,
                expected_lease_token=record.lease_token,
                message=str(error),
            )
        except (ManagedInputConflict, ManagedInputStateError):
            current = await asyncio.to_thread(
                self._store.get_round, self._execution_id, record.round_id
            )
            if current is None or current.status != "failed":
                # A renewal failure can be reported before the lease's
                # deadline even though ownership is no longer trustworthy.
                # ``fail_recovery`` intentionally rejects a still-live lease,
                # so use the current owner token to record the same terminal
                # boundary immediately.  If the lease expires while this
                # fallback races, the original recovery error remains typed.
                try:
                    await asyncio.to_thread(
                        self._store.fail,
                        self._execution_id,
                        record.round_id,
                        owner_id=record.owner_id,
                        lease_token=record.lease_token,
                        code="recovery_unavailable",
                        message=str(error),
                    )
                except Exception:
                    # The fallback can lose the lease between its compare-
                    # and-set check and this retry.  Reuse the original token
                    # once more so an expired round still reaches the typed
                    # recovery terminal state.
                    try:
                        await asyncio.to_thread(
                            self._store.fail_recovery,
                            self._execution_id,
                            record.round_id,
                            expected_lease_token=record.lease_token,
                            message=str(error),
                        )
                    except Exception:
                        current = await asyncio.to_thread(
                            self._store.get_round,
                            self._execution_id,
                            record.round_id,
                        )
                        if current is None or current.status != "failed":
                            error = ManagedInputRecoveryError(
                                "managed input recovery could not be committed"
                            )
        finally:
            await self._release_failed_round()
            self._notify_error(record.round_id, error)

    def _remove_waiter(self, round_id: str) -> None:
        with self._waiter_lock:
            self._waiters.pop(round_id, None)

    def _notify(self, round_id: str) -> None:
        with self._waiter_lock:
            waiter = self._waiters.get(round_id)
        if waiter is None:
            return
        loop, future = waiter

        def wake() -> None:
            if not future.done():
                future.set_result(None)

        if not loop.is_closed():
            loop.call_soon_threadsafe(wake)

    def _notify_error(self, round_id: str, error: BaseException) -> None:
        with self._waiter_lock:
            waiter = self._waiters.get(round_id)
        if waiter is None:
            return
        loop, future = waiter

        def fail() -> None:
            if not future.done():
                future.set_exception(error)

        if not loop.is_closed():
            loop.call_soon_threadsafe(fail)

    async def _stop_renewal(self) -> None:
        task, self._renew_task = self._renew_task, None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _emit_request(self, record: ManagedInputRecord) -> None:
        await asyncio.to_thread(
            self._recorder.emit,
            EventKind.ELICITATION_REQUEST,
            payload={
                "round_id": record.round_id,
                "round_index": record.round_index,
                "server": record.pending.server,
                "operation_kind": record.pending.operation_kind,
                "operation_name": record.pending.operation_name,
                "logical_operation_id": record.pending.logical_operation_id,
                "requests": {
                    key: request.model_dump(mode="json")
                    for key, request in record.pending.requests.items()
                },
            },
            lifecycle_phase=LifecyclePhase.MCP_CALL,
        )

    async def _emit_response(
        self, record: ManagedInputRecord, responses: Mapping[str, object]
    ) -> None:
        await asyncio.to_thread(
            self._recorder.emit,
            EventKind.ELICITATION_RESPONSE,
            payload={
                "round_id": record.round_id,
                "round_index": record.round_index,
                "responses": dict(responses),
            },
            lifecycle_phase=LifecyclePhase.MCP_CALL,
        )

    async def _transition_running(self) -> None:
        snapshot = await asyncio.to_thread(self._recorder.snapshot)
        if snapshot.lifecycle is not ExecutionStatus.WAITING_FOR_INPUT:
            raise ManagedRuntimeStateError(
                "managed input response requires a waiting execution"
            )
        await asyncio.to_thread(
            self._recorder.emit,
            EventKind.EXECUTION_STATE_CHANGED,
            payload={"lifecycle": ExecutionStatus.RUNNING_TURN.value},
            lifecycle_phase=LifecyclePhase.TURN,
        )

    async def _transition_waiting(self) -> None:
        snapshot = await asyncio.to_thread(self._recorder.snapshot)
        if snapshot.lifecycle is not ExecutionStatus.RUNNING_TURN:
            raise ManagedRuntimeStateError(
                "managed input requires an active running turn"
            )
        await asyncio.to_thread(
            self._recorder.emit,
            EventKind.EXECUTION_STATE_CHANGED,
            payload={"lifecycle": ExecutionStatus.WAITING_FOR_INPUT.value},
            lifecycle_phase=LifecyclePhase.TURN,
        )

    async def _restore_running_after_failure(self) -> None:
        snapshot = await asyncio.to_thread(self._recorder.snapshot)
        if snapshot.lifecycle is ExecutionStatus.WAITING_FOR_INPUT:
            await asyncio.to_thread(
                self._recorder.emit,
                EventKind.EXECUTION_STATE_CHANGED,
                payload={"lifecycle": ExecutionStatus.RUNNING_TURN.value},
                lifecycle_phase=LifecyclePhase.TURN,
            )


def _copy_operation_parameters(
    operation_parameters: Mapping[str, object] | None,
) -> dict[str, object]:
    if operation_parameters is None:
        return {}
    try:
        copied = copy.deepcopy(dict(operation_parameters))
        return {
            key: value
            for key, value in copied.items()
            if key not in _ManagedInputCoordinator._ROUND_MUTABLE_PARAMETER_KEYS
        }
    except (TypeError, ValueError) as exc:
        raise ManagedRuntimeMismatch(
            "elicitation operation parameters are not valid"
        ) from exc


def _canonical_operation_parameters(parameters: Mapping[str, object]) -> str:
    try:
        return json.dumps(
            parameters,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ManagedRuntimeMismatch(
            "elicitation operation parameters are not JSON-serializable"
        ) from exc


__all__ = ["ManagedRuntimeMismatch", "ManagedRuntimeStateError"]
