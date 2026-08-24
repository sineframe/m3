import json

import pytest

from mcp_pal.harness import manifest
from mcp_pal.harness.manifest import ManifestValidationError, export_manifest, validate_manifest


def test_manifest_rejects_literal_secrets_and_unknown_fields():
    with pytest.raises(ManifestValidationError):
        validate_manifest({"command": "agent", "env": {"TOKEN": "secret"}})
    with pytest.raises(ManifestValidationError):
        validate_manifest({"command": "agent", "unexpected": True})


def test_manifest_export_contains_references_only():
    value = validate_manifest({"command": "agent", "env": {"TOKEN": "${TEAM_TOKEN}"}})
    exported = json.loads(export_manifest(value["manifest"]))
    assert exported["env"] == {"TOKEN": "${TEAM_TOKEN}"}
    assert "TEAM_TOKEN_VALUE" not in json.dumps(exported)


def test_manifest_shape_validation_is_ambient_pure(monkeypatch):
    monkeypatch.setattr(manifest, "_local_readiness", lambda *_: pytest.fail("shape validation inspected local state"))
    result = validate_manifest({"command": "agent", "env": {"TOKEN": "${TEAM_TOKEN}"}})
    assert result["valid"] is True
    assert result["local_ready"] is None
    assert result["missing_environment"] == []


def test_manifest_explicit_local_check_reports_missing_reference(monkeypatch):
    monkeypatch.delenv("TEAM_TOKEN", raising=False)
    result = validate_manifest({"command": "python", "env": {"TOKEN": "${TEAM_TOKEN}"}}, check_local=True)
    assert result["local_ready"] is False
    assert result["missing_environment"] == ["TEAM_TOKEN"]
