"""Typed immutable-cache selection; isolated launches remain the default."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from ._types.settings import UNSET, CachePolicy, Unset
from ._types.specs import AgentSpec
from .configuration import Config, merge_settings
from .errors import ModelValidationError, UnsupportedFeature
from .types import OpenCode


def cache_override(spec: AgentSpec, value: CachePolicy | Unset) -> AgentSpec:
    if value is UNSET:
        return spec
    if not isinstance(value, CachePolicy):
        raise ModelValidationError("cache requires a typed cache policy")
    return spec.model_copy(update={"cache": merge_settings(spec.cache, value)})


def resolve_cache(spec: AgentSpec, config: Config) -> AgentSpec:
    layers = [config.cache]
    harness = spec.harness
    if harness is not None:
        configured = getattr(config.harnesses, harness.kind, None)
        if configured is not None:
            layers.append(configured.cache)
        layers.append(harness.cache)
    layers.append(spec.cache)
    policy = merge_settings(*layers)
    validate_cache(spec, policy)
    return spec.model_copy(update={"cache": policy})


def launch_cache(spec: AgentSpec) -> CachePolicy:
    harness = spec.harness
    policy = merge_settings(harness.cache, spec.cache) if harness else spec.cache
    validate_cache(spec, policy)
    return policy


def validate_cache(spec: AgentSpec, policy: CachePolicy) -> None:
    harness = spec.harness
    if policy.mode == "immutable-seed":
        if not (
            isinstance(harness, OpenCode)
            and harness.runtime == "managed"
            and harness.version == "1.18.35"
            and sys.platform == "darwin"
            and os.uname().machine == "arm64"
        ):
            raise UnsupportedFeature(
                "immutable cache does not support this harness build"
            )
        if isinstance(policy.root, str) and not (
            Path(policy.root).is_absolute() or policy.root.startswith("~/")
        ):
            raise ModelValidationError(
                "immutable cache root must be absolute or start with ~/"
            )


def pinned_cache_root(
    policy: CachePolicy, environment: Mapping[str, str]
) -> str | None:
    """Resolve from the first launch snapshot, before HOME/XDG isolation."""
    if policy.mode == "isolated":
        return None
    home = environment.get("HOME")
    if isinstance(policy.root, str):
        if policy.root.startswith("~/"):
            if not home:
                raise UnsupportedFeature("immutable cache home is unavailable")
            root = Path(home) / policy.root[2:]
        else:
            root = Path(policy.root)
    else:
        configured = environment.get("XDG_CACHE_HOME")
        if configured:
            root = Path(configured) / "m3" / "immutable"
        elif home:
            root = (
                Path(home)
                / ("Library/Caches" if sys.platform == "darwin" else ".cache")
                / "m3"
                / "immutable"
            )
        else:
            raise UnsupportedFeature("immutable cache home is unavailable")
    if not root.is_absolute():
        raise UnsupportedFeature("immutable cache root must be absolute")
    return str(root)
