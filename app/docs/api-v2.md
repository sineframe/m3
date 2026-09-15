# API v2 capability guide

## What API v2 can do

API v2 reads and manages MCP Pal executions in the SQLite store selected for
the app. It can accept direct or agent execution specs, submit them to the
configured SDK worker (embedded by default), list and inspect saved executions,
cancel active executions, delete terminal executions, read bounded evidence,
and summarize evaluations that are already saved. It does not treat a pytest
result or a normal Python assertion as an execution or evaluation result.

### Where readable executions come from

| Source | How it is persisted | How API v2 reads it |
|---|---|---|
| API-created execution | `POST /api/v2/executions` sends a `DirectSpec` or `AgentSpec` through `MCPTestKit`. MCPTestKit saves/submits it using the API-selected SQLite file; the configured worker executes it and records results. The standard app configures an embedded worker. | The API can list, read, report, cancel, and delete it. |
| SDK, CLI, or pytest execution | SDK code uses `SQLiteExecutionStore(path)`. `mcp-pal test` always supplies `--mcp-pal-results-db` (default `.mcp-pal/executions.sqlite`, or the CLI `--results-db` path). Direct pytest can opt in with the same plugin flag. | Point the API at that exact same SQLite file; it can then list, read, and report those MCP Pal executions. |
| In-memory SDK execution | No SQLite store is selected, so data exists only in that SDK process. | Another API process cannot read it. |

These are MCP Pal executions made inside tests. Pytest item outcomes and
ordinary assertions are not written to this store.

### API-created execution flow

1. Send a direct or agent spec to `POST /api/v2/executions`.
2. Poll the returned ID until its snapshot is terminal.
3. Read the saved report with `GET /api/v2/executions/{execution_id}/report`.

### SDK/CLI/pytest shared-database flow

1. Run SDK code with `SQLiteExecutionStore(path)`, or run `mcp-pal test` with
   its default database or `--results-db path`.
2. Start the API/UI with that exact SQLite path.
3. List or report the saved MCP Pal executions through API v2.
4. Run `kit.evaluate(...)` or an `EvaluationRunner` against the execution
   using that same store, then read the saved evaluation in the report or
   query it with `POST /api/v2/evaluations/aggregate`.

## Evaluation boundary

API v2 currently cannot register or run an evaluator. `ExecutionSpec.evaluations`
is saved as part of the execution spec only. Each declaration has an evaluator
`name` and `required` flag, but API v2 does not register or run it; there is no
standalone API endpoint to run an evaluator. SDK users run `kit.evaluate(...)` or an
`EvaluationRunner` against an execution and the same SQLite store. The saved
evaluation then appears in the execution report, and
`POST /api/v2/evaluations/aggregate` can summarize it.

MCP Pal does not provide a built-in LLM judge. SDK users may write an
evaluator callback, including one that calls an LLM; credentials and client
setup remain user-owned. API v2 only reads saved evaluation results and
provenance, then calculates summaries when asked.

## Machine-readable contract and common envelope

`/openapi.json` is the machine-readable request/response schema, including
the `ExecutionSpec` and `TraceView` discriminators. This guide explains the
behavior, persistence effect, and important fields clients use.
The main public models are `DirectSpec`, `AgentSpec`,
`ExecutionState`, `ExecutionReport`, `RawEvidence`, and
`EvaluationReport`.

Execution, report, evidence, evaluation, feedback, and probe responses carry
`version: "v2"`. Control-plane profile list/object responses (including
imports) and harness export responses retain their bare shapes for the pinned
UI client. Route-handler validation and domain errors use this v2 envelope;
middleware, security, and transport failures may use framework-level error
responses.

```json
{"version":"v2","error":{"code":"...","message":"...","details":{}}}
```

Validation errors are value-free. They use `invalid_request` for general
request shape errors, `invalid_execution_spec` for a malformed `spec`, and
`invalid_evaluation_aggregate_query` for an invalid aggregate body. The
aggregate and evidence POSTs are read operations and are allowed in the
read-only viewer; they do not start work or mutate saved executions.

Control-plane route-handler errors are machine-readable v2 envelopes as well.
Profile lookup errors use `profile_not_found`, duplicate names use
`profile_conflict`, and invalid profile or probe inputs use `invalid_profile`
or `invalid_probe_request`; messages do not echo submitted values. Probe history
is filtered by the exact probe dimensions. For a full probe created with a
non-empty session configuration, pass that configuration as the URL-encoded
JSON `session_config` query parameter to retrieve the same history.

## Routes

| Method and path | Purpose |
|---|---|
| `POST /api/v2/executions` | Start a direct or agent execution. |
| `GET /api/v2/executions` | List saved executions with paging and filters. |
| `GET /api/v2/executions/{execution_id}` | Read one execution and spec. |
| `POST /api/v2/executions/{execution_id}/cancel` | Cancel an active execution. |
| `DELETE /api/v2/executions/{execution_id}` | Delete a terminal execution. |
| `GET /api/v2/executions/{execution_id}/report` | Read report, trace, turns, evidence, and evaluations. |
| `POST /api/v2/evidence/read` | Read one bounded evidence value. |
| `POST /api/v2/evaluations/aggregate` | Calculate pass-rate trends and health. |
| `GET /api/v2/feedback/{run_id}` | Read saved test feedback, optionally compared with `baseline_run_id`. |
| `GET /api/v2/profiles` | List saved MCP server profiles. |
| `POST /api/v2/profiles` | Create an MCP server profile. |
| `GET /api/v2/profiles/{profile_id}` | Read one MCP server profile and revisions. |
| `PATCH /api/v2/profiles/{profile_id}` | Update MCP profile metadata. |
| `POST /api/v2/profiles/{profile_id}/revisions` | Add an immutable MCP profile revision. |
| `POST /api/v2/profiles/{profile_id}/archive` | Archive an MCP profile. |
| `POST /api/v2/profiles/{profile_id}/restore` | Restore an MCP profile. |
| `GET /api/v2/harness-profiles` | List saved ACP harness profiles. |
| `POST /api/v2/harness-profiles` | Create an ACP harness profile. |
| `GET /api/v2/harness-profiles/{profile_id}` | Read one harness profile and revisions. |
| `PATCH /api/v2/harness-profiles/{profile_id}` | Update harness profile metadata. |
| `POST /api/v2/harness-profiles/{profile_id}/revisions` | Add an immutable harness revision. |
| `POST /api/v2/harness-profiles/{profile_id}/archive` | Archive a harness profile. |
| `POST /api/v2/harness-profiles/{profile_id}/restore` | Restore a harness profile. |
| `GET /api/v2/harness-profiles/{profile_id}/export` | Export a harness profile. |
| `POST /api/v2/harness-profiles/import` | Import a harness profile. |
| `GET /api/v2/harness-profiles/{profile_id}/probes` | Read harness probe history. |
| `POST /api/v2/harness-profiles/{profile_id}/probes` | Start a harness probe. |
| `POST /api/v2/harness-profiles/{profile_id}/probes/{probe_id}/cancel` | Cancel a harness probe. |
| `GET /api/v2/capabilities` | Read local capability and readiness details. |
| `GET /api/v2/readiness` | Read the local readiness snapshot. |
| `GET /api/v2/health` | Check application storage health. |

## API-created execution details

`POST /api/v2/executions` accepts only `{"spec": ...}`; unknown envelope
fields are rejected. `spec.kind` is the discriminator:

| `kind` | Required discriminator-specific fields | Meaning |
|---|---|---|
| `direct` | `servers` (at least one), `operation` | MCP Pal performs one direct MCP operation. `operation.kind` is one of `list_tools`, `list_resources`, `list_resource_templates`, `list_prompts`, `call_tool`, `read_resource`, `get_prompt`, or `ping`; its fields follow the matching OpenAPI schema. |
| `agent` | `servers` (at least one), exactly one of `harness` or `harness_profile` | MCP Pal sends the `message` to the selected agent harness. |

Both variants also support `run_id`, `case_id`, `protocol`, positive
`timeout_seconds`, `goal`, `evaluations` declarations (saved in the spec only), artifact/workspace/
tool/permission/elicitation/sampling/filesystem/terminal policies, and JSON
`metadata` map whose values are JSON scalars. Direct specs add `validate_schemas`; agent specs add the optional
`message`. Server bindings take exactly one of a `stdio`, `streamable_http`, or
`sse` server, or a server profile reference. `in_process` is runtime-only and
is rejected by this JSON API. Harness values use the `claude_code`, `opencode`,
or `acp` discriminator, or a harness profile reference.

Policy kinds and modes are `workspace.kind` = `temporary`, `copy`,
`git_worktree`, `read_only`, or `in_place`; `tool_policy.kind` = `restrictive`,
`full`, or `native`; `permission_policy.mode` = `deny`, `prompt`, or `allow`;
`elicitation_policy.mode` and `sampling_policy.mode` = `deny` or `allow`;
`filesystem_policy.mode` = `deny`, `read_only`, or `read_write`; and
`terminal_policy.mode` = `deny` or `allow`. Risk-acknowledgement fields are
required for in-place workspaces and full tool access. `case_id` identifies a
logical case across trials; `execution_id` identifies one trial.

Direct operation request and result discriminators are:

| `operation.kind` | Useful request fields | `direct_result` fields |
|---|---|---|
| `list_tools` | optional `server`/`cursor` strings (default `null`), `all_pages` boolean (default `true`) | `tools`, `next_cursor` |
| `list_resources` | optional `server`/`cursor` strings (default `null`), `all_pages` boolean (default `true`) | `resources`, `next_cursor` |
| `list_resource_templates` | optional `server`/`cursor` strings (default `null`), `all_pages` boolean (default `true`) | `resource_templates`, `next_cursor` |
| `list_prompts` | optional `server`/`cursor` strings (default `null`), `all_pages` boolean (default `true`) | `prompts`, `next_cursor` |
| `call_tool` | optional `server` string (default `null`), required `name` string (max 256), `arguments` map (default `{}`) | `content`, `structured_content`, `is_error` |
| `read_resource` | optional `server` string (default `null`), required `uri` string | `contents` |
| `get_prompt` | optional `server` string (default `null`), required `name` string (max 256), `arguments` map (default `{}`) | `description`, `messages` |
| `ping` | optional `server` string (default `null`) | `result_type` |

The result field shapes are typed in OpenAPI. Process-local SDK `raw` objects
are deliberately excluded from API JSON and durable evidence.

```json
{
  "spec": {
    "kind": "direct",
    "run_id": "nightly-42",
    "case_id": "deepwiki/read-structure",
    "servers": [{"server": {"kind": "streamable_http", "name": "deepwiki", "url": "https://mcp.deepwiki.com/mcp"}}],
    "operation": {"kind": "call_tool", "server": "deepwiki", "name": "read_wiki_structure", "arguments": {"repoName": "modelcontextprotocol/servers"}}
  }
}
```

For the API-created path, send this to `POST /api/v2/executions`. The `202` response contains
`version`, `execution_id`, `snapshot`, and the validated `spec`. `run_id`
groups a run. `case_id` names the logical case across repeated trials. One
execution ID is one trial. Matrix helpers set a stable case from matrix and
cell; trial number remains a label.

Poll `GET /api/v2/executions/{execution_id}` until the snapshot is terminal.
Then `GET /api/v2/executions/{execution_id}/report` returns:

```json
{"version":"v2","execution_id":"...","spec":{},"report":{},"trace":{}}
```

The `202` create response and `200` read response use the execution envelope:

| Field | Meaning |
|---|---|
| `version` | Always `v2`. |
| `execution_id` | Stable ID for this execution/trial. |
| `snapshot` | Current `execution_id`, optional `run_id`, `lifecycle`, optional terminal `outcome`, monotonic `sequence`, `created_at`, optional `finished_at`, and optional session `provenance` (`mode`, source execution/session IDs, and optional source turn ID). |
| `spec` | The validated direct or agent spec, including its `kind` discriminator. |

Lifecycle values are `created`, `queued`, `starting`, `idle`,
`running_turn`, `closing`, and `finished`. Terminal outcomes are `completed`,
`failed`, `timed_out`, `cancelled`, and `interrupted`.

The list response is `{version, page}` with `items`, `total`, `limit`, and
`offset`. Cancel returns the execution envelope. Delete returns
`{version, execution_id, deleted: true}`. A report can contain a partial trace
when the provider did not emit complete evidence.

Route details:

- `GET /api/v2/executions` accepts integer `limit` (1-100), integer `offset`
  (0 or greater),
  `lifecycle`, and `outcome` query parameters. It returns `200` with
  `{version, page:{items, total, limit, offset}}`; each item is an execution
  snapshot, newest-created first. `lifecycle` and `outcome` use the enum
  values above. It reads saved snapshots and has no write effect. Invalid
  values return `422 invalid_request` or `invalid_execution_filter`.
- `POST /api/v2/executions` accepts the body above and returns `202` with the
  execution envelope. It writes the spec and initial snapshot, submits to the
  configured worker, and records events/results in the selected SQLite store.
  The standard app uses its embedded worker.
  Errors are `422 invalid_execution_spec` for an invalid discriminator or
  spec, and `422 execution_submission_failed` when submission cannot be made.
  Saved server or harness references that cannot be resolved use the
  `profile_resolution_failed` envelope: missing references return `404`, while
  archived references return `409`.
- `GET /api/v2/executions/{execution_id}` accepts the path ID and returns the
  execution envelope with `200`, or `422 invalid_execution_id`, `404
  execution_not_found`, or `500 execution_data_unavailable`. It reads the
  saved snapshot and spec.
- `POST /api/v2/executions/{execution_id}/cancel` accepts optional query
  `reason` (string, default `null`) and returns the execution envelope with
  `200`. It writes a
  cancellation command and terminal snapshot on success. It can return `422
  invalid_execution_id`, `404 execution_not_found`, `409 execution_terminal`,
  or `409 cancellation_conflict`.
- `DELETE /api/v2/executions/{execution_id}` accepts the path ID and returns
  `{version, execution_id, deleted: true}` with `200`. It can return `422
  invalid_execution_id`, `404 execution_not_found`, `409 execution_active`, or
  `409 execution_conflict`; success removes the terminal execution and its
  saved rows.
- `GET /api/v2/executions/{execution_id}/report` accepts integer
  `after_sequence` (default -1, minimum -1), integer `event_limit` (default
  100, 1-1000), and integer `artifact_limit` (default 100, 1-1000). It
  returns the report envelope with `200`, or `422 invalid_execution_id` or
  `invalid_report_cursor`, `404 execution_not_found`, `409
  execution_not_terminal`, `500 trace_unavailable`, or `500
  execution_data_unavailable`. It reads events, traces, turns, artifacts,
  evidence references, and saved evaluations.

### Report fields and paging

`GET .../report` returns `{version, execution_id, spec, report, trace}`.
The persisted `report` contains:

| Field | Meaning |
|---|---|
| `snapshot` | The terminal or current execution snapshot. |
| `events` | Ordered stable events for this page. Each event has `event_id`, `execution_id`, `sequence`, `kind`, `timestamp`, `monotonic_offset_ms`, optional session/turn/server/connection/correlation fields, `lifecycle_phase`, `payload`, optional payload/evidence references, and provenance. |
| `event_count`, `events_truncated`, `next_after_sequence` | Total event count, whether the returned event page is truncated, and the cursor to pass as the next `after_sequence`. Events returned have `sequence > after_sequence`. |
| `turns` | Finalized agent turn results. The snapshot has `turn_id`, `session_id`, `number`, `lifecycle` (`queued`, `running`, `finished`), `outcome` (`completed`, `failed`, `timed_out`, `cancelled`, `interrupted`), `created_at`, and optional `finished_at`. A turn can include `response:{content,metadata}`, `error`, `trace`, and redaction-safe `evidence`. Content blocks use `text`, `file`, `image`, `audio`, `resource_link`, or `opaque` kinds. |
| `direct_result` | The direct operation result when the execution was direct; its `kind` discriminator selects tools/resources/prompts/list/ping result fields. Otherwise `null`. |
| `artifacts`, `artifact_count`, `artifacts_truncated` | Redacted artifact references and bounded artifact paging metadata. Each reference has `artifact_id`, `execution_id`, `name`, optional `media_type`, `size_bytes`, `sha256`, and `redacted: true`. |
| `evaluations` | Explicitly saved `EvaluationRecord` values: evaluation ID, name, status, `required`, optional message/score/rationale/metrics/provenance/goal, execution/turn/case/run IDs, subject kind/digest, metadata, and `created_at`. Provenance can contain `kind`, `provider`, `model`, `rubric_id`, `rubric_version`, and `config_digest`. |
| `error` | Optional typed execution error with `code`, `message`, `retryable`, and `details`. |
| `evidence` | Optional `complete`/`partial` marker with limitations and a reason. |

`event_limit` and `artifact_limit` bound one response; use the event cursor to
continue events. The API does not expose a separate artifact cursor. Reports
are read-only. A non-terminal execution can be read as a snapshot, but its
report route cannot return a finalized trace and therefore returns
`execution_not_terminal`.

### Trace fields and observation availability

`trace` is a `TraceView` with `schema_id`, `schema_version`, `trace_id`,
`execution_id`, `outcome`, `completeness`, `limitations`, `runtime`,
`summary`, and ordered `timeline`. `runtime.kind` distinguishes direct,
OpenCode, Claude Code, and ACP runtime shapes. `summary` includes timing,
usage, turn/message/reasoning/tool counts, successful/failed tool counts,
protocol errors, activity health, and cleanup status.

Every timeline entry shares `entry_id`, `kind`, optional parent/session/turn/
server/connection IDs, `sequence_start`, `sequence_end`, `timing`, `status`,
provenance, and `limitations`. The `kind` discriminator selects these main
client-useful fields:

| Timeline kind | Main fields |
|---|---|
| `lifecycle` | `phase`. |
| `message` | `message_id`, `role`, `content`, `stop_reason`. |
| `reasoning` | `block_id`, `content`. |
| `tool_call` | `call_id`, provider/server/tool IDs, arguments, result, `tool_status`, correlation, JSON-RPC ID, server latency, policy evidence, reported/wire evidence, and conflicts. |
| `protocol` | `protocol`, method, direction, JSON-RPC ID, request/response, protocol error, and HTTP exchange metadata. |
| `transport` | `phase` (`connected` or `disconnected`), configured transport, and instrumented transport. |
| `initialization` | Protocol/server versions, instructions, capabilities, and advertised tools/resources/resource templates/prompts. |
| `usage` | Input/output/reasoning/cache/total token counts, cost, and currency. |
| `interaction` | `interaction_kind`, request, and response. |
| `process` | Executable, PID, exit code, signal, and stderr. |
| `workspace` | Workspace `change`. |
| `artifact` | The redacted artifact reference. |
| `evaluation` | The evaluation result. |
| `diagnostic` | Diagnostic `code` and `message`. |
| `raw_message` | Evidence `source`, direction, media type, bounded preview/reference, size, and redaction flag. |
| `provider` | Provider name/category and provider data. |

Fields that depend on a provider are `Observation` values, so a field can be
present while its value is unavailable. Use the entry-specific OpenAPI model
for exact nested types.

Provider-dependent fields are `Observation` objects with `state`, optional
`value`, optional `reason`, provenance, and an optional evidence reference.
Only `state: "observed"` means a value was observed. States such as
`not_emitted`, `unsupported`, `unavailable`, `provider_hidden`, `encrypted`,
`redacted`, and `truncated` are meaningful results, not missing JSON fields.
`completeness: "partial"` always includes `limitations`; consumers must not
infer absent events or values from a partial trace.

## Read evidence

Send `{"reference":{"evidence_id":"...","sha256":"..."},"max_bytes":65536}`
to `POST /api/v2/evidence/read`; the request body rejects unknown fields.
`reference.evidence_id` is required; optional reference fields are the
64-character lowercase `sha256` digest, `size_bytes`, `media_type`, and
`storage_key`. When supplied, each optional field must match the stored
metadata; otherwise the route returns `raw_evidence_integrity_error`.
`sha256` also verifies content integrity. `max_bytes` defaults to 1,048,576
with an allowed range of 1 through 1,048,576. The `200` response is
`{version, evidence}`:

| Evidence field | Meaning |
|---|---|
| `reference` | The evidence ID and stored digest/size/media type/storage key when known. |
| `media_type` | Stored content type. |
| `content` | Redacted JSON or text content, bounded by `max_bytes`. |
| `size_bytes`, `returned_size_bytes` | Full stored size and bytes returned. |
| `truncated`, `redacted` | Whether the response is shorter than the stored value; persisted evidence is always marked redacted. |

Missing references return `404 raw_evidence_not_found`; a digest mismatch or
integrity failure returns `500 raw_evidence_integrity_error`. Invalid request
shape or bounds return `422 invalid_request`. This route reads the saved blob
and does not change execution state.

## Aggregate saved evaluations

`POST /api/v2/evaluations/aggregate` calculates a report from saved raw
evaluations. It does not write summary rows.

```json
{
  "from": "2026-08-01T00:00:00Z",
  "to": "2026-09-01T00:00:00Z",
  "group_by": ["time.day", "evaluator"],
  "filters": {"evaluator": "mcp_pal.output.has_text.v1"},
  "limit": 200,
  "offset": 0
}
```

The request fields are:

| Field | Type and rules |
|---|---|
| `from`, `to` | Optional timezone-aware ISO timestamps. The range is half-open: execution snapshot `created_at >= from` and `< to`; when both are present, `from < to`. These bounds use execution time, not later evaluation time. |
| `group_by` | Optional unique labels. Supported system labels are `run_id`, `trial_id`, `turn_id`, `evaluator`, `evaluation_status`, `subject_kind`, `case_id`, `execution_kind`, `server`, `tool`, `transport`, `harness`, `model`, `evaluation_kind`, `judge_provider`, `judge_model`, `rubric_id`, `matrix.id`, `matrix.cell`, `trial.number`, and one of `time.hour`, `time.day`, `time.week`. User labels use `metadata.<key>`. |
| `filters` | Optional map from those non-time labels to one value or a non-empty list of values. `time.*` cannot be filtered. |
| `limit`, `offset` | Group paging; `limit` defaults to 200 and is 1-1000, `offset` defaults to 0 and is non-negative. |

The query must either filter to exactly one evaluator or include `evaluator`
in `group_by`; it may do both. This prevents one pass rate from combining
different evaluators. Unknown labels, duplicate group labels, invalid time
buckets, empty filter lists, mixed evaluator filters, naive timestamps, and
`from >= to` are rejected with `422 invalid_evaluation_aggregate_query`.

Use one evaluator filter or include `evaluator` in `group_by`. This keeps a
deterministic evaluator separate from a user-supplied LLM evaluator. When
several evaluator groups are returned, combined totals have `pass_rate: null`
and `average_score: null`; each group has its own values.

`judge_provider`, `judge_model`, and `rubric_id` are optional labels copied
from the provenance saved with a user-supplied evaluator result. They do not
mean that API v2 selected, called, or configured a judge.

For `case_id`, an explicit persisted/spec value wins. Matrix executions derive
a stable case from `matrix.id` plus `matrix.cell`. If neither is available,
aggregation may expose a stable `spec:<sha256>` ID or, for spec-less traces, a
stable `trace:<sha256>` ID.

Pass rate is `passed / (passed + failed)`. Inconclusive, error, and not-run
results remain in `status_counts` but do not enter that denominator.
Individual malformed legacy rows are skipped safely. Store/read failures
return `evaluation_data_unavailable`.

Example response shape:

```json
{
  "version":"v2",
  "aggregate": {
    "totals": {"trial_count":3,"measured_count":3,"pass_rate":0.6667},
    "groups": [{"key":{"time.day":"2026-08-20","evaluator":"mcp_pal.output.has_text.v1"},"values":{}}],
    "total_groups":1,"limit":200,"offset":0
  }
}
```

The `200` aggregate response is `{version, aggregate}`. `aggregate` contains
`from`, `to`, `totals`, `groups`, `total_groups`, `limit`, and `offset`.
`totals` and each group `values` contain:

| Values field | Meaning |
|---|---|
| `trial_count` | Distinct executions/trials represented. |
| `evaluation_count` | Latest selected evaluation records after deduplication. |
| `measured_count`, `status_counts` | Passed plus failed denominator count, and counts for every status (`passed`, `failed`, `inconclusive`, `error`, `not_run`). |
| `pass_rate` | `passed / (passed + failed)`, or `null` when there are no measured results. Combined totals are `null` when multiple evaluators are present. |
| `average_score`, `score_count` | Average of present scores and the number of scored results. Combined average is `null` when multiple evaluators are present. |
| `health` | Execution/tool health described below. |

`groups` contains `{key, values}`. `key` maps each requested group label to
its value; `total_groups` is the unpaged group count. The health object has
`execution_count`, `tool_calls:{total,successful,failed}`,
`protocol_error_count`, `outcome_counts`, and
`execution_duration_ms`/`server_latency_ms` objects. Each latency object has
`count`, `p50`, and `p95`; unavailable measurements have count zero and null
percentiles. Health counts each execution once per group, even when it has
multiple evaluator records. The route reads saved rows, snapshots, specs, and
traces and writes no summary rows; store/read failures return `500
evaluation_data_unavailable`.

Before filtering and counting, the store keeps the newest record for the same
execution, turn, evaluator, subject kind, and subject digest. This prevents a
later re-evaluation from being counted twice or hidden by an older result.

## Example flows

### Direct repeated trials

Submit the same direct spec three times with one `run_id` and `case_id`, poll
each ID, then evaluate each saved report with
`mcp_pal.output.has_text.v1`. Group by `run_id` and `evaluator` for one pass
rate, or by `trial_id` when the UI needs report links.

### Chained calls

Keep a chained direct client open for the whole workflow. Its ordered calls are
one execution and one trial. Evaluate the finalized trace after the client
closes, then group by `case_id` and `evaluator`; health includes all calls in
that trial.

### Matrix trials

`ToolMatrix` and `HarnessMatrix` set one stable case from matrix and cell and
put the trial number in labels. Run each case several times, evaluate with the
same evaluator name, and group by `case_id` plus `evaluator` to compare cells.
Use `trial_id` to open one repetition.

### User-supplied LLM evaluator

MCP Pal does not include an LLM judge. An SDK user can write an evaluator
callback that calls an LLM and returns the same `EvaluationDecision` as a
deterministic evaluator. Save provider, model, and rubric provenance with
that result. Group by `evaluator`, `judge_provider`, or `judge_model`; do not
combine its pass rate with a deterministic evaluator. API v2 only groups the
saved result and its provenance; it never registers or runs the callback.

## Live example gate

The DeepWiki Streamable HTTP example is opt-in:

```bash
MCP_PAL_RUN_DEEPWIKI_LIVE=1 \
uv run --locked --project app --group test --group typecheck \
pytest -q app/tests/e2e/test_deepwiki_live_evaluation.py
```

It submits real executions, reopens SQLite, evaluates saved traces, queries
run and calendar trends, checks health/latency, and opens one returned trial
report. Normal CI skips this external test.
