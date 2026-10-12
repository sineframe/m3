from __future__ import annotations

import pickle

import pytest

from m3._literal_capsule import CapsuleBytes


def test_private_capsule_never_enters_repr_or_pickle_and_is_consumed_once() -> None:
    capsule = CapsuleBytes(b"private-auth-value")
    assert "private-auth-value" not in repr(capsule)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(capsule)
    assert capsule.consume() == b"private-auth-value"
    with pytest.raises(RuntimeError, match="already consumed"):
        capsule.consume()


@pytest.mark.parametrize("value", [b"", b"x" * (8 * 1024 * 1024 + 1)])
def test_capsule_size_is_bounded(value: bytes) -> None:
    with pytest.raises(ValueError, match="size"):
        CapsuleBytes(value)
