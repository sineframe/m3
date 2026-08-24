# MCP Pal v0.2 — Phase 0 Baseline and Migration Checklist

Status: complete on 2026-08-23
Baseline commit: `81b58b8` (`main`, synchronized with `origin/main`)
Implementation contract: [`python-testing-sdk-v0.2.md`](python-testing-sdk-v0.2.md), especially section 9

This document freezes the observable v0.1 baseline before the v0.2 SDK
restructure. It is a temporary migration checklist, not a compatibility promise.
The v0.2 plan and its public contract tests remain authoritative.

## Phase gate rule

Every implementation phase must end with:

- A clean build and passing deterministic suite for that phase and all earlier phases.
- `git diff --check` passing.
- No unrelated generated files in the worktree.
- No leaked subprocesses, threads, event loops, ports, temporary workspaces, or databases from lifecycle tests.
- Public imports, schemas, package contents, and dependency direction reviewed when affected.
- The capability-to-test/documentation migration checklist updated.

No later milestone may be used to bypass a failing earlier milestone gate.

## Toolchain and command baseline

The supported baseline was run on macOS arm64 with CPython 3.13.11.

| Concern | Current command | Baseline |
|---|---|---|
| Setup | `uv sync --extra dev --locked` | Passes; `tool.uv.dev-dependencies` deprecation warning |
| Full deterministic suite | `uv run --python 3.13 pytest -q` | 160 passed, 1 warning in 70.08 seconds after test stabilization; see below |
| Unit suite | `uv run pytest -q tests/unit` | 48 passed |
| Integration suite | `uv run pytest -q tests/integration` | Included in the green full-suite baseline |
| Compile | `uv run python -m compileall -q src` | Passes |
| Import smoke test | `uv run python -c 'from mcp_pal.main import app; print(app.title)'` | Passes, but imports application/database state and is not the future SDK import check |
| Build | `uv build --out-dir <temporary-directory>` | Current 0.1.0 wheel and sdist build |
| Diff validation | `git diff --check` | Passes |
| API | `uv run uvicorn mcp_pal.main:app --reload` | Starts; `GET /api/v1/health` and `/docs` return HTTP 200 |
| UI | `uv run streamlit run src/mcp_pal/ui/app.py` | Starts; `/_stcore/health` returns HTTP 200 |
| Harness CLI | `uv run mcp-pal-harness --help` | Passes |
| Reference bridge CLI | `uv run mcp-pal-reference-bridge --help` | Passes |
| Planned SDK CLI | `uv run mcp-pal test` | Absent by design until Phase 17 |
| Root recipes | `just ...` | `just` is not installed locally; raw commands above are authoritative |

Current tool versions recorded during Phase 0:

- `uv 0.9.28`
- CPython `3.13.11`
- pytest `9.1.1`
- git `2.50.1`
- Node.js `24.13.0`
- Claude Code `2.1.240`
- OpenCode `1.16.0`

Known warnings and platform observations:

- `tool.uv.dev-dependencies` is deprecated and moves to dependency groups in Phase 1.
- FastAPI/Starlette emits an `httpx` deprecation warning through `TestClient`.
- Streamlit reports that Watchdog is not installed.
- `just`, Ruff, and a strict type checker are not installed/configured in the current project.
- Python 3.14 is outside the v0.2 support target. A diagnostic 3.14 run showed ACP timing/cleanup sensitivity; the supported 3.13 baseline is green.
- A Phase 0 gate rerun exposed that the ACP lifecycle-phase test's 200 ms
  process-wide deadline could expire in an earlier setup phase under suite
  load. The test now uses a one-second global deadline, retaining the behavior
  under test while removing host-scheduling dependence.
- The ACP timeout/reaping vertical-slice test had the same scheduling flaw: its
  100 ms deadline could expire before the child wrote the PID used by the
  cleanup assertion. Its bounded deadline is now one second; the timeout and
  process-reaping assertions are unchanged.
- Concurrent process-heavy integration suites can interfere through process-global temporary-directory assertions. Agent work must use isolated worktrees, `TMPDIR`, database paths, and port allocation; cleanup suites remain serialized until isolation is proven.

## Repository and public entry-point inventory

Current package metadata:

- Project `mcp-pal`, version `0.1.0`, Python `>=3.13`.
- Flat package at `src/mcp_pal`; no uv workspace or `sdk/` project.
- Base dependencies currently include FastAPI, Uvicorn, Streamlit, Pydantic,
  SQLAlchemy, HTTP clients, and ACP.
- The official MCP Python SDK is not declared.
- The only optional extra is `dev`.

Installed/runtime entry points:

- `mcp-pal-harness` -> `mcp_pal.harness.cli:main`
- `mcp-pal-reference-bridge` -> `mcp_pal.bridge.reference:main`
- ASGI: `mcp_pal.main:app`
- Streamlit: `src/mcp_pal/ui/app.py`
- There is no public `mcp-pal` command and the root `mcp_pal` package exports no SDK symbols.

Current import surface used by tests or application code:

- `mcp_pal.main`: `app`, `create_app`
- `mcp_pal.cli`: current compatibility `main` alias to the harness CLI
- `mcp_pal.api`: `app`, `create_app`
- `mcp_pal.config`: `Settings`, `get_settings`
- `mcp_pal.domain.builtin_profiles`: Excalidraw profile constants
- `mcp_pal.domain.validation`: profile/config validators
- `mcp_pal.domain.events`: event normalization and MCP summary/assertion helpers
- `mcp_pal.harness`: run specs, results, runners, manifest operations, read-only tools
- `mcp_pal.harness.acp`: ACP runner and protocol/full probes
- `mcp_pal.harness.claude_cli`: Claude Code runner
- `mcp_pal.harness.opencode_cli`: OpenCode runner and config translation
- `mcp_pal.persistence.database`: SQLAlchemy base, engine, sessions, initialization
- `mcp_pal.persistence.models`: profiles, revisions, runs, events, traces, probes, snapshots
- `mcp_pal.services.run_manager`: in-process FIFO execution manager
- `mcp_pal.trace`: provider trace builders, normalization, capture, and redaction
- `mcp_pal.transport`: stdio and HTTP/SSE capture proxies
- `mcp_pal.ui`: Streamlit application, health and trace projections
- `mcp_pal.bridge.reference`: deterministic ACP reference bridge operations
- `mcp_pal.fixtures`: echo MCP and structured CLI fixtures used by examples/tests

Existing package-level re-exports are also part of the migration inventory:

- `mcp_pal.harness`: runner/spec/result types, manifest operations, and tool constants
- `mcp_pal.api`: ASGI application factory and instance
- `mcp_pal.trace`: Claude trace construction and redaction
- `mcp_pal.transport`: HTTP proxy and unsafe-upstream error

These imports describe existing observable behavior. They do not become the
v0.2 public export contract. Phase 2 replaces the current package-boundary test
with the explicit v0.2 export manifest.

## Configuration and environment inventory

Settings-backed environment variables:

- `ANTHROPIC_API_KEY`
- `OPENROUTER_API_KEY`
- `CLAUDE_EXECUTABLE`
- `CLAUDE_MODEL_IDS`
- `OPENCODE_API_KEY`
- `OPENCODE_EXECUTABLE`
- `OPENCODE_MODEL_IDS`
- `DATABASE_PATH`
- `RUN_TIMEOUT_SECONDS`
- `CLAUDE_MAX_TURNS`
- `CLAUDE_MAX_BUDGET_USD`

Other environment behavior:

- `MCP_PAL_API_URL` selects the Streamlit API URL.
- OpenCode provider readiness may inspect `<PROVIDER>_API_KEY` and `<PROVIDER>_AUTH_TOKEN`.
- Harness manifests and MCP server configs use `${ENVIRONMENT_VARIABLE}` references.
- Harness isolation manages `HOME`, `PWD`, `OPENCODE_CONFIG`,
  `OPENCODE_CONFIG_DIR`, and XDG config/data/state/cache roots.
- Current `Settings` construction automatically loads `.env`; Phase 3
  intentionally removes this from library code and retains only explicit
  application/CLI loading.

## Persistence inventory

- Default database: `./mcp_pal.db`, ignored by Git.
- Raw paths are converted to SQLite URLs; the current engine helper creates
  parent directories.
- Importing `mcp_pal.persistence.database` creates a global engine.
- API startup creates additive tables and marks queued/running rows failed.
- Current logical records are MCP profiles/revisions, harness
  profiles/revisions/probes, runs, run events, provider-shaped run traces, and
  run harness snapshots.
- There is no database-reset command.

Intentional v0.2 replacement:

- Fresh schema only; no legacy detection, import, backup, or migration.
- `Execution`, sessions, turns, canonical events, evaluations, artifacts,
  content-addressed blobs, leases, cancellation, and durable commands replace
  the current run/trace persistence.
- Import-time engine/database initialization is removed from the SDK surface.
- Startup lease loss becomes `interrupted`, not a blanket `failed` mutation.
- `tests/integration/test_acp_api.py::test_old_sqlite_schema_starts_with_additive_tables`
  is intentionally replaced by fresh-schema and guarded-reset tests.

## Profile and execution-field inventory

MCP profile identity:

- `id`, `name`, `description`, `archived`, `current_revision_id`, timestamps.
- Immutable revisions contain `id`, `profile_id`, `revision_number`, `mcp_json`, and `created_at`.
- Supported profile input is one `mcpServers` object containing stdio, HTTP, or SSE definitions.

Harness profile identity:

- `id`, `name`, `description`, `archived`, `current_revision_id`, timestamps.
- Immutable revisions contain an ACP v1 manifest and `trusted_unsandboxed` acknowledgement.
- Manifest fields are `schema_version`, `protocol`, `protocol_version`, `command`, `args`, and reference-only `env`.
- Probe records include kind, status, evidence, transport, mode, session configuration, and optional agent identity.

Current run input/state:

- Harness, harness revision, agent mode, session config, model, prompt,
  expected output, MCP profile revision, selected server, and tool mode.
- Timeout, turn and budget limits.
- One overloaded status plus provider-named output, exit/error/stderr, cost,
  turns, session ID, assertion strings, and timestamps.
- Clone can preserve original revisions or explicitly select latest revisions.

The section 9.5 mapping intentionally replaces these with execution specs,
ordered server bindings, `UserMessage`, optional SDK `goal`, sessions, turns,
typed content, lifecycle plus outcome, immutable results, and explicit
evaluations.

## API route inventory

All current routes are under `/api/v1`.

Health and capability:

- `GET /health`
- `GET /capabilities`

Harness profiles:

- `GET|POST /harness-profiles`
- `GET|PATCH /harness-profiles/{profile_id}`
- `POST /harness-profiles/{profile_id}/archive`
- `POST /harness-profiles/{profile_id}/restore`
- `POST /harness-profiles/{profile_id}/revisions`
- `GET /harness-profiles/{profile_id}/export`
- `POST /harness-profiles/import`
- `GET /harness-profiles/{profile_id}/probes`
- `POST /harness-profiles/{profile_id}/probe`

MCP profiles:

- `GET|POST /profiles`
- `GET /profiles/{profile_id}`
- `POST /profiles/{profile_id}/revisions`
- `POST /profiles/{profile_id}/archive`
- `POST /profiles/{profile_id}/restore`

Runs:

- `POST|GET|DELETE /runs`
- `GET|DELETE /runs/{run_id}`
- `GET /runs/{run_id}/events`
- `POST /runs/{run_id}/clone`
- `POST /runs/{run_id}/cancel`
- `GET /runs/{run_id}/report`

The current list API uses offset pagination. Phase 14 removes `/api/v1` and
replaces it with shared-model `/api/v2` execution/session/profile/probe routes,
cursor pagination, resumable SSE, idempotency, and the typed error envelope.

## Streamlit action inventory

Current pages:

- New Run
- MCP Profiles
- Run History
- Harness Profiles

Current user actions:

- Create, inspect, revise, archive, and restore MCP profiles.
- Create, import, edit, revise, archive, restore, export, and probe harness profiles.
- Select an exact MCP profile revision, harness, model, server, tool mode, and ACP options.
- Enter one prompt and required expected output; submit and poll one run.
- Acknowledge unverified unsandboxed ACP execution.
- Cancel an active run and render its terminal report/trace.
- Filter history, inspect and download reports, clone, delete, and clear history with confirmation.
- Toggle protocol visibility and inspect trace activity.

Phase 15 preserves the one-goal/one-server application behavior and profile,
probe, clone, cancel, history, deletion, report, and terminal-trace actions. It
intentionally removes local HTTP calls and provider-specific trace semantics
from the UI.

## Current module ownership map

| Current area | v0.2 owner | Migration treatment |
|---|---|---|
| `domain/events.py` | `core/events` and `trace` | Preserve normalization knowledge; replace provider-shaped authority with canonical events |
| `domain/validation.py` | `core/config` and typed server profiles | Preserve validation/security behavior; replace dict-only API |
| `domain/builtin_profiles.py`, `persistence/seeds.py` | Application profile service | Keep app defaults out of core SDK imports |
| `harness/base.py` | Public specs/results plus agent adapter contract | Replace mutable v0.1 run types |
| `services/run_manager.py` | Execution orchestration and storage-backed worker | Preserve FIFO/cleanup techniques; replace in-process ownership |
| `persistence/*` | Optional storage implementations | Fresh schema; remove global/import-time state |
| `trace/*` | Canonical trace plus raw-evidence adapters | Preserve capture, correlation, partial evidence and redaction |
| `transport/*` | Direct transport/capture layer | Preserve wire capture and private-network protections |
| `harness/acp.py` | ACP agent adapter | Preserve process/probe/capture behavior; expose continuing sessions |
| `harness/claude_cli.py` | Claude agent adapter | Replace one-shot process with continuous stream JSON |
| `harness/opencode_cli.py` | OpenCode agent adapter | Replace one-shot run with isolated serve/session |
| `harness/process_group.py`, `group_launcher.py` | Shared process lifecycle | Preserve no-shell launch and descendant cleanup |
| `harness/manifest.py` | Typed harness/profile contracts | Preserve reference-only secrets and trust acknowledgement |
| `bridge/reference.py`, fixtures | Test/example support | Preserve deterministic fixtures outside core runtime |
| `api/*` | Thin application API adapter | Remove execution/storage ownership and API-specific domain duplication |
| `ui/*` | Direct-SDK Streamlit adapter | Preserve UX behavior; remove HTTP and provider parsing |
| `config.py` | Core config resolver plus app/CLI dotenv boundary | Implement four-level precedence and source diagnostics |
| `cli.py` | Public CLI adapter | Add `test` and `doctor`; invoke shared public services |
| root `__init__.py` | Public synchronous SDK exports | Explicit, side-effect-free export manifest |

## Behavior-to-future-test migration checklist

Each row remains open until the named v0.2 contract test exists and the current
implementation is no longer the sole proof of the behavior.

| Current proof | Behavior to preserve or replace intentionally | Future owner / planned test section | Status |
|---|---|---|---|
| `test_validation.py` | MCP shape/name/size, server selection, env references, private trust | Phases 2–3; sections 6.B, 6.H | [ ] |
| `test_package_boundaries.py` | Import boundaries | Phases 1–2; sections 6.A, 9.4 | [ ] |
| `test_trace.py` | Claude capture, partial streams, timing, correlation, redaction | Phase 4; section 6.J | [ ] |
| `test_normalized_calls.py` | Claude/OpenCode wire correlation, duplicates and unmatched evidence | Phase 4; sections 6.J, 9.12 | [ ] |
| `test_acp_trace.py` | Typed IDs, ACP-only/wire-only calls, unknown frames | Phase 4; sections 6.J, 9.11–9.12 | [ ] |
| `test_events.py` | Normalized activity and selected-server isolation | Phases 2 and 4; sections 6.J, 9.7 | [ ] |
| `test_harness_manifest.py` | Reference-only secrets and safe manifest export | Phases 2–3; sections 6.B, 6.G | [ ] |
| `test_reference_bridge.py` | Deterministic ACP bridge and subprocess behavior | Phases 7 and 11; sections 6.D, 6.G | [ ] |
| `test_acp_runtime_hardening.py` | Deadlines, stderr, malformed frames, redaction and cleanup | Phases 4, 9 and 11; sections 6.G, 6.J | [ ] |
| `test_acp_transports.py` | HTTP/SSE capture, private upstream policy and cleanup | Phases 5 and 11; sections 6.C, 6.G | [ ] |
| `test_acp_vertical_slice.py` | ACP execution, timeout/cancel and missing-env preflight | Phases 9 and 11; sections 6.E, 6.G | [ ] |
| `test_harness_probes.py` | Readiness, dimensions, identity, nonce verification | Phase 3 and 11; sections 6.B, 6.G | [ ] |
| `test_native_characterization.py` | Claude/OpenCode command, config and cancellation contracts | Phase 11; section 6.G live characterization | [ ] |
| `test_runner_and_parity.py` | Capture, OpenCode isolation/auth/recovery and process cleanup | Phases 4 and 11; sections 6.G, 6.J | [ ] |
| `test_api.py` | Profiles, revisions, submission, clone, cancel, report and secret safety | Phase 14; section 6.L | [ ] |
| `test_acp_api.py` | ACP snapshots, reports and partial traces; intentionally delete legacy-schema behavior | Phases 13–14; sections 6.K–6.L | [ ] |
| `test_gate4_black_box.py` | Real ASGI/process-tree black-box cleanup | Phases 14, 19–20; sections 6.L, 6.O–6.P | [ ] |
| `test_ui_app.py` | Profile/probe/run/history/clone/cancel/delete/report behavior | Phase 15; section 6.M | [ ] |
| `test_trace_view.py` | Safe trace labels, timing and unavailable-wire presentation | Phase 15; section 6.M | [ ] |
| `test_ui_health.py` | Readiness presentation | Phases 3 and 15; sections 6.B, 6.M | [ ] |

## Phase 0 completion record

- [x] Section 9 was read in full and is the implementation contract.
- [x] Current setup, test, compile, build, API, UI, and CLI commands are recorded.
- [x] The supported Python 3.13 deterministic suite is green and known warnings/platform behavior are recorded.
- [x] Current imports, entry points, database locations, environment variables, profile fields, API routes, and UI actions are inventoried.
- [x] Current execution, trace, harness, storage, API, UI, CLI, and configuration modules are mapped to v0.2 owners.
- [x] Existing behavior and tests are classified as preservation inputs or intentional replacements.
- [x] Every current behavior family is linked to its future SDK phase/test section.
- [x] The pre-change worktree was clean and contained no unrelated generated files.
- [x] The clean-build and deterministic-suite phase gate is established above.
