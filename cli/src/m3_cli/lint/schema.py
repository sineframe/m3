"""JSON Schema walking, and a model of how Codex sanitizes input schemas."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

SAFE_INT = 2**53 - 1

_SINGLE = ("contains", "propertyNames", "not", "if", "then", "else")
_MAPS = ("properties", "patternProperties", "dependentSchemas", "$defs", "definitions")
_LISTS = ("prefixItems", "anyOf", "oneOf", "allOf")
COMBINATORS = ("anyOf", "oneOf", "allOf")


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def json_depth(value: Any) -> int:
    """Nesting depth: containers count one plus their deepest child.

    Iterative, so a hostile server cannot exhaust the Python stack.
    """

    deepest = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict):
            children: Iterable[Any] = node.values()
        elif isinstance(node, list):
            children = node
        else:
            continue
        deepest = max(deepest, depth + 1)
        stack.extend((child, depth + 1) for child in children)
    return deepest


def _children(schema: dict[str, Any], pointer: str) -> list[tuple[str, Any, str]]:
    children: list[tuple[str, Any, str]] = []
    for keyword in _MAPS:
        mapping = schema.get(keyword)
        if isinstance(mapping, dict):
            for key, child in mapping.items():
                path = f"{pointer}/{keyword}/{_escape(str(key))}"
                children.append((path, child, keyword))
    for keyword in ("additionalProperties", *_SINGLE):
        child = schema.get(keyword)
        if isinstance(child, (dict, bool)):
            children.append((f"{pointer}/{keyword}", child, keyword))
    items = schema.get("items")
    if isinstance(items, (dict, bool)):
        children.append((f"{pointer}/items", items, "items"))
    elif isinstance(items, list):
        for index, child in enumerate(items):
            children.append((f"{pointer}/items/{index}", child, "items"))
    for keyword in _LISTS:
        sequence = schema.get(keyword)
        if isinstance(sequence, list):
            for index, child in enumerate(sequence):
                children.append((f"{pointer}/{keyword}/{index}", child, keyword))
    return children


def iter_subschemas(
    schema: Any, pointer: str = "", position: str = "root"
) -> Iterator[tuple[str, Any, str]]:
    """Yield ``(json_pointer, subschema, keyword)`` for the schema and each
    subschema, depth first in document order, without recursion."""

    stack = [(pointer, schema, position)]
    while stack:
        entry = stack.pop()
        yield entry
        if isinstance(entry[1], dict):
            stack.extend(reversed(_children(entry[1], entry[0])))


# Keywords whose subschemas survive Codex's sanitizer (see CODEX_KEPT); it
# drops the others (if/then/else, not, patternProperties, ...) unread.
_CODEX_RETAINED = {
    "properties",
    "$defs",
    "definitions",
    "additionalProperties",
    "items",
    "anyOf",
    "oneOf",
    "allOf",
}


def iter_codex_subschemas(schema: Any) -> Iterator[tuple[str, Any, str]]:
    """Like iter_subschemas, but only through branches Codex keeps, so
    nothing inside a discarded branch is judged as Codex input."""

    stack = [("", schema, "root")]
    while stack:
        entry = stack.pop()
        yield entry
        if isinstance(entry[1], dict):
            children = [
                child
                for child in _children(entry[1], entry[0])
                if child[2] in _CODEX_RETAINED
                # A list-form items is itself the parse failure; Codex never
                # reads its elements.
                and not (child[2] == "items" and isinstance(entry[1]["items"], list))
            ]
            stack.extend(reversed(children))


def iter_property_maps(schema: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield every ``properties`` object with its JSON pointer."""

    for pointer, subschema, _ in iter_subschemas(schema):
        if isinstance(subschema, dict) and isinstance(
            subschema.get("properties"), dict
        ):
            yield f"{pointer}/properties", subschema["properties"]


CODEX_KEPT = {
    "$ref",
    "type",
    "description",
    "enum",
    "items",
    "minItems",
    "properties",
    "required",
    "additionalProperties",
    "anyOf",
    "oneOf",
    "allOf",
    "$defs",
    "definitions",
}
CODEX_CONSTRAINT_KEYWORDS = {
    "format",
    "pattern",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
    "patternProperties",
    "propertyNames",
    "contains",
    "if",
    "then",
    "else",
    "not",
    "dependentRequired",
    "dependentSchemas",
    "nullable",
    "unevaluatedProperties",
    "unevaluatedItems",
}
_CODEX_TYPES = {"string", "number", "boolean", "integer", "object", "array", "null"}
_NUMERIC = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf")


def _types(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _inferred_type(schema: dict[str, Any]) -> str | None:
    if any(key in schema for key in ("properties", "required", "additionalProperties")):
        return "object"
    if "items" in schema or "prefixItems" in schema:
        return "array"
    if "enum" in schema or "format" in schema:
        return "string"
    if any(key in schema for key in _NUMERIC):
        return "number"
    return None


def codex_normalize(schema: Any) -> Any:
    """Approximate the schema Codex sends to the model for an MCP tool."""

    if isinstance(schema, bool):
        return {"type": "string"}
    if not isinstance(schema, dict):
        return schema
    source = dict(schema)
    if "const" in source:
        source["enum"] = [source.pop("const")]
    result: dict[str, Any] = {k: v for k, v in source.items() if k in CODEX_KEPT}
    if "type" in result:
        raw = result["type"]
        if isinstance(raw, list):
            kept = [
                item for item in raw if isinstance(item, str) and item in _CODEX_TYPES
            ]
            if kept:
                result["type"] = kept
            else:
                del result["type"]
        elif not isinstance(raw, str) or raw not in _CODEX_TYPES:
            del result["type"]
    if "type" not in result:
        inferred = _inferred_type(source)
        if inferred is not None:
            result["type"] = inferred
    if (
        "type" not in result
        and "$ref" not in result
        and not any(key in result for key in COMBINATORS)
    ):
        return {}
    types = _types(result.get("type"))
    if "object" in types and not isinstance(result.get("properties"), dict):
        result["properties"] = {}
    if "array" in types and not isinstance(result.get("items"), (dict, bool)):
        result["items"] = {"type": "string"}
    if isinstance(result.get("properties"), dict):
        result["properties"] = {
            key: codex_normalize(value) for key, value in result["properties"].items()
        }
    if isinstance(result.get("items"), (dict, bool)):
        result["items"] = codex_normalize(result["items"])
    if isinstance(result.get("additionalProperties"), dict):
        result["additionalProperties"] = codex_normalize(result["additionalProperties"])
    for keyword in COMBINATORS:
        if isinstance(result.get(keyword), list):
            result[keyword] = [codex_normalize(item) for item in result[keyword]]
    for keyword in ("$defs", "definitions"):
        if isinstance(result.get(keyword), dict):
            result[keyword] = {
                key: codex_normalize(value) for key, value in result[keyword].items()
            }
    return result


def utf8_size(text: str) -> int:
    """UTF-8 byte length; lone surrogates (valid in JSON escapes) count too."""

    return len(text.encode("utf-8", "surrogatepass"))


def utf16_len(text: str) -> int:
    """Length as JavaScript counts it (UTF-16 code units): Claude Code's
    character limits apply to this, so an emoji counts twice."""

    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def metaschema_errors(schema: dict[str, Any]) -> list[str]:
    """Pointers and keywords of JSON Schema 2020-12 meta-schema violations."""

    from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

    validator = Draft202012Validator(Draft202012Validator.META_SCHEMA)
    errors: list[str] = []
    for error in validator.iter_errors(schema):
        path = "/".join(map(str, error.absolute_path)) or "(root)"
        errors.append(f"{path}: {error.validator}")
        if len(errors) == 5:
            break
    return errors
