"""Protocol-neutral MCP MRTR normalization used by owned clients.

This module deliberately contains no transport or lifecycle code.  Direct
clients and the Pi bridge use the same request/response normalization so a
retry has one protocol contract at either boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from jsonschema import (  # type: ignore[import-untyped]
    Draft202012Validator,
    SchemaError,
    ValidationError,
)
from mcp import types as _mcp_types
from mcp.client.session import ClientRequestContext
from pydantic import TypeAdapter
from referencing import Registry
from referencing.exceptions import Unresolvable

from .elicitation import (
    ElicitationRequest,
    ElicitationResponse,
    FormElicitationRequest,
    UrlElicitationRequest,
)
from .errors import ElicitationExpectationError, ProtocolError, UnsupportedFeature


@dataclass(frozen=True, slots=True)
class InputRequired:
    input_requests: Mapping[str, Any] | None
    request_state: str | None


class InputRequiredLike(Protocol):
    """Structural input-required view shared by direct and bridge results."""

    @property
    def input_requests(self) -> Mapping[str, Any] | None: ...

    @property
    def request_state(self) -> str | None: ...


def attribute(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        if name in value:
            return value[name]
        camel = name.split("_")[0] + "".join(
            part.title() for part in name.split("_")[1:]
        )
        return value.get(camel, default)
    return getattr(value, name, default)


def plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain_json(item) for item in value]
    return value


def dump(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return cast(dict[str, Any], plain_json(value))
    method = getattr(value, "model_dump", None)
    if callable(method):
        result = method(mode="json", by_alias=True)
        if isinstance(result, Mapping):
            return cast(dict[str, Any], plain_json(result))
    return {}


def input_required(value: Any) -> InputRequired | None:
    if (
        attribute(value, "result_type") != "input_required"
        and attribute(value, "resultType") != "input_required"
    ):
        return None
    requests = attribute(value, "input_requests")
    if requests is None:
        requests = attribute(value, "inputRequests")
    plain_requests = (
        {str(key): dump(item) for key, item in requests.items()}
        if isinstance(requests, Mapping)
        else None
    )
    state = attribute(value, "request_state", attribute(value, "requestState"))
    if state is not None and not isinstance(state, str):
        raise ProtocolError(
            "input-required result has malformed requestState",
            details={"field": "requestState"},
        )
    return InputRequired(plain_requests, state)


_REQUEST_ADAPTER: TypeAdapter[_mcp_types.InputRequest] = TypeAdapter(
    _mcp_types.InputRequest
)


def normalize_requests(
    required: InputRequiredLike,
    *,
    session: Any,
    operation_kind: Literal["tool", "prompt", "resource"],
    operation_name: str,
    server_name: str | None = None,
) -> tuple[dict[str, ElicitationRequest], dict[str, _mcp_types.InputRequest]]:
    elicitation: dict[str, ElicitationRequest] = {}
    other: dict[str, _mcp_types.InputRequest] = {}
    for key, raw_request in (required.input_requests or {}).items():
        try:
            request = _REQUEST_ADAPTER.validate_python(raw_request)
        except Exception as error:
            raise ElicitationExpectationError(
                "input-required result contains an invalid input request",
                details={"request_key": str(key)},
            ) from error
        if isinstance(request, _mcp_types.ElicitRequest):
            params = request.params
            # Read the optional URL identifier from the typed params object.
            # Serializing the entire params model is unsafe: form schemas may
            # contain MCP's immutable mapping implementation, which Pydantic's
            # JSON serializer intentionally does not know how to encode.
            raw_id = getattr(params, "elicitation_id", None)
            info = getattr(getattr(session, "server_info", None), "name", None)
            server = (
                server_name if isinstance(server_name, str) and server_name else None
            )
            if server is None and isinstance(info, str) and info:
                server = info
            common: dict[str, Any] = {
                "request_key": str(key),
                "message": params.message,
                "meta": plain_json(params.meta) if params.meta is not None else None,
                "task": dump(params.task) if params.task is not None else None,
                "server": server,
                "operation_kind": operation_kind,
                "operation_name": operation_name,
            }
            if isinstance(params, _mcp_types.ElicitRequestFormParams):
                elicitation[str(key)] = FormElicitationRequest(
                    **common, requested_schema=plain_json(params.requested_schema)
                )
            else:
                elicitation[str(key)] = UrlElicitationRequest(
                    **common,
                    url=params.url,
                    elicitation_id=raw_id
                    if isinstance(raw_id, str)
                    else params.elicitation_id,
                )
        elif isinstance(
            request, (_mcp_types.CreateMessageRequest, _mcp_types.ListRootsRequest)
        ):
            other[str(key)] = request
        else:
            raise UnsupportedFeature(
                "MCP input-required result contains an unsupported request variant",
                details={"request_key": str(key)},
            )
    return elicitation, other


def validate_response(
    request: ElicitationRequest, response: ElicitationResponse
) -> None:
    if not isinstance(request, FormElicitationRequest) or response.action != "accept":
        return
    if request.requested_schema is None:
        return
    try:
        validator = Draft202012Validator(
            dict(request.requested_schema), registry=Registry()
        )
        validator.validate(dict(response.content or {}))
    except (SchemaError, ValidationError, Unresolvable) as error:
        raise ElicitationExpectationError(
            "elicitation response does not match the requested schema",
            details={"request_key": request.request_key},
        ) from error


async def resolve_other(
    session: Any,
    requests: Mapping[str, _mcp_types.InputRequest],
) -> dict[str, _mcp_types.InputResponse]:
    responses: dict[str, _mcp_types.InputResponse] = {}
    if not requests:
        return responses
    dispatch = getattr(session, "dispatch_input_request", None)
    if not callable(dispatch):
        raise UnsupportedFeature("MCP input-required request handler is unavailable")
    for key, request in requests.items():
        context = ClientRequestContext(
            session=cast(Any, session),
            request_id=f"m3-mrtr-{uuid4().hex}",
            meta=getattr(getattr(request, "params", None), "meta", None),
        )
        response = await dispatch(context, request)
        if isinstance(response, _mcp_types.ErrorData):
            raise UnsupportedFeature(
                "configured MCP input handler declined an input-required request",
                details={"request_key": key},
            )
        responses[key] = response
    return responses


__all__ = [
    "InputRequired",
    "InputRequiredLike",
    "attribute",
    "dump",
    "input_required",
    "normalize_requests",
    "plain_json",
    "resolve_other",
    "validate_response",
]
