"""Portable, fail-closed tool-policy evaluation.

The evaluator only reasons about advertised tool identities.  It does not
execute tools or infer capabilities from a provider's name.  Harnesses must
explicitly report that they can enforce a requested portable policy before a
session opens.  Argument-aware rules are intentionally out of this public
model; adapters may report argument evidence separately for later evaluation.
"""

from __future__ import annotations

from collections.abc import Iterable as _Iterable
from typing import Callable as _Callable

from .errors import UnsupportedFeature as _UnsupportedFeature
from .types import (
    FullToolPolicy as _FullToolPolicy,
    NativeToolPolicy as _NativeToolPolicy,
    RestrictiveToolPolicy as _RestrictiveToolPolicy,
    ToolPolicy as _ToolPolicy,
)
from pydantic import Field as _Field, field_validator as _field_validator

from .types import FrozenModel as _FrozenModel


class ToolDescriptor(_FrozenModel):
    """A qualified advertised tool identity used during preflight."""

    server: str = _Field(min_length=1, max_length=256)
    name: str = _Field(min_length=1, max_length=256)
    destructive: bool = False

    @_field_validator("server", "name")
    @classmethod
    def _safe_name(cls, value: str) -> str:
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
            raise ValueError("tool identity contains unsafe control characters")
        return value

    @_field_validator("server")
    @classmethod
    def _server_is_qualifiable(cls, value: str) -> str:
        if ":" in value:
            raise ValueError("server identity cannot contain ':'")
        return value

    @property
    def qualified_name(self) -> str:
        return f"{self.server}:{self.name}"


class ToolPolicyEvidence(_FrozenModel):
    """Truthful requested/enforced/observed policy status."""

    requested: str
    enforced: str | None = None
    observed: str | None = None
    unavailable: tuple[str, ...] = ()
    portable: bool = True
    nonportable_reason: str | None = None


class ToolPolicyDecision(_FrozenModel):
    """Decision for one qualified tool call."""

    allowed: bool
    requires_confirmation: bool = False
    reason: str
    evidence: ToolPolicyEvidence


ConfirmationHook = _Callable[[ToolDescriptor], bool]


def _pattern(value: str) -> tuple[str | None, str]:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("tool policy entries must be safe non-empty text")
    if ":" in value:
        server, name = value.split(":", 1)
        if not server or not name or ":" in name:
            raise ValueError("tool policy entries must use server:name")
        return server, name
    return None, value


def _matches(pattern: str, descriptor: ToolDescriptor, *, duplicates: set[str]) -> bool:
    server, name = _pattern(pattern)
    if name != descriptor.name:
        return False
    if server is not None:
        return server == descriptor.server
    # An unqualified name is intentionally rejected by the evaluator when
    # more than one server advertises it; callers must disambiguate.
    return descriptor.name not in duplicates


def _destructive_decision(
    descriptor: ToolDescriptor,
    evidence: ToolPolicyEvidence,
    *,
    allowed: bool,
    reason: str,
    confirm: ConfirmationHook | None,
) -> ToolPolicyDecision:
    if not allowed:
        return ToolPolicyDecision(allowed=False, reason=reason, evidence=evidence)
    if not descriptor.destructive:
        return ToolPolicyDecision(allowed=True, reason="allowed", evidence=evidence)
    if confirm is None:
        return ToolPolicyDecision(
            allowed=False,
            requires_confirmation=True,
            reason="destructive tool requires confirmation",
            evidence=evidence,
        )
    try:
        confirmed = bool(confirm(descriptor))
    except Exception:
        confirmed = False
    return ToolPolicyDecision(
        allowed=confirmed,
        requires_confirmation=True,
        reason="allowed" if confirmed else "destructive tool confirmation denied",
        evidence=evidence,
    )


class ToolPolicyEvaluator:
    """Evaluate portable and explicitly native tool policies."""

    def __init__(self, tools: _Iterable[ToolDescriptor]) -> None:
        values = tuple(tools)
        if any(not isinstance(tool, ToolDescriptor) for tool in values):
            raise ValueError("tool descriptors are invalid")
        identities = {(tool.server, tool.name) for tool in values}
        if len(identities) != len(values):
            raise ValueError("duplicate qualified tool identity")
        counts: dict[str, int] = {}
        for tool in values:
            counts[tool.name] = counts.get(tool.name, 0) + 1
        self._tools = values
        self._duplicates = {name for name, count in counts.items() if count > 1}

    @property
    def tools(self) -> tuple[ToolDescriptor, ...]:
        return self._tools

    def preflight(
        self,
        policy: _ToolPolicy,
        *,
        harness_name: str,
        supports_enforcement: bool,
    ) -> ToolPolicyEvidence:
        if not isinstance(policy, (_RestrictiveToolPolicy, _FullToolPolicy, _NativeToolPolicy)):
            raise _UnsupportedFeature("tool policy is unsupported")
        if isinstance(policy, _NativeToolPolicy):
            if policy.harness != harness_name:
                raise _UnsupportedFeature("native tool policy targets a different harness")
            return ToolPolicyEvidence(
                requested="native",
                enforced="native",
                observed="preflight",
                portable=False,
                nonportable_reason=policy.nonportable_reason,
            )
        if not supports_enforcement:
            raise _UnsupportedFeature("harness cannot enforce the requested portable tool policy")
        kind = "full" if isinstance(policy, _FullToolPolicy) else "restrictive"
        return ToolPolicyEvidence(requested=kind, enforced="portable", observed="preflight")

    def decide(
        self,
        policy: _ToolPolicy,
        descriptor: ToolDescriptor,
        *,
        harness_name: str,
        supports_enforcement: bool,
        confirm: ConfirmationHook | None = None,
    ) -> ToolPolicyDecision:
        if (descriptor.server, descriptor.name) not in {
            (tool.server, tool.name) for tool in self._tools
        }:
            raise _UnsupportedFeature("tool is not advertised by the server group")
        evidence = self.preflight(
            policy,
            harness_name=harness_name,
            supports_enforcement=supports_enforcement,
        )
        if isinstance(policy, _NativeToolPolicy):
            return _destructive_decision(
                descriptor,
                evidence,
                allowed=True,
                reason="native policy delegated to harness",
                confirm=confirm,
            )
        if isinstance(policy, _FullToolPolicy):
            return _destructive_decision(
                descriptor,
                evidence,
                allowed=True,
                reason="unrestricted policy acknowledged",
                confirm=confirm,
            )
        try:
            denied = any(_matches(value, descriptor, duplicates=self._duplicates) for value in policy.denied_tools)
            allowed = bool(policy.allowed_tools) and any(
                _matches(value, descriptor, duplicates=self._duplicates) for value in policy.allowed_tools
            )
        except (TypeError, ValueError):
            # Malformed policy entries must not accidentally turn into an
            # allow decision or expose arbitrary parser details.
            raise _UnsupportedFeature("tool policy entry is invalid") from None
        return _destructive_decision(
            descriptor,
            evidence,
            allowed=allowed and not denied,
            reason="tool denied by restrictive policy" if denied or not allowed else "allowed",
            confirm=confirm,
        )


def evaluate_tool_policy(
    policy: _ToolPolicy,
    tools: _Iterable[ToolDescriptor],
    *,
    harness_name: str,
    supports_enforcement: bool,
) -> ToolPolicyEvidence:
    """Run policy preflight and return only truthful usage evidence."""

    return ToolPolicyEvaluator(tools).preflight(
        policy,
        harness_name=harness_name,
        supports_enforcement=supports_enforcement,
    )


__all__ = ["ConfirmationHook", "ToolDescriptor", "ToolPolicyDecision", "ToolPolicyEvidence", "ToolPolicyEvaluator", "evaluate_tool_policy"]
