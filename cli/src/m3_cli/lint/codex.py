"""Models of how Codex turns an MCP input schema into what the model sees.

Ported from codex-rs: `tools/src/json_schema.rs` (unreachable definition
pruning), `tools/src/json_schema/compaction.rs` (large-schema compaction) and
`code-mode-protocol/src/json_schema_types.rs` (the code-mode TypeScript
renderer). They start from `codex_normalize`, the model of Codex's sanitizer.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

from .schema import codex_normalize, utf8_size

COMPACT_TOOL_SCHEMA_BYTES = 5_000
INPUT_SCHEMA_MAX_BYTES = 16_000
_RENDER_WORK_MULTIPLIER = 4
_MAX_LOCAL_REF_EXPANSIONS_PER_PATH = 2
_MAX_TOTAL_LOCAL_REF_EXPANSIONS = 32
_MAX_COMPACT_DEPTH = 3
_DEFINITION_TABLES = ("$defs", "definitions")
_CHILD_KEYS = ("items", "anyOf", "oneOf", "allOf")
_COMPOSITIONS = ("anyOf", "oneOf", "allOf")
_RENDERABLE = (
    "const",
    "enum",
    "anyOf",
    "oneOf",
    "allOf",
    "type",
    "properties",
    "additionalProperties",
    "required",
    "items",
    "prefixItems",
)


def _compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _definition_ref(ref: Any) -> tuple[str, str] | None:
    """(table, name) of a local `#/$defs/<name>/...` reference."""

    if not isinstance(ref, str) or not ref.startswith("#"):
        return None
    pointer = unquote(ref[1:])
    if not pointer.startswith("/"):
        return None
    parts = [_unescape(part) for part in pointer[1:].split("/")]
    if len(parts) < 2 or parts[0] not in _DEFINITION_TABLES:
        return None
    return parts[0], parts[1]


def _map_children(
    schema: dict[str, Any], include_definitions: bool, visit: Callable[[Any], Any]
) -> dict[str, Any]:
    """`schema` with each schema child replaced by `visit(child)`, following
    Codex's `for_each_schema_child_mut`."""

    result = dict(schema)
    if isinstance(result.get("properties"), dict):
        result["properties"] = {
            name: visit(child) for name, child in result["properties"].items()
        }
    for key in _CHILD_KEYS:
        if key in result:
            result[key] = visit(result[key])
    additional = result.get("additionalProperties")
    if "additionalProperties" in result and not isinstance(additional, bool):
        result["additionalProperties"] = visit(additional)
    if include_definitions:
        for table in _DEFINITION_TABLES:
            if isinstance(result.get(table), dict):
                result[table] = {
                    name: visit(child) for name, child in result[table].items()
                }
    return result


def _refs_outside_definitions(value: Any) -> list[str]:
    refs: list[str] = []
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            if isinstance(node.get("$ref"), str):
                refs.append(node["$ref"])
            stack.extend(
                child for key, child in node.items() if key not in _DEFINITION_TABLES
            )
    return refs


def prune_unreachable_definitions(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return schema
    reachable: set[tuple[str, str]] = set()
    pending = [
        target
        for target in map(_definition_ref, _refs_outside_definitions(schema))
        if target is not None
    ]
    while pending:
        target = pending.pop()
        if target in reachable:
            continue
        reachable.add(target)
        table = schema.get(target[0])
        if isinstance(table, dict) and target[1] in table:
            pending.extend(
                found
                for found in map(
                    _definition_ref, _refs_outside_definitions(table[target[1]])
                )
                if found is not None
            )
    result = dict(schema)
    for table in _DEFINITION_TABLES:
        if isinstance(result.get(table), dict):
            kept = {
                name: child
                for name, child in result[table].items()
                if (table, name) in reachable
            }
            if kept:
                result[table] = kept
            else:
                del result[table]
    return result


def codex_prepare(schema: Any) -> Any:
    """The sanitized schema Codex keeps, before compaction."""

    return prune_unreachable_definitions(codex_normalize(schema))


def codex_normalized_size(schema: Any) -> int:
    """UTF-8 bytes of the sanitized schema, as Codex measures for compaction."""

    return utf8_size(_compact_json(codex_prepare(schema)))


# --- compaction (applied while the schema is over 5000 bytes) ---------------


def _strip_descriptions(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_descriptions(item) for item in value]
    if isinstance(value, dict):
        stripped = {key: child for key, child in value.items() if key != "description"}
        return _map_children(stripped, True, _strip_descriptions)
    return value


def _rewrite_definition_refs(value: Any) -> Any:
    if isinstance(value, list):
        return [_rewrite_definition_refs(item) for item in value]
    if isinstance(value, dict):
        if _definition_ref(value.get("$ref")) is not None:
            return {}
        return _map_children(value, False, _rewrite_definition_refs)
    return value


def _drop_definitions(value: Any) -> Any:
    value = _rewrite_definition_refs(value)
    if isinstance(value, dict):
        value = {k: v for k, v in value.items() if k not in _DEFINITION_TABLES}
    return value


def _is_complex(schema: dict[str, Any]) -> bool:
    return any(
        key in schema
        for key in (*_CHILD_KEYS, "properties", "additionalProperties", "$ref")
    )


def _collapse_deep_objects(value: Any, depth: int = 0) -> Any:
    if isinstance(value, list):
        return [_collapse_deep_objects(item, depth) for item in value]
    if isinstance(value, dict):
        if depth >= _MAX_COMPACT_DEPTH and _is_complex(value):
            return {}
        return _map_children(
            value, False, lambda child: _collapse_deep_objects(child, depth + 1)
        )
    return value


def _prune_compositions(value: Any) -> Any:
    if isinstance(value, list):
        return [_prune_compositions(item) for item in value]
    if isinstance(value, dict):
        if any(key in value for key in _COMPOSITIONS):
            return {}
        return _map_children(value, False, _prune_compositions)
    return value


_COMPACTION_PASSES: tuple[Callable[[Any], Any], ...] = (
    _strip_descriptions,
    _drop_definitions,
    _collapse_deep_objects,
    _prune_compositions,
)


def codex_compact(schema: Any, max_bytes: int = COMPACT_TOOL_SCHEMA_BYTES) -> Any:
    """Codex's increasingly lossy passes, each run only while still too large.
    `schema` is already sanitized (codex_prepare)."""

    for compaction_pass in _COMPACTION_PASSES:
        if utf8_size(_compact_json(schema)) <= max_bytes:
            break
        schema = codex_normalize(compaction_pass(schema))
    return schema


# --- code-mode TypeScript rendering ----------------------------------------


def _code_mode_identifier(name: str) -> str:
    identifier = "".join(
        char
        if char in "_$"
        or (char.isascii() and (char.isalpha() or (index > 0 and char.isdigit())))
        else "_"
        for index, char in enumerate(name)
    )
    return identifier or "_"


def _literal_upper_bound(value: Any) -> int:
    if value is None or value is True:
        return 4
    if value is False:
        return 5
    if isinstance(value, (int, float)):
        return len(json.dumps(value))
    if isinstance(value, str):
        return utf8_size(value) * 6 + 2
    if isinstance(value, list):
        return 2 + sum(1 + _literal_upper_bound(item) for item in value)
    if isinstance(value, dict):
        return 2 + sum(
            4 + utf8_size(str(key)) * 6 + _literal_upper_bound(child)
            for key, child in value.items()
        )
    return 0


def _resolve_pointer(root: Any, pointer: str) -> tuple[bool, Any]:
    if pointer == "":
        return True, root
    node = root
    for raw in pointer[1:].split("/"):
        part = _unescape(raw)
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif (
            isinstance(node, list)
            and re.fullmatch(r"0|[1-9][0-9]{0,8}", part)
            and int(part) < len(node)
        ):
            node = node[int(part)]
        else:
            return False, None
    return True, node


class _Renderer:
    def __init__(self, root: Any, max_bytes: int) -> None:
        self.root = root
        self.nested_resources = 0
        self.active: dict[str, int] = {}
        self.remaining_refs = _MAX_TOTAL_LOCAL_REF_EXPANSIONS
        self.remaining_work = max_bytes * _RENDER_WORK_MULTIPLIER
        self.exhausted = False

    def consume(self, size: int) -> bool:
        if size > self.remaining_work:
            self.exhausted = True
            return False
        self.remaining_work -= size
        return True

    def push(self, lines: list[str], line: str) -> bool:
        if not self.consume(utf8_size(line)):
            return False
        lines.append(line)
        return True

    def render(self, schema: Any) -> str:
        if self.exhausted:
            return "unknown"
        nested = (
            schema is not self.root and isinstance(schema, dict) and "$id" in schema
        )
        if nested:
            self.nested_resources += 1
        if schema is True:
            rendered = "unknown"
        elif schema is False:
            rendered = "never"
        elif isinstance(schema, dict):
            rendered = self.render_map(schema)
        else:
            rendered = "unknown"
        if nested:
            self.nested_resources -= 1
        return rendered if self.consume(utf8_size(rendered)) else "unknown"

    def literal(self, value: Any) -> str:
        if _literal_upper_bound(value) > self.remaining_work:
            self.exhausted = True
            return "unknown"
        return _compact_json(value)

    def render_map(self, schema: dict[str, Any]) -> str:
        if self.exhausted:
            return "unknown"
        if "$ref" in schema:
            return self.render_ref(schema)
        if "const" in schema:
            return self.literal(schema["const"])
        if isinstance(schema.get("enum"), list):
            literals: list[str] = []
            for value in schema["enum"]:
                literal = self.literal(value)
                if self.exhausted or not self.consume(utf8_size(literal)):
                    return "unknown"
                literals.append(literal)
            if literals:
                return " | ".join(literals)
        for key in ("anyOf", "oneOf"):
            if isinstance(schema.get(key), list):
                variants = []
                for variant in schema[key]:
                    if self.exhausted:
                        return "unknown"
                    variants.append(self.render(variant))
                if variants:
                    return " | ".join(variants)
        if isinstance(schema.get("allOf"), list):
            parts = []
            for variant in schema["allOf"]:
                if self.exhausted:
                    return "unknown"
                part = self.render(variant)
                parts.append(f"({part})" if " | " in part else part)
            if parts:
                return " & ".join(parts)
        if "type" in schema:
            types = schema["type"]
            if isinstance(types, list):
                rendered = []
                for name in (item for item in types if isinstance(item, str)):
                    if self.exhausted:
                        return "unknown"
                    rendered.append(self.render_type(schema, name))
                if rendered:
                    return " | ".join(rendered)
            if isinstance(types, str):
                return self.render_type(schema, types)
        if any(
            key in schema for key in ("properties", "additionalProperties", "required")
        ):
            return self.render_object(schema)
        if "items" in schema or "prefixItems" in schema:
            return self.render_array(schema)
        return "unknown"

    def render_ref(self, schema: dict[str, Any]) -> str:
        referenced = "unknown"
        ref = schema.get("$ref")
        if self.nested_resources == 0 and isinstance(ref, str) and ref.startswith("#"):
            pointer = unquote(ref[1:])
            if pointer == "" or pointer.startswith("/"):
                active = self.active.get(pointer, 0)
                found, target = _resolve_pointer(self.root, pointer)
                if active >= _MAX_LOCAL_REF_EXPANSIONS_PER_PATH or (
                    found and self.remaining_refs == 0
                ):
                    referenced = "unknown"
                elif found:
                    self.remaining_refs -= 1
                    self.active[pointer] = active + 1
                    referenced = self.render(target)
                    if active == 0:
                        del self.active[pointer]
                    else:
                        self.active[pointer] = active
        if self.exhausted:
            return "unknown"
        siblings = {
            key: value
            for key, value in schema.items()
            if key not in ("$ref", "$defs", "definitions")
        }
        if not any(key in siblings for key in _RENDERABLE):
            return referenced
        sibling = self.render_map(siblings)
        if referenced == "unknown":
            return sibling
        if sibling == "unknown":
            return referenced
        return f"({referenced}) & ({sibling})"

    def render_type(self, schema: dict[str, Any], name: str) -> str:
        if name == "string":
            return "string"
        if name in ("number", "integer"):
            return "number"
        if name in ("boolean", "null"):
            return name
        if name == "array":
            return self.render_array(schema)
        if name == "object":
            return self.render_object(schema)
        return "unknown"

    def render_array(self, schema: dict[str, Any]) -> str:
        if "items" in schema:
            item = self.render(schema["items"])
            return "unknown" if self.exhausted else f"Array<{item}>"
        if isinstance(schema.get("prefixItems"), list):
            items = []
            for item in schema["prefixItems"]:
                if self.exhausted:
                    return "unknown"
                items.append(self.render(item))
            if items:
                return f"[{', '.join(items)}]"
        return "unknown[]"

    def additional_line(
        self,
        lines: list[str],
        schema: dict[str, Any],
        properties: dict[str, Any],
        prefix: str,
    ) -> bool:
        if "additionalProperties" in schema:
            additional = schema["additionalProperties"]
            if additional is False:
                return True
            rendered = "unknown" if additional is True else self.render(additional)
            return self.push(lines, f"{prefix}[key: string]: {rendered};")
        if not properties:
            return self.push(lines, f"{prefix}[key: string]: unknown;")
        return True

    def render_property(self, name: str, value: Any, required: list[str]) -> str:
        if utf8_size(name) > self.remaining_work:
            self.exhausted = True
            return "unknown"
        optional = "" if name in required else "?"
        shown = name if _code_mode_identifier(name) == name else _compact_json(name)
        rendered = self.render(value)
        return "unknown" if self.exhausted else f"{shown}{optional}: {rendered};"

    def render_object(self, schema: dict[str, Any]) -> str:
        raw_required = schema.get("required")
        required = (
            [item for item in raw_required if isinstance(item, str)]
            if isinstance(raw_required, list)
            else []
        )
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        ordered = sorted(properties.items())
        lines: list[str] = []
        described = any(
            isinstance(value, dict)
            and isinstance(value.get("description"), str)
            and value["description"]
            for _, value in ordered
        )
        if described:
            if not self.push(lines, "{"):
                return "unknown"
            for name, value in ordered:
                description = (
                    value.get("description") if isinstance(value, dict) else None
                )
                if isinstance(description, str):
                    for line in re.split(r"\r?\n", description):
                        line = line.strip()
                        if not line:
                            continue
                        if utf8_size(line) + 5 > self.remaining_work or not self.push(
                            lines, f"  // {line}"
                        ):
                            return "unknown"
                rendered = self.render_property(name, value, required)
                if self.exhausted or not self.push(lines, f"  {rendered}"):
                    return "unknown"
            if not self.additional_line(
                lines, schema, properties, "  "
            ) or not self.push(lines, "}"):
                return "unknown"
            return "\n".join(lines)
        for name, value in ordered:
            rendered = self.render_property(name, value, required)
            if self.exhausted or not self.push(lines, rendered):
                return "unknown"
        if not self.additional_line(lines, schema, properties, ""):
            return "unknown"
        if not lines:
            return "{}"
        return "{ " + " ".join(lines) + " }"


def code_mode_type_too_large(schema: Any) -> bool:
    """Whether Codex code mode renders this input schema's argument type as
    `unknown` because it exceeds its 16,000-byte budget."""

    prepared = codex_compact(codex_prepare(schema))
    renderer = _Renderer(prepared, INPUT_SCHEMA_MAX_BYTES)
    rendered = renderer.render(prepared)
    return renderer.exhausted or utf8_size(rendered) > INPUT_SCHEMA_MAX_BYTES
