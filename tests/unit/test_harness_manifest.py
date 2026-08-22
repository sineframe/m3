import json

import pytest

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
