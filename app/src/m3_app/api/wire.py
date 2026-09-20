"""Product-neutral wire mapping for the application-owned v2 API.

The SDK keeps its ``m3.*`` identities internally.  This module is the one
transport boundary where those reserved, product-owned values are translated
to neutral API values.  Mapping is deliberately keyed by the surrounding
field/path; arbitrary strings, user metadata values, prompts, and raw
evidence are never searched or rewritten.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any, cast

_SCHEMA_INTERNAL_TO_WIRE = {
    "m3.trace_view": "trace_view",
    "m3.event": "event",
    "m3.harness.v1": "harness.v1",
}

_EVALUATOR_INTERNAL_TO_WIRE = {
    "m3.execution.completed.v1": "execution.completed.v1",
    "m3.tool_call.succeeded.v1": "tool_call.succeeded.v1",
    "m3.output.has_text.v1": "output.has_text.v1",
}

_RESERVED_METADATA_INTERNAL_TO_WIRE = {
    "m3.run_id": "run_id",
    "m3.worker_id": "worker_id",
    "m3.case_id": "case_id",
}

_OWNED_EVENT_SOURCES = {
    "m3",
    "m3.direct.stream",
    "m3.direct.transport",
    "m3.execution.direct",
    "m3.recorder",
    "m3.workspace",
    "m3.harness",
    "m3.server_group",
    "m3.capture",
    "m3.agent.adapter",
}


def _mapped_exact(value: Any, mapping: Mapping[str, str], *, reverse: bool) -> Any:
    if not isinstance(value, str):
        return value
    if reverse:
        for internal, neutral in mapping.items():
            if value == neutral:
                return internal
        return value
    return mapping.get(value, value)


def _schema_id(value: Any, *, reverse: bool = False) -> Any:
    return _mapped_exact(value, _SCHEMA_INTERNAL_TO_WIRE, reverse=reverse)


def _evaluator_name(value: Any, *, reverse: bool = False) -> Any:
    mapped = _mapped_exact(value, _EVALUATOR_INTERNAL_TO_WIRE, reverse=reverse)
    if not isinstance(value, str):
        return mapped
    if reverse:
        if value.startswith("matcher.") and value.endswith(".v1"):
            return "m3." + value
        return mapped
    if value.startswith("m3.matcher.") and value.endswith(".v1"):
        return value.removeprefix("m3.")
    return mapped


def _metadata_key(value: Any, *, reverse: bool = False) -> Any:
    if not isinstance(value, str):
        return value
    mapped = _mapped_exact(value, _RESERVED_METADATA_INTERNAL_TO_WIRE, reverse=reverse)
    if reverse:
        if value.startswith("matrix."):
            return "m3." + value
        return mapped
    if value.startswith("m3.matrix."):
        return value.removeprefix("m3.")
    return mapped


def _source(value: Any, *, reverse: bool = False) -> Any:
    if not isinstance(value, str):
        return value
    if reverse:
        if value == "internal":
            return "m3"
        for internal in _OWNED_EVENT_SOURCES:
            if value == internal.removeprefix("m3."):
                return internal
        return value
    if value == "m3":
        return "internal"
    return value.removeprefix("m3.") if value in _OWNED_EVENT_SOURCES else value


def _metadata(value: Mapping[Any, Any], *, reverse: bool) -> dict[Any, Any]:
    mapped_keys = [_metadata_key(key, reverse=reverse) for key in value]
    if len(set(mapped_keys)) != len(mapped_keys):
        # Both a reserved and a user-owned spelling can be present. Mapping
        # either onto the other would silently discard a caller's value.
        return deepcopy(dict(value))
    result: dict[Any, Any] = {}
    for key, item in value.items():
        mapped_key = _metadata_key(key, reverse=reverse)
        # Metadata values are user-owned and must remain byte-for-byte values;
        # only reserved metadata keys cross this boundary.
        result[mapped_key] = deepcopy(item)
    return result


def _aggregate_label(value: Any, *, reverse: bool) -> Any:
    if not isinstance(value, str) or not value.startswith("metadata."):
        return value
    prefix, suffix = value.split(".", 1)
    return prefix + "." + _metadata_key(suffix, reverse=reverse)


def _path_contains(path: tuple[str, ...], name: str) -> bool:
    return any(name in part.strip("/").split("/") for part in path)


_OPAQUE_FIELDS = frozenset(
    {
        "arguments",
        "agent_capabilities",
        "config_options",
        "context",
        "content",
        "details",
        "evidence",
        "input",
        "message",
        "mcp_json",
        "output",
        "outputs",
        "payload",
        "policy",
        "prompt",
        "raw",
        "result",
        "results",
        "session_config",
        "stderr",
        "stdout",
        "structured_content",
        "tool_arguments",
        "tool_result",
        "value",
    }
)


def _manifest(value: Mapping[Any, Any], *, reverse: bool) -> dict[Any, Any]:
    """Map only the typed manifest version; args/env are user-owned data."""

    result = {key: deepcopy(item) for key, item in value.items()}
    if "schema_version" in result:
        result["schema_version"] = _schema_id(result["schema_version"], reverse=reverse)
    return result


def _walk(value: Any, *, path: tuple[str, ...], reverse: bool) -> Any:
    if isinstance(value, Mapping):
        result: dict[Any, Any] = {}
        event_object = {str(key) for key in value} >= {
            "event_id",
            "sequence",
            "kind",
        }
        for key, item in value.items():
            name = str(key)
            child_path = (*path, name)
            if name == "metadata" and isinstance(item, Mapping):
                result[key] = _metadata(item, reverse=reverse)
            elif (
                name == "evaluation_stats"
                and isinstance(item, Mapping)
                and _path_contains(path, "feedback")
            ):
                mapped_names = [
                    _evaluator_name(evaluator, reverse=reverse) for evaluator in item
                ]
                result[key] = (
                    deepcopy(dict(item))
                    if len(set(mapped_names)) != len(mapped_names)
                    else {
                        _evaluator_name(evaluator, reverse=reverse): deepcopy(stats)
                        for evaluator, stats in item.items()
                    }
                )
            elif (
                name == "evaluator"
                and _path_contains(path, "feedback")
                and any(part in {"failures", "evaluation_changes"} for part in path)
            ):
                result[key] = _evaluator_name(item, reverse=reverse)
            elif name == "manifest" and isinstance(item, Mapping):
                result[key] = _manifest(item, reverse=reverse)
            elif (
                name == "value"
                and isinstance(item, Mapping)
                and "manifest" in item
                and _path_contains(path, "revisions")
            ):
                result[key] = _walk(item, path=child_path, reverse=reverse)
            elif name in _OPAQUE_FIELDS:
                # These fields may be protocol or user bytes represented as
                # JSON. Never reinterpret identifiers inside them.
                result[key] = deepcopy(item)
            elif name == "schema" and (event_object or _path_contains(path, "events")):
                result[key] = _schema_id(item, reverse=reverse)
            elif name == "schema_id" and _path_contains(path, "trace"):
                result[key] = _schema_id(item, reverse=reverse)
            elif name == "name" and any(
                part in {"evaluation", "evaluations"} for part in path
            ):
                result[key] = _evaluator_name(item, reverse=reverse)
            elif name == "source" and "provenance" in path:
                result[key] = _source(item, reverse=reverse)
            elif (
                name in {"group_by", "evaluators"}
                and isinstance(item, Sequence)
                and not isinstance(item, (str, bytes, bytearray))
            ):
                result[key] = [_aggregate_label(part, reverse=reverse) for part in item]
            elif name == "filters" and isinstance(item, Mapping):
                result[key] = _walk_filters(item, reverse=reverse)
            elif name == "key" and isinstance(item, Mapping):
                result[key] = _walk_group_key(item, reverse=reverse)
            else:
                result[key] = _walk(item, path=child_path, reverse=reverse)
        return result
    if isinstance(value, list):
        return [_walk(item, path=path, reverse=reverse) for item in value]
    if isinstance(value, tuple):
        return [_walk(item, path=path, reverse=reverse) for item in value]
    return value


def _walk_filters(value: Mapping[Any, Any], *, reverse: bool) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key, item in value.items():
        mapped_key = str(key)
        if mapped_key.startswith("metadata."):
            prefix, suffix = mapped_key.split(".", 1)
            mapped_key = prefix + "." + _metadata_key(suffix, reverse=reverse)
        if (
            mapped_key == "evaluator"
            and isinstance(item, Sequence)
            and not isinstance(item, (str, bytes, bytearray))
        ):
            result[mapped_key] = [
                _evaluator_name(part, reverse=reverse) for part in item
            ]
        elif mapped_key == "evaluator":
            result[mapped_key] = _evaluator_name(item, reverse=reverse)
        else:
            result[mapped_key] = deepcopy(item)
    return result


def _walk_group_key(value: Mapping[Any, Any], *, reverse: bool) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key, item in value.items():
        mapped_key = _aggregate_label(key, reverse=reverse)
        result[mapped_key] = (
            _evaluator_name(item, reverse=reverse) if key == "evaluator" else item
        )
    return result


def internalize_request(path: str, payload: Any) -> Any:
    """Translate neutral v2 JSON request values to SDK-internal values."""

    return _walk(payload, path=(path,), reverse=True)


def neutralize_response(path: str, payload: Any) -> Any:
    """Translate SDK response values to the neutral v2 wire contract."""

    return _walk(payload, path=(path,), reverse=False)


def neutralize_openapi(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Neutralize product-owned constants in an OpenAPI document."""

    output = deepcopy(dict(schema))
    info = output.get("info")
    if isinstance(info, dict):
        info["title"] = "Test Results API"
        # Keep the access boundary visible in the generated public document.
        # This function neutralizes product-owned values; it must not discard
        # the application-level description supplied by FastAPI.
        info.setdefault(
            "description",
            "Local, unauthenticated API for test execution results. Bind this service to loopback.",
        )

    def property_name(path: tuple[str, ...]) -> str | None:
        for index in range(len(path) - 2, -1, -1):
            if path[index] == "properties" and index + 1 < len(path):
                return path[index + 1]
        return None

    def visit(value: Any, *, key: str | None = None, path: tuple[str, ...] = ()) -> Any:
        if isinstance(value, Mapping):
            return {
                str(k): visit(
                    v,
                    key=key if key in {"example", "examples"} else str(k),
                    path=(*path, str(k)),
                )
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [visit(item, key=key, path=path) for item in value]
        if isinstance(value, str):
            field = property_name(path)
            if field in {"schema", "schema_id", "schema_version"}:
                return _schema_id(value)
            if field in {
                "evaluator",
                "evaluator_name",
                "evaluation_name",
                "matcher",
            } or (field == "name" and any("Evaluation" in part for part in path)):
                return _evaluator_name(value)
            if key in {"const", "default", "example", "examples", "enum"}:
                # OpenAPI examples/defaults are only rewritten when the
                # literal is one of the exact product-owned values above.
                # Arbitrary example text must remain untouched.
                if key == "default" and field not in {
                    "schema",
                    "schema_id",
                    "schema_version",
                    "evaluator",
                    "evaluator_name",
                    "evaluation_name",
                    "matcher",
                }:
                    return value
                mapped = _schema_id(value)
                if mapped != value:
                    return mapped
                return _evaluator_name(value)
            if key == "$id":
                for internal, neutral in _SCHEMA_INTERNAL_TO_WIRE.items():
                    value = value.replace(f"/{internal}.", f"/{neutral}.")
                value = value.replace("https://m3.local", "https://api.local")
                value = value.replace("/m3.", "/")
                return value
            if key in {
                "schema",
                "schema_id",
                "evaluator",
                "evaluator_name",
                "evaluation_name",
                "matcher",
            }:
                if key in {"schema", "schema_id"}:
                    return _schema_id(value)
                return _evaluator_name(value)
            return value
        return value

    return cast(dict[str, Any], visit(output))


__all__ = ["internalize_request", "neutralize_openapi", "neutralize_response"]
