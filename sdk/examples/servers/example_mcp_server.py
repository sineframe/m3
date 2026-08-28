"""Small stateful MCP server used by the executable SDK examples.

Run this file directly to serve MCP over stdio. The examples intentionally use
the official low-level MCP server API so the tests cross a real process and
protocol boundary.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

_orders: dict[str, dict[str, Any]] = {}
_subscriptions: set[str] = set()
_session_observations: dict[str, Any] = {
    "logging_level": None,
    "progress_notifications": 0,
    "roots_changed": 0,
}


def _cursor(params: Any) -> str | None:
    value = getattr(params, "cursor", None)
    return value if isinstance(value, str) else None


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    output_properties: dict[str, Any] | None = None,
) -> types.Tool:
    output_schema = None
    if output_properties is not None:
        output_schema = {
            "type": "object",
            "properties": output_properties,
            "required": list(output_properties),
            "additionalProperties": False,
        }
    return types.Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
        output_schema=output_schema,
    )


_TOOLS = (
    _tool(
        "normalize_customer",
        "Convert a customer name into a stable identifier",
        {"name": {"type": "string", "minLength": 1}},
        required=["name"],
        output_properties={"customer_id": {"type": "string"}},
    ),
    _tool(
        "create_order",
        "Create an order for a normalized customer",
        {
            "customer_id": {"type": "string", "minLength": 1},
            "item": {"type": "string", "minLength": 1},
            "quantity": {"type": "integer", "minimum": 1},
        },
        required=["customer_id", "item", "quantity"],
        output_properties={"order_id": {"type": "string"}},
    ),
    _tool(
        "get_order",
        "Retrieve an order created in this server session",
        {"order_id": {"type": "string", "minLength": 1}},
        required=["order_id"],
        output_properties={
            "order_id": {"type": "string"},
            "customer_id": {"type": "string"},
            "item": {"type": "string"},
            "quantity": {"type": "integer"},
        },
    ),
    _tool(
        "shipping_quote",
        "Calculate a deterministic shipping quote",
        {
            "weight_kg": {"type": "number", "exclusiveMinimum": 0},
            "zone": {"type": "string", "enum": ["local", "regional", "international"]},
        },
        required=["weight_kg", "zone"],
        output_properties={
            "amount": {"type": "number"},
            "currency": {"type": "string"},
        },
    ),
    _tool(
        "batch_total",
        "Sum a list of numeric values",
        {"values": {"type": "array", "items": {"type": "number"}}},
        required=["values"],
        output_properties={"total": {"type": "number"}},
    ),
    _tool(
        "always_fails",
        "Return a normal MCP tool error for assertion examples",
        {},
    ),
    _tool(
        "process_id",
        "Return the server process identifier",
        {},
        output_properties={"pid": {"type": "integer"}},
    ),
    _tool(
        "session_observations",
        "Return notifications and session settings observed by the server",
        {},
    ),
)

_CATALOG_ONLY_TOOL = _tool(
    "catalog_marker",
    "Second-page marker used to demonstrate pagination",
    {},
)


async def _list_tools(_context: Any, params: Any) -> types.ListToolsResult:
    # Two pages ensure list_all_tools() is exercised by the quick-start example.
    if _cursor(params) == "page-2":
        return types.ListToolsResult(tools=[_CATALOG_ONLY_TOOL])
    return types.ListToolsResult(tools=list(_TOOLS), next_cursor="page-2")


def _tool_result(structured: dict[str, Any]) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(text=json.dumps(structured, sort_keys=True))],
        structured_content=structured,
    )


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(text=message)], is_error=True
    )


async def _call_tool(
    _context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    arguments = params.arguments or {}
    if params.name == "normalize_customer":
        customer_id = re.sub(
            r"[^a-z0-9]+", "-", str(arguments["name"]).strip().lower()
        ).strip("-")
        return _tool_result({"customer_id": customer_id})
    if params.name == "create_order":
        order_id = f"order-{len(_orders) + 1:03d}"
        _orders[order_id] = {
            "order_id": order_id,
            "customer_id": arguments["customer_id"],
            "item": arguments["item"],
            "quantity": arguments["quantity"],
        }
        return _tool_result({"order_id": order_id})
    if params.name == "get_order":
        order = _orders.get(str(arguments["order_id"]))
        return _tool_result(order) if order is not None else _error("order not found")
    if params.name == "shipping_quote":
        multipliers = {"local": 2.0, "regional": 3.5, "international": 7.0}
        amount = round(
            5 + float(arguments["weight_kg"]) * multipliers[str(arguments["zone"])], 2
        )
        return _tool_result({"amount": amount, "currency": "USD"})
    if params.name == "batch_total":
        return _tool_result(
            {"total": sum(float(value) for value in arguments["values"])}
        )
    if params.name == "always_fails":
        return _error("expected example failure")
    if params.name == "process_id":
        return _tool_result({"pid": os.getpid()})
    if params.name == "session_observations":
        return _tool_result(
            {
                **_session_observations,
                "subscriptions": sorted(_subscriptions),
            }
        )
    return _error(f"unknown tool: {params.name}")


async def _list_resources(_context: Any, _params: Any) -> types.ListResourcesResult:
    return types.ListResourcesResult(
        resources=[
            types.Resource(
                name="testing-guide",
                uri="memory://testing-guide",
                description="A short guide exposed by the example server",
                mime_type="text/markdown",
            )
        ]
    )


async def _read_resource(
    _context: Any, params: types.ReadResourceRequestParams
) -> types.ReadResourceResult:
    text = "# Testing guide\n\nDiscover capabilities before asserting behavior."
    return types.ReadResourceResult(
        contents=[
            types.TextResourceContents(
                uri=params.uri, mime_type="text/markdown", text=text
            )
        ]
    )


async def _list_resource_templates(
    _context: Any, _params: Any
) -> types.ListResourceTemplatesResult:
    return types.ListResourceTemplatesResult(
        resource_templates=[
            types.ResourceTemplate(
                name="order",
                uri_template="memory://orders/{order_id}",
                description="An order stored in the current server session",
                mime_type="application/json",
            )
        ]
    )


async def _list_prompts(_context: Any, _params: Any) -> types.ListPromptsResult:
    return types.ListPromptsResult(
        prompts=[
            types.Prompt(
                name="review_order",
                description="Ask an assistant to review an order",
                arguments=[types.PromptArgument(name="order_id", required=True)],
            )
        ]
    )


async def _get_prompt(
    _context: Any, params: types.GetPromptRequestParams
) -> types.GetPromptResult:
    order_id = (params.arguments or {}).get("order_id", "unknown")
    return types.GetPromptResult(
        description="Review a stored order",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(
                    text=f"Review order {order_id} for correctness."
                ),
            )
        ],
    )


async def _completion(
    _context: Any, _params: types.CompleteRequestParams
) -> types.CompleteResult:
    return types.CompleteResult(
        completion=types.Completion(
            values=["order-001", "order-002"], total=2, has_more=False
        )
    )


async def _subscribe(
    _context: Any, params: types.SubscribeRequestParams
) -> types.EmptyResult:
    _subscriptions.add(str(params.uri))
    return types.EmptyResult()


async def _unsubscribe(
    _context: Any, params: types.UnsubscribeRequestParams
) -> types.EmptyResult:
    _subscriptions.discard(str(params.uri))
    return types.EmptyResult()


async def _set_logging_level(
    _context: Any, params: types.SetLevelRequestParams
) -> types.EmptyResult:
    _session_observations["logging_level"] = params.level
    return types.EmptyResult()


async def _progress(_context: Any, _params: types.ProgressNotificationParams) -> None:
    _session_observations["progress_notifications"] += 1


async def _roots_changed(
    _context: Any, _params: types.NotificationParams | None
) -> None:
    _session_observations["roots_changed"] += 1


async def main() -> None:
    server: Server[object] = Server(
        "mcp-pal-example-server",
        version="1.0.0",
        instructions="Deterministic server for MCP Pal SDK examples",
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
        on_list_resources=_list_resources,
        on_list_resource_templates=_list_resource_templates,
        on_read_resource=_read_resource,
        on_subscribe_resource=_subscribe,
        on_unsubscribe_resource=_unsubscribe,
        on_list_prompts=_list_prompts,
        on_get_prompt=_get_prompt,
        on_completion=_completion,
        on_set_logging_level=_set_logging_level,
        on_progress=_progress,
        on_roots_list_changed=_roots_changed,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    anyio.run(main)
