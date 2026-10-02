<!-- Generated from docs/site/reference/acp.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# ACP reference

Install your ACP agent and give M3 its launch command. M3 starts it as a subprocess and exchanges ACP v1 frames over stdin and stdout. See [connect an ACP-compatible agent](guides-agents-acp-connect.md) for an existing agent and [expose a custom agent through ACP](guides-agents-acp-wrapper.md) for a wrapper example.

## Manifest contract

The manifest is a JSON object. The validator accepts the fields below and rejects unknown fields.

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

`m3.harness.manifest.validate_manifest(value)` checks the manifest's shape. With `check_local=True`, it also checks the executable and referenced environment variables on the host, without launching the process. `load_manifest` accepts a path or `-` for stdin. `export_manifest` returns sorted, indented JSON. These helpers raise `ManifestValidationError` for malformed manifests.

Omitted optional fields use the defaults above. Readiness reports `acp_environment_unavailable` for a missing environment reference and `acp_executable_missing` for an unavailable command. During execution, these conditions produce a harness startup failure. The run stops with the selected harness.

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

M3 sends `initialize` with protocol version 1. The response requires only the protocol version; the agent may advertise its identity in `agentInfo` and supported features in `agentCapabilities`. M3 then sends `session/new` with the selected MCP servers and workspace path. The response requires only a session ID; modes and configuration options are optional. See the [ACP 0.12.1 schema](https://github.com/agentclientprotocol/agent-client-protocol/blob/v0.12.1/schema/schema.json) for these response fields.

M3 applies `agent_mode_id` and each `session_config` value before the first prompt. If a selected mode or option is absent from the advertised list, the adapter raises `HarnessStartupError("ACP harness could not start")`. Public executions report this as a startup failure.

Readiness validates the executable, manifest environment references, server credentials, prompt content types, and tool policy before launch. It may create and remove a temporary probe `HOME`. This preflight check neither starts the process nor negotiates session modes or configuration options. Rejected selections fail during session startup, before the first prompt.

## Process and credential boundary

By default, M3 starts ACP in a temporary workspace. An explicitly configured workspace root is used instead, so the agent can write into that directory. M3 gives ACP a temporary `HOME` in either case. Its child environment is allowlisted: `PATH` contains the agent executable directory and platform default path; `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `XDG_CACHE_HOME` point into the temporary home; locale, timezone, `NO_COLOR`, and `CI` receive fixed values. M3 resolves the manifest's `${ENV}` references and adds those named child variables. Other parent environment variables are not inherited.

Values referenced by the manifest are registered for redaction in captured ACP frames and response text. Environment values in the recorded `session/new` MCP-server list are redacted. Do not put literal secrets in manifests or prompts. Use the environment configuration described in [configuration reference](reference-configuration.md) for local credential setup. An ACP wrapper that starts another process is responsible for narrowing the environment it passes to that process.

During session cleanup, M3 closes the ACP connection, reaps the child, and removes its temporary control directory. It does not remove an explicitly configured workspace root. On POSIX, cleanup terminates the owned process group, including descendants that remain in that group. On Windows, it terminates the direct child; descendant cleanup is not guaranteed. If a turn is cancelled or times out, M3 sends `session/cancel` when possible and stops the owned process. Cleanup failures are reflected in session evidence.

## Tool policy and evidence

For portable restrictive and full policies, M3's capture proxy must enforce the policy on every selected server connection. Readiness fails with `tool_policy_unsupported` when that check fails, even if the agent reports that it restricts tools.

For ACP, `NativeToolPolicy` accepts harness `acp`, `mode: "agent_default"`, and one selected server key. This ACP-specific policy lets the agent choose tools. Other native ACP policy shapes fail readiness. For portable policies, M3 sets `supports_tool_policy` to true after checking that the capture proxy enforces the policy.

ACP `tool_call` and `tool_call_update` frames describe the agent's actions. M3's capture proxy records the MCP requests and responses. To check what the server returned, assert against the captured result and status. Some agent reports have no matching MCP response.

The policy record shows which policy you selected, which policy M3 enforced, and what M3 observed during the run. ACP has no portable cost or usage budget. Missing usage values remain unavailable.

## Capability and compatibility limits

The adapter supports text prompts, session cancellation, request timeouts, ACP updates, and configured session mode and options. M3 records the capabilities the agent advertises in `initialize` as metadata. Unadvertised capabilities remain unknown.

Configure a permission handler to answer approval requests. M3 rejects a request when its handler is missing.

M3 advertises filesystem and terminal capabilities as disabled, even when handlers are configured. Agents following the protocol should not request these operations. Elicitation is also unsupported for ACP.

For extension methods containing `sampling` in their name, M3 passes the request prompt or message to the sampling handler. A missing handler or a declined request returns `acp_interaction_required: sampling`. Other unknown extension methods return an empty object.

Install and update your agent yourself; its version affects ACP behavior. See [agent harness compatibility](guides-agents-harnesses.md) for feature support.
