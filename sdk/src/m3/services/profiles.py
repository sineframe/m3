"""Read-only saved profile resolution for SDK execution boundaries.

Profile records are application-owned data.  The SDK only depends on this
small read contract so a toolkit can resolve a saved profile without knowing
anything about SQLAlchemy or the application's profile service.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from .._types.specs import AgentSpec
from ..errors import MCPError
from ..storage.ephemeral import ProfileResolver
from ..types import (
    DirectSpec,
    HarnessProfileRef,
    HarnessSpec,
    HTTPServer,
    RevisionSelection,
    SecretReference,
    ServerBinding,
    ServerProfileRef,
    ServerValue,
    StdioServer,
    TrustLevel,
)


class ProfileResolutionError(MCPError):
    """A saved profile could not be resolved into a runnable SDK value."""

    code = "profile_resolution_failed"


@dataclass(frozen=True)
class ResolvedServer:
    ref: ServerProfileRef
    value: ServerValue
    profile: Any
    revision: Any

    @property
    def provenance(self) -> Mapping[str, str]:
        return {
            "kind": "server_profile",
            "profile_id": _identifier(self.profile.id),
            "profile_name": str(self.profile.name),
            "revision_id": _identifier(self.revision.id),
            "revision_number": str(self.revision.revision_number),
            "server_name": self.value.name,
        }


@dataclass(frozen=True)
class ResolvedHarness:
    ref: HarnessProfileRef
    value: HarnessSpec
    profile: Any
    revision: Any

    @property
    def provenance(self) -> Mapping[str, str]:
        return {
            "kind": "harness_profile",
            "profile_id": _identifier(self.profile.id),
            "profile_name": str(self.profile.name),
            "revision_id": _identifier(self.revision.id),
            "revision_number": str(self.revision.revision_number),
            "harness_kind": str(self.value.kind),
        }


_HARNESS_ADAPTER: TypeAdapter[HarnessSpec] = TypeAdapter(HarnessSpec)
_ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _identifier(value: Any) -> str:
    root = getattr(value, "root", None)
    return str(root if root is not None else value)


def _secret_or_literal(value: Any) -> SecretReference | str:
    """Apply the application MCP document credential convention."""
    if isinstance(value, SecretReference):
        return value
    if isinstance(value, Mapping) and set(value) == {"source", "name"}:
        try:
            return SecretReference.model_validate(dict(value))
        except (TypeError, ValueError) as exc:
            raise ValueError("MCP credential reference is invalid") from exc
    if isinstance(value, str):
        match = _ENV_REFERENCE.fullmatch(value)
        if match:
            return SecretReference(source="environment", name=match.group(1))
        return value
    raise ValueError("MCP credential values must be strings or references")


def server_value_from_mapping(name: str, raw: Mapping[str, Any]) -> ServerValue:
    """Convert an application/UI MCP descriptor using one shared parser."""
    typ = str(raw.get("type", raw.get("kind", "stdio"))).lower()
    if typ == "streamable_http":
        typ = "http"
    trust = TrustLevel(str(raw.get("trust", TrustLevel.UNTRUSTED.value)))
    if typ == "stdio":
        return StdioServer(
            name=name,
            trust=trust,
            command=str(raw.get("command", "")),
            args=tuple(str(item) for item in (raw.get("args") or ())),
            environment={
                str(key): _secret_or_literal(item)
                for key, item in (
                    raw.get("env", raw.get("environment", {})) or {}
                ).items()
            },
            cwd=raw.get("cwd"),
        )
    headers = {
        str(key): _secret_or_literal(item)
        for key, item in (raw.get("headers") or {}).items()
    }
    if typ == "http":
        return HTTPServer(
            name=name, trust=trust, url=str(raw.get("url", "")), headers=headers
        )
    raise ValueError(f"unsupported MCP transport: {typ}")


def _resolve_parts(
    resolver: ProfileResolver,
    profile_id: str,
    selection: RevisionSelection,
    *,
    kind: Literal["server", "harness"],
) -> tuple[Any, Any]:
    try:
        profile, revision = resolver.resolve_profile(profile_id, selection, kind=kind)
    except ProfileResolutionError:
        raise
    except Exception as exc:
        raise ProfileResolutionError(
            "saved profile could not be resolved",
            details={"kind": kind, "reason": "unavailable"},
        ) from exc
    if profile is not None and getattr(profile, "kind", None) != kind:
        raise ProfileResolutionError(
            "saved profile kind does not match the reference",
            details={"kind": kind, "reason": "wrong_kind"},
        )
    if profile is None:
        raise ProfileResolutionError(
            "saved profile could not be resolved",
            details={"kind": kind, "reason": "missing"},
        )
    if bool(getattr(profile, "archived", False)):
        raise ProfileResolutionError(
            "saved profile is archived",
            details={"kind": kind, "reason": "archived"},
        )
    if revision is None:
        raise ProfileResolutionError(
            "saved profile revision does not exist",
            details={"kind": kind, "reason": "missing_revision"},
        )
    if _identifier(getattr(revision, "profile_id", "")) != _identifier(profile.id):
        raise ProfileResolutionError(
            "saved profile revision does not belong to the profile",
            details={"kind": kind, "reason": "revision_mismatch"},
        )
    value = getattr(revision, "value", None)
    if not isinstance(value, Mapping):
        raise ProfileResolutionError(
            "saved profile revision is malformed",
            details={"kind": kind, "reason": "malformed"},
        )
    return profile, revision


def resolve_server_reference(
    ref: ServerProfileRef, resolver: ProfileResolver
) -> ResolvedServer:
    profile, revision = _resolve_parts(
        resolver, ref.profile_id.root, ref.revision, kind="server"
    )
    document = dict(revision.value)
    raw: Mapping[str, Any]
    servers = document.get("mcpServers")
    if isinstance(servers, Mapping):
        candidate = servers.get(ref.server_name)
        if not isinstance(candidate, Mapping):
            raise ProfileResolutionError(
                "saved server is not present in the profile revision",
                details={"kind": "server", "reason": "server_missing"},
            )
        raw = candidate
    else:
        raw = document
    raw = dict(raw)
    # The name is explicit on the reference and therefore authoritative for
    # the launch selector.  Persisted descriptors may omit it; when present,
    # a disagreement indicates a stale/malformed profile, not a rename.
    persisted_name = raw.get("name")
    if persisted_name is not None and persisted_name != ref.server_name:
        raise ProfileResolutionError(
            "saved server name does not match the reference",
            details={"kind": "server", "reason": "name_mismatch"},
        )
    raw["name"] = ref.server_name
    try:
        value = server_value_from_mapping(ref.server_name, raw)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ProfileResolutionError(
            "saved server profile revision is malformed",
            details={"kind": "server", "reason": "malformed"},
        ) from exc
    return ResolvedServer(ref, value, profile, revision)


def resolve_harness_reference(
    ref: HarnessProfileRef, resolver: ProfileResolver
) -> ResolvedHarness:
    profile, revision = _resolve_parts(
        resolver, ref.profile_id.root, ref.revision, kind="harness"
    )
    raw = dict(revision.value)
    manifest = raw.get("manifest")
    if not isinstance(manifest, Mapping) or not bool(raw.get("trusted_unsandboxed")):
        raise ProfileResolutionError(
            "saved harness profile is not trusted or is malformed",
            details={"kind": "harness", "reason": "untrusted_or_malformed"},
        )
    if not manifest.get("command") or not isinstance(manifest.get("command"), str):
        raise ProfileResolutionError(
            "saved harness manifest is malformed",
            details={"kind": "harness", "reason": "malformed"},
        )
    candidate: Mapping[str, Any] = {
        "kind": "acp",
        "name": "acp",
        "model": "agent-default",
        "executable": manifest["command"],
        "manifest": dict(manifest),
    }
    try:
        value = _HARNESS_ADAPTER.validate_python(dict(candidate))
    except (ValidationError, ValueError, TypeError) as exc:
        raise ProfileResolutionError(
            "saved harness profile revision is malformed",
            details={"kind": "harness", "reason": "malformed"},
        ) from exc
    return ResolvedHarness(ref, value, profile, revision)


def resolve_agent_spec(
    spec: AgentSpec, resolver: ProfileResolver
) -> tuple[AgentSpec, tuple[Mapping[str, str], ...]]:
    """Resolve all saved references in an agent spec exactly once."""

    servers = []
    provenance: list[Mapping[str, str]] = []
    for ordinal, binding in enumerate(spec.servers):
        if binding.profile is None:
            servers.append(binding)
            continue
        resolved = resolve_server_reference(binding.profile, resolver)
        servers.append(
            ServerBinding(
                server=resolved.value,
                alias=binding.alias or resolved.value.name,
                required=binding.required,
            )
        )
        provenance.append({**resolved.provenance, "_ordinal": str(ordinal)})
    harness = spec.harness
    harness_profile = spec.harness_profile
    if harness_profile is not None:
        resolved_harness = resolve_harness_reference(harness_profile, resolver)
        harness = resolved_harness.value
        harness_profile = None
        provenance.append(resolved_harness.provenance)
    return (
        spec.model_copy(
            update={
                "servers": tuple(servers),
                "harness": harness,
                "harness_profile": harness_profile,
            }
        ),
        tuple(provenance),
    )


def resolve_execution_spec(
    spec: AgentSpec | DirectSpec, resolver: ProfileResolver | None
) -> tuple[AgentSpec | DirectSpec, tuple[Mapping[str, str], ...]]:
    """Resolve saved profile references at the execution launch boundary."""

    refs = any(item.profile is not None for item in spec.servers)
    harness_ref = isinstance(spec, AgentSpec) and spec.harness_profile is not None
    if not refs and not harness_ref:
        return spec, ()
    if resolver is None:
        raise ProfileResolutionError(
            "saved profile resolution requires a configured execution store",
            details={"reason": "resolver_unavailable"},
        )
    if isinstance(spec, AgentSpec):
        return resolve_agent_spec(spec, resolver)
    servers: list[ServerBinding] = []
    provenance: list[Mapping[str, str]] = []
    for ordinal, binding in enumerate(spec.servers):
        if binding.profile is None:
            servers.append(binding)
            continue
        resolved = resolve_server_reference(binding.profile, resolver)
        servers.append(
            ServerBinding(
                server=resolved.value,
                alias=binding.alias or resolved.value.name,
                required=binding.required,
            )
        )
        provenance.append({**resolved.provenance, "_ordinal": str(ordinal)})
    return spec.model_copy(update={"servers": tuple(servers)}), tuple(provenance)


__all__ = [
    "ProfileResolver",
]
