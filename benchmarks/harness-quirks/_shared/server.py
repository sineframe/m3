"""Synthetic discovery and multi-step MCP fixtures.

Run with --scenario recovery|structured|resource|prompt|tools_changed|boolean_sibling|bracket_property|large_error.
The nonce is supplied by the caller so each trial can use a fresh synthetic value.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from mcp.shared.subscriptions import ToolsListChanged
from mcp_types.version import MODERN_PROTOCOL_VERSIONS


def schema(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        result["required"] = required
    return result


def append_log(path: Path | None, event: dict[str, Any]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")


def build_tools(scenario: str, nonce: str) -> list[types.Tool]:
    if scenario == "recovery":
        return [
            types.Tool(
                name="begin",
                description="Begin the synthetic update and return its nonce.",
                inputSchema=schema({"item": {"type": "string"}}, ["item"]),
            ),
            types.Tool(
                name="commit",
                description="Commit using the returned nonce, revision, and idempotency key.",
                inputSchema=schema(
                    {
                        "nonce": {"type": "string"},
                        "revision": {"type": "integer"},
                        "idempotency_key": {"type": "string"},
                    },
                    ["nonce", "revision", "idempotency_key"],
                ),
            ),
        ]
    if scenario == "structured":
        return [
            types.Tool(
                name="lookup",
                description="Look up the synthetic record; report its result code.",
                inputSchema=schema({"key": {"type": "string"}}, ["key"]),
                outputSchema=schema(
                    {"result_code": {"type": "string"}, "nonce": {"type": "string"}},
                    ["result_code", "nonce"],
                ),
            )
        ]
    if scenario == "resource":
        return [
            types.Tool(
                name="resource_uri",
                description="Return the URI of the synthetic resource.",
                inputSchema=schema({}),
            )
        ]
    if scenario == "prompt":
        return [
            types.Tool(
                name="ping",
                description="Return a basic readiness marker.",
                inputSchema=schema({}),
            )
        ]
    if scenario == "tools_changed":
        return [
            types.Tool(
                name="arm",
                description="Arm the synthetic tool catalog.",
                inputSchema=schema({}),
            )
        ]
    if scenario == "boolean_sibling":
        return [
            types.Tool(
                name="ping",
                description="Return the synthetic ping marker.",
                inputSchema=schema({}),
            ),
            types.Tool(
                name="put",
                description="Store a synthetic payload.",
                inputSchema=schema({"payload": True}, ["payload"]),
            ),
        ]
    if scenario == "bracket_property":
        return [
            types.Tool(
                name="ids",
                description="Return the requested synthetic IDs.",
                inputSchema=schema(
                    {"ids[]": {"type": "array", "items": {"type": "string"}}}, ["ids[]"]
                ),
            )
        ]
    if scenario == "large_error":
        return [
            types.Tool(
                name="fail",
                description="Return the synthetic validation error containing a marker.",
                inputSchema=schema({"confirm": {"type": "boolean"}}, ["confirm"]),
            )
        ]
    raise ValueError("unsupported scenario")


async def run(scenario: str, nonce: str, log_path: Path | None) -> None:
    uri = f"fixture://resource/{nonce}"
    state: dict[str, Any] = {"begun": False, "armed": False, "commit_results": {}}
    subscription_bus = (
        InMemorySubscriptionBus() if scenario == "tools_changed" else None
    )
    listen_handler = (
        ListenHandler(subscription_bus) if subscription_bus is not None else None
    )

    async def list_tools(ctx: Any, _params: Any) -> types.ListToolsResult:
        tools = build_tools(scenario, nonce)
        if scenario == "tools_changed" and state["armed"]:
            tools.append(
                types.Tool(
                    name="unlock",
                    description="Return the synthetic unlock marker.",
                    inputSchema=schema({"nonce": {"type": "string"}}, ["nonce"]),
                )
            )
        append_log(
            log_path,
            {
                "method": "tools/list",
                "names": [tool.name for tool in tools],
                "protocol": ctx.protocol_version,
                "list_changed_capability": scenario == "tools_changed",
            },
        )
        return types.ListToolsResult(tools=tools)

    async def call_tool(
        ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        args = dict(params.arguments or {})
        append_log(
            log_path, {"method": "tools/call", "name": params.name, "arguments": args}
        )
        name = params.name
        if scenario == "recovery":
            if (
                name == "begin"
                and isinstance(args.get("item"), str)
                and 1 <= len(args["item"]) <= 80
            ):
                state["begun"] = True
                append_log(log_path, {"event": "begin_accepted", "nonce": nonce})
                return types.CallToolResult(
                    content=[
                        types.TextContent(
                            text=f"begun:{args['item']}; nonce={nonce}; expected revision=6"
                        )
                    ]
                )
            if name == "commit":
                key = args.get("idempotency_key")
                if (
                    not state["begun"]
                    or args.get("nonce") != nonce
                    or type(args.get("revision")) is not int
                    or not isinstance(key, str)
                    or not (1 <= len(key) <= 80)
                ):
                    return types.CallToolResult(
                        content=[
                            types.TextContent(
                                text="Invalid commit fields; use the begin nonce and integer revision."
                            )
                        ],
                        isError=True,
                    )
                if key in state["commit_results"]:
                    append_log(
                        log_path,
                        {
                            "event": "commit_replayed",
                            "idempotency_key": key,
                            "outcome": "already_committed",
                        },
                    )
                    return types.CallToolResult(
                        content=[types.TextContent(text=state["commit_results"][key])]
                    )
                if args["revision"] != 7:
                    append_log(
                        log_path,
                        {
                            "event": "commit_rejected",
                            "idempotency_key": key,
                            "expected_revision": 7,
                            "received_revision": args["revision"],
                        },
                    )
                    return types.CallToolResult(
                        content=[
                            types.TextContent(
                                text="Revision conflict: expected revision 7; retry with the same idempotency key."
                            )
                        ],
                        isError=True,
                    )
                state["commit_results"][key] = f"committed:{nonce}"
                append_log(
                    log_path,
                    {
                        "event": "commit_accepted",
                        "idempotency_key": key,
                        "nonce": nonce,
                        "outcome": "committed",
                    },
                )
                return types.CallToolResult(
                    content=[types.TextContent(text=state["commit_results"][key])]
                )
        elif (
            scenario == "structured"
            and name == "lookup"
            and isinstance(args.get("key"), str)
            and 1 <= len(args["key"]) <= 80
        ):
            return types.CallToolResult(
                content=[],
                structuredContent={"result_code": "record-ready", "nonce": nonce},
            )
        elif scenario == "resource" and name == "resource_uri":
            return types.CallToolResult(
                content=[types.TextContent(text=f"Resource URI: {uri}")]
            )
        elif scenario == "prompt" and name == "ping":
            return types.CallToolResult(content=[types.TextContent(text="ready")])
        elif scenario == "tools_changed" and name == "arm":
            state["armed"] = True
            append_log(log_path, {"event": "catalog_armed", "nonce": nonce})
            if ctx.protocol_version in MODERN_PROTOCOL_VERSIONS:
                assert subscription_bus is not None
                await subscription_bus.publish(ToolsListChanged())
                append_log(
                    log_path,
                    {
                        "event": "catalog_changed_published",
                        "protocol": ctx.protocol_version,
                        "channel": "subscriptions/listen",
                    },
                )
            else:
                await ctx.session.send_tool_list_changed()
                append_log(
                    log_path,
                    {
                        "event": "catalog_changed_published",
                        "protocol": ctx.protocol_version,
                        "channel": "notifications/tools/list_changed",
                    },
                )
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        text=f"armed; catalog now includes unlock; nonce={nonce}"
                    )
                ]
            )
        elif (
            scenario == "tools_changed"
            and name == "unlock"
            and args.get("nonce") == nonce
        ):
            return types.CallToolResult(
                content=[types.TextContent(text=f"unlocked:{nonce}")]
            )
        elif scenario == "boolean_sibling" and name == "ping":
            return types.CallToolResult(
                content=[types.TextContent(text=f"ping:{nonce}")]
            )
        elif (
            scenario == "boolean_sibling"
            and name == "put"
            and isinstance(args.get("payload"), dict)
        ):
            return types.CallToolResult(content=[types.TextContent(text="stored")])
        elif (
            scenario == "bracket_property"
            and name == "ids"
            and isinstance(args.get("ids[]"), list)
            and len(args["ids[]"]) <= 20
            and all(isinstance(x, str) and len(x) <= 80 for x in args["ids[]"])
        ):
            return types.CallToolResult(
                content=[types.TextContent(text=f"ids:{nonce}")]
            )
        elif (
            scenario == "large_error" and name == "fail" and args.get("confirm") is True
        ):
            marker = f"marker-{nonce}"
            body = "E" * ((12000 - len(marker)) // 2) + marker
            body += "E" * (12000 - len(body))
            return types.CallToolResult(
                content=[types.TextContent(text=body)], isError=True
            )
        return types.CallToolResult(
            content=[types.TextContent(text="Invalid synthetic request.")], isError=True
        )

    async def list_resources(_ctx: Any, _params: Any) -> types.ListResourcesResult:
        resources = (
            [
                types.Resource(
                    uri=uri,
                    name="synthetic-record",
                    description="Synthetic fixture resource",
                    mimeType="text/plain",
                )
            ]
            if scenario == "resource"
            else []
        )
        append_log(
            log_path,
            {"method": "resources/list", "uris": [str(r.uri) for r in resources]},
        )
        return types.ListResourcesResult(resources=resources)

    async def read_resource(
        _ctx: Any, params: types.ReadResourceRequestParams
    ) -> types.ReadResourceResult:
        resource_uri = str(params.uri)
        append_log(log_path, {"method": "resources/read", "uri": resource_uri})
        if scenario == "resource" and resource_uri == uri:
            return types.ReadResourceResult(
                contents=[
                    types.TextResourceContents(
                        uri=uri, mimeType="text/plain", text=f"resource-marker:{nonce}"
                    )
                ]
            )
        return types.ReadResourceResult(contents=[])

    async def list_prompts(_ctx: Any, _params: Any) -> types.ListPromptsResult:
        prompts = (
            [
                types.Prompt(
                    name="synthetic-brief", description="Return the synthetic marker."
                )
            ]
            if scenario == "prompt"
            else []
        )
        append_log(
            log_path, {"method": "prompts/list", "names": [p.name for p in prompts]}
        )
        return types.ListPromptsResult(prompts=prompts)

    async def get_prompt(
        _ctx: Any, params: types.GetPromptRequestParams
    ) -> types.GetPromptResult:
        append_log(log_path, {"method": "prompts/get", "name": params.name})
        if scenario == "prompt" and params.name == "synthetic-brief":
            return types.GetPromptResult(
                messages=[
                    types.PromptMessage(
                        role="user",
                        content=types.TextContent(
                            text=f"Synthetic marker to report: {nonce}"
                        ),
                    )
                ]
            )
        return types.GetPromptResult(messages=[])

    server: Server[object] = Server(
        f"discovery-{scenario}",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_subscriptions_listen=listen_handler,
        on_list_resources=list_resources if scenario == "resource" else None,
        on_read_resource=read_resource if scenario == "resource" else None,
        on_list_prompts=list_prompts if scenario == "prompt" else None,
        on_get_prompt=get_prompt if scenario == "prompt" else None,
    )
    async with stdio_server() as (read_stream, write_stream):
        options = server.create_initialization_options(
            NotificationOptions(tools_changed=scenario == "tools_changed")
        )
        await server.run(read_stream, write_stream, options)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        required=True,
        choices=[
            "recovery",
            "structured",
            "resource",
            "prompt",
            "tools_changed",
            "boolean_sibling",
            "bracket_property",
            "large_error",
        ],
    )
    parser.add_argument("--log", type=Path)
    parser.add_argument("--nonce", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.nonce):
        parser.error("nonce must be 1-80 ASCII letters, digits, underscore, or hyphen")
    anyio.run(run, args.scenario, args.nonce, args.log)


if __name__ == "__main__":
    main()
