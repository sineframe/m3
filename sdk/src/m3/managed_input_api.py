"""Public execution-handle boundary for managed elicitation input."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Protocol, cast

from ._types.specs import AgentSpec
from .elicitation import ElicitationResponse, PendingElicitationRound
from .errors import (
    ManagedInputStateError,
    ManagedInputValidationError,
    ModelValidationError,
)
from .storage.managed_input import ManagedInputStore

HumanInput = Literal["fail", "managed"]


def _execution_key(value: object) -> str:
    root = getattr(value, "root", None)
    return root if isinstance(root, str) else str(value)


_MANAGED_STORE_METHODS = (
    "get_round",
    "list_rounds",
    "submit_responses",
)


class ManagedInputProvider(Protocol):
    """Persistent store boundary with lazy managed-input initialization."""

    def resolve_managed_input_store(self) -> ManagedInputStore: ...


def validate_human_input(value: object) -> HumanInput:
    if value not in {"fail", "managed"}:
        raise ModelValidationError(
            "human_input must be 'fail' or 'managed'",
            details={"operation": "execution.submit"},
        )
    return cast(HumanInput, value)


def apply_human_input(spec: object, value: object = "fail") -> object:
    policy = validate_human_input(value)
    if not isinstance(spec, AgentSpec):
        if policy == "managed":
            raise ModelValidationError(
                "managed human input is supported only for agent executions",
                details={"operation": "execution.submit"},
            )
        return spec
    if policy == "managed" and getattr(spec, "elicitation", None) is not None:
        raise ModelValidationError(
            "managed human input cannot be combined with a predefined elicitation plan",
            details={"operation": "execution.submit"},
        )
    return spec


def managed_input_provider(store: object) -> ManagedInputProvider:
    resolver = getattr(store, "resolve_managed_input_store", None)
    if not callable(resolver):
        raise ModelValidationError(
            "managed human input requires a persistent managed-input-capable store",
            details={"operation": "execution.submit"},
        )
    return cast(ManagedInputProvider, store)


def managed_store(store: object) -> ManagedInputStore:
    candidate = managed_input_provider(store).resolve_managed_input_store()
    if candidate is None or not all(
        callable(getattr(candidate, name, None)) for name in _MANAGED_STORE_METHODS
    ):
        raise ModelValidationError(
            "managed human input requires a persistent managed-input-capable store",
            details={"operation": "execution.submit"},
        )
    return candidate


def pending_elicitation(
    store: object, execution_id: object
) -> PendingElicitationRound | None:
    candidate = managed_store(store)
    execution_key = _execution_key(execution_id)
    records = tuple(
        record
        for record in candidate.list_rounds(execution_key)
        if record.status == "pending"
    )
    if len(records) > 1:
        raise ManagedInputStateError(
            "managed-input execution has multiple pending elicitation rounds"
        )
    if records:
        return PendingElicitationRound.model_validate(
            records[0].pending.model_dump(mode="json")
        )
    return None


def respond_elicitation(
    store: object,
    execution_id: object,
    round_id: str,
    responses: Mapping[str, ElicitationResponse],
    *,
    idempotency_key: str,
) -> None:
    if not isinstance(round_id, str) or not round_id:
        raise ManagedInputValidationError("round_id must be a non-empty string")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ManagedInputValidationError("idempotency_key must be a non-empty string")
    candidate = managed_store(store)
    execution_key = _execution_key(execution_id)
    record = candidate.get_round(execution_key, round_id)
    if record is None:
        raise ManagedInputValidationError("managed-input round does not exist")
    candidate.submit_responses(
        execution_key,
        round_id,
        responses,
        owner_id=record.owner_id,
        lease_token=record.lease_token,
        response_idempotency_key=idempotency_key,
    )


def command_human_input(payload: Mapping[str, object]) -> HumanInput:
    """Read the durable policy, preserving fail as the old-envelope default."""

    return validate_human_input(payload.get("human_input", "fail"))


__all__ = [
    "HumanInput",
    "apply_human_input",
    "command_human_input",
    "managed_input_provider",
    "managed_store",
    "pending_elicitation",
    "respond_elicitation",
    "validate_human_input",
]
