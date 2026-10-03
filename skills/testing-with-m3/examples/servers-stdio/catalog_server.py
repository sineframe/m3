"""MCP server whose tool list changes at runtime, used by the list-change guide."""

from __future__ import annotations

from mcp.server.mcpserver import Context, MCPServer

app = MCPServer("m3-catalog-example")


def refund_order(order_id: str) -> str:
    """Refund an order. Advertised only after admin tools are enabled."""
    return f"refunded {order_id}"


@app.tool()
async def enable_admin_tools(ctx: Context) -> str:
    """Advertise the admin tools and announce the tool-list change."""
    app.add_tool(refund_order)
    await ctx.notify_tools_changed()
    return "admin tools enabled"


if __name__ == "__main__":
    app.run("stdio")
