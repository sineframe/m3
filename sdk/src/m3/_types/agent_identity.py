"""Stable, serializable identity of the agent used for an execution."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from .base import FrozenModel
from .events import Event, EventKind


class HarnessIdentity(FrozenModel):
    kind: str = Field(min_length=1, max_length=64)
    runtime: Literal["system", "managed"] = "system"
    requested_selector: str | None = Field(default=None, max_length=64)
    resolved_version: str | None = Field(default=None, max_length=64)
    target: str | None = Field(default=None, max_length=128)
    digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    verification_method: str | None = Field(default=None, max_length=64)
    immutable_release: bool | None = None


class ModelIdentity(FrozenModel):
    requested_id: str = Field(min_length=1, max_length=512)
    provider: str | None = Field(default=None, max_length=128)
    observed_id: str | None = Field(default=None, max_length=512)


class AgentIdentity(FrozenModel):
    harness: HarnessIdentity
    model: ModelIdentity


def project_agent_identity(
    events: Sequence[Event], initial: AgentIdentity | None = None
) -> AgentIdentity | None:
    """Project identity from events, including partial startup evidence.

    The request event is emitted before managed acquisition, so a failed
    download retains its selector. A later resolution event adds the exact
    runtime without rewriting the submitted specification.
    """

    identity = initial
    for event in events:
        payload = event.payload
        if event.kind is EventKind.HARNESS_SELECTION:
            try:
                identity = AgentIdentity.model_validate(payload)
            except (TypeError, ValueError):
                continue
        elif event.kind is EventKind.HARNESS_RUNTIME_RESOLVED and identity is not None:
            resolved = {
                key: payload[key]
                for key in (
                    "resolved_version",
                    "target",
                    "digest",
                    "verification_method",
                    "immutable_release",
                )
                if key in payload
            }
            try:
                harness = HarnessIdentity.model_validate(
                    {**identity.harness.model_dump(mode="python"), **resolved}
                )
            except (TypeError, ValueError):
                continue
            identity = AgentIdentity(harness=harness, model=identity.model)
        elif event.kind is EventKind.PROVIDER_EVENT and identity is not None:
            # Native adapters already normalize observed model and provider
            # identifiers into scalar provider events. Never infer an observed
            # model from the requested selection.
            category = payload.get("category")
            value = payload.get("data")
            if category not in {"model", "provider"} or not isinstance(value, str):
                continue
            if not value or len(value) > (512 if category == "model" else 128):
                continue
            values = identity.model.model_dump(mode="python")
            values["observed_id" if category == "model" else "provider"] = value
            try:
                model = ModelIdentity.model_validate(values)
            except (TypeError, ValueError):
                continue
            identity = AgentIdentity(harness=identity.harness, model=model)
    return identity


__all__ = [
    "AgentIdentity",
    "HarnessIdentity",
    "ModelIdentity",
    "project_agent_identity",
]
