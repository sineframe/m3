def test_sdk_does_not_expose_application_modules():
    import importlib.util

    for name in (
        "mcp_pal.api",
        "mcp_pal.ui",
        "mcp_pal.main",
        "mcp_pal.persistence",
        "mcp_pal.config",
    ):
        assert importlib.util.find_spec(name) is None
