---
title: "Managed harness runtimes"
description: "Reference for native harness selection, version resolution, executable acquisition, provenance, cache configuration, leases, and failure behavior."
---

# Managed harness runtimes

Managed mode selects a native harness executable, records its resolved
identity, and keeps reusable binaries in an external cache. It does not
provide provider credentials or create an operating-system sandbox.

## Runtime and version selection

Native agent selections accept `runtime="system"` or `runtime="managed"`.
System is the default and uses the executable discovered by that adapter. A
system installation does not guarantee an exact version. Managed accepts an
explicit semantic version or `latest`:

| SDK selection | CLI selection | Meaning |
| --- | --- | --- |
| `{"harness": "codex", "models": [model]}` | `--harness codex=MODEL` | System executable. |
| `{"harness": "codex", "models": [model], "runtime": "managed", "version": "0.155.1"}` | `--runtime managed --harness codex@0.155.1=MODEL` | Resolve or reuse that pin. |
| `{"harness": "codex", "models": [model], "runtime": "managed", "version": "latest"}` | `--runtime managed --harness codex=MODEL` | Resolve the current release for this invocation. |

An SDK `version` requires `runtime="managed"`; `AgentSpec` validation raises
`ValueError("harness version requires managed runtime")` otherwise. A CLI
`KIND@VERSION=MODEL` likewise requires `--runtime managed`; the CLI rejects
that combination during option validation. With managed mode and no explicit
version, the SDK and CLI resolve `latest`. Do not supply `latest` where the
test asserts an immutable pin. The accepted explicit version is
`MAJOR.MINOR.PATCH` with an optional lowercase prerelease suffix. Build
metadata and a leading `v` are not accepted by the selector parser.

Supported native adapter names are `codex`, `pi`, `claude_code`, and
`opencode`. Selector aliases accepted by runtime acquisition include
`claude_code` for Claude Code and `open-code` for OpenCode. ACP is not a
managed native runtime; CLI validation rejects `--runtime managed` with an ACP
selection. A custom ACP agent owns its launch executable and cache.

`latest` uses release metadata. The CLI creates an invocation-scoped pin shared
by workers in that `m3 test` invocation, including xdist workers. A later CLI
invocation resolves it again. A concrete version can use a valid matching
cache entry without a metadata request. Cache hits are keyed by harness,
resolved version, target, and archive digest.

## Release metadata and target rules

The built-in recipes use these upstream release sources and tag conventions.
The selected release still has to publish one asset matching the computed
target.

| Harness | Metadata source | Versioned GitHub tag |
| --- | --- | --- |
| Codex | `api.github.com/repos/openai/codex/releases/{latest or tag}` | `rust-v<VERSION>` |
| Pi | `api.github.com/repos/earendil-works/pi/releases/{latest or tag}` | `v<VERSION>` |
| OpenCode | `api.github.com/repos/anomalyco/opencode/releases/{latest or tag}` | `v<VERSION>` |
| Claude Code | `downloads.claude.ai/claude-code-releases/latest` or `/{VERSION}/manifest.json` | Vendor manifest version; not a Git tag lookup |

Target strings are computed by `detect_target(kind, env=...)`. `amd64` and
`x86_64` normalize to `x64`; `aarch64` normalizes to `arm64`. The resolver
reads target overrides from the selector mapping's `env` field, not from a new
CLI option:

| Override | Effect |
| --- | --- |
| `M3_RUNTIME_ARCH` | Replaces `platform.machine()` before architecture normalization. |
| `M3_ROSETTA=1` | On arm64 macOS, labels the target architecture `x64-rosetta`. |
| `M3_RUNTIME_LIBC` | For OpenCode on Linux, selects the libc label; otherwise libc is detected and falls back to `glibc`. |
| `M3_RUNTIME_AVX2=1` | For OpenCode on Linux, selects the `avx2` variant instead of `baseline`. |

Examples of target labels include `darwin-arm64-64`, `linux-x64-64`,
`windows-arm64-64`, `linux-x64-glibc-baseline`, and
`linux-x64-musl-avx2`. These labels describe M3's lookup input; they do not
promise that a vendor publishes an asset for it.

## Acquisition and recorded identity

The resolver reads the vendor release manifest, selects one target asset, and
requires an independent SHA-256 value from GitHub release metadata. Claude Code
uses the checksum in its platform manifest. A caller-supplied runtime selector
must provide its own `sha256`; a hash computed only after downloading is not
independent verification. M3 stages the archive, compares its SHA-256, rejects
unsafe archive paths and entry types, extracts within size and member limits,
checks the expected executable with `--version`, writes a receipt, and then
creates a session lease. It does not infer a provider identity from an
executable download.

Extraction preserves the owner execute bit of regular files and normalizes
file modes to `0755` (executable) or `0644`. Setuid, setgid, sticky, and group
or world write bits are never kept.

For Codex, M3 prefers the official `codex-package-<target>.tar.gz` archive,
which contains `bin/codex`, `codex-package.json`, and supporting resources. It
falls back to the standalone `codex-<target>` archive only for releases that
publish no package. When the release also publishes `codex-code-mode-host` for
the target, Codex needs `bin/codex-code-mode-host` next to `bin/codex` to run
MCP tool calls. In that case M3 requires the package asset, requires the
companion to be an executable regular file in the archive, and records it in
the provenance `companions` list. M3 also requires `codex-package.json` to
match the selected version, target, and entrypoint. If such a release has no
package asset, acquisition fails instead of installing a runtime that cannot
call tools. Releases that publish no host, including all releases without a
package, record an empty `companions` list.

The stable agent identity separates the requested selector from resolved
metadata. `snapshot.agent.harness` records `kind`, `runtime`, and
`requested_selector`; after resolution it can also record `resolved_version`,
`target`, `digest`, `verification_method`, and `immutable_release`. Model
identity separately records the requested model ID, observed provider, and
observed model when the adapter reports them. An unresolved startup retains
the requested selector without fabricating resolved fields.

## Platform support

The resolver computes OS and CPU targets, then applies vendor filename or
manifest-key matching rules. The rules below do not claim that matching assets
exist in any particular vendor release:

| Harness | Target details |
| --- | --- |
| Codex | `codex-package-{x86_64\|aarch64}-{apple-darwin\|unknown-linux-musl\|pc-windows-msvc}.tar.gz` preferred. Without a package: `codex-{x86_64\|aarch64}-apple-darwin` on macOS, `-unknown-linux-musl` preferred then `-unknown-linux-gnu` on Linux, and `-pc-windows-msvc.exe.zip` on Windows. |
| Pi | `pi-{darwin\|linux\|windows}-{x64\|arm64}` with `.tar.gz`, `.tgz`, or `.zip` suffix. |
| Claude Code | Looks up a platform key derived from the target in the vendor manifest and uses that entry's checksum. M3 does not hard-code a fixed platform allowlist. |
| OpenCode | macOS/Windows patterns include OS and `x64` or `arm64`. Linux patterns additionally encode libc and baseline/AVX2; arm64 has a separate musl suffix. |

M3 fails if the target's manifest key or matching asset is missing or
ambiguous. Cache roots inside the project or `PATH` are rejected; so are
blocked system-directory roots (including Windows system directories).

## Cache location and precedence

Managed sessions download missing runtimes and reuse valid cached assets
automatically. The default cache persists between runs; per-execution state is
created separately.

Set an external path with `M3_HARNESS_CACHE_DIR`,
`MCPTestKit(harness_cache_dir=...)`, `AgentSession(..., harness_cache_dir=...)`,
or the CLI test option `--harness-cache-dir`. Precedence is AgentSession
argument, kit argument, CLI-provided environment, `M3_HARNESS_CACHE_DIR`, then
the OS default. The standalone cache commands accept `--cache-dir` (alias
`--harness-cache-dir`) directly, which takes precedence over the environment:

| OS | Default cache root |
| --- | --- |
| Windows | `%LOCALAPPDATA%/m3/harnesses` (or the standard Local AppData path) |
| macOS | `~/Library/Caches/m3/harnesses` |
| Linux and other supported Unix targets | `$XDG_CACHE_HOME/m3/harnesses`, falling back to `~/.cache/m3/harnesses` |

`m3 runtime cache list` and `prune` also accept `--project-root`. Without an
explicit path or `M3_HARNESS_CACHE_DIR`, M3 uses the same OS default. Cache roots
must be outside the project and `PATH`, and outside blocked system directories.
Symlinked cache paths are rejected. In CI, retain the selected cache directory
between jobs to avoid downloading the same pinned releases again.

The receipt stores a top-level `format` number (currently `2`), the
`provenance` object, the SHA-256 of every installed file in `files`, and
`executables`, the sorted relative paths of files with the owner execute bit
(an empty list on Windows). The `provenance` object stores `kind`, `version`,
`target`, query-stripped `url`, `sha256`, `source`, `executable`, `companions`,
`asset_name`, `verification_method`, and `immutable_release`. A cache entry is
reused only when the receipt format matches, the file hashes and executable
list match the installed tree, and the main executable and every companion are
still executable on POSIX systems.

`m3 runtime cache list --cache-dir PATH` reports entries by kind, version,
target, digest, and status:

| Status | Meaning |
| --- | --- |
| `ready` | The receipt is valid and the installed tree matches it. |
| `outdated` | A receipt written by an earlier M3 version with a different `format`. It is never a cache hit. The next use of that runtime downloads it again, and `m3 runtime cache prune` removes it. |
| `corrupt` | A malformed receipt, changed files, or changed executable bits. It is not accepted as a cache hit. |

Progress events report resolution, download, verification, extraction,
readiness, and cache hits; download URLs omit query values. The cache stores
immutable executable assets and receipts. Adapter configuration, credentials,
MCP server state, and harness home/config files are per execution and are not
shared as cache assets.

Runtime acquisitions and sessions share cache entries. M3 serializes entry
installation and pruning with a lock. An active session holds a lease, shown
as `in_use` by CLI listing. Sessions release their leases when closed.

## Inspect and prune cached runtimes

To inspect downloaded versions or troubleshoot a cache entry, run:

```sh
m3 runtime cache list
```

The JSON output includes each entry's harness, version, target, digest, and
status. To inspect a non-default cache, pass `--cache-dir PATH` or set
`M3_HARNESS_CACHE_DIR`.

Cleanup is optional. To reclaim disk space after trying several versions,
close sessions and kits, then run:

```sh
m3 runtime cache prune
```

Pruning removes entries without active leases, including corrupt and outdated entries.
Later tests reacquire any runtimes they need. Keep the cache between comparison
runs to retain the download savings. Use the same `--cache-dir PATH` override
if your tests use a custom cache.

Pruning preserves leased entries. The CLI waits briefly for leases to clear,
then exits with an operational error if entries remain in use. It does not
stop running sessions.

## Authentication and failure behavior

Downloading a binary and authenticating it to a model provider are separate
operations. A warm runtime cache can avoid a vendor download while the test
still needs provider network access. A provider login does not avoid an
uncached runtime download.

| Adapter | Auth source used at launch | Runtime isolation detail |
| --- | --- | --- |
| Codex | When `credential_references` is empty, M3 copies `CODEX_HOME/auth.json` (default `~/.codex/auth.json`) to its private child home if present. Explicit references suppress that copy and map child variables from the process environment. | Uses a per-execution `CODEX_HOME`; the copied auth file is mode `0600`, is not followed if it is a symlink, and is removed with the execution workspace. A managed executable does not supply the login. |
| Pi | Pi resolves its own provider credential from child environment/provider configuration. M3 can map `credential_references` from named environment variables. | Launches with a temporary isolated home; host Pi settings are not copied. |
| Claude Code | Claude resolves auth from supported environment or its configured login mode. M3 can map `credential_references`; host auth files are not copied into the isolated child home. | Receives a per-execution home, MCP config, and selected child environment. |
| OpenCode | OpenCode uses its configured provider environment. M3 can map `credential_references` into named child variables; host OpenCode settings are not copied. | Starts an isolated server process and home/config tree for the execution. |

See the [configuration reference](configuration.md) for supported mappings,
provider variable examples, and login prerequisites. Credential specifics do
not alter runtime download verification.

`RuntimeManager.acquire()` raises the public `RuntimeValidationError` (a
`RuntimeError` and `ValueError`) for selector/acquisition failures. Common
message strings identify these conditions:

| Condition | Runtime validation message |
| --- | --- |
| Unsupported kind | `unsupported runtime kind` |
| Bad semantic version | `invalid runtime version` |
| Missing URL and metadata URL | `selector must provide a download URL or manifest_url` |
| Missing independent checksum | `runtime metadata lacks an independent sha256` |
| Digest mismatch | `runtime sha256 mismatch` |
| No/ambiguous GitHub asset for target | `no ... release asset matches target ...` / `ambiguous ... release asset matches target ...` |
| Cache symlink or tampered receipt/tree | `runtime cache path contains a symlink` / `runtime cache entry failed verification` |
| Expected executable absent | `runtime archive contains no expected executable` |
| Required companion missing, not a regular file, or not executable | `runtime archive lacks required companion executable` |
| `codex-package.json` disagrees with the selected release | `codex package metadata does not match the selected release` |
| Codex release publishes the host but only a standalone asset matches | `codex release publishes codex-code-mode-host for target ... but no codex-package asset` |
| Version smoke test failure | `runtime executable failed --version smoke check` / `runtime executable reported an unexpected version` |
| Entry lock timeout | `runtime cache entry is busy` |
| Latest manifest lock timeout | `runtime manifest resolution lock timed out` |

In `AgentSession`, managed runtime resolution is the first startup action,
before server startup, probes, or provider launch. Acquisition exceptions are
recorded as a failed startup and surfaced to the caller as
`TransportError("agent session startup failed")`; the detailed vendor error is
not propagated as a stable session API. A missing native executable or
provider credential is a harness startup/readiness failure, not a download
failure. Invalid CLI selection and an exhausted cache-prune wait use
operational exit code 2.

Managed binaries are reusable. Per-execution home/configuration and progress
files are temporary; the active lease is released when the agent session
closes. Cancellation during acquisition leaves the worker responsible for
staging cleanup; if acquisition completes after its caller is cancelled, M3
releases the resulting lease. Pruning skips an entry with an active lease.
A runtime cache controls where M3 stores vendor executables. It does not
restrict the harness process to a filesystem, network, or CPU sandbox.
