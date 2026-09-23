"""Optional, deterministic Hypothesis strategies for JSON Schema values.

Hypothesis is imported only when a strategy is requested.  The base SDK can
therefore import this module without installing the ``property`` extra.
"""

from __future__ import annotations

import importlib
import math
import re
import warnings
from collections.abc import Mapping
from datetime import timezone
from typing import Any


def _hypothesis() -> Any:
    try:
        from hypothesis import strategies as st
    except ImportError:
        raise ImportError(
            "schema strategies require the 'property' extra: sf-m3[property]"
        ) from None
    return st


def _jsonschema() -> Any:
    try:
        jsonschema = importlib.import_module("jsonschema")
    except ImportError:
        raise ImportError("schema strategies require jsonschema") from None
    return jsonschema


def _resolve(schema: Any, root: Any) -> Any:
    if not isinstance(schema, Mapping) or not isinstance(schema.get("$ref"), str):
        return schema
    reference = schema["$ref"]
    if not reference.startswith("#/"):
        raise ValueError("only local JSON Schema references are supported")
    value = root
    for part in reference[2:].split("/"):
        if not isinstance(value, Mapping):
            raise ValueError("JSON Schema reference does not resolve to a mapping")
        key = part.replace("~1", "/").replace("~0", "~")
        if key not in value:
            raise ValueError("JSON Schema local reference is missing")
        value = value[key]
    return value


def _validates(schema: Any, value: Any, root: Any) -> bool:
    if isinstance(schema, Mapping) and schema.get("nullable") is True and value is None:
        return True
    jsonschema = _jsonschema()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        resolver = (
            jsonschema.RefResolver.from_schema(root)
            if isinstance(root, Mapping)
            else None
        )
    validator = jsonschema.Draft202012Validator(schema, resolver=resolver)
    return bool(validator.is_valid(value))


def _any_json(st: Any, depth: int = 0) -> Any:
    if depth >= 3:
        return st.one_of(
            st.none(), st.booleans(), st.integers(-10, 10), st.text(max_size=8)
        )
    child = _any_json(st, depth + 1)
    return st.one_of(
        st.none(),
        st.booleans(),
        st.integers(-100, 100),
        st.floats(allow_nan=False, allow_infinity=False, width=32),
        st.text(max_size=16),
        st.lists(child, max_size=4),
        st.dictionaries(st.text(min_size=1, max_size=8), child, max_size=4),
    )


def _string_strategy(schema: Mapping[str, Any], st: Any) -> Any:
    minimum = int(schema.get("minLength", 0))
    maximum = int(schema.get("maxLength", 32))
    if maximum < minimum:
        raise ValueError("string maxLength is less than minLength")
    fmt = schema.get("format")
    if fmt == "email":
        strategy = st.from_regex(
            r"[A-Za-z0-9][A-Za-z0-9._%+-]{0,15}@[A-Za-z0-9.-]+\.[A-Za-z]{2,8}",
            fullmatch=True,
        )
    elif fmt == "ipv4":
        strategy = st.tuples(
            st.integers(0, 255),
            st.integers(0, 255),
            st.integers(0, 255),
            st.integers(0, 255),
        ).map(lambda parts: ".".join(str(part) for part in parts))
    elif fmt == "ipv6":
        strategy = st.just("2001:db8::1")
    elif fmt == "uuid":
        strategy = st.uuids().map(str)
    elif fmt == "date":
        strategy = st.dates().map(str)
    elif fmt == "date-time":
        strategy = st.datetimes(timezones=st.just(timezone.utc)).map(
            lambda value: value.isoformat().replace("+00:00", "Z")
        )
    elif fmt in {"uri", "uri-reference", "url"}:
        strategy = st.just("https://example.test/resource")
    else:
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                strategy = st.from_regex(pattern, fullmatch=True)
            except re.error as error:
                raise ValueError("schema string pattern is invalid") from error
        else:
            strategy = st.text()
    strategy = strategy.filter(lambda value: minimum <= len(value) <= maximum)
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and fmt is not None:
        try:
            strategy = strategy.filter(
                lambda value: re.search(pattern, value) is not None
            )
        except re.error as error:
            raise ValueError("schema string pattern is invalid") from error
    return strategy


def _numeric_strategy(schema: Mapping[str, Any], st: Any, *, integer: bool) -> Any:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if schema.get("exclusiveMinimum") is not None:
        minimum = (
            schema["exclusiveMinimum"]
            if minimum is None
            else max(minimum, schema["exclusiveMinimum"])
        )
        if integer:
            minimum = math.floor(float(minimum)) + 1
    if schema.get("exclusiveMaximum") is not None:
        maximum = (
            schema["exclusiveMaximum"]
            if maximum is None
            else min(maximum, schema["exclusiveMaximum"])
        )
        if integer:
            maximum = math.ceil(float(maximum)) - 1
    multiple = schema.get("multipleOf")
    if multiple is not None:
        multiple_value = float(multiple)
        if multiple_value <= 0 or not math.isfinite(multiple_value):
            raise ValueError("multipleOf must be a positive finite number")
        low = (
            math.ceil(float(minimum) / multiple_value) if minimum is not None else -100
        )
        high = (
            math.floor(float(maximum) / multiple_value) if maximum is not None else 100
        )
        if high < low:
            raise ValueError("numeric schema has no values satisfying its bounds")
        strategy = st.integers(low, high).map(
            lambda value: (
                int(value * multiple_value) if integer else value * multiple_value
            )
        )
    elif integer:
        strategy = st.integers(min_value=minimum, max_value=maximum)
    else:
        strategy = st.floats(
            allow_nan=False,
            allow_infinity=False,
            min_value=minimum,
            max_value=maximum,
            width=32,
        )
    if schema.get("exclusiveMinimum") is not None and not integer:
        strategy = strategy.filter(lambda value: value > schema["exclusiveMinimum"])
    if schema.get("exclusiveMaximum") is not None and not integer:
        strategy = strategy.filter(lambda value: value < schema["exclusiveMaximum"])
    return strategy


def _stable_key(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, default=repr)


def _valid_strategy(schema: Any, st: Any, root: Any) -> Any:
    if isinstance(schema, bool):
        return _any_json(st) if schema else st.nothing()
    if not isinstance(schema, Mapping):
        raise TypeError("schema must be a mapping or boolean")
    resolved = _resolve(schema, root)
    if resolved is not schema:
        return _valid_strategy(resolved, st, root)
    if "const" in schema:
        return st.just(schema["const"])
    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not values:
            raise ValueError("schema enum must be a non-empty list")
        return st.sampled_from(values)
    branches = schema.get("anyOf")
    if isinstance(branches, list) and branches:
        strategy = st.one_of(
            *[_valid_strategy(branch, st, root) for branch in branches]
        )
    elif isinstance(schema.get("oneOf"), list) and schema["oneOf"]:
        branches = schema["oneOf"]
        strategy = st.one_of(
            *[_valid_strategy(branch, st, root) for branch in branches]
        )
        strategy = strategy.filter(
            lambda value: (
                sum(_validates(branch, value, root) for branch in branches) == 1
            )
        )
    elif isinstance(schema.get("allOf"), list) and schema["allOf"]:
        strategy = _valid_strategy(schema["allOf"][0], st, root).filter(
            lambda value: _validates(schema, value, root)
        )
    elif "not" in schema:
        strategy = _any_json(st).filter(
            lambda value: not _validates(schema["not"], value, root)
        )
    else:
        kind = schema.get("type")
        if isinstance(kind, list):
            strategy = st.one_of(
                *[_valid_strategy({**schema, "type": item}, st, root) for item in kind]
            )
        elif kind == "object":
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            if (
                not isinstance(properties, Mapping)
                or not isinstance(required, list)
                or not all(isinstance(name, str) for name in required)
            ):
                raise ValueError("object schema properties and required are invalid")
            if not set(required).issubset(properties):
                raise ValueError("schema requires properties that are not declared")
            required_fields = {
                name: _valid_strategy(properties[name], st, root) for name in required
            }
            optional_fields = {
                name: _valid_strategy(value, st, root)
                for name, value in properties.items()
                if name not in required
            }
            strategy = st.fixed_dictionaries(required_fields, optional=optional_fields)
            additional = schema.get("additionalProperties", True)
            if additional is not False:
                key_strategy = st.text(min_size=1, max_size=8).filter(
                    lambda key: key not in properties
                )
                additional_schema = (
                    additional if isinstance(additional, (Mapping, bool)) else True
                )
                extra = st.dictionaries(
                    key_strategy,
                    _valid_strategy(additional_schema, st, root),
                    max_size=4,
                )
                strategy = st.builds(
                    lambda known, extras: {**extras, **known}, strategy, extra
                )
            minimum = int(schema.get("minProperties", 0))
            maximum = int(schema.get("maxProperties", 1000))
            strategy = strategy.filter(lambda value: minimum <= len(value) <= maximum)
        elif kind == "array":
            minimum = int(schema.get("minItems", 0))
            maximum = int(schema.get("maxItems", 4))
            if maximum < minimum:
                raise ValueError("array maxItems is less than minItems")
            strategy = st.lists(
                _valid_strategy(schema.get("items", {}), st, root),
                min_size=minimum,
                max_size=maximum,
            )
            if schema.get("uniqueItems"):
                strategy = strategy.filter(
                    lambda value: (
                        len({_stable_key(item) for item in value}) == len(value)
                    )
                )
        elif kind == "string":
            strategy = _string_strategy(schema, st)
        elif kind == "integer":
            strategy = _numeric_strategy(schema, st, integer=True)
        elif kind == "number":
            strategy = _numeric_strategy(schema, st, integer=False)
        elif kind == "boolean":
            strategy = st.booleans()
        elif kind == "null":
            strategy = st.none()
        elif kind is None:
            strategy = _any_json(st)
        else:
            raise ValueError(f"unsupported JSON schema type: {kind!r}")
    if schema.get("nullable") is True:
        strategy = st.one_of(st.none(), strategy)
    return strategy.filter(lambda value: _validates(schema, value, root))


def _wrong_type_strategy(kind: str, st: Any) -> Any:
    candidates: dict[str, Any] = {
        "object": st.one_of(
            st.none(), st.booleans(), st.integers(), st.text(), st.lists(st.none())
        ),
        "array": st.one_of(
            st.none(),
            st.booleans(),
            st.integers(),
            st.text(),
            st.dictionaries(st.text(min_size=1, max_size=4), st.none()),
        ),
        "string": st.one_of(
            st.none(),
            st.booleans(),
            st.integers(),
            st.dictionaries(st.text(min_size=1, max_size=4), st.none()),
        ),
        "integer": st.one_of(
            st.none(),
            st.booleans(),
            st.text(),
            st.dictionaries(st.text(min_size=1, max_size=4), st.none()),
        ),
        "number": st.one_of(
            st.none(),
            st.booleans(),
            st.text(),
            st.dictionaries(st.text(min_size=1, max_size=4), st.none()),
        ),
        "boolean": st.one_of(st.none(), st.integers(), st.text(), st.lists(st.none())),
        "null": st.one_of(st.booleans(), st.integers(), st.text(), st.lists(st.none())),
    }
    return candidates[kind]


def invalid_schema_strategy(schema: dict[str, Any] | bool) -> Any:
    """Build a strategy whose generated values fail the supplied schema."""
    st = _hypothesis()
    if schema is True:
        raise ValueError("a schema accepting every value has no invalid instance")
    if schema is False:
        return _any_json(st)
    if not isinstance(schema, dict):
        raise TypeError("schema must be a mapping")
    kind = schema.get("type")
    if isinstance(kind, list):
        strategy = _any_json(st)
    elif "const" in schema:
        strategy = _any_json(st).filter(lambda value: value != schema["const"])
    elif isinstance(schema.get("enum"), list):
        values = schema["enum"]
        strategy = _any_json(st).filter(lambda value: value not in values)
    elif "not" in schema:
        strategy = _valid_strategy(schema["not"], st, schema)
    elif isinstance(schema.get("allOf"), list) and schema["allOf"]:
        strategy = _any_json(st)
    elif isinstance(schema.get("anyOf"), list) and schema["anyOf"]:
        strategy = _any_json(st)
    elif isinstance(schema.get("oneOf"), list) and schema["oneOf"]:
        strategy = _any_json(st)
    elif kind == "object" and schema.get("required"):
        required = schema["required"]
        if not isinstance(required, list) or not required:
            raise ValueError("object required must be a non-empty list")
        strategy = _valid_strategy(schema, st, schema).map(
            lambda value: {
                key: item for key, item in value.items() if key != required[0]
            }
        )
    elif kind == "object" and schema.get("additionalProperties") is False:
        strategy = st.dictionaries(
            st.text(min_size=1, max_size=8), st.none(), min_size=1, max_size=1
        )
    elif kind == "array" and int(schema.get("minItems", 0)) > 0:
        strategy = st.just([])
    elif kind == "array" and "maxItems" in schema:
        size = int(schema["maxItems"]) + 1
        strategy = st.lists(st.none(), min_size=size, max_size=size)
    elif kind == "string" and int(schema.get("minLength", 0)) > 0:
        strategy = st.just("")
    elif kind == "string" and "maxLength" in schema:
        strategy = st.just("x" * (int(schema["maxLength"]) + 1))
    elif kind == "string" and isinstance(schema.get("pattern"), str):
        pattern = schema["pattern"]
        strategy = st.text(max_size=16).filter(
            lambda value: re.search(pattern, value) is None
        )
    elif kind in {"integer", "number"} and schema.get("minimum") is not None:
        strategy = st.just(
            float(schema["minimum"]) - 1
            if kind == "number"
            else int(schema["minimum"]) - 1
        )
    elif kind in {"integer", "number"} and schema.get("maximum") is not None:
        strategy = st.just(
            float(schema["maximum"]) + 1
            if kind == "number"
            else int(schema["maximum"]) + 1
        )
    elif isinstance(kind, str) and kind in {
        "object",
        "array",
        "string",
        "integer",
        "number",
        "boolean",
        "null",
    }:
        strategy = _wrong_type_strategy(kind, st)
    else:
        raise ValueError(
            "schema has no supported constraint from which to derive an invalid value"
        )
    return strategy.filter(lambda value: not _validates(schema, value, schema))


def schema_strategy(schema: dict[str, Any] | bool, *, valid: bool = True) -> Any:
    """Build a Hypothesis strategy for valid or intentionally invalid values."""
    st = _hypothesis()
    if not isinstance(schema, (dict, bool)):
        raise TypeError("schema must be a mapping")
    return (
        _valid_strategy(schema, st, schema)
        if valid
        else invalid_schema_strategy(schema)
    )


__all__ = ["invalid_schema_strategy", "schema_strategy"]
