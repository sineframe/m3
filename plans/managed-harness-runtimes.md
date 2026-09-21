# Managed harness versions for m3 tests

## Summary and public interface

Add a managed runtime for the four native harnesses: Claude Code, OpenCode, Codex, and Pi. A run may select several versions of the same harness and report each harness, version, and model as a separate configuration. ACP agents remain outside this feature.

Managed mode downloads the platform specific **CLI executable** into m3’s cache and runs it by absolute path. It never replaces a system executable. Asset selection must exclude desktop applications, app server packages, symbols, source archives, and unrelated helpers.

```bash
m3 test --runtime=managed \
  --harness opencode@1.18.30=opencode/big-pickle \
  --harness opencode@1.18.31=opencode/big-pickle \
  --harness codex@0.155.1=gpt-5.6-sol \
  -- tests/test_shipping.py
```

```python
with MCPTestKit(harness_cache_dir="/shared/m3-harness-cache") as kit:
    agents = kit.agents([
        {
            "harness": "opencode",
            "runtime": "managed",
            "version": "1.18.30",
            "models": ["opencode/big-pickle"],
        },
        {
            "harness": "opencode",
            "runtime": "managed",
            "version": "1.18.31",
            "models": ["opencode/big-pickle"],
        },
    ])
    for agent in agents:
        result = agent.run("Get a shipping quote", server=shipping_server)
        print(agent.harness, agent.model, result.outcome)
```

- Add `runtime: Literal["system", "managed"] = "system"` and `version: str | None` to native harness specifications, `kit.agents(...)` selections, and directly constructed `AgentSpec` harnesses. Selection expansion performs no network or process I/O.
- Add `--runtime=system|managed`, `--harness KIND[@VERSION]=MODEL[,MODEL...]`, and `--harness-cache-dir PATH` to `m3 test`. The CLI runtime supplies the default for CLI selected agents; an explicit Python agent setting wins for that agent. Include runtime and version in duplicate selection keys.
- System mode without a version uses the machine’s installed executable. A version in system mode is a usage error pointing to `--runtime=managed`. Managed mode without a version selects `latest`. Never silently switch modes.
- Add `harness_cache_dir` to **both** `MCPTestKit` and `AsyncMCPTestKit`. Cache root precedence is explicit kit argument, CLI flag, `M3_HARNESS_CACHE_DIR`, then platform default: `~/Library/Caches/m3/harnesses` on macOS, `${XDG_CACHE_HOME:-~/.cache}/m3/harnesses` on Linux, and `%LOCALAPPDATA%\m3\harnesses` on Windows, falling back to `~/AppData/Local/m3/harnesses`.

## Managed run lifecycle

1. **Select:** Validate kind, runtime, and version before forming cache paths. Record `harness.selection` with the requested harness, selector, and model so setup failures retain an identity.
2. **Detect target:** Determine OS and CPU architecture from the running machine, plus Linux libc where recipes differ. Select only an exact supported target; give an actionable unsupported target error instead of guessing.
3. **Resolve release:** A pinned selector finds its vendor release and exact CLI asset. The first unresolved `latest` selector per invocation fetches current vendor metadata. Share an invocation pin directory across CLI and xdist workers, with a selector lock and atomic mapping from `(kind, target, requested selector)` to canonical version, sanitized asset identity, digest, and verification method. All workers therefore use one version even if a release changes during the run. Keep this selector lock distinct from the install lock.
4. **Acquire:** Validate the cache receipt and executable hash. On a hit, emit “loaded from cache.” Otherwise take the per-install lock, recheck, stream the asset to a private staging directory, verify it, safely extract if applicable, smoke check its version, write a receipt, and atomically publish it. Workers waiting for the same install reuse the published result.
5. **Start:** Have adapter resolution produce a synchronous managed adapter wrapper. At the start of `AgentSession._start_adapter`, its asynchronous `prepare()` resolves and acquires the executable, retains a cache lease, and creates the native adapter with an absolute executable path. Emit `harness.runtime_resolved` through the recorder before vendor preflight and `adapter.open`. Preparation finishes before any test call uses that harness. Give each execution a separate writable home/config directory while preserving existing credential handoff. Release the lease and temporary directory on success, error, and cancellation.
6. **Report:** Run configurations under existing pytest and xdist concurrency controls. A resolution or install failure fails that configuration with a setup error; other configurations continue.

## Registry, cache, and safeguards

- Add a provider recipe registry with metadata endpoints, exact CLI asset name templates, target mappings, executable names, archive formats, and integrity sources. Claude Code uses its native binary and manifest checksum. OpenCode, Codex, and Pi use their CLI release assets; use a vendor checksum when available, otherwise the GitHub release asset digest. Describe that digest as **GitHub provided download integrity**, not a publisher signature. Record the verification method and immutable release status. GitHub documents [asset digests](https://github.blog/changelog/2025-06-03-releases-now-expose-digests-for-release-assets/) and [immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases/). Refuse assets with no acceptable digest.
- Store installs under `<root>/<kind>/<canonical-version>/<target>/sha256-<digest>/`, with separate harness subdirectories. Receipts contain canonical version, target, sanitized source identity, asset name, archive and executable hashes, verification method, and immutable release status. Never persist a signed URL query string.
- Limit versions to 64 ASCII characters in canonical `major.minor.patch` form with an optional lowercase prerelease suffix. Reject leading zeroes, separators, dot segments, absolute paths, controls, and unsupported characters. Normalize only known vendor tag prefixes, such as `v` for OpenCode/Pi and `rust-v` for Codex, then validate again. Apply validation to CLI input, Python input, vendor metadata, and receipts. Check real path containment and reject symlinks in cache path components.
- Require HTTPS for metadata, downloads, and every redirect. Authenticate only to the intended metadata host, strip authorization on redirects, and redact URL queries, proxy credentials, and sensitive exception text from logs, events, pins, and receipts.
- Enforce streaming download and extraction limits. These are **hard rejection ceilings, not expected CLI sizes**. Current macOS ARM CLI release assets are [44.1 MB for OpenCode](https://github.com/anomalyco/opencode/releases/tag/v1.18.31), [86.4 MB for Codex](https://github.com/openai/codex/releases/expanded_assets/rust-v0.155.1), and [29 MB for Pi](https://github.com/earendil-works/pi/releases/expanded_assets/v0.86.1).

  | Selected CLI recipe | Download ceiling | Expanded ceiling | Entry ceiling | Expansion ratio ceiling |
  | --- | ---: | ---: | ---: | ---: |
  | Claude Code direct native binary | 512 MiB | 512 MiB | 1 | Not applicable |
  | OpenCode CLI archive | 256 MiB | 512 MiB | 64 | 100:1 |
  | Codex CLI archive or direct executable | 384 MiB | 768 MiB | 64 | 100:1 for archives |
  | Pi standalone CLI archive | 256 MiB | 512 MiB | 128 | 100:1 |

  Reject traversal, symlinks, hard links, device entries, duplicate unsafe destinations, and limits exceeded during streaming. A future legitimate asset above a ceiling must fail clearly until its recipe is reviewed and updated.

- Add `m3 runtime cache list` and `m3 runtime cache prune`. `list` reads only the selected root and prints sorted kind, version, target, digest, and cache status; an empty cache is a successful result. `prune` removes **all** harness entries from that root and reports entries and bytes removed; running it twice succeeds. Both commands accept `--harness-cache-dir` and use the same environment/default precedence. Neither contacts vendors. Prune coordinates with install locks and active leases, waits up to 10 seconds, then exits with an operational error if an entry is still in use. Do not prune automatically.

## CLI progress and report data

The CLI displays preparation while `m3 test` runs:

```text
m3: OpenCode latest (darwin-arm64): resolving release
m3: OpenCode 1.18.31: downloading 42/44 MB (95%)
m3: OpenCode 1.18.31: verifying
m3: OpenCode 1.18.31: installing
m3: OpenCode 1.18.31: ready; running test
m3: OpenCode 1.18.30: loaded from cache; running test
```

- Before launching pytest, the CLI supervisor creates a private `0700` directory and `0600` JSONL file and passes its path to pytest and xdist workers. Workers append short phase events and throttled download byte events under a file lock. The supervisor replaces blocking `process.wait()` with a loop that polls pytest and reads appended lines about every 200 ms. It prints each phase once, throttles byte output to about 250 ms, and merges workers waiting on the same install. Pytest capture remains enabled.
- Events contain `kind`, `target`, `requested_selector`, nullable `resolved_version`, phase, and bounded byte counts. The key is `(kind, target, requested_selector, resolved_version)`; the initial latest resolution event has no resolved version. Bound lines to 2 KiB and total input to 16 MiB; validate phase, strings, and numeric fields against recipe limits. Ignore malformed events. Read the original file descriptor and stop consuming the stream if the path is replaced or truncated, without delaying pytest. Remove the temporary directory on supervisor exit.
- Add a shared optional agent identity model to `ExecutionState`, `ExecutionReport`, and `TraceView`. Its harness fields include kind, runtime mode, requested selector, resolved version, target, digest, and verification details. Its model fields include requested model, provider where known, and observed model ID from vendor evidence. Project identity from `harness.selection`, `harness.runtime_resolved`, and existing observations. Persist it in SQLite and ephemeral snapshots; expose it in app list/detail/report API payloads and exported trace dumps. Old records load with absent fields; additive JSON fields need no SQL column migration.
- Update feedback configuration labels and identity digests to use resolved version and digest plus requested model. Two `latest` runs resolving differently must produce distinct matrix configurations. An unresolved setup error retains its requested selector. Keep the report matrix generic across case × configuration.

## Documentation

Update CLI and SDK documentation with both runtime modes, multiple version examples, `latest`, target detection, cache defaults and overrides, progress, integrity limits, errors, and both cache commands. Update app API and trace schema documentation for agent identity. Update `skills/testing-with-m3/SKILL.md` and its CLI/test pattern references with managed runtime usage and examples; **do not advertise prune in the skill**.

## Test plan and release gates

Build deterministic tests around a local fake release server and small fake CLI assets for **each of the four provider recipes**. These tests must use real files, subprocesses, cache directories, and CLI invocation where the behavior crosses process boundaries; mock only vendor metadata and asset delivery. Organize them alongside the existing SDK unit/integration/e2e, CLI supervisor, and app API tests.

| Area | Required cases |
| --- | --- |
| Public selection and models | Default system mode; managed pinned and latest; repeated kind/model with different versions; same version duplicate rejection; direct `AgentSpec`; `kit.agents`; side effect free collection; system mode plus version error; ACP plus managed error; malformed version corpus from Python and CLI; frozen model serialization round trips. |
| Sync and async parity | Identical `harness_cache_dir` signatures and behavior in `MCPTestKit` and `AsyncMCPTestKit`; explicit argument over CLI over environment over default; all platform defaults; invalid/unwritable roots; close and cancellation cleanup for both APIs. |
| Target and recipe selection | Parameterize OS, architecture, and Linux libc for each supported recipe; unsupported targets fail before download; each recipe selects the CLI asset rather than similarly named desktop, app server, package, source, or helper assets; executable name and reported version match the selected recipe. |
| Release resolution | Pinned and latest lookups; latest fetched once within an invocation but refreshed in a new invocation; metadata changed between xdist workers still yields one lookup and one version/digest; corrupt and partial shared pins; simultaneous pinned and latest selections; cache hit after pin resolution. |
| Download and integrity | Correct digest, mismatch, missing digest, truncated response, lying `Content-Length`, interrupted stream, timeout, HTTP error, retryable failure, redirect to HTTP, cross host authorization stripping, signed URL redaction, proxy credential redaction, and no sensitive text in errors, receipts, pins, progress, or trace. |
| Resource and archive safety | For each recipe, test exactly at and one byte over download and expanded limits; archive entry count and ratio boundaries; traversal, absolute entries, symlinks, hard links, devices, duplicate destinations, malformed archives, and decompression failure. Assert no published install after rejection. |
| Cache lifecycle | Cold install, warm hit without download, corrupted receipt, changed executable hash, partial staging directory, atomic replacement, two processes racing for one install, stale lock recovery, read only cache error, symlinked path component, in use lease, and cleanup after success, failure, timeout, or cancellation. |
| Adapter behavior | Managed executable absolute path reaches each native adapter before preflight; system executable remains untouched; runtime event precedes preflight/open; test execution blocks until acquisition completes; one failed configuration does not block other configurations; per-execution writable directories differ. |
| Progress transport | Expected cold, warm, waiting, and failure messages through an actual `m3 test` subprocess; xdist events deduplicated; no loss of pytest summary or exit status; byte throttling; malformed JSON, unknown phase, negative/huge counters, oversized strings and lines, total budget, truncation, path replacement, and cleanup of progress files. |
| Reports and persistence | Harness and model identity in state, report, trace, SQLite, ephemeral store, app API list/detail/report, and exported trace JSON; old stored records; observed model projection; two different resolved `latest` versions get distinct labels and digests; unresolved setup error retains selector; multiple cases × versions × models produce the expected matrix cells. |

Test **every new CLI command and option through the public executable**, alongside parser unit tests:

- `m3 test --runtime=system`, `--runtime=managed`, pinned `--harness`, versionless `--harness`, multiple versions, `--harness-cache-dir`, environment cache override, options before `--`, and forwarded pytest arguments. Check collected item counts, actual selected executable paths, persisted report identities, stdout progress, and child exit code.
- Reject invalid runtime values, invalid kind/version syntax, system mode with a version, managed ACP, duplicate exact selections, conflicting cache paths, and unsupported targets. Confirm usage errors occur before pytest starts and do not leak secrets.
- `m3 runtime cache list`: empty and populated roots, all four harness subdirectories, deterministic sort order, custom root and environment precedence, corrupt entries, help text, and proof that listing makes no network request or install.
- `m3 runtime cache prune`: remove entries for **all four** harnesses, verify `list` is empty afterward, repeat on an empty cache, exercise custom root and environment precedence, test active leases and concurrent installs, reject a symlink escape, verify failure exit status, and prove unrelated files outside the selected root remain untouched.
- `m3 runtime --help`, `m3 runtime cache --help`, and each command’s `--help`; unknown subcommands and flags; subprocess exit codes and stderr for failures. Run the CLI tests with a real pytest child, including `-n 2` for shared pin and install behavior.

Add a required provider backed live test using pinned OpenCode `1.18.30` and `1.18.31` against the existing local MCP tool server. It must verify a tool call and two distinct report cells, then run again to prove cache hits; add a managed `latest` smoke test. Add opt-in live managed smoke tests for Claude Code, Codex, and Pi to exercise each real vendor asset and adapter when credentials are available. Run the OpenCode multi-version live job successfully before release. Run SDK, CLI, and app suites plus the xdist integration cases; use the project’s supported macOS, Linux, and Windows CI runners for target specific smoke checks.

The managed directory isolates m3’s executable and writable runtime state from the system installation. It does not claim to be an operating system security sandbox.
