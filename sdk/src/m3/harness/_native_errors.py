"""Bounded native diagnostics projected with a pinned launch secret registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Literal, get_args, get_origin

from pydantic import ValidationError

from ..trace.redaction import RedactionConfig, RedactionError, redact_for_api
from ..types import ErrorCode, ErrorInfo
from ._private_policy import PrivateToolCalls
from .contracts import HarnessLaunch, HarnessStartupError, HarnessTurnResult
from .observations import HarnessObservation, MetadataObservedObservation

_MESSAGE_LIMIT = 4096
_INPUT_LIMIT = 65536
_CODE_LIMIT = 128


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

    Every explicit env/header literal may be a credential regardless of its
    key. The launch resolver supplies resolved references/login values. This
    seam must receive the authoritative launch-bundle registry when that
    resolver is introduced; it cannot discover credentials a native harness
    loads independently from files or plugins.
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
    """Keep selected native scalar evidence, never an unchecked native body.

    No registry means no free text. Redact whole input before truncating so a
    credential crossing the output boundary cannot leak a prefix. Oversized
    input is omitted rather than sliced before redaction. The fixed evidence
    shape bounds both projection work and serialized output.
    """

    evidence: dict[str, Any] = {"harness": harness, "phase": phase}
    # Only bounded, known diagnostic fields may accompany native text. Never
    # carry arbitrary caller/provider mappings into a fail-closed envelope.
    supplied = details or {}
    http_status = supplied.get("http_status")
    if isinstance(http_status, int) and not isinstance(http_status, bool):
        if 100 <= http_status <= 599:
            evidence["http_status"] = http_status
    message: object = native if isinstance(native, str) else None
    native_code: object = None
    if isinstance(native, Mapping):
        message = native.get("message")
        native_code = native.get("code", native.get("name"))
        data = native.get("data")
        if not isinstance(message, str) and isinstance(data, Mapping):
            message = data.get("message")
    if isinstance(native_code, int) and not isinstance(native_code, bool):
        # JSON integers can be arbitrarily large; do not serialize unbounded
        # provider-controlled numeric evidence.
        if -(2**63) <= native_code < 2**63:
            evidence["native_code"] = native_code
    if redaction is None:
        if supplied.get("reason") == "required_mcp_failed_to_initialize":
            evidence["reason"] = "required_mcp_failed_to_initialize"
        evidence["native_diagnostic"] = "unavailable_registry"
        return NativeErrorInfo(code=code, message=summary[:512], details=evidence)
    projection: dict[str, str] = {
        "summary": summary if len(summary) <= _INPUT_LIMIT else "Native harness failed"
    }
    reason = supplied.get("reason")
    if isinstance(reason, str) and len(reason) <= _CODE_LIMIT:
        projection["reason"] = reason
    if isinstance(message, str):
        if len(message) > _INPUT_LIMIT:
            evidence["native_diagnostic"] = "oversized"
        else:
            projection["native_message"] = message
    if isinstance(native_code, str) and len(native_code) <= _INPUT_LIMIT:
        projection["native_code"] = native_code
    try:
        safe = {
            key: redact_for_api(value, config=redaction)
            for key, value in projection.items()
        }
    except RedactionError:
        evidence["native_diagnostic"] = "redaction_failed"
        return NativeErrorInfo(
            code=code, message="Native harness diagnostic unavailable", details=evidence
        )
    safe_summary = safe["summary"][:512]
    if "native_code" in safe:
        evidence["native_code"] = safe["native_code"][:_CODE_LIMIT]
    if "reason" in safe:
        evidence["reason"] = safe["reason"]
    safe_message = safe.get("native_message")
    if isinstance(safe_message, str) and safe_message:
        evidence["native_message"] = safe_message[:_MESSAGE_LIMIT]
        if len(safe_message) > _MESSAGE_LIMIT:
            evidence["native_message_truncated"] = True
        safe_summary += ": " + evidence["native_message"]
    if len(safe_summary) > _MESSAGE_LIMIT:
        evidence["message_truncated"] = True
    return NativeErrorInfo(
        code=code, message=safe_summary[:_MESSAGE_LIMIT], details=evidence
    )


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
