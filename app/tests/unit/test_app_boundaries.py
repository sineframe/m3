def test_application_imports_and_uses_sdk() -> None:
    from mcp_pal import MCPTestKit
    from mcp_pal_app.api import create_app

    assert create_app and MCPTestKit


def test_application_openapi_uses_distribution_version() -> None:
    import importlib.metadata

    from mcp_pal_app.api import create_app

    assert create_app().openapi()["info"]["version"] == importlib.metadata.version(
        "mcp-pal"
    )
