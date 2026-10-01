# Capability documentation implementation contract

Maintainer coordination file. This is not registered as product documentation.

## Scope and ownership

This change covers the five approved capability areas only: ACP connection and
wrapper use; elicitation (composed plans, responses, agent paths, manual/direct
operations, and managed input); credentials; native managed runtimes and runtime
cache; and repeated tests across native agent harnesses. The ACP writer also
owns the existing `acp.md` overview. The elicitation writer owns existing
`plans.md` and `managed-input.md` pages and their references. Each keeps those
pages canonical while incorporating scoped links and necessary capability
updates.

The ACP writer owns `guides/agents/acp-connect.md` (Connect an ACP-compatible
agent), `guides/agents/acp-wrapper.md` (Expose your custom agent through ACP),
and `reference/acp.md`. The elicitation writer owns
`guides/elicitation/composed.md`, `responses.md`, `agents.md`, `manual.md`, and
`reference/python/m3/managed-input.md`; existing `plans.md` and
`reference/python/m3/elicitation.md` remain canonical. The credentials writer
owns `guides/credentials.md` and `reference/credentials.md`.

The managed-runtime writer owns the rewrite of
`guides/agents/managed-runtimes.md` (Run a pinned agent harness), plus
`guides/agents/versions.md` (Compare harness versions),
`guides/agents/runtime-cache.md` (Inspect and maintain runtime cache), and
`reference/managed-runtimes.md`. It covers native Codex, Pi, Claude Code, and
OpenCode adapters; pin and latest resolution; runtime provenance and identity;
cache defaults, precedence, validation, leases, and pruning; auth versus binary
acquisition; session cleanup and cancellation; typed failures; and the OS
process boundary. ACP and `managed_runtime.py` as private managed-input code
are outside the executable native-runtime contract.

The multi-harness writer owns `guides/agents/multiple-harnesses.md` (Run the
same test across agent harnesses), plus focused updates to `harnesses.md` and
`matrices.md`. Its unchanged pytest action compares Codex, Pi, Claude Code, and
OpenCode using one local shipping server, prompt, tool identity, arguments,
and assertions. The full selection is eight executions (four harnesses by two
trials). The ACP extension merges a marker manifest with the CLI selection for
ten executions; local ACP is labeled deterministic. Do not imply equal policy
enforcement across adapters or that trial count is retry count.

The integration owner maintains navigation and shared integration: README,
CLI/pytest/matrix/first-test/CI/examples landing links, links outside the
ACP/elicitation pages, API reference generator gaps, scoped compatibility evidence, and shared
`agents-first-test/example.json` mappings. Writers own their new example
projects and manifests. Keep each example project self-contained. Do not edit
another writer's page or manifest. The renderer combines mappings by page, so
each Python block needs a correctly ordered source mapping, including inline
server files.

Acceptance includes all named subguides: two ACP guides; six elicitation guides
including the canonical plans and managed-input pages; one credentials guide;
three managed-runtime guides; and the multi-harness guide plus shared harness,
matrix, and ACP overview updates. The new ACP and elicitation API/reference
pages are part of the same delivery.

## Author contract

Every practical guide puts its complete main project inline after prerequisites
and before commands. Include every named server, test, configuration, fixture,
manifest, and task-bearing file. Downloads support the inline example and do
not require assembling code across guides. Variations follow the main verified
result and name the exact file plus insertion/replacement point; dependent
multi-file edits show complete resulting files. Reference examples stay
focused with imports and context; concepts may be illustrative.

Each source project lives under `sdk/examples/docs/<guide-id>`. Its
`example.json` owns page IDs, files, dependencies, commands, exit codes,
assertions, compatibility, external requirements, credentials, cleanup, and
verification mode. Keep rendered code synchronized and test executable
variations. Use reader-created IDs in commands. Commands state their working
directory. Capture outputs from verified runs and explain what each assertion
proves. Separate supported, unsupported, and unverified behavior. Record
release, symbols, tests or observations, status, and limits. Never invent
outputs or live evidence.

Use concrete, active developer prose. Avoid marketing adjectives, staged
objections, unprompted contrast framing, general em/en dash connectors, and
repetitive endings. A feature limitation should state what the reader should do.

## Validation

Preserve the exhaustive API inventory. Fix only scoped omissions for public
properties, enums, default factories, and supported compatibility imports; do
not change SDK exports or behavior, or publish internal APIs. Inspect this
worktree's current `main` changes first. The audit baseline is release
`0.2.18`; this worktree is candidate content from untagged commit `25738ca`
plus these edits and must be labeled a development preview. Build the landing
site from a separate temporary landing worktree because its prepare-docs step
mutates generated content. Do not update any production release pin.

Run the five repository docs checks in `docs/contributing.md`. Execute each
deterministic example from a clean directory outside the repository against the
candidate package/source. Build the combined landing site with
`M3_DOCS_DIR=/Users/utk/Documents/code/m3-docs-capabilities npm run build` in
that temporary landing worktree, after reading its instructions. Do not run
broad paid live suites. Record
unavailable live prerequisites and the remaining harness/model/provider/OS
matrix precisely. Readiness checks, collection, mocks, and syntax checks do not
prove live provider behavior.

## Integration verification record

Recorded 2026-10-01. Audit baseline: release `v0.2.18`. Candidate: untagged
commit `25738cac1cf9ffc9e60b4680bfc0185398939446` plus this worktree's
documentation changes. This is a development preview, not a published release.
The Python API inventory was rendered and checked with the repository's
canonical `/Users/utk/Documents/code/m3/.venv/bin/python` (Python 3.13.15); the
script resolves its SDK imports from this worktree. Using the candidate Python
3.14.7 environment changed inspected typing representations, so that
environment was not used to update the inventory.

The five repository documentation checks passed on the final source snapshot:

- `python3 scripts/render_docs_examples.py --check` — current.
- `python3 scripts/render_docs_navigation.py --check` — current.
- `python3 scripts/validate_docs_site.py` — 80 pages validated.
- `python3 scripts/validate_docs_examples.py` — 18 manifests validated.
- `/Users/utk/Documents/code/m3/.venv/bin/python scripts/render_docs_api_reference.py --check` — current.

The recursive source-manifest audit found 18 projects with no listed-versus-
present file mismatches. A same-build landing audit found 80/80 routes and
Markdown twins with zero code-copy/fence mismatches. All nine new source-project
links resolve into rendered links to the corresponding candidate project paths.
The existing first-test project is the one of 18 manifests without a source
link; its guide already links to that task's setup page.
The final read-only mapping audit checked 59 file-backed Python blocks against
their manifest source and order; all matched. In `managed-runtimes.md`, block
three is `test_local_contract.py` and block four is
`test_managed_runtime_fixture.py`, matching their nearby labels and commands.

The API inventory change is +1,285/-868 lines. It adds M3-defined public
properties, enum values, and actual default-factory values; changes the member
heading to `Public members`; and does not list Pydantic's `model_extra` or
`model_fields_set`. It does not alter SDK exports or behavior. The focused
`sdk/tests/unit/test_docs_api_reference.py` test asserts representative
properties, enum values, and factories and rejects those framework properties.

Candidate SDK and CLI checks used the isolated editable environment
`/private/tmp/m3-docs-candidate-venv` (Python 3.14.7, macOS arm64), with
`sdk[pytest,judge]`, `app`, and `cli` installed from this worktree. The four
managed-runtime SDK unit suites, managed-runtime CLI suite, and API-generator
regression test passed: `142 passed, 1 warning in 11.22s`. The warning is the
archive-security test's deliberate duplicate ZIP member. A separate run of
`sdk/tests/unit/test_matrix_models.py` and the API-generator regression test
passed: `45 passed in 0.56s`.

The exact commands were:

```sh
/private/tmp/m3-docs-candidate-venv/bin/python -m pytest -q \
  sdk/tests/unit/test_docs_api_reference.py \
  sdk/tests/unit/test_managed_runtime_core.py \
  sdk/tests/unit/test_managed_runtime_security_boundaries.py \
  sdk/tests/unit/test_managed_runtime_session.py \
  sdk/tests/unit/test_managed_runtime_identity.py \
  cli/tests/test_managed_runtime_cli.py

/private/tmp/m3-docs-candidate-venv/bin/python -m pytest -q \
  sdk/tests/unit/test_matrix_models.py \
  sdk/tests/unit/test_docs_api_reference.py
```

Clean external project copies were made under `/private/tmp`, outside the
checkout; they were copied from this candidate, not downloaded from the
publisher. In the managed-runtime project, `python -m pytest -q
test_local_contract.py` passed (`1 passed in 1.24s`). It exercises a local
shipping server, recorded MCP result matcher, and registered evaluator without
runtime acquisition or provider access. In the multi-harness project, the
deterministic local ACP command
`m3 test --python /private/tmp/m3-docs-candidate-venv/bin/python --harness
acp=fixture --trials 2 -- tests/test_agents.py` passed (`2 passed in 3.63s`),
with two completed executions and zero MCP tool-error results. The same
project's CLI `--collect-only` commands collected 8 tests for four native
selectors by two trials and 10 with the ACP selector added (both `0.01s`);
these are collection counts, not provider runs. An initial ACP invocation
overlapped two collection processes and hit an M3 SQLite `database is
unavailable` internal error. The serialized rerun above passed.

The collection commands used placeholder model strings and were run from
`/private/tmp/m3-docs-multi-final.IL9VTW`:

```sh
/private/tmp/m3-docs-candidate-venv/bin/m3 test \
  --python /private/tmp/m3-docs-candidate-venv/bin/python \
  --harness codex=dummy-codex --harness pi=dummy-pi \
  --harness claude_code=dummy-claude --harness opencode=dummy-opencode \
  --trials 2 -- tests/test_agents.py --collect-only

/private/tmp/m3-docs-candidate-venv/bin/m3 test \
  --python /private/tmp/m3-docs-candidate-venv/bin/python \
  --harness codex=dummy-codex --harness pi=dummy-pi \
  --harness claude_code=dummy-claude --harness opencode=dummy-opencode \
  --harness acp=fixture --trials 2 -- tests/test_agents.py --collect-only
```

The ACP and elicitation owner reports a combined candidate run of the
elicitation plans, managed-input API/storage, ACP contract, and manifest suites:
`82 passed in 5.51s` using an isolated Python 3.14.7 candidate environment
with `pytest-asyncio 1.4.0`; no provider tests ran. Separately, clean external
project copies passed using editable candidate source and Python 3.14.7 from
`/private/tmp/m3-capability-clean-copies.Jd3M0p`, with the shared environment
at `/private/tmp/m3-capability-clean-copies.Jd3M0p/venv`: elicitation plans
(2 passed in 2.14s), composed (3 in 1.99s), responses (4 in 2.05s), manual
(1 in 2.08s), managed-input store (1 in 1.54s), ACP connect (1 in 3.03s), ACP
wrapper (1 in 3.09s), and credentials (3 in 4.07s), for 16 deterministic
clean-copy passes. Project directories were `elicitation-plans`,
`elicitation-composed`, `elicitation-responses`, `elicitation-manual`,
`elicitation-managed-input`, `acp-connect`, `acp-wrapper`, and `credentials`.
Each command was `python -m pytest -q` with the owning file (`test_plan.py`,
`test_composed.py`, `test_responses.py`, `test_manual.py`,
`test_managed_store.py`, `test_connect.py`, `test_wrapper.py`, or
`test_credentials.py`) from its copied project directory.
The live agent and managed-input tests in those projects were not run. Isolated
candidate CLI credential suites separately passed `20 tests in 34.30s`. A
candidate offline workspace dry run with the corrected workflow extras ran
`uv sync --all-packages --extra judge --extra pytest --dry-run --offline`:
62 packages would install, including pytest 9.1.1 and pytest-asyncio 1.4.0 and
the workspace `sf-m3`, `sf-m3-app`, and `sf-m3-cli` packages. No workflow,
GitHub Actions provider calls, external credential validation, or live login
flows were run.

The runtime-cache owner reports an actual Codex `0.156.1` acquisition on
macOS arm64 / Python 3.14.7 (2026-10-01), target `darwin-arm64-64`, SHA-256
`2bd64af14dedd47795f2f6bfd5d125cf79199acc2c7ba222144e08127111a5ca`. A second
acquisition returned a cache hit without another download event; pruning
preserved active leases and removed the entry after release. The isolated
temporary cache was pruned and cleaned. This acquisition proves that asset and
cache path only; it is not a provider or harness session.

All four native CLI executables were present at Codex CLI 0.159.3, Pi 0.85.1,
Claude Code 2.1.278, and OpenCode 1.18.31. Model variables were not configured.
Non-mutating checks did not establish native login availability, so auth status
is unknown. No live provider requests were made for any native adapter, no
version-comparison live run was made, and no PowerShell equivalence run was
made. The native four-row result matrix remains unverified; local collection
and ACP results do not satisfy it.

The combined landing preview is built only in the separate temporary landing
worktree `/private/tmp/landing-doc-build.mEMkMP/tree`. Its latest completed
build passed with 80 routes, 11 compatibility routes, and 17,023 links/assets
across 92 pages and 91 Markdown twins, plus LLM outputs and development
identity; landing output outside `dist/docs` remained unchanged. A build-only
`prepare-docs.mjs` change in that temporary worktree copies the existing
harness and banner assets from the M3 candidate. That publisher change is not
part of this worktree or a production release pin; the original landing
checkout remained clean and its release-pin checksum was unchanged. All 13/13
banner/harness/LLM assets are present in the preview. The temporary publisher's
prepare step also passed with the pinned `v0.2.18` archive when the new optional
asset directories were absent (66 staged pages); this was not a full release
site build. Rendered source-project links use the exact base commit
`25738cac1cf9ffc9e60b4680bfc0185398939446`; the link URL shape and local paths
are verified, but the new files are not present at that base ref. Remote
reachability remains unverified until a commit containing them is published.
The site publisher does not generate source archives. No archive download is
claimed or was tested.

The first-test fixture was restored to its prior single-tool shipping server.
The multi-server matrix now has its own self-contained
`sdk/examples/docs/agents-matrices` project and manifest. Four obsolete shared
example files (`test_acp_agent.py`, `test_agent_matrix.py`, `test_harness.py`,
and `test_managed_runtime.py`) were removed from `agents-first-test` after
reference search confirmed their new owning projects replace their old
mappings; the original files remain recoverable from the base commit.
