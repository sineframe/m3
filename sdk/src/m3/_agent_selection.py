"""Author facing agent selection and execution helpers.

Selections are deliberately small immutable records.  They expand ordinary
Python dictionaries into the existing serializable ``AgentSpec`` at the point
where an execution is requested; selecting agents never starts a process or
connects to an MCP server.
"""

from __future__ import annotations

import os as _os
import re as _re
from collections.abc import Mapping as _Mapping
from dataclasses import dataclass as _dataclass
from typing import Any as _Any

from .errors import UnsupportedFeature as _UnsupportedFeature
from .harness.manifest import ManifestValidationError as _ManifestValidationError
from .harness.manifest import validate_manifest as _validate_manifest
from .types import (
    ACPAgent as _ACPAgent,
)
from .types import (
    AgentSpec as _AgentSpec,
)
from .types import (
    ClaudeCode as _ClaudeCode,
)
from .types import (
    Codex as _Codex,
)
from .types import (
    FullToolPolicy as _FullToolPolicy,
)
from .types import (
    HarnessProfileRef as _HarnessProfileRef,
)
from .types import (
    NativeToolPolicy as _NativeToolPolicy,
)
from .types import (
    OpenCode as _OpenCode,
)
from .types import (
    Pi as _Pi,
)
from .types import (
    RestrictiveToolPolicy as _RestrictiveToolPolicy,
)
from .types import (
    SecretReference as _SecretReference,
)
from .types import (
    ServerBinding as _ServerBinding,
)
from .types import (
    TextContent as _TextContent,
)
from .types import (
    UserMessage as _UserMessage,
)

DEFAULT_EXECUTION_TIMEOUT_SECONDS = 180.0
_NAME = _re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_KINDS = {"claude", "claude_code", "claude-code", "opencode", "codex", "pi", "acp"}


def _name(value: _Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _validate_credential_mapping(mapping: _Mapping[str, str] | None) -> None:
    if mapping is None:
        return
    if not isinstance(mapping, _Mapping):
        raise TypeError("credential_env must be a mapping of target to source names")
    for target, source in mapping.items():
        target = _name(target, "credential target")
        source = _name(source, "credential source")
        if _NAME.fullmatch(target) is None or _NAME.fullmatch(source) is None:
            raise ValueError("credential environment names must be Python identifiers")


def _credential_refs(
    kind: str, model: str, mapping: _Mapping[str, str] | None
) -> dict[str, _SecretReference]:
    values: dict[str, str] = {}
    if kind in {"claude_code", "codex", "opencode", "pi"}:
        source = (
            "ANTHROPIC_API_KEY"
            if kind == "claude_code"
            else "OPENAI_API_KEY"
            if kind == "codex"
            else None
        )
        if kind in {"opencode", "pi"} and "/" in model:
            source = {
                "opencode": "OPENCODE_API_KEY",
                "openai": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
            }.get(model.split("/", 1)[0])
            if kind == "pi" and model.startswith("openai-codex/"):
                source = "PI_CODING_AGENT_DIR"
        if source and source in _os.environ:
            values[source] = source
    if mapping is not None:
        _validate_credential_mapping(mapping)
        mapped_targets: set[str] = set()
        for target, source in mapping.items():
            target = _name(target, "credential target")
            source = _name(source, "credential source")
            if source not in _os.environ:
                raise ValueError(
                    f"credential source environment variable {source!r} is not set"
                )
            if target in mapped_targets:
                raise ValueError(f"duplicate credential target {target!r}")
            mapped_targets.add(target)
            values[target] = source
    return {
        target: _SecretReference(source="environment", name=source)
        for target, source in values.items()
    }


def _harness(entry: _Mapping[str, _Any], kind: str, model: str) -> _Any:
    name = entry.get("name", kind)
    kwargs: dict[str, _Any] = {"name": _name(name, "name"), "model": model}
    for field in (
        "executable",
        "provider",
        "dialect",
        "manifest",
        "agent_mode_id",
        "session_config",
    ):
        if field in entry:
            kwargs[field] = entry[field]
    if kind in {"opencode", "pi"} and "provider" not in kwargs and "/" in model:
        kwargs["provider"] = model.split("/", 1)[0]
    refs = _credential_refs(kind, model, entry.get("credential_env"))
    if "credential_references" in entry:
        refs.update(entry["credential_references"])
    if kind == "claude_code":
        return _ClaudeCode(
            credential_references=refs,
            **{k: v for k, v in kwargs.items() if k in {"name", "model", "executable"}},
        )
    if kind == "opencode":
        return _OpenCode(credential_references=refs, **kwargs)
    if kind == "codex":
        return _Codex(
            credential_references=refs,
            **{k: v for k, v in kwargs.items() if k in {"name", "model", "executable"}},
        )
    if kind == "pi":
        return _Pi(
            credential_references=refs,
            **{
                k: v
                for k, v in kwargs.items()
                if k in {"name", "model", "executable", "provider"}
            },
        )
    if kind == "acp":
        manifest = kwargs.get("manifest")
        if not isinstance(manifest, _Mapping) or not manifest:
            raise ValueError("ACP selection requires a runnable manifest")
        return _ACPAgent(**kwargs)
    raise ValueError(f"unknown harness kind {kind!r}")


def _binding(value: _Any) -> _ServerBinding:
    if (
        hasattr(value, "server")
        and hasattr(value, "name")
        and not isinstance(value, _ServerBinding)
    ):
        return _ServerBinding(server=value.server, alias=value.name)
    if isinstance(value, _ServerBinding):
        return value
    if isinstance(value, _Mapping):
        return _ServerBinding.model_validate(value)
    return _ServerBinding(server=value)


def _server_scope(binding: _ServerBinding) -> str:
    """Return the stable server selector used by native server-scoped policies."""

    if binding.alias:
        return binding.alias
    if binding.server is not None:
        return binding.server.name
    if binding.profile is not None:
        return binding.profile.server_name
    raise ValueError("server binding has no selectable server name")


def _message(value: str | _UserMessage) -> _UserMessage:
    if isinstance(value, _UserMessage):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("message must be a non-empty string or UserMessage")
    return _UserMessage(content=(_TextContent(text=value),))


@_dataclass(frozen=True, slots=True)
class _Selection:
    kit: _Any
    harness: str
    model: str | None
    name: str
    trial: int
    entry: _Mapping[str, _Any]

    def _spec(
        self,
        message: str | _UserMessage | None,
        *,
        server: _Any = None,
        servers: _Any = None,
        tools: _Any = None,
        **options: _Any,
    ) -> _AgentSpec:
        if (server is None) == (servers is None):
            raise ValueError("provide exactly one of server or servers")
        values = [server] if server is not None else list(servers)
        bindings = tuple(_binding(value) for value in values)
        policy_option = options.get("tool_policy")
        if tools is not None and policy_option is not None:
            raise ValueError("tools and tool_policy cannot be supplied together")
        if self.model is None:
            profile = self.entry["harness_profile"]
            fields = {k: v for k, v in options.items() if k != "timeout"}
            fields["timeout_seconds"] = options.get(
                "timeout",
                self.entry.get("_execution_timeout", DEFAULT_EXECUTION_TIMEOUT_SECONDS),
            )
            if tools is not None:
                if not isinstance(tools, (list, tuple)) or len(set(tools)) != len(
                    tools
                ):
                    raise ValueError("tools must be a list without duplicates")
                aliases = {_server_scope(binding) for binding in bindings}
                if any(
                    not isinstance(item, str)
                    or ":" not in item
                    or item.split(":", 1)[0] not in aliases
                    for item in tools
                ):
                    raise ValueError("tools must use a bound server:tool qualifier")
                fields["tool_policy"] = _RestrictiveToolPolicy(
                    allowed_tools=tuple(tools)
                )
            elif "tool_policy" not in fields:
                fields["tool_policy"] = _FullToolPolicy(acknowledge_risk=True)
            if self.entry.get("_case_id") and "case_id" not in fields:
                fields["case_id"] = self.entry["_case_id"]
            metadata = dict(fields.get("metadata") or {})
            for key, value in {
                "harness_config": f"profile:{self.name}",
                "trial": self.trial,
                "m3.matrix.harness": "profile",
                "m3.matrix.trial": self.trial,
                "m3.matrix.matrix_id": self.entry.get("_matrix_id"),
                "m3.matrix.cell_id": self.entry.get("_cell_id"),
            }.items():
                if key in metadata:
                    raise ValueError(f"metadata key {key!r} is reserved")
                metadata[key] = value
            fields["metadata"] = metadata
            fields.update(servers=bindings, harness_profile=profile, message=message)
            return _AgentSpec(**fields)
        kind = self.harness
        harness = _harness(self.entry, kind, self.model)
        policy = options.pop("tool_policy", None)
        if tools is not None and policy is not None:
            raise ValueError("tools and tool_policy cannot be supplied together")
        if tools is not None:
            if not isinstance(tools, (list, tuple)):
                raise ValueError("tools must be None or a list of qualified tool names")
            aliases = {_server_scope(binding) for binding in bindings}
            if len(set(tools)) != len(tools):
                raise ValueError("tools must not contain duplicates")
            for item in tools:
                if (
                    not isinstance(item, str)
                    or ":" not in item
                    or item.split(":", 1)[0] not in aliases
                ):
                    raise ValueError("tools must use a bound server:tool qualifier")
            if kind == "claude_code":
                raise _UnsupportedFeature(
                    "Claude Code does not support exact tool restrictions; assert the selected tool in the trace"
                )
            policy = _RestrictiveToolPolicy(allowed_tools=tuple(tools))
        if policy is None:
            if kind == "claude_code":
                if len(bindings) != 1:
                    raise ValueError(
                        "Claude Code agent tests require exactly one bound server"
                    )
                policy = _NativeToolPolicy(
                    harness="claude-code",
                    policy={"mode": "mcp_only", "server": _server_scope(bindings[0])},
                    nonportable_reason="Claude Code enforces MCP access at server scope",
                )
            else:
                policy = _FullToolPolicy(acknowledge_risk=True)
        allowed = {
            "case_id",
            "run_id",
            "protocol",
            "timeout",
            "goal",
            "evaluations",
            "artifact_policy",
            "declared_artifacts",
            "workspace",
            "permission_policy",
            "elicitation_policy",
            "sampling_policy",
            "filesystem_policy",
            "terminal_policy",
            "metadata",
        }
        unknown = set(options) - allowed
        if unknown:
            raise TypeError(f"unknown agent execution option {sorted(unknown)[0]!r}")
        fields = {k: v for k, v in options.items() if k != "timeout"}
        fields["timeout_seconds"] = options.get(
            "timeout",
            self.entry.get("_execution_timeout", DEFAULT_EXECUTION_TIMEOUT_SECONDS),
        )
        fields.update(
            servers=bindings, harness=harness, message=message, tool_policy=policy
        )
        if "case_id" not in fields and self.entry.get("_case_id"):
            fields["case_id"] = self.entry["_case_id"]
        metadata = dict(fields.get("metadata") or {})
        for key, value in {
            "harness_config": f"{kind}:{self.model}"
            + (f":{self.name}" if self.name != kind else ""),
            "trial": self.trial,
            "m3.matrix.harness": kind,
            "m3.matrix.trial": self.trial,
            "m3.matrix.matrix_id": self.entry.get("_matrix_id"),
            "m3.matrix.cell_id": self.entry.get("_cell_id"),
        }.items():
            if key in metadata:
                raise ValueError(f"metadata key {key!r} is reserved")
            metadata[key] = value
        fields["metadata"] = metadata
        return _AgentSpec(**fields)

    def run(
        self,
        message: str | _UserMessage,
        *,
        server: _Any = None,
        servers: _Any = None,
        tools: _Any = None,
        **options: _Any,
    ) -> _Any:
        return self.kit.run(
            self._spec(
                _message(message),
                server=server,
                servers=servers,
                tools=tools,
                **options,
            )
        )

    def submit(
        self,
        message: str | _UserMessage,
        *,
        server: _Any = None,
        servers: _Any = None,
        tools: _Any = None,
        **options: _Any,
    ) -> _Any:
        return self.kit.submit(
            self._spec(
                _message(message),
                server=server,
                servers=servers,
                tools=tools,
                **options,
            )
        )

    def session(
        self,
        *,
        server: _Any = None,
        servers: _Any = None,
        tools: _Any = None,
        **options: _Any,
    ) -> _Any:
        session_options = {
            key: options.pop(key)
            for key in ("adapter", "runtime_servers", "interaction_handlers")
            if key in options
        }
        return self.kit.agent_session(
            self._spec(None, server=server, servers=servers, tools=tools, **options),
            **session_options,
        )


class _AsyncSelection(_Selection):
    async def run(
        self,
        message: str | _UserMessage,
        *,
        server: _Any = None,
        servers: _Any = None,
        tools: _Any = None,
        **options: _Any,
    ) -> _Any:
        return await self.kit.run(
            self._spec(
                _message(message),
                server=server,
                servers=servers,
                tools=tools,
                **options,
            )
        )


def expand(
    kit: _Any, entries: _Any, trials: int = 1, *, async_mode: bool = False
) -> tuple[_Selection, ...]:
    if isinstance(entries, _Mapping) or isinstance(entries, (str, bytes)):
        raise TypeError("agents must be a list of dictionaries")
    if isinstance(trials, bool) or not isinstance(trials, int) or trials <= 0:
        raise ValueError("trials must be a positive integer")
    output: list[_Selection] = []
    seen: set[tuple[str, str, str]] = set()
    seen_profiles: set[tuple[str, str]] = set()
    for raw in entries:
        if not isinstance(raw, _Mapping):
            raise TypeError("each agent selection must be a dictionary")
        if "harness_profile" in raw:
            allowed_profile = {
                "harness_profile",
                "name",
                "_case_id",
                "_matrix_id",
                "_cell_id",
                "_execution_timeout",
            }
            unknown = set(raw) - allowed_profile
            if unknown:
                raise ValueError(
                    f"unsupported fields for saved harness profile: {sorted(unknown)[0]!r}"
                )
            profile = raw["harness_profile"]
            ref = _HarnessProfileRef.model_validate(profile)
            profile_name = _name(raw.get("name", str(ref.profile_id)), "name")
            profile_key = (str(ref.profile_id), profile_name)
            if profile_key in seen_profiles:
                raise ValueError(
                    f"duplicate saved harness profile selection {profile_key!r}"
                )
            seen_profiles.add(profile_key)
            profile_entry = dict(raw)
            profile_entry["harness_profile"] = ref
            for trial in range(1, trials + 1):
                output.append(
                    (_AsyncSelection if async_mode else _Selection)(
                        kit, "profile", None, profile_name, trial, profile_entry
                    )
                )
            continue
        kind = _name(raw.get("harness"), "harness").lower().replace("-", "_")
        kind = {"claude": "claude_code"}.get(kind, kind)
        if kind not in {"claude_code", "opencode", "codex", "pi", "acp"}:
            raise ValueError(f"unknown harness kind {kind!r}")
        models = raw.get("models")
        if not isinstance(models, (list, tuple)) or not models:
            raise ValueError("models must be a non-empty list")
        allowed_by_kind = {
            "claude_code": {
                "harness",
                "models",
                "name",
                "executable",
                "credential_env",
                "credential_references",
                "_case_id",
                "_matrix_id",
                "_cell_id",
                "_execution_timeout",
            },
            "codex": {
                "harness",
                "models",
                "name",
                "executable",
                "credential_env",
                "credential_references",
                "_case_id",
                "_matrix_id",
                "_cell_id",
                "_execution_timeout",
            },
            "opencode": {
                "harness",
                "models",
                "name",
                "executable",
                "provider",
                "dialect",
                "credential_env",
                "credential_references",
                "_case_id",
                "_matrix_id",
                "_cell_id",
                "_execution_timeout",
            },
            "pi": {
                "harness",
                "models",
                "name",
                "executable",
                "provider",
                "credential_env",
                "credential_references",
                "_case_id",
                "_matrix_id",
                "_cell_id",
                "_execution_timeout",
            },
            "acp": {
                "harness",
                "models",
                "name",
                "manifest",
                "agent_mode_id",
                "session_config",
                "_case_id",
                "_matrix_id",
                "_cell_id",
                "_execution_timeout",
            },
        }
        allowed = allowed_by_kind[kind]
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(
                f"unsupported fields for agent selection: {sorted(unknown)[0]!r}"
            )
        if kind == "acp" and not isinstance(raw.get("manifest"), _Mapping):
            raise ValueError("ACP selection requires a runnable manifest")
        if kind == "acp":
            try:
                _validate_manifest(dict(raw["manifest"]))
            except _ManifestValidationError as exc:
                raise ValueError(f"invalid ACP manifest: {exc}") from exc
        _validate_credential_mapping(raw.get("credential_env"))
        for field in ("executable", "provider"):
            if field in raw and (
                not isinstance(raw[field], str) or not raw[field].strip()
            ):
                raise ValueError(f"{field} must be a non-empty string")
        if "dialect" in raw and raw["dialect"] not in {"auto", "legacy", "v2"}:
            raise ValueError("dialect must be one of: auto, legacy, v2")
        for model in models:
            model = _name(model, "model")
            config_name = _name(raw.get("name", kind), "name")
            key = (kind, config_name, model)
            if key in seen:
                raise ValueError(f"duplicate agent selection {key!r}")
            seen.add(key)
            for trial in range(1, trials + 1):
                output.append(
                    (_AsyncSelection if async_mode else _Selection)(
                        kit, kind, model, config_name, trial, raw
                    )
                )
    return tuple(output)
