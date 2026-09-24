"""Exact association of captured MCP elicitation entries with Codex prompts.

Codex's App Server request does not retain the MCP input-request key.  This
module only associates requests when the standard fields exposed by both
protocols identify one response, or when every indistinguishable keyed
request has the same response.  It deliberately has no ordering or timing
fallback.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .._mrtr import input_required, normalize_requests, plain_json, validate_response
from ..elicitation import (
    ElicitationRequest,
    ElicitationResponse,
    FormElicitationRequest,
    UrlElicitationRequest,
)
from ..errors import ElicitationExpectationError

_FORM_FIELDS = ("mode", "message", "requestedSchema")
_URL_FIELDS = ("mode", "message", "url")
_OPTIONAL_EXPOSED_FIELDS = ("_meta", "task", "elicitationId")
_CODEX_MAX_MRTR_ROUNDS = 9


def canonical_json(value: Any) -> str:
    """Return a stable JSON spelling while preserving JSON value types."""

    try:
        return json.dumps(
            plain_json(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError):
        raise ElicitationExpectationError(
            "Codex elicitation prompt contains a non-JSON value",
            details={"reason": "unsupported_native_representation"},
        ) from None


def _request_fields(request: ElicitationRequest) -> dict[str, Any]:
    if isinstance(request, FormElicitationRequest):
        fields: dict[str, Any] = {
            "mode": "form",
            "message": request.message,
            "requestedSchema": plain_json(request.requested_schema),
        }
    elif isinstance(request, UrlElicitationRequest):
        fields = {"mode": "url", "message": request.message, "url": request.url}
        if request.elicitation_id is not None:
            fields["elicitationId"] = request.elicitation_id
    else:  # pragma: no cover - the public request union is exhaustive
        raise ElicitationExpectationError(
            "MCP elicitation request has an unsupported type",
            details={"reason": "unsupported_native_representation"},
        )
    # Codex 0.156.1's flattened app-server type serializes the absent MCP
    # `_meta` field as null. MCP gives absent metadata the same meaning.
    fields["_meta"] = plain_json(request.meta)
    if request.task is not None:
        fields["task"] = plain_json(request.task)
    return fields


def _prompt_key(
    params: Mapping[str, Any], *, server_name: str
) -> tuple[str, tuple[str, ...], str] | None:
    """Describe exactly the typed request fields exposed by app-server."""

    mode = params.get("mode")
    if mode == "form":
        required_fields = _FORM_FIELDS
    elif mode == "url":
        required_fields = _URL_FIELDS
    else:
        return None
    if any(field not in params for field in required_fields):
        return None
    field_names = list(required_fields)
    for field in _OPTIONAL_EXPOSED_FIELDS:
        if field in params:
            field_names.append(field)
    fields = {field: params[field] for field in field_names}
    return server_name, tuple(field_names), canonical_json(fields)


def _response_key(response: ElicitationResponse) -> str:
    return canonical_json(
        response.model_dump(mode="json", by_alias=True, exclude_none=True)
    )


class CodexPromptAssociator:
    """Match one complete keyed MCP round to Codex's individual prompts."""

    def __init__(
        self,
        requests: Mapping[str, ElicitationRequest],
        responses: Mapping[str, ElicitationResponse],
    ) -> None:
        if set(requests) != set(responses):
            raise ElicitationExpectationError(
                "Codex elicitation response keys do not match the observed round",
                details={"reason": "round_response_key_mismatch"},
            )
        self._requests: dict[str, tuple[str, dict[str, Any]]] = {}
        self._responses: dict[str, ElicitationResponse] = {}
        for key, request in requests.items():
            if not request.server:
                raise ElicitationExpectationError(
                    "observed MCP elicitation has no server binding",
                    details={"reason": "missing_server_identity"},
                )
            self._requests[key] = (request.server, _request_fields(request))
            self._responses[key] = responses[key]
        self._prompt_candidates: list[frozenset[str]] = []

    def response_for(
        self,
        params: Mapping[str, Any],
        *,
        server_name: str,
    ) -> ElicitationResponse:
        """Return the uniquely safe answer for one native app-server prompt."""

        prompt = _prompt_key(params, server_name=server_name)
        if prompt is None:
            raise ElicitationExpectationError(
                "Codex elicitation prompt has an unsupported representation",
                details={"reason": "unsupported_native_representation"},
            )
        prompt_server, field_names, canonical_prompt = prompt
        prompt_content = json.loads(canonical_prompt)
        candidates = frozenset(
            key
            for key, (request_server, request_fields) in self._requests.items()
            if request_server == prompt_server
            and all(
                field in request_fields
                and canonical_json(request_fields[field])
                == canonical_json(prompt_content[field])
                for field in field_names
            )
        )
        if not candidates:
            raise ElicitationExpectationError(
                "Codex elicitation prompt does not match the observed MCP round",
                details={"reason": "unmatched_native_prompt"},
            )
        candidate_responses = {
            _response_key(self._responses[key]) for key in candidates
        }
        if len(candidate_responses) != 1:
            raise ElicitationExpectationError(
                "Codex cannot distinguish identical prompts with different planned answers",
                details={"reason": "ambiguous_native_prompt"},
            )
        self._prompt_candidates.append(candidates)
        return self._responses[next(iter(sorted(candidates)))]

    def complete(self) -> None:
        """Require one native prompt for every observed keyed elicitation."""

        if len(self._prompt_candidates) != len(self._requests):
            raise ElicitationExpectationError(
                "Codex did not request every elicitation in the observed MCP round",
                details={"reason": "missing_native_prompt"},
            )
        # Check the complete prompt multiset as a bipartite matching problem.
        # No key is chosen from native arrival order; the returned answers were
        # already proven equal across every candidate key for each prompt.
        assigned_request_to_prompt: dict[str, int] = {}

        def assign(prompt_index: int, visited: set[str]) -> bool:
            for request_key in self._prompt_candidates[prompt_index]:
                if request_key in visited:
                    continue
                visited.add(request_key)
                previous = assigned_request_to_prompt.get(request_key)
                if previous is None or assign(previous, visited):
                    assigned_request_to_prompt[request_key] = prompt_index
                    return True
            return False

        if not all(
            assign(prompt_index, set())
            for prompt_index in range(len(self._prompt_candidates))
        ):
            raise ElicitationExpectationError(
                "Codex native prompts do not cover the observed elicitation round",
                details={"reason": "duplicate_or_missing_native_prompt"},
            )

    @property
    def expected_prompt_count(self) -> int:
        return len(self._requests)


@dataclass(frozen=True, slots=True)
class _ObservedCall:
    connection_id: str
    server_name: str
    name: str
    arguments: Any
    params: Mapping[str, Any]


@dataclass(slots=True)
class _ObservedRound:
    call: _ObservedCall
    request_state: str | None
    request_state_present: bool
    requests: Mapping[str, ElicitationRequest]
    responses: Mapping[str, ElicitationResponse] | None
    associator: CodexPromptAssociator | None
    native_prompts: list[tuple[str | int, Mapping[str, Any]]]
    sent: bool = False
    continuation_confirmed: bool = False


class CodexMRTRAction:
    """Observe one action's MCP exchanges and answer matching native prompts.

    Codex App Server does not expose MCP request keys or round ids.  This
    object therefore requires a complete, content-associated native prompt
    group before sending any answer, then verifies Codex's exact keyed retry
    on the MCP wire before considering delivery complete.
    """

    def __init__(
        self,
        *,
        launch: Any,
        plan: Any,
        round_limit: int,
        thread_id: Any,
        turn_id: Any,
        write_native_response: Any,
        managed_round_handler: Any = None,
        managed_round_completed: Any = None,
        managed_round_abort: Any = None,
        protocol_error_for_server: Any = None,
    ) -> None:
        self._launch = launch
        self._plan = plan
        self._round_limit = round_limit
        self._thread_id = thread_id
        self._turn_id = turn_id
        self._write_native_response = write_native_response
        self._managed_round_handler = managed_round_handler
        self._managed_round_completed = managed_round_completed
        self._managed_round_abort = managed_round_abort
        self._protocol_error_for_server = protocol_error_for_server
        self._managed_round_active = False
        self._matcher = plan.matcher() if plan is not None else None
        self._subscription: Any = None
        self._observer_task: asyncio.Task[None] | None = None
        self._coordinator_task: asyncio.Task[None] | None = None
        self._changed = asyncio.Event()
        self._observation_changed = asyncio.Event()
        self._coordinate_lock = asyncio.Lock()
        self._closed = False
        self._failure: BaseException | None = None
        self._requires_turn_interrupt = True
        self._pending_native_rejection: ElicitationExpectationError | None = None
        self._native_rejection_item_seen = False
        self.failure_event = asyncio.Event()
        self._calls: dict[tuple[str, type[Any], Any], _ObservedCall] = {}
        self._configs = {
            str(config.connection_id): config
            for config in launch.configurations
            if config.available
        }
        self._operation: _ObservedCall | None = None
        self._rounds = 0
        self._native_tool_items: Counter[str] = Counter()
        self._active_native_tool_items: dict[str, str] = {}
        self._terminal_operations: Counter[str] = Counter()
        self._current_round: _ObservedRound | None = None
        self._awaiting_retry: _ObservedRound | None = None
        self._retry_in_flight: _ObservedRound | None = None
        self._native_prompts: list[tuple[str | int, Mapping[str, Any]]] = []
        self._retry_confirmed = asyncio.Event()

    @property
    def failure(self) -> BaseException | None:
        return self._failure

    @property
    def requires_turn_interrupt(self) -> bool:
        return self._requires_turn_interrupt

    def fail(self, error: BaseException, *, interrupt_turn: bool = True) -> None:
        if self._failure is None:
            self._failure = error
            self._requires_turn_interrupt = interrupt_turn
            self.failure_event.set()
            self._changed.set()

    async def _complete_managed_retry(self, *, operation_complete: bool) -> None:
        retry = self._retry_in_flight
        if retry is None:
            return
        if self._managed_round_active and self._managed_round_completed is not None:
            await self._managed_round_completed(operation_complete=operation_complete)
        self._managed_round_active = False
        self._retry_in_flight = None
        if (
            operation_complete
            and self._operation is not None
            and self._same_operation(self._operation, retry.call)
        ):
            self._operation = None

    async def start(self) -> None:
        manager = getattr(self._launch, "capture", None)
        subscribe = getattr(manager, "subscribe", None)
        if (
            self._plan is not None or self._managed_round_handler is not None
        ) and not callable(subscribe):
            raise ElicitationExpectationError(
                "Codex elicitation requires live MCP observation",
                details={"reason": "live_observation_unavailable"},
            )
        if callable(subscribe):
            self._subscription = subscribe(self._configs.keys(), maxsize=256)
            self._observer_task = asyncio.create_task(self._observe())
        self._coordinator_task = asyncio.create_task(self._coordinate())

    def set_turn_identity(self) -> None:
        """Wake deferred prompt matching after turn/start supplies its id."""

        self._changed.set()

    def submit_native_prompt(self, frame: Mapping[str, Any]) -> None:
        if self._closed or self._failure is not None:
            return
        if self._plan is None and self._managed_round_handler is None:
            self.fail(
                ElicitationExpectationError(
                    "Codex requested elicitation without an action-bound plan",
                    details={"reason": "unexpected_elicitation"},
                )
            )
            return
        request_id = frame.get("id")
        if isinstance(request_id, bool) or not isinstance(request_id, (int, str)):
            self.fail(
                ElicitationExpectationError(
                    "Codex elicitation identity is invalid",
                    details={"reason": "invalid_native_identity"},
                )
            )
            return
        params = frame.get("params")
        if not isinstance(params, Mapping):
            self.fail(
                ElicitationExpectationError(
                    "Codex elicitation prompt has no typed parameters",
                    details={"reason": "unsupported_native_representation"},
                )
            )
            return
        if self._awaiting_retry is not None:
            self.fail(
                ElicitationExpectationError(
                    "Codex emitted another native elicitation before the previous keyed retry",
                    details={"reason": "native_prompt_before_retry"},
                )
            )
            return
        self._native_prompts.append((request_id, params))
        self._changed.set()

    def observe_native_tool_item(self, item: Mapping[str, Any]) -> None:
        """Record raw app-server item identity before trace redaction."""

        if self._plan is None and self._managed_round_handler is None:
            return
        try:
            item_key = self._native_item_key(item)
        except ElicitationExpectationError as error:
            self.fail(error)
            return
        item_id = item.get("id")
        if isinstance(item_id, str):
            self._active_native_tool_items.pop(item_id, None)
        self._native_tool_items[item_key] += 1
        status = item.get("status")
        if (
            self._operation is not None
            and item_key == self._operation_key(self._operation)
            and (status == "failed" or item.get("error") is not None)
        ):
            if self._pending_native_rejection is not None:
                self._native_rejection_item_seen = True
                failure = self._pending_native_rejection
            else:
                failure = ElicitationExpectationError(
                    "Codex marked the observed MCP tool operation as failed",
                    details={"reason": "native_tool_item_failed"},
                )
            self.fail(failure, interrupt_turn=False)
        self._observation_changed.set()

    def observe_native_tool_start(self, item: Mapping[str, Any]) -> None:
        """Track same-server calls that can originate an indistinguishable prompt."""

        if self._plan is None and self._managed_round_handler is None:
            return
        item_id = item.get("id")
        server = item.get("server")
        if not isinstance(item_id, str) or not isinstance(server, str):
            self.fail(
                ElicitationExpectationError(
                    "Codex native tool item has insufficient identity",
                    details={"reason": "native_tool_item_unbound"},
                )
            )
            return
        self._active_native_tool_items[item_id] = server
        self._changed.set()

    async def _observe(self) -> None:
        try:
            async for event in self._subscription:
                try:
                    payload = event.payload
                    if isinstance(payload, list):
                        for envelope in payload:
                            if not isinstance(envelope, Mapping):
                                raise ElicitationExpectationError(
                                    "MCP observer returned a malformed batch envelope",
                                    details={"reason": "invalid_observation"},
                                )
                            await self._consume_event(
                                event.connection_id, event.direction, envelope
                            )
                    elif isinstance(payload, Mapping):
                        await self._consume_event(
                            event.connection_id, event.direction, payload
                        )
                    else:
                        raise ElicitationExpectationError(
                            "MCP observer returned a malformed envelope",
                            details={"reason": "invalid_observation"},
                        )
                finally:
                    await self._subscription.acknowledge(event)
                    self._observation_changed.set()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            if not self._closed:
                self.fail(error)
        else:
            if not self._closed:
                self.fail(
                    ElicitationExpectationError(
                        "MCP observation ended during a Codex action",
                        details={"reason": "observation_ended"},
                    )
                )

    async def _consume_event(
        self, connection_id: str, direction: str, envelope: Mapping[str, Any]
    ) -> None:
        if self._failure is not None:
            return
        request_id = envelope.get("id")
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            return
        identity = (connection_id, type(request_id), request_id)
        if direction == "client_to_server":
            if envelope.get("method") != "tools/call":
                return
            params = envelope.get("params")
            if not isinstance(params, Mapping):
                raise ElicitationExpectationError(
                    "observed MCP tool call has malformed parameters",
                    details={"reason": "invalid_mcp_call"},
                )
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise ElicitationExpectationError(
                    "observed MCP tool call has no tool name",
                    details={"reason": "invalid_mcp_call"},
                )
            config = self._configs.get(connection_id)
            if config is None:
                raise ElicitationExpectationError(
                    "observed MCP connection is not bound to Codex configuration",
                    details={"reason": "unknown_mcp_connection"},
                )
            arguments = params.get("arguments", {})
            call = _ObservedCall(
                connection_id, config.key, name, arguments, dict(params)
            )
            self._calls[identity] = call
            if self._awaiting_retry is not None and self._same_operation(
                call, self._awaiting_retry.call
            ):
                if not self._is_expected_retry(params, self._awaiting_retry):
                    raise ElicitationExpectationError(
                        "Codex continuation did not carry the observed request state and keyed answers",
                        details={"reason": "continuation_mismatch"},
                    )
                self._awaiting_retry.continuation_confirmed = True
                if self._current_round is self._awaiting_retry:
                    self._current_round = None
                self._retry_in_flight = self._awaiting_retry
                self._awaiting_retry = None
                self._retry_confirmed.set()
                self._changed.set()
            return
        if direction != "server_to_client":
            return
        response_call = self._calls.get(identity)
        if response_call is None:
            return
        del self._calls[identity]
        if "error" in envelope:
            self._terminal_operations[self._operation_key(response_call)] += 1
            self._observation_changed.set()
            if self._retry_in_flight is not None and self._same_operation(
                response_call, self._retry_in_flight.call
            ):
                await self._complete_managed_retry(operation_complete=True)
            return
        if "result" not in envelope:
            return
        required = input_required(envelope.get("result"))
        if required is None:
            self._terminal_operations[self._operation_key(response_call)] += 1
            self._observation_changed.set()
            if self._retry_in_flight is not None and self._same_operation(
                response_call, self._retry_in_flight.call
            ):
                await self._complete_managed_retry(operation_complete=True)
            if self._operation is not None and self._same_operation(
                response_call, self._operation
            ):
                # A complete response to the exact continuation closes this
                # logical tool operation.  A native resolved notification is
                # not used as delivery evidence.
                if self._awaiting_retry is not None and self._same_operation(
                    response_call, self._awaiting_retry.call
                ):
                    raise ElicitationExpectationError(
                        "Codex completed an MCP operation without a verified continuation",
                        details={"reason": "continuation_missing"},
                    )
                self._operation = None
            return
        if self._protocol_error_for_server is not None:
            protocol_error = self._protocol_error_for_server(response_call.server_name)
            if protocol_error is not None:
                raise protocol_error
        if required.input_requests is None:
            raise ElicitationExpectationError(
                "Codex returned input-required without an input request map",
                details={"reason": "invalid_input_required"},
            )
        config = self._configs.get(connection_id)
        if config is None:
            raise ElicitationExpectationError(
                "observed MCP connection is not bound to Codex configuration",
                details={"reason": "unknown_mcp_connection"},
            )
        elicitation, other = normalize_requests(
            required,
            session=None,
            operation_kind="tool",
            operation_name=response_call.name,
            server_name=config.key,
        )
        if other:
            raise ElicitationExpectationError(
                "Codex App Server cannot answer non-elicitation MCP input requests",
                details={"reason": "unsupported_input_request"},
            )
        if self._operation is not None and not self._same_operation(
            response_call, self._operation
        ):
            raise ElicitationExpectationError(
                "more than one MCP operation requested elicitation during this action",
                details={"reason": "concurrent_elicitation"},
            )
        if self._retry_in_flight is not None and self._same_operation(
            response_call, self._retry_in_flight.call
        ):
            await self._complete_managed_retry(operation_complete=False)
        if self._current_round is not None or self._awaiting_retry is not None:
            raise ElicitationExpectationError(
                "Codex began another elicitation round before retrying the previous one",
                details={"reason": "round_overlap"},
            )
        if self._operation is None:
            self._operation = response_call
        self._rounds += 1
        # On the characterized Codex 0.156.1 boundary, Codex itself rejects
        # inputRequired round ten and reports a failed native MCP tool item
        # without a native prompt. Preserve that harness outcome when the
        # caller allows ten or more rounds; a lower M3 limit still interrupts
        # immediately and does not wait for the harness cap.
        if (
            self._rounds == _CODEX_MAX_MRTR_ROUNDS + 1
            and self._round_limit >= self._rounds
        ):
            self._pending_native_rejection = ElicitationExpectationError(
                "Codex rejected the tenth MRTR round at its native round limit",
                details={"reason": "round_limit_exceeded"},
            )
            self._observation_changed.set()
            return
        if self._rounds > self._round_limit:
            raise ElicitationExpectationError(
                "Codex elicitation exceeded the configured round limit",
                details={"reason": "round_limit_exceeded"},
            )
        if not elicitation:
            if self._native_prompts:
                raise ElicitationExpectationError(
                    "Codex emitted a native prompt for a state-only input-required round",
                    details={"reason": "unexpected_native_prompt"},
                )
            # Codex 0.156.1 retries state-only inputRequired results itself,
            # without a native elicitation request or inputResponses field.
            # Keep its continuation subject to the same exact wire check and
            # round limit while letting Codex retain retry ownership.
            current = _ObservedRound(
                call=response_call,
                request_state=required.request_state,
                request_state_present=(
                    isinstance(envelope.get("result"), Mapping)
                    and "requestState" in envelope["result"]
                ),
                requests={},
                responses={},
                associator=CodexPromptAssociator({}, {}),
                native_prompts=[],
                sent=True,
            )
            self._current_round = None
            self._retry_confirmed.clear()
            self._awaiting_retry = current
            self._changed.set()
            return
        if (
            self._plan is None or self._matcher is None
        ) and self._managed_round_handler is None:
            raise ElicitationExpectationError(
                "MCP tool requested elicitation without an action-bound plan",
                details={"reason": "unexpected_elicitation"},
            )
        responses: Mapping[str, ElicitationResponse] | None = None
        associator: CodexPromptAssociator | None = None
        if self._matcher is not None:
            responses = self._matcher.match_round(elicitation)
            for key, response in responses.items():
                validate_response(elicitation[key], response)
            associator = CodexPromptAssociator(elicitation, responses)
        self._current_round = _ObservedRound(
            call=response_call,
            request_state=required.request_state,
            request_state_present=(
                isinstance(envelope.get("result"), Mapping)
                and "requestState" in envelope["result"]
            ),
            requests=elicitation,
            responses=responses,
            associator=associator,
            native_prompts=[],
        )
        self._changed.set()

    async def _coordinate(self) -> None:
        try:
            while not self._closed and self._failure is None:
                await self._changed.wait()
                self._changed.clear()
                async with self._coordinate_lock:
                    await self._try_answer_round()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self.fail(error)

    async def _try_answer_round(self) -> None:
        current = self._current_round
        if self._failure is not None or current is None or current.sent:
            return
        if current.responses is None:
            if self._managed_round_handler is None:
                raise ElicitationExpectationError(
                    "Codex managed elicitation handler is unavailable",
                    details={"reason": "managed_handler_unavailable"},
                )
            self._managed_round_active = True
            responses = await self._managed_round_handler(
                current.call,
                current.requests,
                current.request_state,
                current.request_state_present,
            )
            if self._closed or self._failure is not None:
                return
            if set(responses) != set(current.requests):
                raise ElicitationExpectationError(
                    "Codex managed responses do not match the observed round keys",
                    details={"reason": "round_response_key_mismatch"},
                )
            for key, response in responses.items():
                validate_response(current.requests[key], response)
            current.responses = dict(responses)
            current.associator = CodexPromptAssociator(
                current.requests, current.responses
            )
        turn_id = self._turn_id()
        if turn_id is None:
            return
        associator = current.associator
        if associator is None:
            raise ElicitationExpectationError(
                "Codex elicitation response association is unavailable",
                details={"reason": "missing_response_associator"},
            )
        expected = associator.expected_prompt_count
        if len(self._native_prompts) < expected:
            return
        if len(self._native_prompts) != expected:
            raise ElicitationExpectationError(
                "Codex emitted an unexpected number of native prompts for one input-required round",
                details={"reason": "native_prompt_count_mismatch"},
            )
        # Validate every prompt and response before writing the first answer.
        answers: list[tuple[str | int, Mapping[str, Any]]] = []
        for request_id, params in self._native_prompts:
            if params.get("threadId") != self._thread_id():
                raise ElicitationExpectationError(
                    "Codex elicitation belongs to a different thread",
                    details={"reason": "native_thread_mismatch"},
                )
            prompt_turn_id = params.get("turnId")
            if prompt_turn_id is not None and prompt_turn_id != turn_id:
                raise ElicitationExpectationError(
                    "Codex elicitation belongs to a different turn",
                    details={"reason": "native_turn_mismatch"},
                )
            server_name = params.get("serverName")
            if not isinstance(server_name, str):
                raise ElicitationExpectationError(
                    "Codex elicitation has no server identity",
                    details={"reason": "missing_server_identity"},
                )
            response = associator.response_for(params, server_name=server_name)
            answers.append((request_id, self._native_result(response)))
        associator.complete()
        if (
            any(
                call.server_name == current.call.server_name
                for call in self._calls.values()
            )
            or sum(
                server == current.call.server_name
                for server in self._active_native_tool_items.values()
            )
            > 1
        ):
            raise ElicitationExpectationError(
                "more than one same-server MCP operation can own the native prompt",
                details={"reason": "concurrent_elicitation"},
            )
        self._retry_confirmed.clear()
        current.sent = True
        self._awaiting_retry = current
        for request_id, result in answers:
            if self._failure is not None or self._closed:
                return
            await self._write_native_response(request_id, result)
        self._native_prompts.clear()
        current.native_prompts = []

    @staticmethod
    def _native_result(response: ElicitationResponse) -> Mapping[str, Any]:
        result: dict[str, Any] = {"action": response.action}
        if response.content is not None:
            result["content"] = plain_json(response.content)
        else:
            result["content"] = None
        if response.meta is not None:
            result["_meta"] = plain_json(response.meta)
        return result

    @staticmethod
    def _same_operation(left: _ObservedCall, right: _ObservedCall) -> bool:
        return (
            left.connection_id == right.connection_id
            and left.server_name == right.server_name
            and canonical_json(_stable_operation_params(left.params))
            == canonical_json(_stable_operation_params(right.params))
        )

    @staticmethod
    def _operation_key(call: _ObservedCall) -> str:
        return canonical_json(
            {
                "server": call.server_name,
                "tool": call.name,
                "arguments": call.params.get("arguments", {}),
            }
        )

    def _native_operations_complete(self) -> bool:
        if self._native_tool_items is None:
            return True
        return all(
            self._terminal_operations[key] >= count
            for key, count in self._native_tool_items.items()
        )

    @staticmethod
    def _wire_response(
        request: ElicitationRequest, response: ElicitationResponse
    ) -> Mapping[str, Any]:
        result: dict[str, Any] = {"action": response.action}
        if response.content is not None:
            result["content"] = plain_json(response.content)
        # Verified Codex 0.156.1 transformation: URL accept arrives from the
        # App Server as content:null and is retried on the MCP wire as {}.
        if isinstance(request, UrlElicitationRequest) and response.action == "accept":
            result["content"] = {}
        if response.meta is not None:
            result["_meta"] = plain_json(response.meta)
        return result

    @classmethod
    def _expected_responses(cls, current: _ObservedRound) -> Mapping[str, Any]:
        if current.responses is None:
            raise ElicitationExpectationError(
                "Codex managed responses are not available for retry verification",
                details={"reason": "missing_response_associator"},
            )
        return {
            key: cls._wire_response(current.requests[key], response)
            for key, response in current.responses.items()
        }

    @classmethod
    def _is_expected_retry(
        cls, params: Mapping[str, Any], current: _ObservedRound
    ) -> bool:
        if current.requests:
            if not isinstance(params.get("inputResponses"), Mapping):
                return False
            if canonical_json(params["inputResponses"]) != canonical_json(
                cls._expected_responses(current)
            ):
                return False
        elif "inputResponses" in params:
            return False
        if current.request_state_present != ("requestState" in params):
            return False
        return params.get("requestState") == current.request_state

    async def finish(
        self,
        *,
        native_tool_items: list[Mapping[str, Any]]
        | tuple[Mapping[str, Any], ...]
        | None = None,
    ) -> None:
        if self._failure is not None:
            return
        if native_tool_items is not None:
            try:
                self._native_tool_items = Counter(
                    self._native_item_key(item) for item in native_tool_items
                )
            except ElicitationExpectationError as error:
                self.fail(error)
                return
        deadline = asyncio.get_running_loop().time() + 5.0
        while self._failure is None:
            self._observation_changed.clear()
            if self._subscription is not None:
                barrier = getattr(self._subscription, "barrier", None)
                if callable(barrier):
                    try:
                        await barrier()
                    except BaseException as error:
                        self.fail(error)
                        return
            if self._failure is not None:
                return
            if self._pending_native_rejection is not None:
                if self._native_rejection_item_seen:
                    self.fail(self._pending_native_rejection, interrupt_turn=False)
                    return
                wait_for = self._observation_changed
                reason = "native_rejection_item_missing"
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    self.fail(self._pending_native_rejection, interrupt_turn=False)
                    return
                try:
                    await asyncio.wait_for(wait_for.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    self.fail(self._pending_native_rejection, interrupt_turn=False)
                    return
                continue
            try:
                async with self._coordinate_lock:
                    await self._try_answer_round()
            except BaseException as error:
                self.fail(error)
                return
            if self._failure is not None:
                return
            if self._awaiting_retry is not None:
                wait_for = self._retry_confirmed
                reason = "continuation_missing"
            elif self._native_prompts or self._current_round is not None:
                self.fail(
                    ElicitationExpectationError(
                        "Codex completed with an unanswered native elicitation",
                        details={"reason": "native_prompt_incomplete"},
                    )
                )
                return
            elif self._calls:
                wait_for = self._observation_changed
                reason = "observation_incomplete"
            elif self._retry_in_flight is not None:
                wait_for = self._observation_changed
                reason = "continuation_result_missing"
            elif not self._native_operations_complete():
                wait_for = self._observation_changed
                reason = "native_tool_observation_incomplete"
            elif self._matcher is not None:
                try:
                    self._matcher.complete()
                except BaseException:
                    wait_for = self._observation_changed
                    reason = "plan_incomplete"
                else:
                    return
            else:
                return
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                self.fail(
                    ElicitationExpectationError(
                        "Codex completed before MRTR wire evidence settled",
                        details={"reason": reason},
                    )
                )
                return
            if wait_for is self._retry_confirmed and wait_for.is_set():
                continue
            try:
                await asyncio.wait_for(wait_for.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                self.fail(
                    ElicitationExpectationError(
                        "Codex completed before MRTR wire evidence settled",
                        details={"reason": reason},
                    )
                )
                return

    @staticmethod
    def _native_item_key(item: Mapping[str, Any]) -> str:
        server = item.get("server")
        tool = item.get("tool")
        if (
            not isinstance(server, str)
            or not server
            or not isinstance(tool, str)
            or not tool
        ):
            raise ElicitationExpectationError(
                "Codex native tool item has insufficient server or tool identity",
                details={"reason": "native_tool_item_unbound"},
            )
        return canonical_json(
            {
                "server": server,
                "tool": tool,
                "arguments": item.get("arguments", {}),
            }
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._changed.set()
        if self._managed_round_active and self._managed_round_abort is not None:
            error = self._failure or ElicitationExpectationError(
                "Codex action ended before managed elicitation delivery was confirmed",
                details={"reason": "managed_delivery_unconfirmed"},
            )
            try:
                await self._managed_round_abort(error)
            except BaseException as abort_error:
                if self._failure is None:
                    self.fail(abort_error)
            finally:
                self._managed_round_active = False
        if self._subscription is not None:
            await self._subscription.aclose()
        tasks = [
            task
            for task in (self._observer_task, self._coordinator_task)
            if task is not None and task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = ["CodexMRTRAction", "CodexPromptAssociator", "canonical_json"]


def _stable_operation_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Keep operation parameters while removing Codex's per-call progress token.

    Codex 0.156.1 increments MCP ``_meta.progressToken`` on each wire call,
    including a keyed retry of the same operation. The remaining metadata and
    all other original parameters remain part of the operation identity.
    """

    stable = {
        key: value
        for key, value in params.items()
        if key not in {"requestState", "inputResponses"}
    }
    meta = stable.get("_meta")
    if isinstance(meta, Mapping) and "progressToken" in meta:
        stable["_meta"] = {
            key: value for key, value in meta.items() if key != "progressToken"
        }
    return stable
