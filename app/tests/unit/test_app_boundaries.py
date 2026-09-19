def test_application_imports_and_uses_sdk() -> None:
    from m3 import MCPTestKit
    from m3_app.api import create_app

    assert create_app and MCPTestKit


def test_application_openapi_uses_distribution_version() -> None:
    import importlib.metadata

    from m3_app.api import create_app

    assert create_app().openapi()["info"]["version"] == importlib.metadata.version("m3")
