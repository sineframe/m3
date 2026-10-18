"""The rule catalogue: server shapes Claude Code or Codex mishandle.

Every effect records the harness, the half-open version range it applies to,
its impact and the kind of evidence behind it. Checks only read the snapshot
and never raise on unexpected shapes; they skip what they do not understand.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Literal

from .collect import ListingResult, Snapshot, capabilities_of, declared
from .schema import (
    CODEX_CONSTRAINT_KEYWORDS,
    COMBINATORS,
    SAFE_INT,
    codex_normalized_size,
    count_properties,
    iter_property_maps,
    iter_subschemas,
    json_depth,
    metaschema_errors,
    utf8_size,
)
from .versions import HarnessKind, Target, VersionRange

Impact = Literal["session", "server", "tool", "degraded", "advisory"]
Severity = Literal["error", "warning", "info"]
SEVERITY: dict[Impact, Severity] = {
    "session": "error",
    "server": "error",
    "tool": "error",
    "degraded": "warning",
    "advisory": "info",
}
SEVERITY_RANK: dict[Severity, int] = {"error": 0, "warning": 1, "info": 2}
Evidence = Literal["live", "source", "issue"]
Category = Literal[
    "protocol",
    "listing",
    "root-schema",
    "schema-validity",
    "codex-projection",
    "names",
    "text",
    "result-hints",
]


@dataclass(frozen=True)
class Effect:
    harness: HarnessKind
    range: VersionRange
    impact: Impact
    summary: str
    evidence: Evidence
    when: frozenset[str] | None = None


@dataclass(frozen=True)
class Hit:
    location: str
    detail: str
    variants: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    category: Category
    effects: tuple[Effect, ...]
    fix: str
    check: Callable[[Snapshot], Iterable[Hit]]


@dataclass(frozen=True)
class Finding:
    rule: Rule
    hit: Hit
    effects: tuple[Effect, ...]
    severity: Severity


# --- effect construction --------------------------------------------------

_EVIDENCE: dict[str, Evidence] = {"L": "live", "S": "source", "I": "issue"}
_IMPACT: dict[str, Impact] = {
    "S": "session",
    "V": "server",
    "T": "tool",
    "D": "degraded",
    "A": "advisory",
}


def _effect(
    harness: HarnessKind,
    introduced: str | None,
    fixed: str | None,
    impact: str,
    evidence: str,
    summary: str,
    when: Iterable[str] | None = None,
) -> Effect:
    return Effect(
        harness=harness,
        range=VersionRange(introduced, fixed),
        impact=_IMPACT[impact],
        summary=summary,
        evidence=_EVIDENCE[evidence],
        when=None if when is None else frozenset(when),
    )


def CC(*args: Any, **kwargs: Any) -> Effect:
    return _effect("claude_code", *args, **kwargs)


def CX(*args: Any, **kwargs: Any) -> Effect:
    return _effect("codex", *args, **kwargs)


# --- shared helpers ---------------------------------------------------------


def _short(value: Any, limit: int = 80) -> str:
    return repr(value)[:limit]


def _tools(snapshot: Snapshot) -> Iterator[dict[str, Any]]:
    for tool in snapshot.tools.items:
        if isinstance(tool, dict):
            yield tool


def _where(tool: dict[str, Any], pointer: str | None = None) -> str:
    location = f"tool {_short(tool.get('name'))}"
    return f"{location} {pointer}" if pointer else location


def _schema(tool: dict[str, Any]) -> dict[str, Any] | None:
    schema = tool.get("inputSchema")
    return schema if isinstance(schema, dict) else None


def _dict_subschemas(schema: Any) -> Iterator[tuple[str, dict[str, Any], str]]:
    for pointer, subschema, position in iter_subschemas(schema):
        if isinstance(subschema, dict):
            yield pointer, subschema, position


def _types(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _strings(value: Any) -> Iterator[str]:
    """Every string key and string value inside a JSON value (no recursion)."""

    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for key, child in node.items():
                if isinstance(key, str):
                    yield key
                stack.append(child)
        elif isinstance(node, list):
            stack.extend(node)


# Both harnesses reject schemas this deep outright (json-depth-too-deep), so
# checks that model recursive processing skip them.
_DEPTH_LIMIT = 125


def _shallow(schema: dict[str, Any]) -> bool:
    return json_depth(schema) < _DEPTH_LIMIT


def _listings(snapshot: Snapshot) -> Iterator[tuple[str, ListingResult]]:
    for method, listing in (
        ("tools/list", snapshot.tools),
        ("prompts/list", snapshot.prompts),
        ("resources/list", snapshot.resources),
        ("resources/templates/list", snapshot.templates),
    ):
        if listing is not None:
            yield method, listing


def _capabilities(snapshot: Snapshot) -> dict[str, Any]:
    return capabilities_of(snapshot.initialize)


# --- protocol ----------------------------------------------------------------


def _check_server_info(snapshot: Snapshot) -> Iterator[Hit]:
    info = snapshot.initialize.get("serverInfo")
    if not isinstance(info, dict):
        yield Hit("initialize", "serverInfo missing or not an object")
        return
    bad = [key for key in ("name", "version") if not isinstance(info.get(key), str)]
    if bad:
        detail = ", ".join(f"serverInfo.{key}" for key in bad)
        yield Hit("initialize", f"{detail} missing or not a string")


def _check_tools_capability(snapshot: Snapshot) -> Iterator[Hit]:
    capabilities = _capabilities(snapshot)
    tools = capabilities.get("tools")
    if tools is not None and not isinstance(tools, dict):
        kind = type(tools).__name__
        yield Hit(
            "initialize",
            f"capabilities.tools is {kind}, not an object",
            frozenset({"not-object"}),
        )
    elif tools is None and snapshot.tools.items:
        count = len(snapshot.tools.items)
        yield Hit(
            "initialize",
            f"capabilities.tools not declared; tools/list returns {count} tools",
            frozenset({"undeclared"}),
        )


def _check_discover_unanswered(snapshot: Snapshot) -> Iterator[Hit]:
    if snapshot.discover in ("timeout", "closed"):
        yield Hit("server/discover", f"probe {snapshot.discover}")


def _check_discover_result(snapshot: Snapshot) -> Iterator[Hit]:
    if snapshot.discover == "result":
        yield Hit("server/discover", "server answered server/discover")


def _check_list_changed(snapshot: Snapshot) -> Iterator[Hit]:
    tools = _capabilities(snapshot).get("tools")
    if isinstance(tools, dict) and tools.get("listChanged") is True:
        yield Hit("initialize", "capabilities.tools.listChanged is true")


# --- listing -----------------------------------------------------------------


def _check_multi_page(snapshot: Snapshot) -> Iterator[Hit]:
    if snapshot.tools.pages > 1:
        yield Hit("tools/list", f"{snapshot.tools.pages} pages")


def _check_over_20_pages(snapshot: Snapshot) -> Iterator[Hit]:
    if snapshot.tools.pages > 20:
        yield Hit("tools/list", f"{snapshot.tools.pages} pages")


def _check_over_100_pages(snapshot: Snapshot) -> Iterator[Hit]:
    for method, listing in _listings(snapshot):
        if listing.pages > 100:
            yield Hit(method, f"{listing.pages} pages", frozenset({method}))


def _check_repeated_cursor(snapshot: Snapshot) -> Iterator[Hit]:
    for method, listing in _listings(snapshot):
        if listing.repeated_cursor:
            detail = f"nextCursor repeated after {listing.pages} pages"
            yield Hit(method, detail, frozenset({method}))


def _check_cursor_length(snapshot: Snapshot) -> Iterator[Hit]:
    for method, listing in _listings(snapshot):
        if listing.max_cursor_bytes > 65536:
            detail = f"cursor {listing.max_cursor_bytes} bytes"
            yield Hit(method, detail, frozenset({method}))


def _check_tool_count(snapshot: Snapshot) -> Iterator[Hit]:
    if len(snapshot.tools.items) > 2048:
        yield Hit("tools/list", f"{len(snapshot.tools.items)} tools")


def _check_listing_size(snapshot: Snapshot) -> Iterator[Hit]:
    total = snapshot.tools.total_bytes
    wide = sum(len(s) for s in _strings(snapshot.tools.items) if not s.isascii())
    if total > 16777216 or wide > 1048576:
        yield Hit(
            "tools/list",
            f"{total} bytes; {wide} characters in non-ASCII strings",
        )


def _check_tools_list_failed(snapshot: Snapshot) -> Iterator[Hit]:
    error = snapshot.tools.error
    if error in ("error", "timeout", "invalid") or (
        error == "method_not_found" and declared(_capabilities(snapshot), "tools")
    ):
        yield Hit("tools/list", str(error))


# --- root schema ---------------------------------------------------------------


def _check_name_type(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        if not isinstance(tool.get("name"), str):
            yield Hit(
                _where(tool),
                "name missing" if "name" not in tool else "name not a string",
            )


_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def _check_metadata_types(snapshot: Snapshot) -> Iterator[Hit]:
    for index, tool in enumerate(snapshot.tools.items):
        if not isinstance(tool, dict):
            yield Hit(f"tools/list item {index}", "tool entry is not an object")
            continue
        bad: list[str] = []
        annotations = tool.get("annotations")
        if "annotations" in tool and not isinstance(annotations, dict):
            bad.append("annotations")
        if isinstance(annotations, dict):
            if "title" in annotations and not isinstance(annotations["title"], str):
                bad.append("annotations.title")
            for hint in _HINTS:
                if hint in annotations and not isinstance(annotations[hint], bool):
                    bad.append(f"annotations.{hint}")
        if "title" in tool and not isinstance(tool["title"], str):
            bad.append("title")
        if "_meta" in tool and not isinstance(tool["_meta"], dict):
            bad.append("_meta")
        if "icons" in tool:
            icons = tool["icons"]
            if not isinstance(icons, list) or any(
                not isinstance(icon, dict) or not isinstance(icon.get("src"), str)
                for icon in icons
            ):
                bad.append("icons")
        if bad:
            yield Hit(_where(tool), "wrong type: " + ", ".join(bad))


def _check_description_type(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        if "description" in tool and not isinstance(tool["description"], str):
            kind = type(tool["description"]).__name__
            yield Hit(_where(tool), f"description is {kind}")


def _check_execution(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        if "execution" not in tool:
            continue
        execution = tool["execution"]
        if not isinstance(execution, dict):
            yield Hit(_where(tool), "execution not an object")
        elif "taskSupport" in execution and execution["taskSupport"] not in (
            "required",
            "optional",
            "forbidden",
        ):
            yield Hit(
                _where(tool),
                f"execution.taskSupport {_short(execution['taskSupport'], 40)}",
            )


def _check_input_schema_missing(snapshot: Snapshot) -> Iterator[Hit]:
    for index, tool in enumerate(snapshot.tools.items):
        if not isinstance(tool, dict):
            yield Hit(f"tools/list item {index}", "tool entry is not an object")
        elif "inputSchema" not in tool:
            yield Hit(_where(tool), "inputSchema missing")
        elif not isinstance(tool["inputSchema"], dict):
            kind = type(tool["inputSchema"]).__name__
            yield Hit(_where(tool), f"inputSchema is {kind}")


def _check_root_type_missing(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        schema = _schema(tool)
        if schema is not None and "type" not in schema:
            yield Hit(_where(tool), "no type at the inputSchema root")


def _check_root_type_not_object(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        schema = _schema(tool)
        if schema is not None and "type" in schema and schema["type"] != "object":
            yield Hit(_where(tool), f"root type {_short(schema['type'], 60)}")


def _check_root_properties(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        schema = _schema(tool)
        if schema is not None and "properties" in schema:
            if not isinstance(schema["properties"], dict):
                kind = type(schema["properties"]).__name__
                yield Hit(_where(tool), f"properties is {kind}")


def _check_root_required_null(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        schema = _schema(tool)
        if schema is not None and "required" in schema and schema["required"] is None:
            yield Hit(_where(tool), "required is null")


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _check_root_required_type(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        schema = _schema(tool)
        if schema is None or "required" not in schema:
            continue
        required = schema["required"]
        if required is not None and not _string_list(required):
            yield Hit(_where(tool), f"required is {_short(required, 40)}")


def _check_root_combinator(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        schema = _schema(tool)
        if schema is None or schema.get("type") != "object":
            continue
        present = [keyword for keyword in COMBINATORS if keyword in schema]
        if not present:
            continue
        variants = set(present)
        properties = schema.get("properties")
        if isinstance(properties, dict) and properties:
            variants.update(
                f"{keyword}+properties"
                for keyword in ("anyOf", "oneOf")
                if keyword in schema
            )
        yield Hit(_where(tool), "root " + ", ".join(present), frozenset(variants))


_CONDITIONALS = ("not", "if", "then", "else", "dependentRequired", "dependentSchemas")


def _check_root_conditionals(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        schema = _schema(tool)
        if schema is None:
            continue
        present = [keyword for keyword in _CONDITIONALS if keyword in schema]
        if present:
            yield Hit(_where(tool), "root " + ", ".join(present))


# --- schema validity -----------------------------------------------------------

_DRAFT_2020_12 = (
    "https://json-schema.org/draft/2020-12/schema",
    "https://json-schema.org/draft/2020-12/schema#",
)


def _check_invalid_2020_12(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        schema = _schema(tool)
        if schema is None or not _shallow(schema):
            continue
        if "$schema" in schema and schema["$schema"] not in _DRAFT_2020_12:
            continue
        errors = metaschema_errors(schema)
        if errors:
            yield Hit(_where(tool), "; ".join(errors))


_PROPERTY_KEY = re.compile(r"[a-zA-Z0-9_.-]{1,64}", re.ASCII)


def _check_property_keys(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        schema = _schema(tool)
        properties = schema.get("properties") if schema is not None else None
        if not isinstance(properties, dict):
            continue
        bad = [key for key in properties if _PROPERTY_KEY.fullmatch(key) is None]
        if not bad:
            continue
        # Claude Code (2.1.0+) NFKC-normalizes keys before they are validated,
        # so keys such as "ﬁle" become valid.
        after_nfkc = any(
            _PROPERTY_KEY.fullmatch(unicodedata.normalize("NFKC", key)) is None
            for key in bad
        )
        variants = frozenset({"raw", "after-nfkc"} if after_nfkc else {"raw"})
        yield Hit(_where(tool), ", ".join(_short(key, 40) for key in bad[:3]), variants)


def _codex_parse_variants(schema: dict[str, Any]) -> set[str]:
    variants: set[str] = set()
    if schema.get("type") == "null":
        variants.add("null-root")
    for _, sub, _ in _dict_subschemas(schema):
        if isinstance(sub.get("items"), list):
            variants.add("items-list")
        required = sub.get("required")
        if required is not None and not _string_list(required):
            variants.add("required-type")
        if "enum" in sub and not isinstance(sub["enum"], list):
            variants.add("enum-type")
        if "additionalProperties" in sub and not isinstance(
            sub["additionalProperties"], (bool, dict)
        ):
            variants.add("additional-type")
        if "properties" in sub:
            properties = sub["properties"]
            if not isinstance(properties, dict) or any(
                value is None for value in properties.values()
            ):
                variants.add("properties-type")
        if any(key in sub and not isinstance(sub[key], list) for key in COMBINATORS):
            variants.add("combinator-type")
        if "minItems" in sub and not (
            _is_int(sub["minItems"]) and sub["minItems"] >= 0
        ):
            variants.add("min-items")
        if "description" in sub and not isinstance(sub["description"], str):
            variants.add("description-type")
        if "$ref" in sub and not isinstance(sub["$ref"], str):
            variants.add("ref-type")
    return variants


def _check_codex_parse(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        schema = _schema(tool)
        if schema is None:
            continue
        variants = _codex_parse_variants(schema)
        if variants:
            yield Hit(_where(tool), ", ".join(sorted(variants)), frozenset(variants))


def _check_depth(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        depth = json_depth(tool.get("inputSchema"))
        tool_depth = json_depth(tool)
        variants = set()
        # The Anthropic API rejects request bodies nested more than 127
        # levels; the schema sits three levels below the body root.
        if depth >= _DEPTH_LIMIT:
            variants.add("cc")
        # Codex (rmcp/serde_json) fails once the tool object is 125 levels deep.
        if tool_depth >= _DEPTH_LIMIT:
            variants.add("cx")
        if variants:
            detail = f"schema depth {depth}, tool depth {tool_depth}"
            yield Hit(_where(tool), detail, frozenset(variants))


_INTEGER_KEYWORDS = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "default",
    "const",
)


def _unsafe_int(value: Any) -> bool:
    return _is_int(value) and abs(value) > SAFE_INT


def _check_unsafe_integers(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        found: list[str] = []
        for pointer, sub, _ in _dict_subschemas(_schema(tool)):
            for keyword in _INTEGER_KEYWORDS:
                value = sub.get(keyword)
                if _unsafe_int(value):
                    found.append(f"{pointer or '/'} {keyword}")
            enum = sub.get("enum")
            if isinstance(enum, list) and any(_unsafe_int(item) for item in enum):
                found.append(f"{pointer or '/'} enum")
        if found:
            yield Hit(_where(tool), ", ".join(found[:3]))


# Matches id, ids, ID, user_id and camelCase userId; not words such as "paid".
_ID_NAME = re.compile(r"^[iI][dD][sS]?$|_[iI][dD]$|[a-z0-9]I[dD]$")


def _check_integer_ids(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        names: list[str] = []
        for _, properties in iter_property_maps(_schema(tool)):
            for name, prop in properties.items():
                if not isinstance(prop, dict) or "integer" not in _types(
                    prop.get("type")
                ):
                    continue
                if _ID_NAME.search(name) or prop.get("format") in ("int64", "uint64"):
                    names.append(_short(name, 40))
        if names:
            yield Hit(_where(tool), "integer " + ", ".join(names[:3]))


def _check_proto(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        for pointer, properties in iter_property_maps(_schema(tool)):
            if "__proto__" in properties:
                yield Hit(_where(tool, pointer), "property __proto__")
                break


def _check_unicode(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        variants: set[str] = set()
        examples: list[str] = []
        for text in _strings(tool):
            if unicodedata.normalize("NFKC", text) != text:
                variants.add("nfkc")
                examples.append(_short(text, 40))
            if any(unicodedata.category(char) == "Cf" for char in text):
                variants.add("format-chars")
                examples.append(_short(text, 40))
        for _, properties in iter_property_maps(_schema(tool)):
            normalized = [unicodedata.normalize("NFKC", key) for key in properties]
            if len(set(normalized)) < len(normalized):
                variants.add("key-collision")
        if variants:
            detail = ", ".join(sorted(variants))
            if examples:
                detail += "; " + ", ".join(dict.fromkeys(examples[:3]))
            yield Hit(_where(tool), detail, frozenset(variants))


# --- codex projection ------------------------------------------------------------


def _check_dropped_constraints(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        found: set[str] = set()
        for _, sub, _ in _dict_subschemas(_schema(tool)):
            found.update(CODEX_CONSTRAINT_KEYWORDS.intersection(sub))
        if found:
            yield Hit(_where(tool), ", ".join(sorted(found)))


def _check_boolean_subschema(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        schema = _schema(tool)
        if schema is None:
            continue
        pointers = [
            pointer
            for pointer, sub, position in iter_subschemas(schema)
            if isinstance(sub, bool) and position != "additionalProperties"
        ]
        if pointers:
            yield Hit(_where(tool), ", ".join(pointers[:3]))


def _check_array_items(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        pointers: list[str] = []
        for pointer, sub, _ in _dict_subschemas(_schema(tool)):
            no_items = "items" not in sub
            if (
                "prefixItems" in sub
                or ("array" in _types(sub.get("type")) and no_items)
                or ("contains" in sub and no_items)
            ):
                pointers.append(pointer or "/")
        if pointers:
            yield Hit(_where(tool), ", ".join(pointers[:3]))


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def _check_const_enum(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        pointers: list[str] = []
        for pointer, sub, _ in _dict_subschemas(_schema(tool)):
            if "const" in sub and not isinstance(sub["const"], str):
                pointers.append(f"{pointer or '/'} const")
            enum = sub.get("enum")
            if isinstance(enum, list) and any(
                not isinstance(item, str) for item in enum
            ):
                mixed = len({_json_type(item) for item in enum}) > 1
                if "type" not in sub or mixed:
                    pointers.append(f"{pointer or '/'} enum")
        if pointers:
            yield Hit(_where(tool), ", ".join(pointers[:3]))


def _nested_objects(schema: dict[str, Any]) -> bool:
    for pointer, sub, _ in _dict_subschemas(schema):
        is_object = "object" in _types(sub.get("type")) or "properties" in sub
        if is_object and pointer.count("/properties/") >= 3:
            return True
    return False


def _compactable(schema: dict[str, Any]) -> bool:
    """Whether Codex compaction has anything to remove from the schema."""

    for _, sub, _ in _dict_subschemas(schema):
        if any(key in sub for key in ("description", "$defs", "definitions")):
            return True
        if any(keyword in sub for keyword in COMBINATORS):
            return True
    return _nested_objects(schema)


def _check_compaction(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        schema = _schema(tool)
        if schema is None or not _shallow(schema) or not _compactable(schema):
            continue
        size = codex_normalized_size(schema)
        variants = set()
        if size > 4000:
            variants.add("over4000")
        if size > 5000:
            variants.add("over5000")
        if not variants:
            continue
        detail = f"normalized {size} bytes"
        if _nested_objects(schema):
            detail += "; nested objects at depth >=3"
        if any(
            keyword in sub
            for _, sub, _ in _dict_subschemas(schema)
            for keyword in COMBINATORS
        ):
            detail += "; anyOf/oneOf/allOf present"
        yield Hit(_where(tool), detail, frozenset(variants))


def _check_ts_size(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        count = count_properties(_schema(tool))
        if count > 700:
            yield Hit(_where(tool), f"{count} properties")


_INDEX = re.compile(r"0|[1-9][0-9]*", re.ASCII)


def _resolve(schema: dict[str, Any], ref: str) -> tuple[bool, Any]:
    if not ref.startswith("#/"):
        return False, None
    node: Any = schema
    for raw in ref[2:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif (
            isinstance(node, list)
            and len(part) <= 9  # no list is that long; int() caps digits
            and _INDEX.fullmatch(part)
            and int(part) < len(node)
        ):
            node = node[int(part)]
        else:
            return False, None
    return True, node


def _refs(schema: Any) -> Iterator[str]:
    for _, sub, _ in _dict_subschemas(schema):
        if isinstance(sub.get("$ref"), str):
            yield sub["$ref"]


def _recursive(schema: dict[str, Any], start: str) -> bool:
    seen: set[str] = set()
    pending = [start]
    while pending:
        ref = pending.pop()
        found, target = _resolve(schema, ref)
        if not found:
            continue
        for inner in _refs(target):
            if inner == start:
                return True
            if inner not in seen:
                seen.add(inner)
                pending.append(inner)
    return False


def _check_refs(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        schema = _schema(tool)
        if schema is None:
            continue
        variants: set[str] = set()
        refs: list[str] = []
        for _, sub, _ in _dict_subschemas(schema):
            if "$dynamicRef" in sub:
                variants.add("unresolved")
            ref = sub.get("$ref")
            if not isinstance(ref, str):
                continue
            variants.add("any-ref")
            refs.append(_short(ref, 40))
            if not _resolve(schema, ref)[0]:
                variants.add("unresolved")
            elif _recursive(schema, ref):
                variants.add("recursive")
        if variants:
            detail = ", ".join(sorted(variants - {"any-ref"}) or ["$ref"])
            if refs:
                detail += "; " + ", ".join(dict.fromkeys(refs[:3]))
            yield Hit(_where(tool), detail, frozenset(variants))


# --- names ---------------------------------------------------------------------


def cc_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


def cx_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _named(snapshot: Snapshot) -> Iterator[tuple[dict[str, Any], str]]:
    for tool in _tools(snapshot):
        name = tool.get("name")
        if isinstance(name, str):
            yield tool, name


def _check_name_length(snapshot: Snapshot) -> Iterator[Hit]:
    server = snapshot.server_name
    for tool, name in _named(snapshot):
        full = f"mcp__{cc_name(server)}__{cc_name(name)}"
        codex = f"mcp__{cx_name(server)}__{cx_name(name)}"
        variants = set()
        if len(full) > 128:
            variants.add("over128")
        if len(codex) > 64:
            variants.add("over64")
        if variants:
            detail = (
                f"full name {len(full)} chars "
                f"(server name {_short(server, 60)} assumed; set --server-name)"
            )
            yield Hit(_where(tool), detail, frozenset(variants))


def _check_name_sanitized(snapshot: Snapshot) -> Iterator[Hit]:
    for tool, name in _named(snapshot):
        variants = set()
        if cc_name(name) != name:
            variants.add("cc")
        if cx_name(name) != name:
            variants.add("cx")
        if variants:
            yield Hit(
                _where(tool),
                f"rewritten as {_short(cx_name(name))}",
                frozenset(variants),
            )


def _check_name_collision(snapshot: Snapshot) -> Iterator[Hit]:
    names = [name for _, name in _named(snapshot)]
    groups: dict[tuple[str, str], list[str]] = {}
    for name in names:
        groups.setdefault(("duplicate", name), []).append(name)
        groups.setdefault(("cc-collision", cc_name(name)), []).append(name)
        groups.setdefault(("cx-collision", cx_name(name)), []).append(name)
    # One hit per colliding group of names, carrying every way it collides.
    merged: dict[tuple[str, ...], set[str]] = {}
    for (variant, _), members in groups.items():
        distinct = tuple(dict.fromkeys(members))
        if variant == "duplicate" and len(members) > 1:
            merged.setdefault(distinct, set()).add(variant)
        elif variant != "duplicate" and len(distinct) > 1:
            merged.setdefault(distinct, set()).add(variant)
    for group, variants in merged.items():
        if variants == {"duplicate"}:
            count = names.count(group[0])
            detail = f"{count} tools named {_short(group[0], 60)}"
        else:
            detail = ", ".join(_short(name, 40) for name in group[:3])
        yield Hit("tools/list", detail, frozenset(variants))


def _check_name_empty(snapshot: Snapshot) -> Iterator[Hit]:
    for tool, name in _named(snapshot):
        if name == "":
            yield Hit(_where(tool), "empty name")


def _check_name_double_underscore(snapshot: Snapshot) -> Iterator[Hit]:
    for tool, name in _named(snapshot):
        if "__" in name:
            yield Hit(_where(tool), "name contains __")


# --- text ----------------------------------------------------------------------


def _check_description_length(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        description = tool.get("description")
        if not isinstance(description, str):
            continue
        variants = set()
        if len(description) > 2048:
            variants.add("over2048")
        if len(description) > 4096:
            variants.add("over4096")
        if variants:
            yield Hit(_where(tool), f"{len(description)} chars", frozenset(variants))


def _check_instructions_length(snapshot: Snapshot) -> Iterator[Hit]:
    instructions = snapshot.initialize.get("instructions")
    if not isinstance(instructions, str):
        return
    size = utf8_size(instructions)
    variants = set()
    if len(instructions) > 2048:
        variants.add("over2048")
    if len(instructions) > 4096:
        variants.add("over4096")
    if size > 8192:
        variants.add("large")
    if size > 524288:
        variants.add("over512k")
    if variants:
        yield Hit(
            "initialize",
            f"instructions {len(instructions)} chars, {size} bytes",
            frozenset(variants),
        )


def _check_description_missing(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        if "description" not in tool:
            yield Hit(_where(tool), "no description")
        elif isinstance(tool["description"], str) and not tool["description"].strip():
            yield Hit(_where(tool), "empty description")


def _unpaired_surrogate(value: Any) -> bool:
    return any(
        any("\ud800" <= char <= "\udfff" for char in text) for text in _strings(value)
    )


def _check_unpaired_surrogates(snapshot: Snapshot) -> Iterator[Hit]:
    # json.loads keeps an unpaired \uD800-\uDFFF escape as a lone surrogate.
    if _unpaired_surrogate(snapshot.initialize):
        yield Hit(
            "initialize",
            "unpaired UTF-16 surrogate in the initialize result",
            frozenset({"initialize"}),
        )
    for index, tool in enumerate(snapshot.tools.items):
        if _unpaired_surrogate(tool):
            where = (
                _where(tool) if isinstance(tool, dict) else f"tools/list item {index}"
            )
            yield Hit(where, "unpaired UTF-16 surrogate", frozenset({"tools"}))


# --- result hints ----------------------------------------------------------------


def _check_output_schema(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        if isinstance(tool.get("outputSchema"), dict):
            yield Hit(_where(tool), "outputSchema declared")


def _check_output_schema_type(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        schema = tool.get("outputSchema")
        if isinstance(schema, dict) and schema.get("type") != "object":
            yield Hit(
                _where(tool), f"outputSchema type {_short(schema.get('type'), 40)}"
            )


def _check_fractional_priority(snapshot: Snapshot) -> Iterator[Hit]:
    for method, listing in (
        ("resources/list", snapshot.resources),
        ("resources/templates/list", snapshot.templates),
    ):
        if listing is None:
            continue
        for item in listing.items:
            annotations = item.get("annotations") if isinstance(item, dict) else None
            priority = (
                annotations.get("priority") if isinstance(annotations, dict) else None
            )
            if isinstance(priority, float) and not priority.is_integer():
                yield Hit(method, f"priority {priority!r}")
                break


def _check_resource_entries(snapshot: Snapshot) -> Iterator[Hit]:
    for method, listing, key in (
        ("resources/list", snapshot.resources, "uri"),
        ("resources/templates/list", snapshot.templates, "uriTemplate"),
    ):
        if listing is None:
            continue
        bad = [
            index
            for index, item in enumerate(listing.items)
            if not isinstance(item, dict)
            or not isinstance(item.get(key), str)
            or not isinstance(item.get("name"), str)
        ]
        if bad:
            shown = ", ".join(str(index) for index in bad[:5])
            yield Hit(method, f"{len(bad)} invalid entries (index {shown})")


def _check_prompts(snapshot: Snapshot) -> Iterator[Hit]:
    if snapshot.prompts is not None and snapshot.prompts.items:
        yield Hit("prompts/list", f"{len(snapshot.prompts.items)} prompts")


def _check_safety_annotations(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        annotations = tool.get("annotations")
        if not isinstance(annotations, dict) or not (
            "readOnlyHint" in annotations or "destructiveHint" in annotations
        ):
            yield Hit(_where(tool), "no readOnlyHint or destructiveHint")


# --- catalogue -----------------------------------------------------------------

_CC_REJECTS_LIST = "Claude Code rejects the whole tools/list."
_CC_ROOT_OBJECT = (
    'Claude Code requires type "object" at the root; '
    "one such tool hides every tool on the server."
)
_CODEX_SKIPS = "Codex skips this tool."
_CODEX_PARSE_FAILURE = "Codex cannot convert this schema and skips the tool."
# Codex reads only tools/list at startup; prompts are never requested and
# resources only when the model asks, so their pagination cannot drop tools.
_CODEX_LISTS = ("tools/list",)
_CODEX_RENAMED = (
    "Codex truncates the name and appends a hash; the model sees a different name."
)
# From 2.1.250 the newer MCP client fixed these, but sessions still on the
# older client (a per-session rollout; they skip the server/discover probe)
# keep the old behaviour; observed live on 2.1.287 and 2.1.302.
_CC_LEGACY_CLIENT = (
    "Claude Code sessions still on its older MCP client "
    "(rollout-gated; they skip the server/discover probe) "
)
_PARSE_VARIANTS = (
    "items-list",
    "required-type",
    "enum-type",
    "additional-type",
    "properties-type",
    "combinator-type",
    "description-type",
    "ref-type",
    "null-root",
)

RULES: tuple[Rule, ...] = (
    # protocol
    Rule(
        "initialize-server-info",
        "serverInfo missing or malformed",
        "protocol",
        (
            CC(
                None,
                None,
                "V",
                "L",
                "Claude Code rejects the initialize result; every tool on the server is lost.",
            ),
            CX(
                None,
                None,
                "V",
                "L",
                "Codex (rmcp) fails to parse initialize; the server does not start.",
            ),
        ),
        "Return serverInfo with string name and version.",
        _check_server_info,
    ),
    Rule(
        "tools-capability-missing",
        "tools capability missing or not an object",
        "protocol",
        (
            CC(
                None,
                None,
                "V",
                "L",
                "Claude Code never calls tools/list without the tools capability; the server shows 0 tools.",
                when=("undeclared",),
            ),
            CC(
                None,
                None,
                "V",
                "L",
                "Claude Code rejects capabilities.tools that is not an object; the server shows 0 tools.",
                when=("not-object",),
            ),
            CX(
                None,
                None,
                "V",
                "L",
                "Codex (rmcp) fails to parse initialize when capabilities.tools is not an object; the server does not start.",
                when=("not-object",),
            ),
        ),
        "Declare capabilities.tools as an object in initialize.",
        _check_tools_capability,
    ),
    Rule(
        "discover-probe-unanswered",
        "server/discover probe not answered",
        "protocol",
        (
            CC(
                "2.1.221",
                None,
                "D",
                "S",
                "Claude Code probes server/discover first; with no reply it kills and respawns a stdio server (startup runs twice) or reconnects over HTTP.",
            ),
        ),
        "Answer unknown methods immediately with JSON-RPC error -32601.",
        _check_discover_unanswered,
    ),
    Rule(
        "modern-protocol-result-fields",
        "server speaks MCP 2026-07-28",
        "protocol",
        (
            CC(
                "2.1.287",
                "2.1.302",
                "V",
                "L",
                "This server speaks MCP 2026-07-28; Claude Code sessions that probe server/discover (rollout-gated) before 2.1.302 drop every tool when tools/list omits resultType, ttlMs or cacheScope.",
            ),
        ),
        'Under 2026-07-28 include resultType, ttlMs and cacheScope ("public" or "private") in every list result.',
        _check_discover_result,
    ),
    Rule(
        "list-changed-ignored",
        "tools.listChanged declared",
        "protocol",
        (
            CX(
                None,
                None,
                "A",
                "S",
                "Codex never refetches tools after notifications/tools/list_changed and caches catalogs for 30 minutes.",
            ),
        ),
        "Expose every tool at startup instead of adding tools later.",
        _check_list_changed,
    ),
    # listing
    Rule(
        "pagination-multi-page",
        "tools/list spans several pages",
        "listing",
        (
            CC(
                None,
                "2.1.144",
                "T",
                "I",
                "Claude Code before 2.1.144 reads only the first tools/list page.",
            ),
            CX(
                None,
                "0.162.0",
                "T",
                "L",
                "Codex before 0.162.0 reads only the first tools/list page.",
            ),
        ),
        "Return all tools in one page.",
        _check_multi_page,
    ),
    Rule(
        "pagination-over-20-pages",
        "tools/list spans more than 20 pages",
        "listing",
        (
            CC(
                "2.1.144",
                None,
                "T",
                "L",
                "Claude Code stops after 20 tools/list pages; later tools are ignored.",
            ),
        ),
        "Use at most 20 pages (larger pages).",
        _check_over_20_pages,
    ),
    Rule(
        "pagination-over-100-pages",
        "listing spans more than 100 pages",
        "listing",
        (
            CX(
                "0.162.0",
                None,
                "V",
                "L",
                "Codex rejects catalogs longer than 100 pages; the server's tools are excluded.",
                when=_CODEX_LISTS,
            ),
        ),
        "Use fewer, larger pages.",
        _check_over_100_pages,
    ),
    Rule(
        "pagination-repeated-cursor",
        "nextCursor repeats",
        "listing",
        (
            CX(
                "0.162.0",
                None,
                "V",
                "L",
                "Codex rejects a catalog whose nextCursor repeats.",
                when=_CODEX_LISTS,
            ),
            CC(
                None,
                "2.1.295",
                "A",
                "I",
                "Claude Code before 2.1.295 re-requests a repeated cursor up to 20 times per connect.",
            ),
        ),
        "Never return the same nextCursor twice.",
        _check_repeated_cursor,
    ),
    Rule(
        "pagination-cursor-too-long",
        "cursor longer than 64 KiB",
        "listing",
        (
            CX(
                "0.162.0",
                None,
                "V",
                "L",
                "Codex rejects cursors longer than 64 KiB.",
                when=_CODEX_LISTS,
            ),
        ),
        "Keep cursors short.",
        _check_cursor_length,
    ),
    Rule(
        "tool-count-over-2048",
        "more than 2048 tools",
        "listing",
        (
            CX(
                "0.147.0",
                None,
                "V",
                "S",
                "Codex rejects tools/list catalogs with more than 2048 tools; every tool on the server is lost.",
            ),
        ),
        "Split the server or expose a search tool plus an executor.",
        _check_tool_count,
    ),
    Rule(
        "listing-too-large",
        "tool listing larger than 16 MB",
        "listing",
        (
            CC(
                "2.1.296",
                None,
                "T",
                "S",
                "Claude Code uses only about the first 16 MB of a listing; later tools are skipped.",
            ),
        ),
        "Shrink tool definitions.",
        _check_listing_size,
    ),
    Rule(
        "tools-list-failed",
        "tools/list failed",
        "listing",
        (
            CC(
                None,
                None,
                "V",
                "L",
                "tools/list failed; Claude Code shows no tools for this server.",
            ),
            CX(
                None,
                None,
                "V",
                "L",
                "tools/list failed; Codex shows no tools for this server.",
            ),
        ),
        "Make tools/list succeed.",
        _check_tools_list_failed,
    ),
    # root schema
    Rule(
        "tool-name-invalid-type",
        "tool name missing or not a string",
        "root-schema",
        (
            CC(
                None,
                None,
                "V",
                "S",
                "One tool without a string name makes Claude Code reject the whole tools/list.",
            ),
            CX(None, None, "V", "S", "Codex (rmcp) rejects the whole tools/list."),
        ),
        "Give every tool a string name.",
        _check_name_type,
    ),
    Rule(
        "tool-metadata-types",
        "tool metadata has the wrong type",
        "root-schema",
        (
            CC(
                None,
                None,
                "V",
                "L",
                "Claude Code rejects the whole tools/list when one tool has wrongly typed metadata; every tool is lost.",
            ),
            CX(
                None,
                None,
                "V",
                "L",
                "Codex (rmcp) fails to parse tools/list; the server does not start.",
            ),
        ),
        "Fix the types (hints are booleans, title is a string, _meta is an object, icons need src).",
        _check_metadata_types,
    ),
    Rule(
        "tool-description-not-string",
        "tool description not a string",
        "root-schema",
        (
            CC(
                None,
                None,
                "V",
                "L",
                "A non-string description makes Claude Code reject the whole tools/list.",
            ),
        ),
        "Omit description or make it a string.",
        _check_description_type,
    ),
    Rule(
        "tool-execution-invalid",
        "tool execution malformed",
        "root-schema",
        (
            CC(None, None, "V", "L", _CC_REJECTS_LIST),
            CX(
                None,
                "0.147.0",
                "V",
                "L",
                "Codex before 0.147.0 rejects the whole tools/list.",
            ),
        ),
        "Use a valid execution.taskSupport or omit execution.",
        _check_execution,
    ),
    Rule(
        "input-schema-missing",
        "inputSchema missing or not an object",
        "root-schema",
        (
            CC(
                None,
                None,
                "V",
                "L",
                "One tool without an object inputSchema hides every tool on the server in Claude Code.",
            ),
            CX(
                None,
                None,
                "V",
                "L",
                "Codex (rmcp) fails to parse tools/list; the server does not start.",
            ),
        ),
        'Always send inputSchema, at minimum {"type":"object","properties":{}}.',
        _check_input_schema_missing,
    ),
    Rule(
        "root-type-missing",
        "inputSchema root has no type",
        "root-schema",
        (CC(None, None, "V", "L", _CC_ROOT_OBJECT),),
        'Add "type": "object" at the schema root.',
        _check_root_type_missing,
    ),
    Rule(
        "root-type-not-object",
        "inputSchema root type is not object",
        "root-schema",
        (
            CC(None, None, "V", "L", _CC_ROOT_OBJECT),
            CX(
                None,
                None,
                "D",
                "L",
                "Codex code mode renders a non-object argument type, and the OpenAI API rejects non-object roots in direct mode.",
            ),
        ),
        'Use exactly "type": "object" at the root.',
        _check_root_type_not_object,
    ),
    Rule(
        "root-properties-not-object",
        "root properties not an object",
        "root-schema",
        (
            CC(None, None, "V", "L", _CC_REJECTS_LIST),
            CX(None, None, "T", "L", _CODEX_SKIPS),
        ),
        'Use "properties": {}.',
        _check_root_properties,
    ),
    Rule(
        "root-required-null",
        "root required is null",
        "root-schema",
        (CC(None, None, "V", "L", _CC_REJECTS_LIST),),
        'Use "required": [] or omit it.',
        _check_root_required_null,
    ),
    Rule(
        "root-required-not-array",
        "root required not an array of strings",
        "root-schema",
        (
            CC(None, None, "V", "L", _CC_REJECTS_LIST),
            CX(None, None, "T", "L", _CODEX_SKIPS),
        ),
        "Use an array of property names.",
        _check_root_required_type,
    ),
    Rule(
        "root-combinator",
        "anyOf/oneOf/allOf at the schema root",
        "root-schema",
        (
            CC(
                None,
                "2.1.200",
                "S",
                "L",
                "Older Claude Code sends root anyOf/oneOf/allOf and the Anthropic API rejects every request.",
            ),
            CC(
                "2.1.200",
                None,
                "D",
                "L",
                "Claude Code flattens root combinators: properties are merged, anyOf/oneOf requirements become optional, and a constraint note is added to the description (rollout-gated; with the gate off the tool is dropped).",
            ),
            CX(
                None,
                None,
                "D",
                "L",
                "Codex code mode renders anyOf/oneOf alternatives instead of the properties; property names are lost.",
                when=("anyOf+properties", "oneOf+properties"),
            ),
        ),
        "Make the alternatives optional properties, enforce the choice in the handler, and state it in the description.",
        _check_root_combinator,
    ),
    Rule(
        "root-conditionals",
        "conditional keywords at the schema root",
        "root-schema",
        (
            CX(
                None,
                None,
                "D",
                "L",
                "Codex drops this constraint; the model never sees it.",
            ),
        ),
        "Describe the constraint in the tool or property description.",
        _check_root_conditionals,
    ),
    # schema validity
    Rule(
        "schema-invalid-2020-12",
        "inputSchema invalid under JSON Schema 2020-12",
        "schema-validity",
        (
            CC(
                None,
                "2.1.216",
                "S",
                "L",
                "The Anthropic API rejects schemas invalid under JSON Schema 2020-12; every request fails while this tool is loaded.",
            ),
            CC(
                "2.1.216",
                None,
                "T",
                "L",
                "Claude Code silently drops tools whose schema is invalid under JSON Schema 2020-12 (rollout-gated).",
            ),
        ),
        "Make the schema valid JSON Schema 2020-12 (for example, unique required entries, numeric exclusiveMinimum, prefixItems instead of list items).",
        _check_invalid_2020_12,
    ),
    Rule(
        "property-key-invalid",
        "property key outside ^[a-zA-Z0-9_.-]{1,64}$",
        "schema-validity",
        (
            CC(
                None,
                "2.1.0",
                "S",
                "L",
                "The Anthropic API rejects property keys outside ^[a-zA-Z0-9_.-]{1,64}$; every request fails.",
                when=("raw",),
            ),
            CC(
                "2.1.0",
                "2.1.216",
                "S",
                "L",
                "The Anthropic API rejects property keys outside ^[a-zA-Z0-9_.-]{1,64}$ even after Claude Code NFKC-normalizes them; every request fails.",
                when=("after-nfkc",),
            ),
            CC(
                "2.1.216",
                None,
                "T",
                "L",
                "Claude Code silently drops tools with property keys outside ^[a-zA-Z0-9_.-]{1,64}$ (rollout-gated).",
                when=("after-nfkc",),
            ),
        ),
        "Rename the properties (for example ids instead of ids[]).",
        _check_property_keys,
    ),
    Rule(
        "codex-schema-parse-failure",
        "schema Codex cannot convert",
        "schema-validity",
        (
            CX(None, None, "T", "L", _CODEX_PARSE_FAILURE, when=_PARSE_VARIANTS),
            CX("0.155.0", None, "T", "L", _CODEX_PARSE_FAILURE, when=("min-items",)),
        ),
        "Fix the malformed keyword.",
        _check_codex_parse,
    ),
    Rule(
        "json-depth-too-deep",
        "schema nested too deeply",
        "schema-validity",
        (
            CC(
                None,
                None,
                "S",
                "L",
                "The Anthropic API rejects request bodies nested more than 127 levels; every request fails.",
                when=("cc",),
            ),
            CX(
                None,
                None,
                "V",
                "L",
                "Codex cannot parse tools/list nested this deeply; the server does not start.",
                when=("cx",),
            ),
        ),
        "Flatten the schema or use $defs with $ref.",
        _check_depth,
    ),
    Rule(
        "integer-literal-unsafe",
        "integer literal beyond ±(2^53−1)",  # noqa: RUF001
        "schema-validity",
        (
            CC(
                None,
                None,
                "D",
                "L",
                "Claude Code parses JSON with doubles; this integer is rounded before it reaches the API.",
            ),
        ),
        "Keep integer literals within ±(2^53−1), or use strings.",  # noqa: RUF001
        _check_unsafe_integers,
    ),
    Rule(
        "integer-id-precision",
        "64-bit id declared as integer",
        "schema-validity",
        (
            CC(
                None, None, "A", "L", "Claude Code rounds integer arguments above 2^53."
            ),
            CX(
                None,
                None,
                "A",
                "S",
                "Codex code mode rounds integer arguments above 2^53.",
            ),
        ),
        "Declare 64-bit ids as type string.",
        _check_integer_ids,
    ),
    Rule(
        "property-named-proto",
        "property named __proto__",
        "schema-validity",
        (
            CC(
                None,
                None,
                "D",
                "L",
                "Claude Code deletes __proto__ keys; the property disappears but stays required.",
            ),
        ),
        "Rename the property.",
        _check_proto,
    ),
    Rule(
        "unicode-normalization",
        "strings change under NFKC or carry format characters",
        "schema-validity",
        (
            CC(
                "2.1.0",
                None,
                "D",
                "L",
                "Claude Code NFKC-normalizes keys and values; enum values change (ﬁle→file) and keys can merge and overwrite each other.",
                when=("nfkc", "key-collision"),
            ),
            CC(
                "2.1.0",
                None,
                "D",
                "L",
                "Claude Code strips format and bidi characters.",
                when=("format-chars",),
            ),
        ),
        "Use plain ASCII for keys and enum values.",
        _check_unicode,
    ),
    # codex projection
    Rule(
        "codex-dropped-constraints",
        "constraints Codex drops",
        "codex-projection",
        (
            CX(
                None,
                None,
                "A",
                "L",
                "Codex drops these keywords; the model never sees the constraint.",
            ),
        ),
        "Repeat important constraints in the property description.",
        _check_dropped_constraints,
    ),
    Rule(
        "boolean-subschema",
        "boolean subschema",
        "codex-projection",
        (
            CX(
                None,
                None,
                "D",
                "L",
                'Codex turns boolean subschemas into {"type":"string"}.',
            ),
            CC(
                "2.1.0",
                "2.1.250",
                "V",
                "L",
                "This Claude Code version drops every tool on the server when a schema contains a boolean subschema.",
            ),
            CC(
                "2.1.250",
                None,
                "V",
                "L",
                _CC_LEGACY_CLIENT
                + "drop every tool on the server when a schema contains a boolean subschema.",
            ),
        ),
        "Use {} for any value; remove never-valid properties.",
        _check_boolean_subschema,
    ),
    Rule(
        "array-items-degraded",
        "array element type Codex cannot show",
        "codex-projection",
        (
            CX(
                None,
                None,
                "D",
                "L",
                "Codex shows this array to the model as Array<string>.",
            ),
        ),
        "Declare items with the real element type; use objects instead of tuples.",
        _check_array_items,
    ),
    Rule(
        "const-enum-coerced",
        "const or enum Codex coerces to string",
        "codex-projection",
        (CX(None, None, "D", "L", "Codex coerces this const or enum to type string."),),
        "Give enums an explicit, uniform type.",
        _check_const_enum,
    ),
    Rule(
        "codex-schema-compaction",
        "input schema Codex compacts",
        "codex-projection",
        (
            CX(
                "0.144.0",
                None,
                "D",
                "L",
                "Codex compacts input schemas over 5000 bytes: descriptions, $defs, objects nested 3+ levels and combinators are removed in that order until it fits (raise with mcp_servers.<name>.tool_input_schema_max_bytes from 0.158.0).",
                when=("over5000",),
            ),
            CX(
                "0.134.0",
                "0.144.0",
                "D",
                "S",
                "Codex compacts input schemas over 4000 bytes.",
                when=("over4000",),
            ),
        ),
        "Shrink the schema below 5000 bytes; put enums directly on properties instead of anyOf with null.",
        _check_compaction,
    ),
    Rule(
        "codex-ts-too-large",
        "argument type too large for Codex code mode",
        "codex-projection",
        (
            CX(
                None,
                None,
                "D",
                "S",
                "Codex code mode renders argument types over 16,000 bytes as unknown.",
            ),
        ),
        "Split the tool.",
        _check_ts_size,
    ),
    Rule(
        "codex-unresolvable-ref",
        "$ref Codex cannot resolve",
        "codex-projection",
        (
            CX(
                None,
                None,
                "D",
                "L",
                "Codex shows unresolvable references as unknown.",
                when=("unresolved",),
            ),
            CX(
                None,
                None,
                "D",
                "L",
                "Codex expands recursive references two levels, then unknown.",
                when=("recursive",),
            ),
            CX(
                None,
                "0.134.0",
                "D",
                "L",
                "Codex before 0.134.0 does not resolve $ref.",
                when=("any-ref",),
            ),
        ),
        "Use local $defs references and avoid recursion.",
        _check_refs,
    ),
    # names
    Rule(
        "tool-name-too-long",
        "full tool name too long",
        "names",
        (
            CC(
                None,
                "2.1.292",
                "S",
                "L",
                "The Anthropic API rejects tool names over 128 characters; every request fails.",
                when=("over128",),
            ),
            CC(
                "2.1.292",
                None,
                "T",
                "L",
                "Claude Code silently drops tools whose full name exceeds 128 characters.",
                when=("over128",),
            ),
            CX("0.149.0", None, "D", "L", _CODEX_RENAMED, when=("over128",)),
            CX(None, "0.149.0", "D", "L", _CODEX_RENAMED, when=("over64",)),
        ),
        "Keep mcp__<server>__<tool> within 128 characters (64 for older Codex).",
        _check_name_length,
    ),
    Rule(
        "tool-name-sanitized",
        "tool name rewritten by the harness",
        "names",
        (
            CC(
                None,
                None,
                "A",
                "L",
                "Claude Code shows this tool under a rewritten name.",
                when=("cc",),
            ),
            CX(
                None,
                None,
                "A",
                "L",
                "Codex shows this tool under a rewritten name (- becomes _).",
                when=("cx",),
            ),
        ),
        "Use snake_case ASCII names.",
        _check_name_sanitized,
    ),
    Rule(
        "tool-name-collision",
        "tool names collide",
        "names",
        (
            CC(
                None,
                "2.1.50",
                "S",
                "L",
                "The Anthropic API rejects duplicate tool names; every request fails.",
                when=("duplicate", "cc-collision"),
            ),
            CC(
                "2.1.50",
                None,
                "T",
                "L",
                "Claude Code keeps only the first tool; calls to the others go to it.",
                when=("duplicate", "cc-collision"),
            ),
            CX(
                None,
                "0.119.0",
                "T",
                "S",
                "Codex drops the second tool.",
                when=("cx-collision",),
            ),
            CX(
                "0.119.0",
                None,
                "D",
                "L",
                "Codex keeps both under hash-suffixed names.",
                when=("cx-collision",),
            ),
            CX(None, None, "T", "L", "Codex keeps one copy.", when=("duplicate",)),
        ),
        "Make names unique after replacing characters outside [A-Za-z0-9_] with _.",
        _check_name_collision,
    ),
    Rule(
        "tool-name-empty",
        "empty tool name",
        "names",
        (
            CC(
                None,
                None,
                "A",
                "L",
                "Claude Code exposes this tool as mcp__<server>__.",
            ),
            CX(None, None, "D", "L", "Codex exposes this tool as _."),
        ),
        "Give the tool a name.",
        _check_name_empty,
    ),
    Rule(
        "tool-name-double-underscore",
        "tool name contains __",
        "names",
        (
            CC(
                None,
                None,
                "A",
                "I",
                "__ separates server and tool in Claude Code; permission rules and hooks can misread it.",
            ),
        ),
        "Avoid __ in tool names.",
        _check_name_double_underscore,
    ),
    # text
    Rule(
        "description-too-long",
        "tool description truncated",
        "text",
        (
            CC(
                "2.1.84",
                "2.1.296",
                "D",
                "L",
                "Claude Code truncates tool descriptions after 2048 characters.",
                when=("over2048",),
            ),
            CC(
                "2.1.296",
                None,
                "D",
                "L",
                "Claude Code truncates tool descriptions after 4096 characters (16384 once loaded through tool search).",
                when=("over4096",),
            ),
        ),
        "Put the essentials first and keep descriptions within 2048 characters.",
        _check_description_length,
    ),
    Rule(
        "instructions-too-long",
        "server instructions too long",
        "text",
        (
            CC(
                "2.1.200",
                "2.1.296",
                "D",
                "S",
                "Claude Code truncates server instructions after 2048 characters.",
                when=("over2048",),
            ),
            CC(
                "2.1.296",
                None,
                "D",
                "L",
                "Claude Code truncates server instructions after 4096 characters.",
                when=("over4096",),
            ),
            CX(
                None,
                None,
                "A",
                "S",
                "Codex sends server instructions with every turn.",
                when=("large",),
            ),
            CX(
                "0.147.0",
                None,
                "D",
                "S",
                "Codex truncates server instructions at 512 KiB.",
                when=("over512k",),
            ),
        ),
        "Keep instructions short.",
        _check_instructions_length,
    ),
    Rule(
        "description-missing",
        "tool description missing",
        "text",
        (
            CC(
                None,
                None,
                "A",
                "S",
                "Claude Code loads MCP tools on demand by searching names and descriptions; this tool is hard to find.",
            ),
            CX(
                None,
                None,
                "A",
                "S",
                "Codex finds MCP tools by searching names and descriptions; this tool is hard to find.",
            ),
        ),
        "Write a description that says what the tool does.",
        _check_description_missing,
    ),
    Rule(
        "unpaired-surrogate",
        "unpaired UTF-16 surrogate escape",
        "text",
        (
            CX(
                None,
                None,
                "V",
                "L",
                "Codex rejects JSON with an unpaired UTF-16 surrogate escape; the server does not start.",
            ),
            CC(
                None,
                "2.1.200",
                "S",
                "L",
                "Older Claude Code sends unpaired surrogates and the Anthropic API rejects every request.",
            ),
            CC(
                "2.1.200",
                None,
                "D",
                "L",
                "Claude Code strips unpaired surrogates from tool definitions.",
                when=("tools",),
            ),
        ),
        "Send valid Unicode; never emit a lone \\uD800-\\uDFFF escape.",
        _check_unpaired_surrogates,
    ),
    # result hints
    Rule(
        "output-schema-structured-only",
        "outputSchema: only structuredContent reaches the model",
        "result-hints",
        (
            CC(
                None,
                None,
                "A",
                "S",
                "When a result has structuredContent, Claude Code gives the model only that; text content is dropped.",
            ),
            CX(
                None,
                None,
                "A",
                "S",
                "In direct mode Codex gives the model only structuredContent.",
            ),
        ),
        "Put everything the model needs into structuredContent.",
        _check_output_schema,
    ),
    Rule(
        "output-schema-not-object",
        "outputSchema root type is not object",
        "result-hints",
        (
            CC(
                None,
                "2.1.250",
                "V",
                "L",
                "This Claude Code version drops every tool on the server when an outputSchema root is not type object.",
            ),
            CC(
                "2.1.250",
                None,
                "V",
                "L",
                _CC_LEGACY_CLIENT
                + "drop every tool on the server when an outputSchema root is not type object.",
            ),
        ),
        'Use type "object" at the outputSchema root.',
        _check_output_schema_type,
    ),
    Rule(
        "fractional-priority",
        "fractional resource priority",
        "result-hints",
        (
            CX(
                None,
                "0.163.0",
                "D",
                "I",
                'Codex before 0.163.0 fails to parse fractional priorities ("Unexpected response type").',
            ),
        ),
        "Use 0 or 1 for priority, or omit it.",
        _check_fractional_priority,
    ),
    Rule(
        "resource-entry-invalid",
        "resource entry malformed",
        "result-hints",
        (
            CC(None, None, "D", "S", "Claude Code rejects this resource listing."),
            CX(None, None, "D", "S", "Codex rejects this resource listing."),
        ),
        "Give every resource uri and name (templates: uriTemplate and name).",
        _check_resource_entries,
    ),
    Rule(
        "prompts-ignored-by-codex",
        "prompts invisible in Codex",
        "result-hints",
        (
            CX(
                None,
                None,
                "A",
                "S",
                "Codex never requests prompts; they are invisible there.",
            ),
        ),
        "Don't put core functionality only in prompts.",
        _check_prompts,
    ),
    Rule(
        "missing-safety-annotations",
        "no readOnlyHint or destructiveHint",
        "result-hints",
        (
            CX(
                None,
                None,
                "A",
                "S",
                "Codex asks for approval on every call to unannotated tools and won't run them in parallel.",
            ),
        ),
        "Set readOnlyHint (and destructiveHint) annotations.",
        _check_safety_annotations,
    ),
)

RULE_ORDER = {rule.id: index for index, rule in enumerate(RULES)}


def _applies(effect: Effect, hit: Hit, targets: list[Target]) -> bool:
    if effect.when is not None and not effect.when & hit.variants:
        return False
    return any(
        target.harness == effect.harness and effect.range.contains(target.version)
        for target in targets
    )


def evaluate(
    snapshot: Snapshot, targets: list[Target], ignore: set[str]
) -> list[Finding]:
    findings: list[Finding] = []
    for rule in RULES:
        if rule.id in ignore:
            continue
        for hit in rule.check(snapshot):
            effects = tuple(e for e in rule.effects if _applies(e, hit, targets))
            if not effects:
                continue
            severity = min(
                (SEVERITY[e.impact] for e in effects), key=SEVERITY_RANK.__getitem__
            )
            findings.append(Finding(rule, hit, effects, severity))
    return findings
