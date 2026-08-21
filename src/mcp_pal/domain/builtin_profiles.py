"""Canonical MCP profiles shipped with MCP Pal."""

EXCALIDRAW_PROFILE_ID = "00000000-0000-4000-8000-000000000001"
EXCALIDRAW_REVISION_ID = "00000000-0000-4000-8000-000000000002"
EXCALIDRAW_MCP_CONFIG = {
    "mcpServers": {
        "excalidraw": {
            "type": "http",
            "url": "https://mcp.excalidraw.com/mcp",
        }
    }
}

__all__ = [
    "EXCALIDRAW_MCP_CONFIG",
    "EXCALIDRAW_PROFILE_ID",
    "EXCALIDRAW_REVISION_ID",
]
