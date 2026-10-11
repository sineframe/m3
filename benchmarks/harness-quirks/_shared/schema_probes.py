"""Independent synthetic schema/result probes for MCP client comparisons.

Run from ``quirks`` with, for example::

    uv run python _shared/schema_probes.py --scenario root_oneof_nested \
      --nonce test_123 --log /tmp/schema-probe.jsonl

Only synthetic event metadata is logged. Tool argument values are never logged.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import anyio
from jsonschema import Draft202012Validator, ValidationError
from mcp import types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server

SCENARIOS = (
    "root_oneof_nested",
    "root_oneof_nested_flat",
    "boolean_subschema",
    "recursive_ref_shallow",
    "recursive_ref_deep",
    "many_tools",
    "combined_result",
)

LONG_TARGET_NAME = "many_tool_target_" + "x" * 48  # exactly 65 characters


def write_event(path: Path | None, event: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, sort_keys=True, ensure_ascii=True) + "\n")


def object_schema(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def nested_oneof_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "const": "alpha"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "value": {"type": "string"},
                                "labels": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["value"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["kind", "items"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "const": "beta"},
                    "groups": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"code": {"type": "integer"}},
                                "required": ["code"],
                                "additionalProperties": False,
                            },
                        },
                    },
                },
                "required": ["kind", "groups"],
                "additionalProperties": False,
            },
        ],
    }


def nested_flat_schema() -> dict[str, Any]:
    return object_schema(
        {
            "kind": {"type": "string", "enum": ["alpha"]},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                        "labels": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        },
        ["kind", "items"],
    )


def recursive_schema(depth: int) -> dict[str, Any]:
    # A legal self-reference, nested under array -> object -> children.
    # The deep variant adds a bounded wrapper chain before the recursive edge.
    node: dict[str, Any] = {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "children": {"type": "array", "items": {"$ref": "#/$defs/Node"}},
        },
        "required": ["label"],
        "additionalProperties": False,
    }
    root: dict[str, Any] = {"$ref": "#/$defs/Node"}
    for index in reversed(range(depth)):
        root = {
            "type": "object",
            "properties": {
                f"level_{index}": root,
                "note": {"type": "string"},
            },
            "required": [f"level_{index}"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": {"tree": root},
        "required": ["tree"],
        "additionalProperties": False,
        "$defs": {"Node": node},
    }


def make_tools(scenario: str, nonce: str) -> list[types.Tool]:
    if scenario == "root_oneof_nested":
        return [
            types.Tool(
                name="submit_nested",
                description="Submit one alpha or beta nested payload and return its acceptance marker.",
                inputSchema=nested_oneof_schema(),
            )
        ]
    if scenario == "root_oneof_nested_flat":
        return [
            types.Tool(
                name="submit_nested_flat",
                description="Submit the alpha nested payload using a flat object schema and return its acceptance marker.",
                inputSchema=nested_flat_schema(),
            )
        ]
    if scenario == "boolean_subschema":
        return [
            types.Tool(
                name="ping",
                description="Return the synthetic ping marker; use this control tool.",
                inputSchema=object_schema({}),
            ),
            types.Tool(
                name="put_boolean_payload",
                description="Store an arbitrary JSON payload; included as a boolean-subschema probe.",
                inputSchema=object_schema({"payload": True}, ["payload"]),
            ),
        ]
    if scenario in ("recursive_ref_shallow", "recursive_ref_deep"):
        depth = 0 if scenario.endswith("shallow") else 5
        return [
            types.Tool(
                name="walk_tree",
                description="Accept a small recursive synthetic tree and return its acceptance marker.",
                inputSchema=recursive_schema(depth),
            )
        ]
    if scenario == "many_tools":
        tools = [
            types.Tool(
                name=f"catalog_tool_{index:04d}",
                description=f"Synthetic catalog probe tool number {index:04d}; no external action.",
                inputSchema=object_schema({"value": {"type": "string"}}, ["value"]),
            )
            for index in range(1000)
        ]
        tools.append(
            types.Tool(
                name=LONG_TARGET_NAME,
                description="Call this exact long-named target to receive the catalog marker.",
                inputSchema=object_schema({"value": {"type": "string"}}, ["value"]),
            )
        )
        return tools
    if scenario == "combined_result":
        return [
            types.Tool(
                name="emit_both_markers",
                description="Return a text marker and a different structured marker; report both exactly.",
                inputSchema=object_schema({}),
                outputSchema=object_schema(
                    {"structured_marker": {"type": "string"}}, ["structured_marker"]
                ),
            )
        ]
    raise ValueError(f"unsupported scenario: {scenario}")


def argument_shape(arguments: dict[str, Any]) -> dict[str, str]:
    """Return keys and JSON value types only; do not expose supplied values."""
    return {key: json_type(value) for key, value in sorted(arguments.items())}


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


async def run(scenario: str, nonce: str, log_path: Path | None) -> None:
    tools = make_tools(scenario, nonce)
    tool_by_name = {tool.name: tool for tool in tools}

    async def list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
        names = [tool.name for tool in tools]
        write_event(
            log_path, {"method": "tools/list", "count": len(names), "names": names}
        )
        return types.ListToolsResult(tools=tools)

    async def call_tool(
        _context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        name = params.name
        args = dict(params.arguments or {})
        accepted = False
        valid = False

        if name not in tool_by_name:
            write_event(
                log_path,
                {"event": "call_rejected", "name": name, "reason": "unknown_tool"},
            )
            return types.CallToolResult(
                content=[types.TextContent(text="Unknown synthetic tool.")],
                isError=True,
            )

        try:
            Draft202012Validator(tool_by_name[name].input_schema).validate(args)
            schema_valid = True
        except ValidationError:
            schema_valid = False

        if scenario in ("root_oneof_nested", "root_oneof_nested_flat"):
            expected_name = (
                "submit_nested"
                if scenario == "root_oneof_nested"
                else "submit_nested_flat"
            )
            valid = (
                schema_valid
                and name == expected_name
                and args
                == {
                    "kind": "alpha",
                    "items": [{"value": "synthetic", "labels": ["probe"]}],
                }
            )
            accepted = valid
        elif scenario == "boolean_subschema" and name == "ping":
            valid = schema_valid and not args
            accepted = valid
        elif scenario == "boolean_subschema" and name == "put_boolean_payload":
            valid = schema_valid and isinstance(args.get("payload"), dict)
            accepted = valid
        elif (
            scenario in ("recursive_ref_shallow", "recursive_ref_deep")
            and name == "walk_tree"
        ):
            tree = args.get("tree")
            if scenario.endswith("shallow"):
                valid = schema_valid and tree == {
                    "label": "root",
                    "children": [{"label": "leaf"}],
                }
            else:
                expected_tree: dict[str, Any] = {
                    "label": "root",
                    "children": [{"label": "leaf"}],
                }
                for level in reversed(range(5)):
                    expected_tree = {f"level_{level}": expected_tree}
                valid = schema_valid and tree == expected_tree
            accepted = valid
        elif scenario == "many_tools" and name == LONG_TARGET_NAME:
            valid = schema_valid and args.get("value") == "synthetic"
            accepted = valid
        elif scenario == "combined_result" and name == "emit_both_markers":
            valid = schema_valid and not args
            accepted = valid

        write_event(
            log_path,
            {
                "event": "call_accepted" if accepted else "call_rejected",
                "name": name,
                "argument_keys": sorted(args),
                "argument_types": argument_shape(args),
                "accepted": accepted,
            },
        )
        if not valid:
            return types.CallToolResult(
                content=[types.TextContent(text="Invalid synthetic probe arguments.")],
                isError=True,
            )

        if scenario == "combined_result":
            return types.CallToolResult(
                content=[types.TextContent(text=f"CONTENT_MARKER_{nonce}")],
                structuredContent={"structured_marker": f"STRUCTURED_MARKER_{nonce}"},
            )
        return types.CallToolResult(
            content=[types.TextContent(text=f"ACCEPTED_MARKER_{nonce}")]
        )

    server: Server[object] = Server(
        f"schema-probe-{scenario}",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(NotificationOptions()),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--nonce", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.nonce):
        parser.error("nonce must be 1-80 ASCII letters, digits, underscore, or hyphen")
    anyio.run(run, args.scenario, args.nonce, args.log)


if __name__ == "__main__":
    main()
