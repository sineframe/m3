import pytest

from mcp_pal.domain import validation
from mcp_pal.domain.validation import (
    ProfileValidationError,
    referenced_environment_variables,
    selected_server_config,
    validate_mcp_config,
)


def test_profile_shape_validation_is_environment_pure(monkeypatch):
    cfg = {
        "mcpServers": {
            "draw": {"command": "node", "args": [], "env": {"TOKEN": "${DRAW_TOKEN}"}}
        }
    }
    result = validate_mcp_config(cfg)
    assert result["missing_environment_variables"] == []
    assert referenced_environment_variables(cfg) == ["DRAW_TOKEN"]
    assert (
        selected_server_config(cfg, "draw")["mcpServers"]["draw"]["command"] == "node"
    )


def test_profile_readiness_check_reports_missing_references(monkeypatch):
    cfg = {
        "mcpServers": {"draw": {"command": "node", "env": {"TOKEN": "${DRAW_TOKEN}"}}}
    }
    monkeypatch.delenv("DRAW_TOKEN", raising=False)
    result = validate_mcp_config(cfg, check_environment=True)
    assert result["missing_environment_variables"] == ["DRAW_TOKEN"]


def test_profile_shape_validation_does_not_touch_environment(monkeypatch):
    monkeypatch.setattr(
        validation,
        "_check_refs",
        lambda *_: pytest.fail("shape validation inspected os.environ"),
    )
    cfg = {
        "mcpServers": {"draw": {"command": "node", "env": {"TOKEN": "${DRAW_TOKEN}"}}}
    }
    assert validate_mcp_config(cfg)["valid"] is True


def test_invalid_profile_rejected():
    with pytest.raises(ProfileValidationError):
        validate_mcp_config({"mcpServers": {}})
    with pytest.raises(ProfileValidationError):
        validate_mcp_config({"mcpServers": {"bad name": {"command": "x"}}})


def test_http_and_oauth_validation():
    assert validate_mcp_config(
        {"mcpServers": {"remote": {"type": "http", "url": "https://example.test/mcp"}}}
    )["valid"]
    with pytest.raises(ProfileValidationError):
        validate_mcp_config(
            {
                "mcpServers": {
                    "remote": {
                        "type": "http",
                        "url": "https://example.test",
                        "oauth": {},
                    }
                }
            }
        )
