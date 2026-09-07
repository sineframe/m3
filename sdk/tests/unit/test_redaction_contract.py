"""Adversarial contract tests for the Phase 4 redaction boundary."""

from __future__ import annotations

import json
import pickle
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr, model_serializer

from mcp_pal.trace.redaction import (
    ArtifactBytesResult,
    REDACTED,
    RedactionConfig,
    RedactionError,
    assertion_view,
    redact,
    redact_artifact_bytes,
    redact_model_json,
    redact_raw_evidence,
    redact_repr,
    redacted_json,
    serialize_redacted,
    project_redacted,
)


def test_nested_shapes_exact_secrets_keys_headers_and_urls_are_redacted() -> None:
    secret = "exact-secret-value"
    config = RedactionConfig(
        secrets=frozenset({secret}),
        sensitive_keys=frozenset({"internal_marker"}),
        credential_file_contents=frozenset({"credential-file-content"}),
        include_environment=False,
    )
    source = {
        "headers": {
            "Authorization": "Bearer exact-secret-value",
            "Cookie": "sid=exact-secret-value",
            "X-Request-ID": "keep-me",
        },
        "header_pairs": [("Authorization", "Bearer exact-secret-value"), ("X-Request-ID", "keep-me")],
        "credential_file": "credential-file-content",
        "internal_marker": "configured-secret",
        "url": "https://user:password@example.test/mcp?token=query-secret&ok=1",
        "nested": ["exact-secret-value", ("credential-file-content", "ordinary")],
    }

    value, paths = redact(source, config=config)

    assert value["headers"]["Authorization"] == REDACTED
    assert value["headers"]["Cookie"] == REDACTED
    assert value["headers"]["X-Request-ID"] == "keep-me"
    assert value["header_pairs"][0] == ("Authorization", REDACTED)
    assert value["header_pairs"][1] == ("X-Request-ID", "keep-me")
    assert value["credential_file"] == REDACTED
    assert value["internal_marker"] == REDACTED
    assert "password" not in value["url"]
    assert "user" not in value["url"]
    assert "token=%5BREDACTED%5D" in value["url"]
    assert value["nested"][1] == (REDACTED, "ordinary")
    assert paths
    assert secret not in redacted_json(source, config=config)


class Credentials(BaseModel):
    api_key: SecretStr
    note: str


def test_pydantic_models_and_exceptions_are_structurally_redacted() -> None:
    model = Credentials(api_key=SecretStr("model-secret"), note="model-secret appears here")
    error = ValueError("request failed with model-secret")
    error.details = {"authorization": "model-secret"}  # type: ignore[attr-defined]

    value, _ = redact({"model": model, "error": error}, secrets={"model-secret"})

    assert value["model"]["api_key"] == REDACTED
    assert value["model"]["note"] == f"{REDACTED} appears here"
    assert value["error"]["message"] == f"request failed with {REDACTED}"
    assert value["error"]["details"]["authorization"] == REDACTED
    assert "model-secret" not in repr(value)


class ReprLeaker:
    def __repr__(self) -> str:
        return "ReprLeaker(secret=repr-secret)"


class BrokenRepr:
    def __repr__(self) -> str:
        raise RuntimeError("repr-secret")


def test_repr_projection_redacts_and_repr_failure_is_fail_closed() -> None:
    assert "repr-secret" not in redact_repr(ReprLeaker(), config=RedactionConfig(secrets=frozenset({"repr-secret"})))
    with pytest.raises(RedactionError, match="object representation failed") as caught:
        redact_repr(BrokenRepr(), config=RedactionConfig(secrets=frozenset({"repr-secret"})))
    assert "repr-secret" not in str(caught.value)


def test_unknown_values_and_malformed_urls_never_fall_back_to_raw() -> None:
    class Opaque:
        pass

    with pytest.raises(RedactionError):
        redact({"opaque": Opaque()})
    with pytest.raises(RedactionError, match="malformed URL"):
        redact("https://[malformed")


def test_raw_evidence_and_all_serialization_projections_are_safe() -> None:
    config = RedactionConfig(secrets=frozenset({"wire-secret"}), include_environment=False)
    evidence = {"raw_event": {"params": {"token": "wire-secret"}}, "result": ["wire-secret"]}
    raw = redact_raw_evidence(evidence, config=config)
    api = serialize_redacted(evidence, config=config)

    assert raw == api
    assert json.dumps(raw)
    assert "wire-secret" not in redacted_json(evidence, config=config)
    for projection in ("persistence", "export", "log", "api", "ui", "raw_evidence"):
        projected = project_redacted(evidence, projection=projection, config=config)
        assert "wire-secret" not in json.dumps(projected)


def test_assertion_view_is_only_explicit_in_process_holder() -> None:
    original = {"answer": "assertion-secret"}
    view = assertion_view(original)
    assert view.value is original
    assert "assertion-secret" not in repr(view)
    with pytest.raises(TypeError):
        pickle.dumps(view)
    safe, _ = redact(view, secrets={"assertion-secret"})
    assert safe == {"answer": REDACTED}


def test_compatibility_call_can_disable_ambient_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_TOKEN", "ambient-secret")
    safe, _ = redact("ambient-secret", secrets=set())
    assert safe == "ambient-secret"
    safe_default, _ = redact("ambient-secret")
    assert safe_default == REDACTED


class HostileMapping(Mapping[str, str]):
    def __getitem__(self, key: str) -> str:
        raise RuntimeError("mapping-secret")

    def __iter__(self) -> Any:
        return iter(("payload",))

    def __len__(self) -> int:
        return 1


class HostileModel(BaseModel):
    @model_serializer(mode="plain")
    def _serialize(self) -> Any:
        raise RuntimeError("model-secret")


class HostileException(Exception):
    def __str__(self) -> str:
        raise RuntimeError("exception-secret")


class HostileList(list[str]):
    def __iter__(self) -> Any:
        raise RuntimeError("list-secret")


def test_hostile_mapping_model_exception_and_container_fail_value_free() -> None:
    hostile_values: tuple[Any, ...] = (HostileMapping(), HostileModel(), HostileException(), HostileList())
    for hostile in hostile_values:
        with pytest.raises(RedactionError) as caught:
            redact(hostile)
        message = str(caught.value)
        assert all(secret not in message for secret in ("mapping-secret", "model-secret", "exception-secret", "list-secret"))


def test_model_json_helper_redacts_before_json_validation_and_rejects_constructed_opaque_values() -> None:
    model = Credentials(api_key=SecretStr("model-secret"), note="model-secret")
    projected = redact_model_json(model, config=RedactionConfig(secrets=frozenset({"model-secret"})))
    assert projected == {"api_key": REDACTED, "note": REDACTED}

    from mcp_pal.types import Event, EventId, EventKind, ExecutionId

    malformed = Event.model_construct(
        event_id=EventId("malformed-json"),
        execution_id=ExecutionId("execution-json"),
        sequence=0,
        kind=EventKind.DIAGNOSTIC,
        monotonic_offset_ms=0.0,
        payload={"raw": object()},
    )
    with pytest.raises(RedactionError) as caught:
        redact_model_json(malformed)
    assert "object" not in str(caught.value)


def test_cycles_and_excessive_depth_fail_closed_without_recursion_error() -> None:
    cyclic: list[Any] = []
    cyclic.append(cyclic)
    with pytest.raises(RedactionError, match="cyclic value"):
        redact(cyclic)

    deep: Any = "leaf-secret"
    for _ in range(80):
        deep = [deep]
    with pytest.raises(RedactionError, match="maximum nesting depth") as caught:
        redact(deep, secrets={"leaf-secret"})
    assert "leaf-secret" not in str(caught.value)


def test_key_collision_after_secret_redaction_fails_closed() -> None:
    with pytest.raises(RedactionError, match="mapping key collision") as caught:
        redact({"collision-secret": 1, REDACTED: 2}, secrets={"collision-secret"})
    assert "collision-secret" not in str(caught.value)


def test_set_projection_is_deterministic_and_opaque_binary_fails_closed() -> None:
    first, _ = redact({"items": {"z", "a", "m"}})
    second, _ = redact({"items": {"m", "z", "a"}})
    assert first == second

    with pytest.raises(RedactionError, match="opaque binary") as caught:
        redact(b"\xffbinary-secret", secrets={"binary-secret"})
    assert "binary-secret" not in str(caught.value)


def test_artifact_bytes_redact_binary_canaries_longest_first_and_preserve_other_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARTIFACT_TOKEN", "ambient-secret")
    config = RedactionConfig.from_environment(
        secrets=("abc", "bc", "configured-secret"),
        credential_file_contents=("file-secret",),
    )
    source = b"\x00\xffabc-abc-bc-configured-secret-file-secret-ambient-secret\x80"

    result = redact_artifact_bytes(source, config=config)

    assert isinstance(result, ArtifactBytesResult)
    assert result.data.startswith(b"\x00\xff")
    assert result.data.endswith(b"\x80")
    assert b"abc" not in result.data
    assert b"configured-secret" not in result.data
    assert b"file-secret" not in result.data
    assert b"ambient-secret" not in result.data
    assert result.redacted_count == 6
    assert result.metadata["original_length"] == len(source)
    assert result.metadata["output_length"] == len(result.data)
    assert result.metadata["redacted_count"] == result.redacted_count
    representation = repr(result)
    assert all(secret not in representation for secret in ("abc", "bc", "configured-secret", "file-secret", "ambient-secret"))


def test_artifact_bytes_no_secret_roundtrip_and_opaque_binary_is_allowed() -> None:
    source = b"\x00\xff\x80opaque-binary\x01"
    result = redact_artifact_bytes(source, config=RedactionConfig(secrets=frozenset({"not-present"})))

    assert result.data == source
    assert result.value == source
    assert result.redacted_count == 0
    assert result.original_length == len(source)
    assert repr(result) == "ArtifactBytesResult(original_length=17, output_length=17, redacted_count=0)"


def test_redaction_error_path_is_sanitized_for_hostile_keys() -> None:
    with pytest.raises(RedactionError) as caught:
        redact(object(), path="$.payload-secret", secrets={"payload-secret"})

    assert "payload-secret" not in caught.value.path
    assert "payload-secret" not in repr(caught.value)
    assert "payload-secret" not in caught.value.__dict__.get("path", "")

    hostile_reason = RedactionError("$.safe", "reason-secret")
    assert "reason-secret" not in str(hostile_reason)
    assert hostile_reason.reason == "value could not be safely redacted"


def test_exact_canaries_are_redacted_but_transformed_values_are_outside_contract() -> None:
    config = RedactionConfig(
        secrets=frozenset({"literal-canary", "reference-canary"}),
        include_environment=False,
    )
    projected, _ = redact(
        {
            "one_field": "literal-canary",
            "other_field": "prefix-reference-canary-suffix",
            "base64": "bGl0ZXJhbC1jYW5hcnk=",
            "hash": "f2f4d4f2",
        },
        config=config,
    )
    assert projected["one_field"] == REDACTED
    assert projected["other_field"] == f"prefix-{REDACTED}-suffix"
    # The contract is exact substring matching; it does not claim reversal
    # of base64, hashes, encryption, or other transformations.
    assert projected["base64"] == "bGl0ZXJhbC1jYW5hcnk="
    assert projected["hash"] == "f2f4d4f2"
