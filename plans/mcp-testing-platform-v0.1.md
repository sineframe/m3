# MCP Testing Platform v0.1

## Decision Register

### Product and scope

- Primary user: MCP server developer.
- Primary workflow: debug one MCP interaction, inspect it, clone it, and retry.
- v0.1 is a local side project; Docker, hosting, multi-user behavior, and production hardening are deferred.
- Supported harnesses: installed Claude Code and OpenCode CLIs.
- Prompts are plain text only, passed exactly as entered.
- No prompt templates, prompt improvement, attachments, workspaces, or batch experiments.
- Excalidraw is the initial use case, but no third-party Excalidraw server is bundled.

### Architecture and persistence

- Streamlit UI with a separate FastAPI backend.
- SQLite persists profiles, revisions, runs, and events across restarts.
- One active run at a time with a FIFO queue.
- Every run is an immutable input-and-result snapshot; there is no separate test-case entity.
- A prior run can be cloned into a new run without modifying history.
- Active runs can be cancelled, terminating the harness and its MCP subprocesses.
- Stored runs remain until manually deleted; support individual deletion and confirmed clear-all.

### MCP configuration

- MCP configurations are named profiles managed in Streamlit and through equivalent APIs.
- Editing creates an immutable profile revision.
- Profiles can contain multiple servers; each run enables exactly one.
- Cloning uses the original revision by default, with an explicit option to use the latest.
- Deleting a profile archives it while preserving revisions referenced by runs.
- Support stdio and non-OAuth HTTP/SSE servers.
- Perform static validation only; actual initialization occurs during a run.
- Secrets come from backend environment variables. Profiles use `${VARIABLE_NAME}` references.
- The run request references `profile_revision_id`; it does not resend MCP JSON.

### Harness execution

- Use each harness's non-interactive structured-output mode and a fresh temporary directory.
- Ignore plugins and project configuration where supported, and expose only the selected MCP server.
- Model selection is a fixed UI dropdown populated from backend-configured exact model IDs.
- Default limits: two minutes for both harnesses; Claude additionally enforces five agent turns and USD 0.50.
- OpenCode does not expose equivalent turn/budget flags, so its API run snapshots report those fields as unavailable rather than pretending they are enforced.
- Limits are backend settings, not per-run UI controls.
- Tool scope is selectable per run:
  - `mcp_only`, the default: selected MCP server only.
  - `mcp_read_only`: selected MCP plus a pinned non-writing allowlist, including agents and web tools.
  - `full`: the harness’s default tools with unrestricted auto-approval.
- Full mode requires no extra confirmation but must carry a persistent high-risk label.

### Reports and verdicts

- Show the MCP lifecycle, tool inputs/results, errors, timing, and final harness response.
- Persist backend-normalized canonical events and raw harness events.
- Harness adapters own native parsing; the API returns canonical events and the UI never parses Claude/OpenCode-specific schemas.
- Persist a versioned backend-normalized `trace.mcp_calls` (`mcp.v1`) contract with selected-server tool, status, timing, arguments, result, harness, and transport. Claude and OpenCode capture stdio, HTTP, and SSE wire JSON-RPC request/response and server latency where frames correlate; unmatched calls retain explicit unavailable fields.
- Redact trace, event, and downloaded-report values before persistence; profile revisions remain local configuration snapshots.
- Preserve and display reasoning payloads if the harness emits them; unavailable or encrypted reasoning remains unavailable.
- Large payloads use truncated inline previews with complete JSON downloads.
- MCP artifacts use generic JSON/text rendering with clickable detected links.
- While running, the UI shows status only; the trace appears after termination.
- Verdicts remain separate:
  - Lifecycle status.
  - MCP technical assertion.
  - Expected-output semantic assertion, always `not_evaluated` in v0.1.
- No selected-server call is an MCP assertion failure.
- Only failed tool calls are an MCP assertion failure.
- Mixed successful and failed calls produce an MCP warning.
- All successful calls produce an MCP pass.

### Human and agent access

- FastAPI is a first-class interface, not merely a private Streamlit backend.
- Profile, revision, run, clone, cancel, history, deletion, event, and report workflows have API parity.
- OpenAPI documentation is the v0.1 agent-facing interface.

## Implementation

### Project foundation

- Create a Python 3.13 `uv` project using FastAPI, Uvicorn, Streamlit, Pydantic Settings, SQLAlchemy, HTTPX, pytest, and pytest-asyncio.
- Provide `.env.example` containing:
  - `ANTHROPIC_API_KEY`
  - `CLAUDE_EXECUTABLE`
  - ordered exact `CLAUDE_MODEL_IDS`
  - `DATABASE_PATH`
  - `RUN_TIMEOUT_SECONDS=120`
  - `CLAUDE_MAX_TURNS=5`
  - `CLAUDE_MAX_BUDGET_USD=0.50`
  - `OPENCODE_API_KEY`
  - `OPENCODE_EXECUTABLE`
  - ordered exact `OPENCODE_MODEL_IDS`
- Add per-harness readiness checks for credentials, executable, required CLI flags, and database access.

### Data model

Create:

- `McpProfile`: ID, name, description, archived state, current revision, and timestamps.
- `McpProfileRevision`: ID, profile ID, revision number, immutable MCP JSON, and timestamp.
- `Run`: ID, optional parent-run ID, profile revision, enabled server, harness, model, tool mode, prompt, expected output, resolved limits, lifecycle state, harness result, exit/error metadata, cost, turns, session ID, and timestamps.
- `RunEvent`: run ID, sequence, timestamp, normalized event type/payload, and raw event payload.

On backend startup, mark previously queued or running records as interrupted failures rather than attempting to resume their processes.

### Profile validation

- Require a harness-neutral `mcpServers` object with at least one server; adapters translate the selected server to native config.
- Validate unique safe server names and a maximum JSON size of 100 KB.
- Validate stdio `command`, `args`, and `env` fields.
- Validate HTTP/SSE type, URL, headers, and environment references.
- Reject interactive OAuth configurations.
- Detect referenced environment-variable names and show missing variables without revealing values.
- Permit non-secret literal configuration values while warning that the complete profile JSON is stored in SQLite.

### Claude CLI adapter

Define a reusable `HarnessRunner` interface accepting a run specification and event callback and returning a normalized harness result.

For each Claude run:

1. Load the immutable profile revision and extract only the selected server.
2. Create a temporary working directory and permission-restricted MCP JSON file.
3. Start the installed `claude` executable with no shell.
4. Pass the exact prompt through stdin using text input mode.
5. Use:
   - `--print`
   - `--bare`
   - `--output-format stream-json`
   - `--verbose`
   - `--strict-mcp-config`
   - `--mcp-config <temporary-file>`
   - `--no-session-persistence`
   - selected exact model ID
   - configured turn and budget limits
6. Apply the selected tool mode:
   - MCP-only: `--tools ""` and allow `mcp__<server>__*`.
   - Read-only: explicitly expose and approve `Agent`, `Read`, `Glob`, `Grep`, `LSP`, `WebFetch`, `WebSearch`, `ToolSearch`, `ListMcpResourcesTool`, `ReadMcpResourceTool`, `TaskGet`, `TaskList`, and `TaskOutput`, plus the selected MCP namespace. Exclude interactive and state-mutating session tools.
   - Full: expose default tools and use unrestricted permission bypass.
7. Parse complete NDJSON events from stdout and capture stderr separately.
8. Persist raw events unchanged and derive normalized events.
9. On cancellation or timeout, terminate the entire process group and retain partial output.
10. Delete temporary files after termination.

### OpenCode CLI adapter

For each OpenCode run:

1. Translate the selected `mcpServers` entry to OpenCode's local/remote `mcp` format, including `${VAR}` to `{env:VAR}` references.
2. Build explicit tool and permission patterns for `mcp_only`, `mcp_read_only`, or `full` mode.
3. Run `opencode --pure run --format json --thinking --model <provider/model>` without a shell and pass the prompt through stdin.
4. Supply `OPENCODE_API_KEY` only through the child environment; credentials saved by `opencode auth login` are also accepted for readiness.
5. Parse `step_start`, `reasoning`, `tool_use`, `text`, `step_finish`, and error records into the same canonical event schema as Claude.
6. Treat a completed OpenCode tool part as a correlated canonical tool call and tool result.
7. If the installed CLI persists a completed session but its JSON process does not exit, recover the completed session export, deduplicate events, terminate the stuck process, and return the recovered result.
8. Isolate HOME/config/plugin discovery for each run while preserving provider credentials through `OPENCODE_API_KEY` or the user's saved credential data path.
9. Delete the temporary OpenCode session after all events and any recovered export have been persisted.

### Status and assertions

Lifecycle states:

- `queued`
- `running`
- `completed`
- `failed`
- `timed_out`
- `cancelled`

Normalize events into:

- `system`
- `mcp_initialization`
- `assistant_text`
- `thinking`
- `tool_call`
- `tool_result`
- `api_retry`
- `error`

Derive the MCP assertion only from selected-server events:

- `passed`: one or more calls and every observed call succeeds.
- `warning`: at least one successful and one failed call.
- `failed`: no call occurs or no call succeeds.
- `not_evaluated`: the process ends before sufficient MCP event data exists.

Keep `semantic_assertion.status = "not_evaluated"` with reason `"LLM judge deferred"`.

## API

Expose versioned JSON endpoints:

- `GET /api/v1/health`
- `GET /api/v1/capabilities`
- `GET /api/v1/profiles`
- `POST /api/v1/profiles`
- `GET /api/v1/profiles/{id}`
- `POST /api/v1/profiles/{id}/revisions`
- `POST /api/v1/profiles/{id}/archive`
- `POST /api/v1/profiles/{id}/restore`
- `POST /api/v1/runs`
- `GET /api/v1/runs`
- `GET /api/v1/runs/{id}`
- `GET /api/v1/runs/{id}/events`
- `POST /api/v1/runs/{id}/clone`
- `POST /api/v1/runs/{id}/cancel`
- `DELETE /api/v1/runs/{id}`
- `DELETE /api/v1/runs`
- `GET /api/v1/runs/{id}/report`

`POST /runs` accepts:

```json
{
  "harness": "claude-code",
  "model": "configured-exact-model-id",
  "prompt": "Exact text sent to the selected harness",
  "expected_output": "Required natural-language goal",
  "profile_revision_id": "uuid",
  "enabled_server": "server-name",
  "tool_mode": "mcp_only"
}
```

Return `202 Accepted` with the run ID and queued status. Clone accepts optional overrides and otherwise copies the original immutable inputs.

## Streamlit UI

Create three pages:

- New Run:
  - Harness dropdown.
  - Model dropdown.
  - Profile and revision selection.
  - Enabled-server selection.
  - Tool-mode selection.
  - Prompt and required expected-output fields.
  - Submission validation.
  - Queued/running status, elapsed time, queue position, and Cancel action.
- MCP Profiles:
  - Create profile.
  - JSON editor and static validation.
  - Missing-environment-variable display.
  - Save new revision.
  - Revision history.
  - Archive and restore.
- Run History:
  - Filterable summaries.
  - Report detail.
  - Clone using original revision.
  - Optional switch to latest profile revision.
  - Cancel active run.
  - Delete run or clear history.
  - Download complete JSON report.

Report layout:

- Lifecycle, MCP assertion, and semantic assertion cards.
- Final harness response beside expected output.
- MCP initialization and activity summary.
- Collapsible normalized timeline.
- Collapsible raw events, thinking payloads, and stderr.
- Truncated previews for large values.
- Clickable links detected in generic MCP results.
- Persistent warning that SQLite and downloaded reports contain redacted trace payloads.

## Test Plan

- Unit tests:
  - Profile/schema validation.
  - Environment-reference detection.
  - Immutable revision creation and archive behavior.
  - Selected-server extraction.
  - Model/tool-mode validation.
  - Exact prompt forwarding and exclusion of expected output.
  - CLI flag construction for all three tool modes.
  - Event normalization and raw preservation.
  - MCP pass/warning/fail derivation.
  - Report serialization.
- Fake-harness integration tests:
  - Successful MCP initialization/call/result.
  - No selected-server call.
  - Only failed calls.
  - Mixed call results.
  - Thinking events.
  - API retries and malformed NDJSON.
  - Large tool output.
  - Non-zero exit, timeout, cancellation, and partial trace retention.
  - OpenCode MCP-config translation, native event normalization, exact stdin prompt, model/harness validation, queue dispatch, and completed-session recovery.
- Backend tests:
  - FIFO queue behavior.
  - Profile revision references.
  - Run submission, polling, cloning, cancellation, deletion, and report download.
  - SQLite persistence and interrupted-run startup handling.
  - API/UI capability parity.
- Streamlit smoke tests:
  - All three pages.
  - Form and profile validation.
  - Status polling.
  - Report rendering.
  - Clone, archive, cancel, and delete flows.
- Manual acceptance:
  - Create an Excalidraw profile.
  - Submit a diagram prompt.
  - Observe MCP initialization and at least one successful selected-server tool result.
  - Inspect final output, raw/normalized trace, and downloadable report.
  - Run the dependency-free local MCP echo server through OpenCode and verify one correlated successful tool call/result.

## Deferred

- Additional harnesses and agent/model combinations beyond Claude Code and OpenCode.
- LLM judging and semantic pass/fail.
- Prompt templates, optimization, Braintrust, regression suites, and batch comparisons.
- Network-level verification that a remote MCP endpoint was hit.
- File/image inputs and project workspaces.
- Excalidraw-specific previews.
- OAuth MCP servers.
- Docker, hosted deployment, and productionization.
