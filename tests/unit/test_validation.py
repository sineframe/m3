import os
import pytest
from mcp_pal.domain.validation import ProfileValidationError, referenced_environment_variables, selected_server_config, validate_mcp_config

def test_profile_validation_and_environment_references(monkeypatch):
    cfg={"mcpServers":{"draw":{"command":"node","args":[],"env":{"TOKEN":"${DRAW_TOKEN}"}}}}
    result=validate_mcp_config(cfg)
    assert result["missing_environment_variables"] == ["DRAW_TOKEN"]
    assert referenced_environment_variables(cfg) == ["DRAW_TOKEN"]
    assert selected_server_config(cfg,"draw")["mcpServers"]["draw"]["command"] == "node"

def test_invalid_profile_rejected():
    with pytest.raises(ProfileValidationError): validate_mcp_config({"mcpServers":{}})
    with pytest.raises(ProfileValidationError): validate_mcp_config({"mcpServers":{"bad name":{"command":"x"}}})

def test_http_and_oauth_validation():
    assert validate_mcp_config({"mcpServers":{"remote":{"type":"http","url":"https://example.test/mcp"}}})["valid"]
    with pytest.raises(ProfileValidationError): validate_mcp_config({"mcpServers":{"remote":{"type":"http","url":"https://example.test","oauth":{}}}})
