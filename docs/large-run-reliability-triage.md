# m3 0.3.1 limitations: reproduction and triage

Investigation date: 2026-10-12 (Asia/Kolkata). Seven investigations ran in parallel across three agents and the coordinating agent; related controls ran concurrently where possible. No product code was changed and no model turns were requested.

## Environment and evidence scope

Tests imported the published `sf-m3==0.3.1` from an isolated environment at `/private/tmp/m3-limitations/version031`. Published `sf-m3-cli==0.3.1` and `sf-m3-app==0.3.1` were also installed there. Python: 3.13.15. Installed harnesses: Codex 0.162.1, Claude Code 2.1.296, OpenCode 1.18.35. These are **different from the harness pins in the supplied report**.

Checkout: `b3f3efa132eb018d6d4b5cf1bfbee28c823785d5`. The pre-existing global CLI identified itself as `0.2.37.dev128`; the checkout SDK metadata said `0.2.0a13`. Neither was substituted for published 0.3.1 SDK behavior. Relevant source references below point into the checkout and were compared against the published package by the investigators. `m3-issues.md` was not present.

These are targeted reproductions, not a rerun of the 10,000-trial benchmark. Real harness startup and MCP subprocesses were used where useful. Model-dependent behavior was exercised with deterministic protocol/HTTP fixtures, explicitly identified below. No global CPU hog, default user results database, or benchmark-scale provider spend was used. Reported historical failure rates, diagnosis time, and throughput were not independently measured.

## Triage

P1 means prioritize for the large-run workflow; P2 means a confirmed correctness/configuration defect with a narrower trigger; P3 means a performance enhancement needing workload measurement. These are work priorities, not security ratings.

| # | Limitation | Result | Priority |
| --- | --- | --- | --- |
| 1 | Fixed startup timeouts | Confirmed with live Codex and deterministic readiness/probe controls | P1 |
| 2 | Error causes dropped | Confirmed with live Codex and an OpenCode HTTP fixture | P1 |
| 3 | Missing harness settings | Live OpenCode timeout failure and public configuration gaps confirmed; Claude claim needs narrowing | P1 for timeouts; P2 for permissions |
| 4 | Completed answer discarded after policy check | Confirmed through actual decoder/session policy code with a synthetic completed turn | P1 |
| 5 | Claude expands server argv differently | Confirmed with an actual Claude child process | P2 |
| 6 | Fresh session caches | Confirmed narrowly; shared runtime/catalog caches already exist | P3 |
| 7 | SQLite failures at 24 writers | Not reproduced in targeted 24-writer storage tests | Unconfirmed |

Suggested order: **#1, #2, #4, #3, #5, #6**. Keep #7 open for a full CLI/xdist workload reproduction rather than treating the original diagnosis as established.

## 1. Startup timeouts

Published m3 drove the actual Codex app-server with a stdio MCP server sleeping **40 seconds** and Codex's native `startup_timeout_sec=60`. The adapter raised an empty `TimeoutError` after **30.276 seconds**. A native-only control with the same delayed server succeeded after **40.308 seconds** when the response wait was 65 seconds. This establishes that the server was healthy and m3's timer cut it off.

A deterministic Codex protocol subprocess delaying `thread/start` by 31 seconds failed after 30.046 seconds. A published OpenCode readiness reader fed its endpoint after 30 seconds failed after 20.174 seconds with `OpenCode server endpoint was unavailable`. This OpenCode case tests the actual readiness method with a controlled stream, not a live overloaded OpenCode server.

The published 0.3.1 CLI project-Python validation probe, run against an executable wrapper sleeping 20 seconds, failed after **15.024 seconds** with `the selected project Python could not be started`.

References: [Codex timeout constant](sdk/src/m3/harness/codex.py#L54), [frame wait](sdk/src/m3/harness/codex.py#L792), [OpenCode readiness loop](sdk/src/m3/harness/opencode.py#L683), [project Python probe](cli/src/m3_cli/supervisor.py#L391).

The Codex timeout applies per frame read: unrelated frames can extend total elapsed startup time. OpenCode's repeated read timeouts likewise need not be an exact wall-clock deadline. Host overload rates and load admission behavior were not empirically measured.

Evidence: [startup investigation](/private/tmp/m3-limitations/startup_errors/findings.md), `results.jsonl`, `direct_control.jsonl`, `opencode_timeout.jsonl`, `cli_python_probe_031.jsonl` in `/private/tmp/m3-limitations/startup_errors`.

## 2. Lost error causes

A real Codex MCP initialization failure included `DETERMINISTIC_MCP_CAUSE: initialization rejected by fixture` in the raw `thread/start` error. Published m3 returned only `Codex thread/start failed (JSON-RPC -32603): required MCP server failed to initialize: slow`, with empty details. The missing text is an actual harness-reported cause, not an inferred diagnosis.

An OpenCode HTTP fixture carrying an `UnknownError` with `DETERMINISTIC_OPENCODE_CAUSE` became `transport_error` / `OpenCode turn failed` with empty details. This verifies the projection path, not a live provider denial.

**The supplied discover-race repro did not fail on Codex 0.162.1:** `DELAY_S=12` completed startup after 12.571 seconds. Also, the supplied `slow_mcp.py` never reads `ANSWER_DISCOVER`; it always returns method-not-found for `server/discover`. A synthetic raw error containing the claimed uncorrelated-response message still loses that cause through m3's projection.

References: [Codex error projection](sdk/src/m3/harness/codex.py#L886), [OpenCode error handling](sdk/src/m3/harness/opencode.py#L1222), [generic OpenCode error](sdk/src/m3/harness/opencode.py#L1301). Codex's source explicitly identifies credential echoes as the rationale for dropping free text. A future change must retain redaction while preserving useful diagnostic content.

Evidence: `live_error.jsonl` and `opencode_error.jsonl` in `/private/tmp/m3-limitations/startup_errors`.

## 3. Missing settings

Published public types reject the attempted `Codex(permission_profile=...)`, `Codex(sandbox={"network_access": true})`, `OpenCode(mcp_timeout=...)`, `ClaudeCode(environment={"MCP_TIMEOUT": ...})`, and `StdioServer(timeout=...)` fields with `extra_forbidden`. The actual Codex adapter initialization code sends `sandbox="workspace-write"`; the generated OpenCode MCP definition contains no timeout.

Real OpenCode processes using the generated config and a **35-second delayed MCP server** returned `status=failed` / `Operation timed out after 30000ms` after 35.364 seconds. The same config with `mcp.slow.timeout=60000` returned `status=connected` after 38.523 seconds. These two cases ran concurrently without model requests. This makes per-server timeout configuration part of the P1 startup work, alongside #1.

The Codex tests establish the configuration gaps and forced request field. They do not independently demonstrate `activePermissionProfile=null` with a real named permission profile, nor test every filesystem confinement requirement. Ambient Claude `MCP_TIMEOUT` is stripped, but low-level adapter environment overrides and the public `credential_env` mapping provide generic escape hatches. Thus the claim that Claude has no supported route is too broad; a dedicated public timeout setting is absent.

References: [Codex forced sandbox](sdk/src/m3/harness/codex.py#L777), [OpenCode MCP config builder](sdk/src/m3/harness/opencode.py#L133), [Claude credential environment forwarding](sdk/src/m3/harness/claude.py#L449).

Evidence: [settings investigation](/private/tmp/m3-limitations/settings_policy/report.md), `settings.json`, and `live_opencode.jsonl` in `/private/tmp/m3-limitations/settings_policy`.

## 4. Completed answers discarded

The published real Codex frame decoder reports both `list_mcp_resources` and `list_mcp_resource_templates` as calls on server `codex`. Feeding those calls and a successfully completed response `4` through the actual `AsyncAgentSession` with a bound server named `slow` and `FullToolPolicy` produced `outcome=failed`, `message="tool policy violation"`, reason `server_unavailable`, and **`response=null`**.

The control with an empty `RestrictiveToolPolicy` returned the completed answer `4`. The high-level agent selection path defaults non-Claude harnesses to `FullToolPolicy` when tools/policy are omitted, so the failure matches that default pathway. This is a deterministic session/decoder reproduction; no model was asked to choose an introspection tool, and the reported 75% incidence was not measured.

References: [high-level default policy](sdk/src/m3/_agent_selection.py#L341), [unbound server rejection](sdk/src/m3/agent_session.py#L2122), [failed result construction](sdk/src/m3/agent_session.py#L1774). Detailed serialized outcomes are in `/private/tmp/m3-limitations/settings_policy/policy.json`.

## 5. Claude argv expansion

The real m3 capture proxy configuration preserved `${ONLY_IN_SERVER:-fallback}` in argv and moved the server-only environment variable into its handoff file. An actual Claude MCP connection checker launched the child, which wrote **`fallback`**. Codex/OpenCode configuration builders preserve the literal, and a direct capture-proxy execution control wrote the literal `${ONLY_IN_SERVER:-fallback}`.

The Claude child intentionally probes argv using `cat`; its MCP health check ends with connection-closed. The observed child argument establishes the launch defect independently of a successful MCP conversation. Codex/OpenCode controls validate config/proxy semantics, not live harness spawns for this case.

References: [environment handoff](sdk/src/m3/transport/capture_proxy.py#L926), [visible child argv](sdk/src/m3/transport/capture_proxy.py#L995), [cleared environment](sdk/src/m3/transport/capture_proxy.py#L1000).

Evidence: `/private/tmp/m3-limitations/expansion_cache/report.md`, `expansion.log`, `passthrough.log`, `observed.txt`.

## 6. Session cache isolation

Two real startup-only adapter sessions for each harness recreated distinct roots, lacked the preceding session's harmless cache marker, and deleted their roots on close. Observed startup times in seconds:

| Harness | First | Second |
| --- | ---: | ---: |
| Claude Code | 0.385 | 0.274 |
| Codex | 0.987 | 0.909 |
| OpenCode | 7.257 | 8.424 |

This confirms default session cache isolation; it **does not establish 10–20 seconds of avoidable overhead**. These direct adapter sessions use installed executables and empty launch server configs, not the complete managed benchmark workload.

The broad claim that nothing read-only is shared is incorrect for 0.3.1. The managed runtime manager has a shared pinned package cache (`M3_HARNESS_CACHE_DIR`), and managed OpenCode uses an immutable model catalog prepared once per run. Fresh native session state and caches remain a potential optimization target after measuring what those existing caches already save.

References: [isolated HOME/XDG environment](sdk/src/m3/harness/native.py#L51), [runtime cache directory](sdk/src/m3/runtime/core.py#L186), [pinned cache reuse](sdk/src/m3/runtime/core.py#L447), [OpenCode catalog](sdk/src/m3/runtime/opencode_models.py#L1).

Evidence: `/private/tmp/m3-limitations/expansion_cache/cache.log` and `report.md`.

## 7. SQLite concurrency

Ran **six independent processes, each using four concurrent writer threads**, against the published `SQLiteExecutionStore`. Each writer performed 100 groups of `ensure_project`, `ensure_suite`, and `save_test_run`, totaling **7,200 store write calls per case**:

| Store setup | Elapsed seconds | Errors |
| --- | ---: | ---: |
| Shared initialized store | 7.594 | 0 |
| Separate store per process | 4.336 | 0 |
| Shared store, simultaneous cold initialization | 7.352 | 0 |

The shared database contained all 2,400 expected run records and used WAL. There were no `database is locked` or rollback errors. Separate stores were faster in this workload, but that does not reproduce the reported failures.

Published 0.3.1 already defaults to **WAL enabled and `busy_timeout_ms=5000`**, exposes those store constructor options, and only rolls back an active transaction. Thus “add WAL” is not an adequate diagnosis for this version.

This directly exercises the storage boundary with 24 concurrent writers; it is **not six full `m3 test -n 4` subprocesses**, which have additional manifest/event/lifecycle activity. Keep the original failure unconfirmed until that workload, transaction duration, filesystem, and exact error stack are captured. An initial invalid-ID fixture was corrected before the measured tests; its validation failures are not counted as store failures.

References: [WAL and timeout defaults](sdk/src/m3/storage/sqlite.py#L700), [guarded rollback](sdk/src/m3/storage/sqlite.py#L1642).

Evidence: `/private/tmp/m3-limitations/storage/stress-valid.log` and `valid-ids/results.json`.

## Reproduction artifacts

Use the isolated interpreter so the global CLI/check-out metadata does not change the package under test:

```sh
/private/tmp/m3-limitations/version031/bin/python /private/tmp/m3-limitations/startup_errors/repro.py
/private/tmp/m3-limitations/version031/bin/python /private/tmp/m3-limitations/startup_errors/direct_control.py
/private/tmp/m3-limitations/version031/bin/python /private/tmp/m3-limitations/startup_errors/opencode_error.py
/private/tmp/m3-limitations/version031/bin/python /private/tmp/m3-limitations/startup_errors/cli_python_probe.py
/private/tmp/m3-limitations/version031/bin/python /private/tmp/m3-limitations/settings_policy/repro.py settings
/private/tmp/m3-limitations/version031/bin/python /private/tmp/m3-limitations/settings_policy/repro.py policy
/private/tmp/m3-limitations/version031/bin/python /private/tmp/m3-limitations/settings_policy/live_opencode.py
/private/tmp/m3-limitations/version031/bin/python /private/tmp/m3-limitations/expansion_cache/repro_expansion.py
/private/tmp/m3-limitations/version031/bin/python /private/tmp/m3-limitations/expansion_cache/repro_passthrough.py
/private/tmp/m3-limitations/version031/bin/python /private/tmp/m3-limitations/expansion_cache/repro_cache.py
/private/tmp/m3-limitations/version031/bin/python /private/tmp/m3-limitations/storage/repro.py
```

Temporary artifacts are local to this machine and have not been committed. For a fresh storage rerun, use a new resolved artifact directory; deterministic IDs deliberately make existing store contents unsuitable for another fresh-count assertion. On macOS, pass resolved `/private/tmp` database paths: m3 correctly rejects `/tmp` as a symlink component.
