def test_application_imports_and_uses_sdk() -> None:
    from m3 import MCPTestKit
    from m3_app.api import create_app

    assert create_app and MCPTestKit


def test_application_openapi_uses_distribution_version() -> None:
    import importlib.metadata

    from m3_app.api import create_app

    assert create_app().openapi()["info"]["version"] == importlib.metadata.version("m3")


def test_validation_location_does_not_keep_legacy_elicitation_policy() -> None:
    from m3_app.api.v2 import _safe_validation_location

    assert _safe_validation_location(("body", "elicitation_policy")) == "body.field"
