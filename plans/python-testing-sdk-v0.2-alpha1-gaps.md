# MCP Pal `0.2.0a1` recorded gaps

This file records known gaps at the Phase 8 alpha gate. They are not silently
claimed as implemented behavior.

## Planned later-phase API

- `MCPTestKit.run(...)`, `submit(...)`, and `agent_session(...)` remain typed
  `UnsupportedFeature` boundaries until the shared agent runtime in Phase 9.
- Persistent workers, SQLite leases, API/UI migration, pytest-plugin runtime,
  documentation/E2E coverage, CI portability, and release automation remain
  owned by their later plan phases.
- The packaged legacy `/api/v1` and HTTP-backed Streamlit application remain
  isolated from the new SDK execution paths. No partial redirection or v1
  database compatibility/detection logic was introduced.

## Pinned dependency limitations

- Official `mcp==2.0.0` exposes no public arbitrary handshake-revision
  selector. MCP Pal supports automatic/current negotiation and rejects other
  explicit revisions before startup; it does not claim revision-matrix
  coverage.
- Official HTTP transports expose no safe connection-level DNS pinning hook.
  MCP Pal validates every resolved address, blocks private/metadata targets,
  and disables redirects, but a DNS-rebinding TOCTOU window remains.
- OAuth is accepted through supplied official `httpx2.Auth` implementations;
  MCP Pal does not implement an automatic browser authorization flow.
- Direct traces observe normalized decoded MCP messages. Raw-wire capture is
  reported as incomplete rather than inferred.
- Intentionally malformed/truncated SSE fixtures can trigger official
  MCP/AnyIO socket-finalizer `ResourceWarning`s after owned cleanup. Repeated
  gates found no retained MCP Pal threads, processes, tasks, or listeners.

## Release and repository follow-ups

- Full-tree strict mypy for legacy API/UI/harness/trace modules is scheduled by
  Phase 20. Phase 0–8 SDK modules, public usage examples, and parity checks are
  strict-clean.
- Package project URLs still point to the existing personal GitHub namespace.
  The supplied Atlan security policy requires an approved `AtlanHQ` canonical
  repository before an external release; an unverified URL was not invented.
- Official MCP emits deprecation warnings for legacy sampling, roots, logging,
  and resource subscription APIs. These paths remain covered because the
  pinned client still exposes them.
