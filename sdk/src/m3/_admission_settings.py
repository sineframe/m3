"""Explicit admission precedence, validated before native preparation."""

from __future__ import annotations

from ._types.settings import Unset
from ._types.specs import AgentSpec
from .configuration import Config, _with_overrides, merge_settings
from .errors import ModelValidationError, UnsupportedFeature
from .types import UNSET, ClaudeCode, Codex, OpenCode, StartupAdmission


def override_admission_config(
    config: Config, override: StartupAdmission | Unset
) -> Config:
    if override is UNSET:
        return config
    if not isinstance(override, StartupAdmission):
        raise ModelValidationError("admission requires typed startup settings")
    return _with_overrides(config, {"admission": override})


def resolve_admission(
    spec: AgentSpec,
    config: Config,
    override: StartupAdmission | Unset = UNSET,
    *,
    kit_override: StartupAdmission | Unset = UNSET,
) -> AgentSpec:
    layers = [config.admission]
    harness = spec.harness
    if isinstance(harness, (Codex, ClaudeCode, OpenCode)):
        layers.append(getattr(config.harnesses, harness.kind).admission)
    if kit_override is not UNSET:
        if not isinstance(kit_override, StartupAdmission):
            raise ModelValidationError("admission requires typed startup settings")
        layers.append(kit_override)
    layers.append(spec.admission)
    if override is not UNSET:
        if not isinstance(override, StartupAdmission):
            raise ModelValidationError("admission requires typed startup settings")
        layers.append(override)
    settings = merge_settings(*layers)
    if settings.enabled:
        if not isinstance(harness, (Codex, ClaudeCode, OpenCode)):
            raise UnsupportedFeature(
                "selected harness does not support startup admission"
            )
        from .harness.admission import validate_host_admission

        validate_host_admission(settings)
    return spec.model_copy(update={"admission": settings})


def admission_override(
    spec: AgentSpec, override: StartupAdmission | Unset
) -> AgentSpec:
    if override is UNSET:
        return spec
    if not isinstance(override, StartupAdmission):
        raise ModelValidationError("admission requires typed startup settings")
    return spec.model_copy(
        update={"admission": merge_settings(spec.admission, override)}
    )
