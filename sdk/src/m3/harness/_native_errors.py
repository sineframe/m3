"""Bounded native diagnostics projected with a pinned launch secret registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal, get_args, get_origin

from pydantic import ValidationError

from ..trace.redaction import RedactionConfig, RedactionError, redact_for_api
from ..types import ErrorCode, ErrorInfo
from ._private_policy import PrivateToolCalls
from .contracts import HarnessLaunch, HarnessStartupError, HarnessTurnResult
from .observations import HarnessObservation, MetadataObservedObservation

_MESSAGE_LIMIT = 4096
_SUMMARY_LIMIT = 512
_INPUT_LIMIT = 65536
_CODE_LIMIT = 128
_MCP_INITIALIZATION_FAILURE = "required_mcp_failed_to_initialize"


class NativeErrorInfo(ErrorInfo):
    """A native error already projected at its launch-secret boundary.

    The controller may retain this envelope without granting the same trust
    to arbitrary provider payloads in custom adapter errors.
    """


class NativeHarnessStartupError(HarnessStartupError):
    """A startup failure carrying the same safe envelope as turn failures."""

    def __init__(self, error: NativeErrorInfo) -> None:
        super().__init__(error.message, details=error.details)
        self.error = error


def launch_error_redaction(
    launch: HarnessLaunch,
    environment: Mapping[str, str],
    resolved_secrets: set[str],
) -> RedactionConfig:
    """Pin known child inputs without rereading the ambient environment.

    Every explicit env/header literal is registered regardless of its key.
    The caller supplies resolved references and login values. Credentials a
    native harness independently loads from files or plugins are absent.
    """

    values = set(resolved_secrets)
    values.update(value for value in environment.values() if value)
    for server in launch.configurations:
        for value in (*server.environment.values(), *server.headers.values()):
            if isinstance(value, str) and value:
                values.add(value)
        if isinstance(server.endpoint, str) and server.endpoint:
            values.add(server.endpoint)
    return RedactionConfig(secrets=frozenset(values), include_environment=False)


def _bounded_integer(value: object, minimum: int, maximum: int) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        if minimum <= value <= maximum:
            return value
    return None


def _bounded_text(value: object, limit: int) -> str | None:
    return value if isinstance(value, str) and len(value) <= limit else None


@dataclass(frozen=True, repr=False)
class _NativeDiagnostic:
    """Selected provider scalars; never retain the provider's entire body."""

    summary: str
    message: str | None
    code: str | int | None
    reason: str | None
    oversized: bool

    @classmethod
    def parse(cls, native: object, summary: str, reason: object) -> _NativeDiagnostic:
        message: object = native
        code: object = None
        if isinstance(native, Mapping):
            message = native.get("message")
            code = native.get("code", native.get("name"))
            data = native.get("data")
            if not isinstance(message, str) and isinstance(data, Mapping):
                message = data.get("message")
        return cls(
            summary=summary,
            message=_bounded_text(message, _INPUT_LIMIT),
            code=(
                _bounded_text(code, _INPUT_LIMIT)
                if isinstance(code, str)
                else _bounded_integer(code, -(2**63), 2**63 - 1)
            ),
            reason=_bounded_text(reason, _CODE_LIMIT),
            oversized=isinstance(message, str) and len(message) > _INPUT_LIMIT,
        )

    def redacted(self, config: RedactionConfig) -> _NativeDiagnostic:
        # Redact complete bounded strings before applying output limits.
        # Slicing first could expose a credential that crosses the boundary.
        def redact(value: str | None) -> str | None:
            return redact_for_api(value, config=config) if value is not None else None

        summary = _bounded_text(self.summary, _INPUT_LIMIT)
        if summary is None:
            summary = "Native harness failed"
        return replace(
            self,
            summary=redact_for_api(summary, config=config),
            message=redact(self.message),
            code=redact(self.code) if isinstance(self.code, str) else self.code,
            reason=redact(self.reason),
        )


def _diagnostic_evidence(
    diagnostic: _NativeDiagnostic,
    harness: str,
    phase: str,
    http_status: object,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {"harness": harness, "phase": phase}
    status = _bounded_integer(http_status, 100, 599)
    if status is not None:
        evidence["http_status"] = status
    if isinstance(diagnostic.code, int):
        evidence["native_code"] = diagnostic.code
    return evidence


def _assemble_native_error(
    diagnostic: _NativeDiagnostic, code: ErrorCode, evidence: dict[str, Any]
) -> NativeErrorInfo:
    message = diagnostic.summary[:_SUMMARY_LIMIT]
    if isinstance(diagnostic.code, str):
        evidence["native_code"] = diagnostic.code[:_CODE_LIMIT]
    if diagnostic.reason is not None:
        evidence["reason"] = diagnostic.reason
    if diagnostic.message:
        native_message = diagnostic.message[:_MESSAGE_LIMIT]
        evidence["native_message"] = native_message
        if len(diagnostic.message) > _MESSAGE_LIMIT:
            evidence["native_message_truncated"] = True
        message += ": " + native_message
    if len(message) > _MESSAGE_LIMIT:
        evidence["message_truncated"] = True
    return NativeErrorInfo(
        code=code, message=message[:_MESSAGE_LIMIT], details=evidence
    )


def native_error_info(
    native: object,
    *,
    summary: str,
    harness: str,
    phase: str,
    redaction: RedactionConfig | None,
    code: ErrorCode = ErrorCode.TRANSPORT_ERROR,
    details: Mapping[str, Any] | None = None,
) -> NativeErrorInfo:
    """Parse selected native fields, redact them, then bound the public error."""

    supplied = details or {}
    diagnostic = _NativeDiagnostic.parse(native, summary, supplied.get("reason"))
    evidence = _diagnostic_evidence(
        diagnostic, harness, phase, supplied.get("http_status")
    )
    if redaction is None:
        if diagnostic.reason == _MCP_INITIALIZATION_FAILURE:
            evidence["reason"] = _MCP_INITIALIZATION_FAILURE
        evidence["native_diagnostic"] = "unavailable_registry"
        return NativeErrorInfo(
            code=code, message=summary[:_SUMMARY_LIMIT], details=evidence
        )
    if diagnostic.oversized:
        evidence["native_diagnostic"] = "oversized"
    try:
        diagnostic = diagnostic.redacted(redaction)
    except RedactionError:
        evidence["native_diagnostic"] = "redaction_failed"
        return NativeErrorInfo(
            code=code, message="Native harness diagnostic unavailable", details=evidence
        )
    return _assemble_native_error(diagnostic, code, evidence)


def project_native_observation(
    observation: HarnessObservation, redaction: RedactionConfig | None, index: int
) -> HarnessObservation:
    """Project native payloads while keeping M3's schema discriminants intact."""

    # Optional absent fields are evidence, particularly a missing tool result
    # versus an explicitly returned JSON null. Preserve that distinction.
    data = observation.model_dump(mode="python", exclude_unset=True)
    # These fields describe M3's typed schema or decoder decisions, not
    # provider text. Native identifiers and payloads are projected separately.
    structural = {"turn_sequence", "wall_time", "monotonic_offset_ms"}
    for name, field in type(observation).model_fields.items():
        annotation = field.annotation
        alternatives = get_args(annotation)
        if get_origin(annotation) is Literal or (
            alternatives
            and all(
                option is type(None) or get_origin(option) is Literal
                for option in alternatives
            )
        ):
            structural.add(name)
    try:
        if redaction is None:
            raise RedactionError("native projection registry is unavailable")
        safe = {
            key: redact_for_api(value, config=redaction)
            for key, value in data.items()
            if key not in structural
        }
        if observation.raw_evidence is not None:
            raw = observation.raw_evidence
            safe["raw_evidence"] = {
                "encoding": raw.encoding,
                "content": redact_for_api(raw.content, config=redaction),
                "media_type": redact_for_api(raw.media_type, config=redaction),
            }
        return type(observation).model_validate({**data, **safe})
    except (RedactionError, ValidationError):
        # Redaction can lengthen a bounded native identifier or invalidate a
        # provider-supplied media type. Publish fixed evidence instead of the
        # unsafe original or a validation exception containing its value.
        return MetadataObservedObservation(
            observation_id=f"native-projection-{observation.turn_sequence}-{index}",
            harness_kind="native",
            turn_sequence=observation.turn_sequence,
            wall_time=observation.wall_time,
            monotonic_offset_ms=observation.monotonic_offset_ms,
            name="native_evidence",
            value={"capture": "unavailable", "reason": "redaction_failed"},
        )


def project_native_turn(
    result: HarnessTurnResult, redaction: RedactionConfig | None
) -> HarnessTurnResult:
    """Project every public native return, including early/failed turn exits."""

    from ..types import TurnResponse

    private = result._private_tool_calls or PrivateToolCalls(
        result.tool_calls, redaction
    )
    evidence = result.turn_evidence
    if evidence is not None:
        evidence = evidence.model_copy(
            update={
                "observations": tuple(
                    project_native_observation(item, redaction, index)
                    for index, item in enumerate(evidence.observations)
                )
            }
        )
    try:
        if redaction is None:
            raise RedactionError("native projection registry is unavailable")
        response = None
        if result.response is not None:
            blocks = []
            for block in result.response.content:
                payload = block.model_dump(mode="python", exclude_unset=True)
                # Content kinds belong to M3's schema. Payload strings and
                # opaque provider mappings remain native, including any
                # nested keys named 'kind'.
                payload.pop("kind", None)
                blocks.append(
                    type(block).model_validate(
                        {
                            "kind": block.kind,
                            **{
                                key: redact_for_api(value, config=redaction)
                                for key, value in payload.items()
                            },
                        }
                    )
                )
            response = TurnResponse(
                content=tuple(blocks),
                metadata=redact_for_api(result.response.metadata, config=redaction),
            )
        safe_calls = []
        for call in result.tool_calls:
            payload = dict(call)
            provenance = payload.get("provenance")
            classification = (
                provenance
                if isinstance(provenance, str)
                and provenance in {"mcp", "harness_builtin", "unknown"}
                else None
            )
            if classification is not None:
                payload.pop("provenance")
            safe_call = {key: private.project(value) for key, value in payload.items()}
            if classification is not None:
                safe_call["provenance"] = classification
            safe_calls.append(safe_call)
        calls = tuple(safe_calls)
        receipt = redact_for_api(result.evidence, config=redaction)
    except (RedactionError, ValidationError):
        if evidence is not None:
            evidence = evidence.model_copy(
                update={
                    "status": "failed",
                    "limitations": tuple(
                        dict.fromkeys((*evidence.limitations, "capture_incomplete"))
                    ),
                }
            )
        return replace(
            result,
            status="failed",
            response=None,
            tool_calls=(),
            _private_tool_calls=private,
            turn_evidence=evidence,
            evidence={},
            error=native_error_info(
                None,
                summary="Native turn projection failed",
                harness="native",
                phase="turn/projection",
                redaction=redaction,
            ),
        )
    return replace(
        result,
        response=response,
        tool_calls=calls,
        _private_tool_calls=private,
        turn_evidence=evidence,
        evidence=receipt,
    )
