"""Synthetic MCP schema projection probes for maps and schema-size compaction.

Deep-path property names and constants are present only in the published schema.
The prompt never supplies them; successful calls return a per-trial marker.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import anyio
from jsonschema import Draft202012Validator
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

SCENARIOS = ("typed_map", "flat_map", "deep_small", "deep_large_budget")
TOOL_NAME = "submit_payload"


def write_event(path: Path | None, event: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")


def deep_expected() -> dict[str, Any]:
    return {"request": {"r7": {"m4": {"v9": "synthetic-leaf-6d2a"}}}}


def deep_path_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "request": {
                "type": "object",
                "properties": {
                    "r7": {
                        "type": "object",
                        "properties": {
                            "m4": {
                                "type": "object",
                                "properties": {
                                    "v9": {
                                        "type": "string",
                                        "const": "synthetic-leaf-6d2a",
                                    }
                                },
                                "required": ["v9"],
                                "additionalProperties": False,
                            }
                        },
                        "required": ["m4"],
                        "additionalProperties": False,
                    }
                },
                "required": ["r7"],
                "additionalProperties": False,
            }
        },
        "required": ["request"],
        "additionalProperties": False,
    }


def schema_for(scenario: str) -> dict[str, Any]:
    if scenario == "typed_map":
        return {
            "type": "object",
            "properties": {
                "items": {"type": "object", "additionalProperties": {"type": "string"}}
            },
            "required": ["items"],
            "additionalProperties": False,
        }
    if scenario == "flat_map":
        return {
            "type": "object",
            "properties": {
                "items": {
                    "type": "object",
                    "properties": {
                        "alpha": {"type": "string"},
                        "beta": {"type": "string"},
                    },
                    "required": ["alpha", "beta"],
                    "additionalProperties": False,
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        }
    schema = deep_path_schema()
    if scenario == "deep_large_budget":
        properties = schema["properties"]
        # Optional shallow enum fields add normalized schema bytes without adding
        # descriptions, refs, or composition keywords that other passes can strip.
        for index in range(56):
            key = f"pad_{index:02d}"
            properties[key] = {
                "type": "string",
                "enum": [
                    f"synthetic-padding-{index:02d}-{choice:02d}" for choice in range(8)
                ],
            }
    return schema


def build_tool(scenario: str) -> types.Tool:
    return types.Tool(
        name=TOOL_NAME,
        description="Submit one input that satisfies this tool's declared schema.",
        inputSchema=schema_for(scenario),
    )


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


def type_tree(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: type_tree(child) for key, child in sorted(value.items())}
    if isinstance(value, list):
        return [type_tree(child) for child in value]
    return json_type(value)


def contract_matches(scenario: str, arguments: dict[str, Any]) -> bool:
    if scenario == "typed_map":
        items = arguments.get("items")
        return (
            isinstance(items, dict)
            and len(items) == 2
            and all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in items.items()
            )
        )
    if scenario == "flat_map":
        # The public schema permits arbitrary string values; don't impose
        # undisclosed A/B constants in the server-side acceptance contract.
        items = arguments.get("items")
        return (
            isinstance(items, dict)
            and set(items) == {"alpha", "beta"}
            and all(isinstance(value, str) for value in items.values())
        )
    expected = deep_expected()
    if arguments.get("request") != expected["request"]:
        return False
    # The large-budget schema's padding fields are optional and may be emitted;
    # the schema validator has already checked their values and key allowlist.
    return scenario == "deep_small" or all(
        key == "request" or key.startswith("pad_") for key in arguments
    )


async def run(scenario: str, nonce: str, log_path: Path | None) -> None:
    tool = build_tool(scenario)
    validator = Draft202012Validator(tool.input_schema)

    async def list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
        normalized_bytes = len(
            json.dumps(
                tool.input_schema,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        write_event(
            log_path,
            {
                "method": "tools/list",
                "count": 1,
                "names": [TOOL_NAME],
                "schema_bytes": normalized_bytes,
            },
        )
        return types.ListToolsResult(tools=[tool])

    async def call_tool(
        _context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        arguments = dict(params.arguments or {})
        errors = list(validator.iter_errors(arguments))
        schema_valid = not errors
        accepted = (
            params.name == TOOL_NAME
            and schema_valid
            and contract_matches(scenario, arguments)
        )
        write_event(
            log_path,
            {
                "event": "call_accepted" if accepted else "call_rejected",
                "name": params.name,
                "argument_types": type_tree(arguments),
                "schema_valid": schema_valid,
                "error_count": len(errors),
                "error_categories": ["schema_validation" for _ in errors],
                "contract_match": contract_matches(scenario, arguments),
            },
        )
        if not accepted:
            return types.CallToolResult(
                content=[types.TextContent(text="Rejected synthetic schema input.")],
                isError=True,
            )
        return types.CallToolResult(
            content=[types.TextContent(text=f"COMPACTION_MARKER_{nonce}")]
        )

    server: Server[object] = Server(
        f"schema-compaction-{scenario}",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.nonce):
        parser.error("nonce must be 1-80 ASCII letters, digits, underscore, or hyphen")
    anyio.run(run, args.scenario, args.nonce, args.log)


if __name__ == "__main__":
    main()
