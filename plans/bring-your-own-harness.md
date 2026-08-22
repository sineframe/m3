# Bring Your Own Harness via ACP — Confidence-Gated Implementation

## Summary and Compatibility Contract

Use stable ACP protocol v1 for custom local harnesses. MCP Pal acts as the ACP client and launches a registered ACP agent over stdio. Non-ACP harnesses require an external ACP bridge; an opaque program without controllable configuration, input, and structured output cannot be supported generically.

Each ACP run uses a fresh process, fresh session, empty temporary cwd, one selected MCP server, one text prompt, and agent-default tool policy.

Claude Code and OpenCode remain native implementations. Do not convert, refactor, subclass, or route them through the ACP runner during this feature.

| Contract | Treatment |
|---|---|
| Claude/OpenCode commands, config, auth, environment, cancellation | Preserve exactly |
| Existing `RunSpec` and native runner implementations | Preserve exactly |
| Claude/OpenCode run request fields | Preserve exactly |
| Existing run/report fields | Preserve; add aliases/metadata only |
| `claude.v1` and `opencode.v1` traces | Preserve exactly |
| MCP profile APIs and revisions | Preserve exactly |
| `/health` built-in behavior | Preserve |
| `/capabilities` response | Intentionally replace with unified descriptors |
| Custom profiles, probes, ACP traces | Additive |
| Existing SQLite run data | Preserve through additive tables |

“Zero regression” therefore means every existing Claude/OpenCode behavior and test continues passing. It does not mean backward compatibility for external consumers of the old `/capabilities` JSON shape; that is the explicit contract change selected for this side project.

## Contracts and Data Model

### Harness manifest

```json
{
  "schema_version": "mcp-pal.harness.v1",
  "protocol": "acp",
  "protocol_version": 1,
  "command": "my-agent",
  "args": ["--acp"],
  "env": {
    "OPENAI_API_KEY": "${TEAM_OPENAI_KEY}"
  }
}
```

Rules:

- Resolve `command` through `PATH` or as an absolute executable path.
- Pass arguments directly without a shell or placeholder interpolation.
- Inherit the backend environment, then apply child-variable-to-host-variable mappings.
- Persist environment references only—never resolved values or literal credentials.
- Valid but locally unavailable manifests may be saved, imported, and exported.
- Missing executable or declared environment blocks probes and runs.
- Missing selected-MCP environment blocks ACP runs without changing native-runner behavior.
- Every manifest change creates an immutable revision.
- Name and description remain editable profile metadata.
- Require acknowledgment on every revision that the executable is trusted and unsandboxed.
- Archive profiles and preserve all revisions/history.
- Imports always create a new unverified profile.
- Exports omit IDs, probe evidence, readiness, resolved paths, and secrets.

Add additive tables:

- `HarnessProfile`
- `HarnessProfileRevision`
- `HarnessProbe`
- `RunHarnessSnapshot`

`RunHarnessSnapshot` stores the exact custom revision, manifest, requested and observed ACP mode/config, effective model, verification provenance, agent identity, configured/instrumented transport, and structured failure. Existing `runs` rows require no destructive migration.

### Runner contracts

Do not alter the native `RunSpec` contract.

Add a separate `AcpRunSpec` containing:

- Prompt and expected-output-independent execution data.
- Selected MCP server configuration.
- Harness revision manifest.
- Agent mode.
- Complete session configuration.
- Timeout and verification metadata.

Reuse `HarnessResult` only through additive optional fields, or add an ACP-specific result converted to the existing manager result shape. No existing field may change meaning.

### Public API

Replace `/api/v1/capabilities` with descriptor objects:

```json
{
  "harnesses": [
    {
      "selection_id": "builtin:claude-code",
      "kind": "builtin",
      "harness": "claude-code",
      "name": "Claude Code",
      "ready": true,
      "models": ["..."],
      "tool_modes": ["mcp_only", "mcp_read_only", "full"],
      "limits": {}
    },
    {
      "selection_id": "profile:uuid",
      "kind": "acp",
      "harness": "acp",
      "name": "My Agent",
      "profile_id": "uuid",
      "revision_id": "uuid",
      "ready": true,
      "models": ["agent-default"],
      "tool_modes": ["agent_default"],
      "agent_modes": [],
      "session_config_options": [],
      "verification": {},
      "warnings": []
    }
  ]
}
```

Add harness profile/revision/import/export/archive/restore endpoints and asynchronous revision-probe endpoints.

ACP run submission continues through `/api/v1/runs`:

```json
{
  "harness": "acp",
  "harness_revision_id": "uuid",
  "model": "agent-default",
  "tool_mode": "agent_default",
  "agent_mode_id": null,
  "session_config": {},
  "profile_revision_id": "mcp-revision-uuid",
  "enabled_server": "server-name",
  "prompt": "Use the server",
  "expected_output": "Description of the expected result"
}
```

- Require the `agent-default` sentinels for ACP.
- Expected output remains required and is never sent to the harness.
- Unprobed runs require empty mode/config.
- Probed runs submit every displayed option value, including displayed defaults.
- Add `effective_model` from the ACP option categorized as `model`.
- Add `final_output`; retain `claude_result` as a compatibility alias.
- Add structured failure: `code`, `phase`, `message`, and redacted diagnostics.
- Add harness-kind and harness-profile history filters.
- Clones preserve exact harness/MCP revisions and full ACP configuration unless explicit latest-revision overrides are provided.

## ACP Runtime, Capture, and Probes

### Runtime

Use the pinned official `agent-client-protocol` Python SDK and negotiate `protocolVersion = 1`.

For every ACP run:

1. Perform manifest, executable, and environment preflight.
2. Create an empty temporary cwd and capture files.
3. Select exactly one server from the immutable MCP revision.
4. Instrument it using existing transport primitives without modifying native runners:
   - Stdio through the existing transparent relay.
   - HTTP/SSE through the existing same-transport reverse proxy.
5. Preserve the current private-upstream restriction.
6. Launch the harness in a new process group.
7. Treat stdout as ACP NDJSON only and stderr as diagnostic output.
8. Attach an SDK raw-message observer before initialization.
9. Advertise text prompts and select/boolean session configuration.
10. Advertise no filesystem, terminal, terminal-auth, or elicitation capabilities.
11. Do not call ACP `authenticate`; return a structured out-of-band-auth failure if required.
12. Validate protocol version and HTTP/SSE capability.
13. Create a session with the temporary cwd and selected instrumented MCP server.
14. Validate requested mode/config against the live session response.
15. Apply mode with `set_session_mode` and options with `set_config_option`.
16. Send one text prompt.
17. Aggregate agent message chunks into `final_output`.
18. Close the session/connection and terminate the process.
19. Always stop proxies, reap the process group, read captures, and delete the workspace.

Failure behavior:

- Malformed/non-JSON stdout fails with a bounded redacted excerpt.
- Unknown valid ACP notifications and `_meta` fields are captured and tolerated.
- Permission, elicitation, filesystem, or terminal requests fail with `acp_interaction_required`.
- Cancellation sends `session/cancel`, waits a bounded grace period, then terminates the process group.
- Timeout produces `timed_out`.
- Normal ACP stop reasons remain lifecycle `completed`; assertions determine test success.
- Every failure produces a partial trace.

### Trace

Add `acp.v1`; leave `claude.v1` and `opencode.v1` unchanged.

Persist all redacted ACP frames with direction, UTC receipt time, and monotonic offset. Normalize messages, emitted thoughts, plans, state, tool activity, protocol activity, and final output in the backend.

MCP wire evidence remains authoritative:

- Arguments come from `tools/call.params.arguments`.
- Results/errors come from the correlated wire response.
- Server latency comes from captured request/response timing.
- ACP-reported calls without wire evidence remain visible but cannot pass the MCP assertion.
- Wire calls without ACP events still appear.
- Keep ACP and wire records separate.
- Add an explicitly inferred visual relationship only for an unambiguous tool-name/time match.
- Show configured and instrumented transports separately.
- Do not interpret vendor `_meta` cost/token fields.

### Probes

Expose separate readiness and verification states:

- Local ready.
- Protocol verified.
- Fully verified for an exact revision, MCP transport, agent mode, session configuration, and agent identity.

Protocol probe:

- Launch and initialize ACP v1.
- Create a session with the packaged stdio echo server.
- Capture capabilities, auth methods, transports, `agentInfo`, modes, and config options.
- Do not send a model prompt.

Full probe:

- Use the selected echo transport.
- Apply the complete selected mode/config.
- Prompt the agent to call an echo tool with a unique nonce.
- Require exact captured nonce arguments, response, streamed update, and normal completion.
- Use one model-consuming turn.

The newest probe wins for its exact dimensions. Failed protocol reprobes make old options historical and force agent defaults. Failed/unverified profiles remain runnable when locally ready, with per-run UI confirmation.

Bind evidence to ACP `agentInfo` when present. A changed name/version downgrades verification. Missing optional identity is allowed with an explicit limitation.

## Confidence-Gated Implementation Sequence

### Gate 0: Freeze native behavior

Before adding ACP:

- Add API-worker characterization tests for fake Claude and fake OpenCode executables.
- Record normalized, temp-path-independent snapshots of:
  - Submitted request.
  - Executable arguments.
  - Generated MCP configuration.
  - Environment/auth behavior.
  - Final run JSON.
  - Report fields.
  - Trace schema and canonical MCP calls.
  - Cancellation and process cleanup.
- Run all 71 existing tests.

Exit criterion: characterization tests and all existing tests pass. Do not proceed otherwise.

### Gate 1: Keyless ACP vertical slice

Before database profiles or UI:

- Add the official SDK dependency.
- Build a deterministic ACP-agent fixture as a real subprocess.
- Give it one hardcoded manifest through `AcpRunSpec`.
- Have it consume `session/new.mcpServers`, launch the injected stdio echo server, perform `tools/list` and `tools/call`, emit thought/tool/message updates, and return `end_turn`.
- Run through `AcpHarnessRunner`.
- Assert exact wire arguments, response, latency, final output, raw ACP frames, and process cleanup.

Exit criterion: the complete ACP→MCP round trip passes without API keys.

This gate proves the highest-risk concept before persistence or UI work.

### Gate 2: Additive persistence and API

- Add custom profile/revision/probe/snapshot tables.
- Add manifest validation and import/export.
- Add ACP run dispatch as a new `RunManager` branch.
- Do not edit Claude/OpenCode runner logic.
- Replace `/capabilities` with unified descriptors and update in-repo consumers.
- Add run snapshot, clone, report, and filtering behavior.

Exit criterion:

- Gate 1 E2E passes.
- All native characterization tests pass unchanged.
- All previous tests pass.

### Gate 3: Probes and UI

- Package the echo MCP server as application code.
- Add protocol and full probes using the same ACP runtime.
- Add Harness Profiles UI.
- Add dynamic ACP mode/config controls and warnings.
- Render only backend-normalized ACP traces.

Exit criterion:

- Protocol and full-probe matrix passes for stdio, HTTP, and SSE.
- Streamlit integration tests pass.
- All prior gates remain green.

### Gate 4: Bridge kit and black-box E2E

- Add the runnable Python reference bridge.
- Add manifest validation/probe CLI and `just` recipes.
- Add README commands and opt-in real-agent smoke instructions.
- Run the complete black-box test described below.

No unrelated refactor, formatting sweep, or native-runner cleanup is allowed in these gates.

## Test Plan and Required E2E

### Required black-box keyless E2E

Run an actual backend subprocess through Uvicorn on an ephemeral port with:

- Temporary SQLite database.
- Temporary environment.
- Real FIFO worker.
- Registered custom harness profile.
- Real deterministic ACP-agent subprocess.
- Real stdio echo MCP subprocess behind the capture relay.

Drive the system with HTTP requests:

1. Wait for backend health.
2. Create the custom harness profile.
3. Create or select the echo MCP profile.
4. Run the protocol probe and poll until complete.
5. Run the full nonce probe and verify exact wire evidence.
6. Submit a normal ACP run.
7. Poll `/runs/{id}` until terminal.
8. Fetch `/runs/{id}/report`.
9. Shut down the backend.

Required assertions:

- Lifecycle is `completed`.
- `harness == "acp"`.
- `model == "agent-default"`.
- `effective_model` matches selected ACP configuration when present.
- `final_output` is exact.
- `mcp_assertion` passes.
- Exact nonce arguments and response are present.
- `server_latency_ms` is non-null and non-negative.
- Trace schema is `acp.v1`.
- Initialize, session/new, config, prompt, updates, and response frames are present.
- Thought and tool updates are normalized.
- Harness/MCP revision snapshots are exact.
- Verification provenance matches the full probe.
- No secret values appear in persisted JSON.
- Backend shutdown leaves no ACP, MCP, proxy, or worker process alive.
- Temporary workspaces are gone.

Add a second black-box cancellation E2E:

- Submit a deliberately hanging ACP agent.
- Cancel through the HTTP API.
- Assert `cancelled`, `session/cancel` evidence, partial trace, dead process group, and responsive worker afterward.

### Remaining automated coverage

- Manifest validation, revisions, imports/exports, missing environment, archives.
- Protocol mismatch, malformed stdout, early exit, auth required, unknown updates.
- Permission, elicitation, filesystem, and terminal requests.
- Stale session mode/config values.
- Agent identity changes.
- Probe status precedence and exact-dimension matching.
- Stdio, HTTP, and SSE MCP capture.
- Concurrent/repeated same-name calls without false correlation.
- Failed-run partial traces.
- Clone reproducibility and latest-revision overrides.
- Old SQLite database startup.
- Streamlit profile creation, harness selection, warnings, submission, history, and ACP waterfall.
- Runnable non-ACP bridge demonstration.
- Full existing Claude/OpenCode suite after every gate.

## Confidence and Deferred Work

Expected confidence after Gate 1:

- Concept: 9.5/10 because session-injected MCP is proven locally through the official protocol and SDK.
- Implementation: 9/10 because the riskiest subprocess/protocol/capture path exists before CRUD/UI expansion.

Expected confidence after the final E2E:

- Concept: 9.5/10.
- Implementation: 9.5/10 for this repository and specified scope.

The remaining 0.5 risk is external: third-party ACP agents may advertise protocol support while mishandling injected MCP servers. Protocol/full probes are the deliberate containment mechanism.

Deferred:

- Containers and security sandboxing.
- Remote ACP/A2A agents.
- Multiple selected MCP servers.
- Real repository workspaces.
- Multi-turn/reusable sessions.
- Interactive authentication, permissions, and elicitation.
- Client filesystem/terminal services.
- Local/private HTTP upstream expansion.
- Transport adaptation.
- Attachments and retained artifacts.
- Vendor metric parsing.
- Automatic bridge generation.
- LLM judging.
