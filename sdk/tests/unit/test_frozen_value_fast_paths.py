"""The exact-type fast paths in ``m3._types.base`` must agree with the general
isinstance-based rules they shortcut, for every value shape."""

from __future__ import annotations

import enum
import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, Field

from m3._types import base
from m3._types.base import FrozenModel, _FrozenMapping


# Reference rules: the general-path implementations, without fast paths.
def _reference_json_safe(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, datetime):
        return True
    if isinstance(value, enum.Enum):
        return _reference_json_safe(value.value)
    if isinstance(value, FrozenModel):
        return True
    if isinstance(value, BaseModel):
        return all(
            _reference_json_safe(getattr(value, name))
            for name, info in type(value).model_fields.items()
            if not info.exclude
        )
    if isinstance(value, Mapping):
        return all(
            isinstance(key, str) and _reference_json_safe(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(_reference_json_safe(item) for item in value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return all(_reference_json_safe(item) for item in value)
    return False


def _reference_deep_freeze(value: Any) -> Any:
    if isinstance(value, _FrozenMapping) or isinstance(value, BaseModel):
        return value
    if isinstance(value, Mapping):
        return _FrozenMapping(
            {
                _reference_deep_freeze(key): _reference_deep_freeze(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_reference_deep_freeze(item) for item in value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_reference_deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        items = (_reference_deep_freeze(item) for item in value)
        return tuple(sorted(items, key=lambda item: (type(item).__name__, repr(item))))
    return value


def _reference_deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            _reference_deep_thaw(key): _reference_deep_thaw(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, frozenset)):
        return [_reference_deep_thaw(item) for item in value]
    return value


class _Color(enum.Enum):
    RED = "red"
    ONE = 1
    NAN = float("nan")


class _Text(str):
    pass


class _Number(int):
    pass


class _Inner(FrozenModel):
    name: str = "inner"


_leaves = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=5),
    st.builds(_Text, st.text(max_size=3)),
    st.builds(_Number, st.integers(-5, 5)),
    st.sampled_from(list(_Color)),
    st.just(datetime(2026, 1, 1, tzinfo=timezone.utc)),
    st.binary(max_size=3),
    st.just(_Inner()),
    st.just(object()),
)
_keys = st.one_of(
    st.text(max_size=4), st.builds(_Text, st.text(max_size=3)), st.integers(0, 3)
)
_values = st.recursive(
    _leaves,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.lists(children, max_size=4).map(tuple),
        st.dictionaries(_keys, children, max_size=4),
        st.dictionaries(_keys, children, max_size=4).map(_FrozenMapping),
        st.frozensets(st.one_of(st.integers(0, 5), st.text(max_size=2)), max_size=4),
        st.sets(st.one_of(st.integers(0, 5), st.text(max_size=2)), max_size=4),
    ),
    max_leaves=12,
)


def _shape(value: Any) -> Any:
    """Comparable structure: container types, scalar types, NaN equal to NaN."""
    if isinstance(value, _FrozenMapping):
        return ("frozen", tuple((_shape(k), _shape(v)) for k, v in value._items))
    if isinstance(value, dict):
        return ("dict", tuple((_shape(k), _shape(v)) for k, v in value.items()))
    if isinstance(value, tuple):
        return ("tuple", tuple(_shape(item) for item in value))
    if isinstance(value, list):
        return ("list", tuple(_shape(item) for item in value))
    if isinstance(value, float) and math.isnan(value):
        return ("nan",)
    if type(value) is object:
        return ("object", id(value))
    return (type(value).__name__, value)


@settings(max_examples=600, deadline=None)
@given(_values)
def test_json_safety_matches_the_general_rules(value: Any) -> None:
    assert base._json_safe(value) == _reference_json_safe(value)


@settings(max_examples=600, deadline=None)
@given(_values)
def test_freezing_matches_the_general_rules(value: Any) -> None:
    assert _shape(base._deep_freeze(value)) == _shape(_reference_deep_freeze(value))


@settings(max_examples=600, deadline=None)
@given(_values)
def test_single_pass_check_and_freeze_matches_check_then_freeze(value: Any) -> None:
    frozen = base._safe_freeze(value)
    if _reference_json_safe(value):
        assert _shape(frozen) == _shape(_reference_deep_freeze(value))
    else:
        assert frozen is base._NOT_JSON_SAFE


@settings(max_examples=600, deadline=None)
@given(_values)
def test_thawing_matches_the_general_rules(value: Any) -> None:
    frozen = _reference_deep_freeze(value)
    assert _shape(base._deep_thaw(frozen)) == _shape(_reference_deep_thaw(frozen))


class _Holder(FrozenModel):
    value: Any = None
    hidden: Any = Field(default=None, exclude=True)


@settings(max_examples=400, deadline=None)
@given(_values, _values)
def test_model_fields_freeze_and_reject_like_the_general_rules(
    value: Any, hidden: Any
) -> None:
    if not _reference_json_safe(value):
        with pytest.raises(ValueError, match="non-JSON-serializable"):
            _Holder(value=value, hidden=hidden)
        return
    model = _Holder(value=value, hidden=hidden)
    assert _shape(model.value) == _shape(_reference_deep_freeze(value))
    # Excluded fields skip the JSON-safety check but are still frozen.
    assert _shape(model.hidden) == _shape(_reference_deep_freeze(hidden))
