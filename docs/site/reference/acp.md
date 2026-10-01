---
title: "ACP reference"
description: "Manifest, launch, session, policy, evidence, and failure behavior for ACP agents."
---

# ACP reference

M3 launches an ACP agent as a user-supplied subprocess and exchanges ACP v1 frames over stdin and stdout. ACP does not use M3-managed native executable downloads. See [connect an ACP-compatible agent](../guides/agents/acp-connect.md) for an existing agent and [expose a custom agent through ACP](../guides/agents/acp-wrapper.md) for a wrapper example.

## Manifest contract

The manifest is a JSON object with these exact fields. Unknown fields are rejected by the public manifest validator.

| Field | Type | Default | Validation and behavior |
| --- | --- | --- | --- |
| `schema_version` | string | `"m3.harness.v1"` | Literal value only. |
| `protocol` | string | `"acp"` | Literal value only. |
| `protocol_version` | integer | `1` | Literal value only. |
| `command` | string | required | Non-empty, at most 1000 characters. Resolved as an executable path or through `PATH`. |
| `args` | array of strings | `[]` | Passed as command arguments; no shell is used. |
| `env` | object of string to string | `{}` | Child variable names must match `[A-Za-z_][A-Za-z0-9_]*`. Values must be references matching `${NAME}`. |

Example:

```json
{
  "schema_version": "m3.harness.v1",
  "protocol": "acp",
  "protocol_version": 1,
  "command": "/usr/local/bin/acp-agent",
  "args": ["--stdio"],
  "env": {"PROVIDER_TOKEN": "${M3_DOCS_PROVIDER_API_KEY}"}
}
```

`m3.harness.manifest.validate_manifest(value)` validates shape without examining the host. With `check_local=True`, it resolves the executable, checks that it is executable, and reports missing referenced variables; it does not launch the process. `load_manifest` accepts a path or `-` for stdin. `export_manifest` returns sorted, indented JSON. These helpers raise `ManifestValidationError` for malformed manifests.

The runtime accepts manifest defaults when the optional schema fields are omitted. Readiness reports `acp_environment_unavailable` for a missing environment reference and `acp_executable_missing` for an unavailable command. An attempted run that cannot start reports a harness startup failure; it does not expose the internal `acp_environment_missing` diagnostic. M3 does not fall back to another harness.

## Agent fields and session selection

`ACPAgent` fields are:

| Field | Type | Default | Behavior |
| --- | --- | --- | --- |
| `kind` | literal string | `"acp"` | Harness discriminator. |
| `name` | string | `"acp"` | Harness name. |
| `manifest` | mapping | `{}` | ACP launch manifest. |
| `agent_mode_id` | non-empty string or `None` | `None` | Selects an ID advertised in the response to `session/new`. |
| `session_config` | mapping | `{}` | Keys must be non-empty strings; values must be strings or booleans. Each key must match an advertised ACP config option ID. |
| `model` | string | required by base harness | Recorded selection label. ACP agents choose and interpret the actual model; M3 does not pass this field as a portable ACP model switch. |
| `runtime` | `"system"` or `"managed"` | `"system"` | `"managed"` is rejected for ACP. |

M3 sends `initialize` with protocol version 1. Its response advertises `agentInfo` and `agentCapabilities`. M3 then sends `session/new` with the selected MCP servers and workspace path; its response supplies the session ID, modes, and configuration options. These fields are defined by the [ACP 0.12.1 schema](https://github.com/agentclientprotocol/agent-client-protocol/blob/v0.12.1/schema/schema.json).

M3 applies `agent_mode_id` and each `session_config` value before the first prompt. If a selected mode or option is absent from the advertised list, the adapter raises `HarnessStartupError("ACP harness could not start")`. Public executions report a startup failure, not a distinct stale-option error.

Readiness checks whether M3 can launch the executable with your manifest, credentials, content, and policy. It does not guarantee model or vendor behavior, tool calls, or evidence types. The check may create and remove a temporary probe `HOME`. You must install the agent yourself.

## Process and credential boundary

By default, M3 starts ACP in a temporary workspace. An explicitly configured workspace root is used instead, so the agent can write into that directory. M3 gives ACP a temporary `HOME` in either case. Its child environment is allowlisted: `PATH` contains the agent executable directory and platform default path; `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `XDG_CACHE_HOME` point into the temporary home; locale, timezone, `NO_COLOR`, and `CI` receive fixed values. M3 resolves the manifest's `${ENV}` references and adds those named child variables. Other parent environment variables are not inherited.

Values referenced by the manifest are registered for redaction in captured ACP frames and response text. Environment values in the recorded `session/new` MCP-server list are redacted. Do not put literal secrets in manifests or prompts. Use the environment configuration described in [configuration reference](configuration.md) for local credential setup. An ACP wrapper that starts another process is responsible for narrowing the environment it passes to that process.

During session cleanup, M3 closes the ACP connection, reaps the child, and removes its temporary control directory. It does not remove an explicitly configured workspace root. On POSIX, cleanup terminates the owned process group, including descendants that remain in that group. On Windows, it terminates the direct child; descendant cleanup is not guaranteed. If a turn is cancelled or times out, M3 sends `session/cancel` when possible and stops the owned process. Cleanup failures are reflected in session evidence.

## Tool policy and evidence

Portable restrictive and full policies require capture-layer enforcement for every selected server connection. If M3 cannot confirm enforcement, readiness fails with `tool_policy_unsupported`. An agent's claim that it restricts tools is insufficient.

For ACP, `NativeToolPolicy` accepts harness `acp`, `mode: "agent_default"`, and one selected server key. The agent chooses the tools under this policy, which is not portable to other harnesses. Other native ACP policy shapes fail readiness. For portable policies, `supports_tool_policy` is true only after M3 confirms capture-proxy enforcement.

ACP `tool_call` and `tool_call_update` frames report what the agent says happened. M3's capture proxy records MCP requests and responses separately. Check the captured result and status to confirm that the server returned data; an agent report can lack a matching wire response.

Policy evidence separates the requested policy from what was enforced and observed. ACP has no portable cost or usage budget. When usage is missing, M3 records it as unavailable without estimating a value.

## Capability and compatibility limits

The adapter supports text prompts, session cancellation, request timeouts, ACP updates, and configured session mode and options. M3 records the capabilities the agent advertises in `initialize` as metadata. Unadvertised capabilities remain unknown.

Permission, terminal, and filesystem requests use configured interaction handlers and fail closed when no applicable handler exists. ACP elicitation fails closed even with a handler. The elicitation support in native Codex and Pi does not apply to ACP.

An extension method containing `sampling` in its name passes the request prompt or message to the configured sampling interaction. If no handler exists or it declines, the request fails with `acp_interaction_required: sampling`. Other unknown extension methods return an empty object. The local guide examples do not exercise these extensions.

ACP behavior varies with the selected executable and its version. M3 does not manage its installation or version. See [agent harness compatibility](../guides/agents/harnesses.md) for feature support.
