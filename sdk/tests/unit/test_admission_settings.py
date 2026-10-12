"""Admission opt-in merges explicit leaves through the normal public APIs."""

import sys

import pytest

from m3._admission_settings import admission_override, resolve_admission
from m3._types.specs import AgentSpec, ServerBinding
from m3.async_api import AsyncMCPTestKit
from m3.configuration import Config
from m3.errors import ModelValidationError, UnsupportedFeature
from m3.sync_api import MCPTestKit
from m3.types import Codex, FullToolPolicy, StartupAdmission, StdioServer


def _spec(**values: object) -> AgentSpec:
    return AgentSpec(
        harness=Codex(model="fixture"),
        tool_policy=FullToolPolicy(acknowledge_risk=True),
        servers=(
            ServerBinding(server=StdioServer(name="slow", command=sys.executable)),
        ),
        **values,
    )


def test_admission_explicit_precedence_preserves_inherited_leaves() -> None:
    config = Config(
        admission=StartupAdmission(enabled=True, slots=8, queue_timeout_seconds=120),
        harnesses={"codex": {"admission": {"slots": 6}}},
    )
    spec = _spec(admission=StartupAdmission(queue_timeout_seconds=40))
    effective = resolve_admission(
        spec, config, StartupAdmission(normalized_load_threshold=None)
    ).admission
    assert effective.enabled
    assert effective.slots == 6
    assert effective.queue_timeout_seconds == 40
    assert effective.normalized_load_threshold is None


def test_session_disable_is_explicit_and_json_preserves_it() -> None:
    spec = _spec(admission=StartupAdmission(enabled=True, slots=2))
    overridden = admission_override(spec, StartupAdmission(enabled=False))
    restored = AgentSpec.model_validate_json(overridden.model_dump_json())
    effective = resolve_admission(restored, Config()).admission
    assert not effective.enabled
    assert effective.slots == 2
    assert "admission" not in _spec().model_dump()


def test_untyped_or_unsupported_admission_fails_before_preparation() -> None:
    with pytest.raises(ModelValidationError, match="typed"):
        admission_override(_spec(), None)  # type: ignore[arg-type]
    with pytest.raises(UnsupportedFeature, match="selected harness"):
        resolve_admission(
            _spec().model_copy(update={"harness": None}),
            Config(admission=StartupAdmission(enabled=True)),
        )


def test_sync_kit_explicit_admission_overlays_file_settings() -> None:
    with MCPTestKit(
        Config(admission=StartupAdmission(enabled=True, slots=8)),
        admission=StartupAdmission(slots=3),
    ) as kit:
        assert kit.config.admission.enabled
        assert kit.config.admission.slots == 3


@pytest.mark.asyncio
async def test_async_kit_explicit_admission_overlays_file_settings() -> None:
    async with AsyncMCPTestKit(
        Config(admission=StartupAdmission(enabled=True, slots=8)),
        admission=StartupAdmission(slots=3),
    ) as kit:
        assert kit.config.admission.enabled
        assert kit.config.admission.slots == 3


def test_sync_session_keeps_kit_override_above_file_harness_settings() -> None:
    with MCPTestKit(
        Config(harnesses={"codex": {"admission": {"enabled": True, "slots": 6}}}),
        admission=StartupAdmission(enabled=False, slots=2),
    ) as kit:
        session = kit.agent_session(_spec())
        assert not session._portal._runtime.session(
            session._handle
        ).spec.admission.enabled
        assert (
            session._portal._runtime.session(session._handle).spec.admission.slots == 2
        )


@pytest.mark.asyncio
async def test_async_session_keeps_kit_override_above_file_harness_settings() -> None:
    async with AsyncMCPTestKit(
        Config(harnesses={"codex": {"admission": {"enabled": True, "slots": 6}}}),
        admission=StartupAdmission(enabled=False, slots=2),
    ) as kit:
        session = kit.agent_session(_spec())
        assert not session.spec.admission.enabled
        assert session.spec.admission.slots == 2
