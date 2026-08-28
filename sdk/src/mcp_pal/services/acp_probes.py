"""Typed ACP protocol/full probe contracts and lifecycle execution."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, TypeAlias, cast

from pydantic import Field, JsonValue as PydanticJsonValue, field_validator, model_validator

from ..trace.redaction import RedactionConfig, redact_for_persistence
from ..types import FrozenModel

JsonValue: TypeAlias = PydanticJsonValue
JsonObject: TypeAlias = Mapping[str, JsonValue]

_MAX_PROBE_BYTES = 64 * 1024
_TERMINAL = frozenset({"verified", "failed", "timed_out", "cancelled"})


class ACPProbeKind(str, Enum):
    PROTOCOL = "protocol"
    FULL = "full"


class ACPProbeStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    VERIFIED = "verified"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


def _canonical(value: Any, *, depth: int = 0) -> JsonValue:
    if depth > 32:
        raise ValueError("session configuration is too deeply nested")
    if isinstance(value, Mapping):
        if len(value) > 256:
            raise ValueError("session configuration contains too many keys")
        converted: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 256:
                raise ValueError("session configuration keys must be bounded strings")
            if key in converted:
                raise ValueError("session configuration keys must be unique")
            converted[key] = _canonical(item, depth=depth + 1)
        return {key: converted[key] for key in sorted(converted)}
    if isinstance(value, (list, tuple)):
        if len(value) > 1024:
            raise ValueError("session configuration contains too many values")
        return [_canonical(item, depth=depth + 1) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > 16 * 1024:
            raise ValueError("session configuration string is too long")
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("session configuration must contain finite values")
        return value
    raise ValueError("session configuration contains an unsupported value")


class ACPProbeDimension(FrozenModel):
    """Exact profile/revision and ACP request dimensions."""

    profile_id: str = Field(min_length=1, max_length=256)
    revision_id: str = Field(min_length=1, max_length=256)
    probe_type: ACPProbeKind
    transport: str = Field(default="stdio", min_length=1, max_length=32)
    agent_mode_id: str | None = Field(default=None, max_length=256)
    session_config: Mapping[str, JsonValue] = Field(default_factory=dict)

    @field_validator("transport", mode="before")
    @classmethod
    def _transport(cls, value: Any) -> str:
        normalized = str(value).strip().lower()
        if normalized not in {"stdio", "http", "sse"}:
            raise ValueError("unsupported ACP transport")
        return normalized

    @field_validator("agent_mode_id")
    @classmethod
    def _mode(cls, value: str | None) -> str | None:
        return value.strip() if value is not None and value.strip() else None

    @field_validator("session_config", mode="before")
    @classmethod
    def _config(cls, value: Mapping[str, Any] | None) -> Mapping[str, JsonValue]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("session_config must be an object")
        result = _canonical(value)
        if not isinstance(result, dict):
            raise ValueError("session_config must be an object")
        return result

    @model_validator(mode="before")
    @classmethod
    def _protocol_dimensions(cls, value: Any) -> Any:
        # initialize/session/new has no mode or config: one neutral protocol
        # dimension prevents meaningless duplicate records.
        if isinstance(value, Mapping):
            value = dict(value)
            if "probe_type" not in value and "kind" in value:
                value["probe_type"] = value.pop("kind")
            if "agent_mode_id" not in value and "mode_id" in value:
                value["agent_mode_id"] = value.pop("mode_id")
        kind = value.get("probe_type") if isinstance(value, Mapping) else None
        kind_value = kind.value if isinstance(kind, ACPProbeKind) else str(kind or "")
        if isinstance(value, Mapping) and kind_value == ACPProbeKind.PROTOCOL.value:
            normalized = dict(value)
            normalized["agent_mode_id"] = None
            normalized["session_config"] = {}
            normalized["transport"] = "stdio"
            return normalized
        return value

    @property
    def kind(self) -> ACPProbeKind:
        return self.probe_type

    @property
    def mode_id(self) -> str | None:
        return self.agent_mode_id

    @property
    def canonical_session_config(self) -> str:
        return json.dumps(_canonical(self.session_config), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @property
    def canonical_key(self) -> str:
        value = "|".join((self.profile_id, self.revision_id, self.probe_type.value, self.transport, self.agent_mode_id or "", self.canonical_session_config))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ACPProbeRequest(ACPProbeDimension):
    """Serializable probe request with a bounded timeout."""

    timeout_seconds: float = Field(default=30.0, gt=0, le=3600)


class ACPAgentIdentity(FrozenModel):
    name: str | None = None
    version: str | None = None
    metadata: Mapping[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                "name": value.get("name"),
                "version": value.get("version"),
                "metadata": {str(key): item for key, item in value.items() if key not in {"name", "version"}},
            }
        return value


class ACPAgentMode(FrozenModel):
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)


class ACPProbeResult(ACPProbeDimension):
    """One terminal or in-flight probe observation."""

    id: str = Field(default_factory=lambda: "acp-probe-" + uuid.uuid4().hex, min_length=1, max_length=256)
    status: ACPProbeStatus
    agent_capabilities: JsonObject = Field(default_factory=dict)
    config_options: tuple[JsonObject, ...] = ()
    evidence: JsonObject = Field(default_factory=dict)
    diagnostics: str | None = Field(default=None, max_length=65536)
    error: str | None = Field(default=None, max_length=2048)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0)

    agent_identity: ACPAgentIdentity | None = None
    agent_modes: tuple[ACPAgentMode, ...] = ()
    current_agent_mode_id: str | None = Field(default=None, max_length=256)

    @field_validator("agent_modes")
    @classmethod
    def _bounded_modes(cls, value: tuple[ACPAgentMode, ...]) -> tuple[ACPAgentMode, ...]:
        if len(value) > 256:
            raise ValueError("ACP agent modes exceed the safe bound")
        return value

    @model_validator(mode="after")
    def _terminal_times(self) -> "ACPProbeResult":
        if self.status.value in _TERMINAL and self.finished_at is None:
            raise ValueError("terminal ACP probe requires finished_at")
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            raise ValueError("probe timestamps are out of order")
        return self


class ACPProbeHistory(FrozenModel):
    items: tuple[ACPProbeResult, ...] = ()

    @property
    def latest(self) -> ACPProbeResult | None:
        return self.items[0] if self.items else None


class ACPProbeStore(Protocol):
    def save_acp_probe(self, result: ACPProbeResult) -> ACPProbeResult: ...
    def get_acp_probe(self, probe_id: str) -> ACPProbeResult | None: ...
    def list_acp_probes(self, dimension: ACPProbeDimension | None = None, *, include_inflight: bool = True) -> tuple[ACPProbeResult, ...]: ...
    def latest_acp_probe(self, dimension: ACPProbeDimension) -> ACPProbeResult | None: ...


def _bounded(value: JsonValue) -> JsonValue:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return {"truncated": True, "reason": "non_json_evidence"}
    size = len(encoded.encode("utf-8"))
    return {"truncated": True, "bytes": size} if size > _MAX_PROBE_BYTES else value


def _contains_secret(value: object, secrets: frozenset[str]) -> bool:
    if isinstance(value, str):
        return any(secret and secret in value for secret in secrets)
    if isinstance(value, Mapping):
        return any(_contains_secret(key, secrets) or _contains_secret(item, secrets) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret(item, secrets) for item in value)
    return False


def redacted_probe(result: ACPProbeResult, config: RedactionConfig | None = None) -> ACPProbeResult:
    """Project hostile runner output before it reaches durable storage."""
    cfg = config or RedactionConfig.from_environment()
    if _contains_secret(result.session_config, cfg.secrets):
        raise ValueError("ACP session configuration contains a secret")
    # JSON mode thaws FrozenModel's nested mappings before bounded traversal.
    value = result.model_dump(mode="json")
    truncation: dict[str, JsonValue] = {}
    for field in ("agent_identity", "agent_capabilities", "evidence"):
        try:
            projected = redact_for_persistence(value[field], config=cfg, path=f"$.acp_probe.{field}")
        except Exception:
            projected = {"truncated": True, "reason": "unsafe_evidence"}
        bounded = _bounded(cast(JsonValue, projected))
        if isinstance(bounded, dict) and bounded.get("truncated"):
            truncation[field] = bounded
            value[field] = {} if field != "agent_identity" else None
        else:
            value[field] = bounded
    try:
        projected_options = redact_for_persistence(value["config_options"], config=cfg, path="$.acp_probe.config_options")
    except Exception:
        projected_options = []
        truncation["config_options"] = {"truncated": True, "reason": "unsafe_evidence"}
    bounded_options = _bounded(cast(JsonValue, projected_options))
    if isinstance(bounded_options, dict) and bounded_options.get("truncated"):
        value["config_options"] = []
        truncation["config_options"] = bounded_options
    else:
        value["config_options"] = bounded_options
    if truncation:
        evidence_value = value.get("evidence")
        evidence_map = dict(evidence_value) if isinstance(evidence_value, Mapping) else {}
        evidence_map["truncation"] = truncation
        value["evidence"] = _bounded(cast(JsonValue, evidence_map))
    for field, limit in (("diagnostics", 65536), ("error", 2048)):
        if value[field] is not None:
            try:
                projected = redact_for_persistence(value[field], config=cfg, path=f"$.acp_probe.{field}")
            except Exception:
                projected = "probe diagnostic was not safely representable"
            value[field] = projected[:limit] if isinstance(projected, str) else projected
    return ACPProbeResult.model_validate(value)


Runner = Callable[[ACPProbeRequest], Mapping[str, JsonValue] | Awaitable[Mapping[str, JsonValue]]]


async def run_acp_probe(
    request: ACPProbeRequest,
    runner: Runner,
    *,
    config: RedactionConfig | None = None,
    probe_id: str | None = None,
    created_at: datetime | None = None,
) -> ACPProbeResult:
    """Execute an injected runner with bounded, cancellation-safe state."""
    started = datetime.now(timezone.utc)
    monotonic = time.monotonic()
    base = request.model_dump(mode="python", exclude={"timeout_seconds"})
    base.update(id=probe_id or "acp-probe-" + uuid.uuid4().hex, status=ACPProbeStatus.RUNNING, started_at=started, created_at=created_at or started)
    try:
        is_async = inspect.iscoroutinefunction(runner) or inspect.iscoroutinefunction(getattr(runner, "__call__", None))
        if is_async:
            value = await asyncio.wait_for(cast(Awaitable[Mapping[str, JsonValue]], runner(request)), timeout=request.timeout_seconds)
        else:
            sync_runner = cast(Callable[[ACPProbeRequest], Mapping[str, JsonValue]], runner)
            value = await asyncio.wait_for(asyncio.to_thread(sync_runner, request), timeout=request.timeout_seconds)
            if inspect.isawaitable(value):
                value = await asyncio.wait_for(cast(Awaitable[Mapping[str, JsonValue]], value), timeout=request.timeout_seconds)
        if not isinstance(value, Mapping):
            raise ValueError("probe runner returned an invalid result")
        allowed = ("status", "agent_identity", "agent_capabilities", "agent_modes", "current_agent_mode_id", "config_options", "evidence", "diagnostics", "error")
        payload = {key: value[key] for key in allowed if key in value}
        raw_status = payload.pop("status", "failed")
        status = raw_status if isinstance(raw_status, ACPProbeStatus) else (ACPProbeStatus(str(raw_status)) if str(raw_status) in _TERMINAL else ACPProbeStatus.FAILED)
        if status is ACPProbeStatus.FAILED and str(payload.get("error", "")).lower() in {"timed_out", "timeout", "timed out"}:
            status = ACPProbeStatus.TIMED_OUT
        finished = datetime.now(timezone.utc)
        base.update(payload, status=status, finished_at=finished, duration_ms=(time.monotonic() - monotonic) * 1000)
    except asyncio.TimeoutError:
        base.update(status=ACPProbeStatus.TIMED_OUT, error="probe timed out", finished_at=datetime.now(timezone.utc), duration_ms=(time.monotonic() - monotonic) * 1000)
    except Exception:
        base.update(status=ACPProbeStatus.FAILED, error="probe failed", finished_at=datetime.now(timezone.utc), duration_ms=(time.monotonic() - monotonic) * 1000)
    try:
        candidate = ACPProbeResult.model_validate(base)
    except Exception:
        # Hostile runner payloads must become a bounded failed observation,
        # not escape the lifecycle boundary or reach persistence raw.
        safe_base = request.model_dump(mode="python", exclude={"timeout_seconds"})
        safe_base.update(
            id=base["id"], created_at=base["created_at"], started_at=base["started_at"],
            status=ACPProbeStatus.FAILED, finished_at=datetime.now(timezone.utc),
            duration_ms=(time.monotonic() - monotonic) * 1000, error="probe result was invalid",
        )
        candidate = ACPProbeResult.model_validate(safe_base)
    return redacted_probe(candidate, config)


__all__ = [
    "ACPAgentIdentity", "ACPAgentMode", "ACPProbeDimension", "ACPProbeHistory", "ACPProbeKind",
    "ACPProbeRequest", "ACPProbeResult", "ACPProbeStatus", "ACPProbeStore",
    "JsonObject", "JsonValue", "Runner", "redacted_probe", "run_acp_probe",
]
