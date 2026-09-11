def test_application_imports_and_uses_sdk() -> None:
    from mcp_pal import MCPTestKit
    from mcp_pal_app.api import create_app
    from mcp_pal_app.persistence.database import Base, make_engine
    from mcp_pal_app.persistence.models import Run
    from mcp_pal_app.services.run_manager import RunManager

    assert create_app and Base and make_engine and Run and RunManager and MCPTestKit


def test_application_openapi_uses_distribution_version() -> None:
    import importlib.metadata

    from mcp_pal_app.api import create_app

    assert create_app().openapi()["info"]["version"] == importlib.metadata.version(
        "mcp-pal"
    )
