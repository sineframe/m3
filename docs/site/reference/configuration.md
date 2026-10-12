---
title: "Configuration"
description: "Project settings, native configuration values, and environment-file loading."
---

# Configuration

## Project identity

`m3.toml` stores project identity. Keep its generated project ID stable; renaming a project changes its name, not that identity.

## Environment files

The CLI automatically loads `.env` from the project root (`--project-root`, or the nearest `m3.toml` at or above the current directory, stopping at the Git root, otherwise the current directory) when that file exists. Pass `--env-file PATH` to load a custom file instead; a missing explicit file is an error. Ambient environment values take precedence, including empty values. File values are not interpolated. The SDK reads from the process environment or a resolver supplied by the application; it does not load dotenv files.

See the [credential reference](credentials.md) for credential destinations and their resolution behavior.

## Diagnostics

`M3_TIMINGS` turns on step timings for a run, and `M3_LOG_LEVEL` and `M3_LOG_FILE` control the local app's API request log. They are environment variables only; there is no `[tool.m3]` key and no SDK argument.

| Variable | Values | Default | Effect |
| --- | --- | --- | --- |
| `M3_TIMINGS` | `1`, `true`, `yes`, `on` (case-insensitive) | unset | Records step timings under `.m3/reports/<run-id>/timings/` during `m3 test`, `m3 ci test`, and `pytest`, and prints a summary after the run. Any other value, or unset, records and prints nothing. |
| `M3_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` (case-insensitive) | `INFO` | Level of the local app's API request log. `DEBUG` adds a per-store-method breakdown for each request. Any other value means `INFO`. |
| `M3_LOG_FILE` | a file path | `.m3/logs/api.log` under the app's working directory | Where the local app writes its API request log. |

See [Find slow steps in a test run](../guides/results/timings.md), which also covers [API request logs](../guides/results/timings.md#api-request-logs).

## SDK settings

The SDK uses two settings at runtime. Each can come from an explicit argument, an `M3_*` environment variable, or a `[tool.m3]` table in `pyproject.toml`.

| Setting | Environment variable | `[tool.m3]` key | Values | Default | Effect |
| --- | --- | --- | --- | --- | --- |
| `artifact_policy` | `M3_ARTIFACT_POLICY` | `artifact_policy` | `failed`, `always`, `never` (lowercase only) | `failed` | Recorded in the resolved configuration only. No SDK runtime path reads it; agent artifact retention is set per agent with `AgentSpec(artifact_policy=...)`, which takes the same values. |
| `protocol_revision` | `M3_PROTOCOL_REVISION` | `protocol_revision` | `auto`, or a revision identifier made of letters, digits, `.`, `_`, and `-` that starts with a letter or digit | `auto` | Default MCP protocol revision for `kit.direct(...)` when the call passes no `protocol=` argument. `auto` and the revision of the bundled MCP client are accepted; any other revision makes `direct(...)` raise `UnsupportedFeature`. |

For each setting the highest-precedence source that provides a value wins:

1. An explicit argument: `load_config(artifact_policy="always")`, `load_config({"protocol_revision": "auto"})`, or a mapping passed as `MCPTestKit(config={...})`.
2. The process environment. `MCPTestKit(env={...})` replaces it, so `MCPTestKit(env={})` ignores ambient `M3_*` variables.
3. The `[tool.m3]` table of the nearest `pyproject.toml`, searching from the working directory (or `MCPTestKit(cwd=...)`) up through its parents. Only the first `pyproject.toml` found is read, even if it has no `[tool.m3]` table.
4. The default.

Passing a ready-made `Config` object to `MCPTestKit` uses it as is, without reading the environment or `pyproject.toml`. A setting that is present but invalid is an error, not a fallback to the next source, and so is an unknown key in `[tool.m3]`. The resolved `Config` records where each value came from in `config.sources` (`explicit`, `environment`, `project`, or `default`).

During a test invocation, m3 resolves configuration once. Pytest workers and kits constructed inside them inherit that configuration, even if the file is subsequently changed or deleted, or a worker changes an `M3_*` variable. Explicit Python overrides still apply afterward, field by field. For example, passing only `HarnessTimeouts(initialize_seconds=15)` preserves an inherited `startup_seconds=90`.

Use `--m3-config PATH` to select a TOML file containing the same `[tool.m3]` tables. You create this file; m3 does not generate an additional settings file. The option is available on `m3 test`, `m3 ci test`, `m3 doctor`, and direct pytest with the m3 plugin:

```sh
m3 test --m3-config run.toml -- tests/
m3 ci test --m3-config run.toml -- tests/
m3 doctor --m3-config run.toml --require config --json
pytest -p m3.pytest_plugin --m3-config run.toml tests/
```

Configuration syntax and values are validated before the CLI probes project Python. Configuration does not bind servers: entries under `[tool.m3.servers]` affect only servers already selected or bound by the test.

Configured values and server selections travel to local workers through a private, single-use bootstrap. Later sessions use the worker's retained copy. Literal values stay frozen for the invocation; credential references remain references for the session's credential resolver. Remote workers are rejected when private configuration would need to be transferred.

```toml
# pyproject.toml
[tool.m3]
protocol_revision = "auto"
```

### Check the resolved settings

`m3 doctor --require config` resolves the same settings and reports them, with the source of each value. Run it from the project directory, or pass `--project-root` to choose where `pyproject.toml` discovery starts:

```sh
M3_ARTIFACT_POLICY=never m3 doctor --require config --json
```

The report's `configuration.settings` object has this shape; `origin` for a `pyproject.toml` value is `pyproject:` followed by the absolute file path. Its separate `configuration.digest` identifies the nonsecret settings projection; private values and their file paths do not affect this digest:

```json
{
  "artifact_policy": "never",
  "protocol_revision": "auto",
  "sources": {
    "artifact_policy": {"source": "environment", "origin": "env:M3_ARTIFACT_POLICY"},
    "protocol_revision": {"source": "default", "origin": "default"}
  }
}
```

The doctor also reads `M3_*` values from the dotenv file described in [Environment files](#environment-files); ambient variables still win. `--require config:FIELD` accepts `artifact_policy` or `protocol_revision`, but it does not narrow the check: both settings are resolved and validated, so an invalid `protocol_revision` fails `config:artifact_policy` too. Any other field name exits 2 with `invalid requirement`. Running `m3 doctor` with no `--require` checks `config` and `storage:memory`.

An invalid setting exits 2 and names the code, field, origin, and reason without echoing the value. With `--json` the error is printed to standard output as `{"ready": false, "error": {...}}`; otherwise the message goes to standard error:

```text
m3 doctor: configuration error (code=invalid_configuration field=artifact_policy origin=env:M3_ARTIFACT_POLICY reason=must be one of failed, always, or never)
```

The `code` is `invalid_configuration` for a bad value, `unknown_setting` for an unrecognized `[tool.m3]` key, and `invalid_project` for a `pyproject.toml` that is not valid TOML or whose `[tool.m3]` is not a table. Exit 1 means the settings resolved but another requirement, such as the project Python environment, is not ready.

## Construct and merge native settings

`Config` also parses typed native timeout, permission, cache and admission values. Construction and loading never launch a process. Agent sessions apply native permissions and MCP budgets after merging their explicit settings and checking the selected runtime. Harness deadlines apply to native startup phases after the selected settings have been resolved.

`load_config(config_file=path)` replaces project discovery with an explicit TOML file containing the same `[tool.m3]` table. It adds no environment variables or CLI flags. Invalid fields fail during parsing; no native process is started by this API. Both file forms merge defaults, file values, existing configuration environment values, and explicit Python values in that order. Alias settings describe existing server bindings and never create servers.

Every native phase budget is a positive finite number of seconds. `session_seconds` bounds native session creation, not session lifetime or a turn. `request_seconds` on `HarnessTimeouts` bounds a native startup protocol request. `total_seconds` is an optional total startup budget. Omitted native leaves use `UNSET` and inherit. Only `total_seconds` and `normalized_load_threshold` accept Python `None` or TOML `false` to disable a budget. Required phases reject `None`; numeric fields reject booleans and strings.

`ResultsSettings.busy_timeout_seconds` controls SQLite stores created by the pytest results integration: manifest, toolkit, and feedback export connections. Omission preserves the existing five-second wait. A toolkit's explicit Python result settings override the invocation settings for its own default store. An explicitly supplied store keeps its constructor options. Positive budgets round up to milliseconds; values larger than SQLite's signed 32-bit millisecond range are rejected. WAL behavior is unchanged.

`merge_settings` overlays explicitly supplied leaves of the same type and validates the merged result. An initialization-only override preserves startup and total budgets. Sparse JSON exports omit `UNSET` and omitted fields, so JSON round trips preserve explicit overrides, including `None`. Nested settings models merge by explicit field. Map entries merge by key; an explicitly supplied empty map clears that map. An empty nested model inherits instead of clearing its fields. Changing only a defined profile's `name` retains earlier explicit filesystem and network leaves; supply explicit rule maps to change those permissions.

A root-only `CachePolicy(root="/new")` is a partial override and can inherit `immutable-seed` mode. Harness cache settings inherit global cache settings before validation. A final `Config` or `merge_settings` result rejects a root without that opt-in, and an explicit `mode="isolated"` with a root is always invalid.

```toml
[tool.m3.harnesses.codex.timeouts]
startup_seconds = 90
total_seconds = 120
[tool.m3.mcp_timeouts]
startup_seconds = 60
[tool.m3.servers.slow.timeouts]
startup_seconds = 75
[tool.m3.cli]
project_python_timeout_seconds = 60
[tool.m3.results]
busy_timeout_seconds = 30
```

Codex permission values choose a named or defined profile, or legacy sandbox options. A profile cannot coexist with legacy sandbox/network overrides. Defined profiles expose a subset of the [Codex 0.160.1 native schema](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/core/config.schema.json): filesystem path decisions (`read`, `write`, `deny`), and network enablement/domain decisions (`allow`, `deny`). Interactive approval policy is separate from filesystem permissions. Claude Code and OpenCode tool rules do not promise OS filesystem confinement. Runtime support must be validated before these values are applied.

### Bound native startup

Set `HarnessTimeouts` on a harness, as `harness_timeouts=` on either kit, or as `harness_timeouts=` on `agent.session(...)`. Values merge by explicit field: global configuration, per-harness configuration, the kit override, harness selection, then session override. An initialization-only override preserves an inherited startup or total budget.

For pytest's selected `agent`, a session override can raise the native ceiling while leaving the selection's other settings in place:

```python
import sys
from pathlib import Path

import pytest

from m3.types import HarnessTimeouts, McpTimeouts, StdioServer

pytestmark = pytest.mark.m3(suite_name="slow-startup")


def test_slow_startup(agent):
    server = StdioServer(
        name="slow",
        command=sys.executable,
        args=[str(Path(__file__).with_name("slow_mcp.py"))],
        environment={"DELAY_S": "40"},
        timeouts=McpTimeouts(startup_seconds=75),
    )
    with agent.session(
        servers=[server],
        harness_timeouts=HarnessTimeouts(total_seconds=120, startup_seconds=90),
    ) as session:
        turn = session.send("Call lookup and report the code.", timeout=120)
        assert turn.error is None
```

Native session entry prepares the workspace and checks capabilities. The first `send()` starts credentials, capture and the native harness; time spent idle between entry and that send does not consume the active total budget. Startup also counts against that send's `timeout=`. Admission queue time suspends the active total budget at the admission checkpoint, while its separate queue deadline and the caller's send deadline continue to run.

`total_seconds` starts with owned preparation and covers active preparation through native and required MCP readiness. Each phase uses the earliest remaining parent deadline; progress frames and HTTP body chunks do not restart it. With total 120 seconds, 15 seconds of preparation leaves 105 seconds. A native ceiling of 90 and an MCP session ceiling of 75 then leave at most 75 seconds for that session phase. Native MCP startup settings also remain in effect, so raise both budgets when a server needs longer to initialize.

| Field | Startup boundary |
| --- | --- |
| `startup_seconds` | Aggregate native launch through required MCP readiness. Also selects the owned configuration preparation ceiling. |
| `initialize_seconds` | Codex initialization; Claude initialization event; OpenCode session creation. |
| `readiness_seconds` | Codex thread readiness; Claude initialization event; OpenCode server endpoint readiness. |
| `session_seconds` | Codex thread creation; Claude initialization event; OpenCode session creation and MCP status confirmation. |
| `request_seconds` | Native startup protocol requests or initial prompt delivery. |
| `total_seconds` | Active preparation and native startup; `None` disables this optional ceiling. |

Omitted fields preserve existing phase defaults: Codex initialization 5 seconds, account requests 15 seconds and thread creation 30 seconds; OpenCode endpoint readiness 20 seconds and session creation 30 seconds. An explicit native startup ceiling replaces the shorter default Codex thread or OpenCode readiness/session ceiling, while explicit phase values take precedence. Claude initialization remains bounded by the caller when no native phase is configured. Owned configuration jobs share one 30-second preparation ceiling when no `startup_seconds` is supplied; this ceiling ends before native execution and does not cap a separately configured longer initialization phase.

Explicit bounded startup uses owned POSIX processes for framework filesystem preparation and native execution. Cancellation fences publication; unresolved writers retain quarantined resources. Bounded Git worktree preparation rejects executable hooks and external filter, fsmonitor or partial-clone commands before checkout. Arbitrary custom artifact-store hooks cannot run under a bounded capture deadline; built-in memory, temporary-file and SQLite stores are supported. Cleanup has one 10-second attempt, with component waits and cancellation grace clipped to its remaining budget. A cleanup failure preserves the original startup or turn error and leaves uncertain resources owned.

### Apply native permissions and MCP budgets

Native harness values accept `permissions=`, and server definitions and bindings accept `timeouts=McpTimeouts(...)`. An agent selection dictionary accepts the same typed permission value under `permissions`. `agent.session(harness_permissions=...)` overrides the selected harness's explicit leaves for that session. The low-level sync and async `agent_session` methods accept that override too.

Permissions merge in this order: `[tool.m3.harnesses.<kind>.permissions]`, the selected harness's `permissions`, and the session override. Server budgets merge global `[tool.m3.mcp_timeouts]`, alias settings, the server definition, then the binding. An empty default model inherits; explicitly supplying `profiles=()` clears inherited profile definitions. Structural conflicts fail before session entry. Native capability checks run after runtime resolution and before MCP startup.

Supply custom Codex profile definitions explicitly. This definition and selector can be reused by different harness cases:

```python
from m3.types import (
    Codex,
    CodexPermissionProfile,
    CodexPermissions,
    McpTimeouts,
    NativeFilesystemPermissions,
    NativeNetworkPermissions,
    StdioServer,
)

profile = CodexPermissionProfile(
    name="project",
    filesystem=NativeFilesystemPermissions(
        rules={":minimal": "read", ":project_roots": "write"},
    ),
    network=NativeNetworkPermissions(enabled=False),
)
harness = Codex(
    model="your-codex-model",
    runtime="managed",
    version="0.162.1",
    permissions=CodexPermissions(profile="project", profiles=(profile,)),
)
server = StdioServer(
    name="slow",
    command="python3",
    args=("slow_mcp.py",),
    timeouts=McpTimeouts(startup_seconds=60, request_seconds=120),
)


def test_native_values() -> None:
    assert harness.permissions.profile == "project"
    assert harness.permissions.profiles == (profile,)
    assert server.timeouts.startup_seconds == 60
    assert server.timeouts.request_seconds == 120
    assert CodexPermissions(profile=profile).profile == profile
```

Use `harness` in a `HarnessCase`, as shown in [agent matrices](../guides/agents/matrices.md), or pass `harness.permissions` in an ordinary agent selection dictionary. The inline shorthand `CodexPermissions(profile=profile)` emits the same native configuration. A string selector must name an explicit definition or a builtin verified for that runtime. Duplicate definitions and conflicting inline definitions fail validation. A later `profile="other"` selector inherits the earlier `profiles` tuple; it does not import an ambient Codex config.

Codex receives the selected profile in its isolated native config and `thread/start` request. M3 omits the legacy `sandbox` override and confirms the returned active profile ID. Builtins verified for the supported releases are `:read-only`, `:workspace`, and `:danger-full-access`. A legacy network setting requires `CodexPermissions(sandbox="workspace-write", network_access=True)`; profile filesystem/network rules cannot coexist with these legacy options. Interactive `approval_policy` is checked against the selected runtime's exported RPC schema, so an obsolete policy fails explicitly.

Codex budgets become `startup_timeout_sec` and `tool_timeout_sec`. OpenCode's native `timeout` is one integer millisecond budget used for startup and requests; supplying distinct values for the two phases is invalid. Claude Code's `MCP_TIMEOUT` is process-wide, so all bound servers must have the same effective startup setting, including omitted settings. Claude request budgets use its per-server `timeout` field and may differ. They must be at least one second. Millisecond timers reject fractional milliseconds and values their native timer would clamp. These native MCP budgets are nested within M3's configured harness startup deadlines. Direct clients use their existing `timeout=` API and reject native harness budgets.

Claude `permission_mode`, `allowed_tools`, and `denied_tools` become native CLI flags. Explicit rules replace inherited allowlists, and cannot widen a selected native tool policy. OpenCode `rules` become native `permission` entries under the same constraint. Native tool approvals and filesystem access are separate from M3's interaction handlers and portable tool policy.

Config-only support is verified for Codex `0.160.1`/`0.162.1`, OpenCode `1.18.31`/`1.18.35`, and the published macOS arm64 Claude Code `2.1.295`/`2.1.296` distributions. Other requested config-only features fail as unknown. RPC/help fields are discovered with bounded isolated offline commands. Facts retain the native release, source revision or distribution integrity, executable digest, dialect and platform; every final session request is validated again. A script launcher needs a verified managed distribution identity for config-only support. Default sessions perform no additional discovery.

Caches default to `isolated`. Opt-in `immutable-seed` copies verified ripgrep 15.1.0 into each isolated OpenCode session. The supported artifact is the macOS arm64 OpenCode 1.18.35 build; other harnesses, platforms, versions and executable digests fail explicitly. Set `cache.root` to an absolute path or a path beginning with `~/`; when omitted, M3 uses its user cache directory, pinned before the native environment is isolated. Every reader holds the artifact lease while validating and copying the seed. Copies use separate files, so a session cannot mutate the shared executable. Corrupt or interrupted generations are quarantined before reuse. Credentials, project files, logs, databases and model catalogs never enter this seed store. Startup admission defaults to disabled, with four slots and a 300-second queue budget when enabled. `admission=StartupAdmission(...)` on either kit or `agent_session()` overrides file settings using the same explicit-field merge as other settings. A kit override applies after file-wide and per-harness settings; selection and session overrides apply afterward. Explicit `enabled=False` disables an inherited opt-in, and `normalized_load_threshold=None` removes an inherited load threshold. Worker bootstrap defaults to one total 30-second budget. Native phase defaults remain unset so the runtime can retain its existing limits.

This complete pytest example constructs each public settings type and verifies merging without launching a harness:

```python
from m3.configuration import Config, load_config, merge_settings
from m3.types import (
    UNSET,
    CachePolicy,
    ClaudeCodePermissions,
    ClaudeCodeSettings,
    CliSettings,
    CodexPermissionProfile,
    CodexPermissions,
    CodexSettings,
    HarnessSettings,
    HarnessSettingsMap,
    HarnessTimeouts,
    McpTimeouts,
    NativeFilesystemPermissions,
    NativeNetworkPermissions,
    OpenCodePermissions,
    OpenCodeSettings,
    ResultsSettings,
    ServerSettings,
    StartupAdmission,
    Unset,
    WorkerBootstrapSettings,
)


def test_configuration_values(tmp_path):
    assert UNSET is Unset.UNSET
    timeouts = merge_settings(
        HarnessTimeouts(startup_seconds=90, total_seconds=120),
        HarnessTimeouts(initialize_seconds=15, session_seconds=30),
    )
    assert timeouts.startup_seconds == 90
    assert timeouts.total_seconds == 120
    profile = CodexPermissionProfile(
        name="project",
        filesystem=NativeFilesystemPermissions(
            rules={":workspace_roots": "write", "**/.env": "deny"}
        ),
        network=NativeNetworkPermissions(enabled=False),
    )
    config = Config(
        harness_timeouts=timeouts,
        mcp_timeouts=McpTimeouts(startup_seconds=60),
        harnesses=HarnessSettingsMap(
            codex=CodexSettings(permissions=CodexPermissions(profile=profile)),
            claude_code=ClaudeCodeSettings(
                permissions=ClaudeCodePermissions(permission_mode="plan")
            ),
            opencode=OpenCodeSettings(
                permissions=OpenCodePermissions(rules={"edit": "deny"})
            ),
        ),
        servers={"slow": ServerSettings(timeouts=McpTimeouts(startup_seconds=75))},
        cli=CliSettings(project_python_timeout_seconds=60),
        results=ResultsSettings(busy_timeout_seconds=30),
        worker_bootstrap=WorkerBootstrapSettings(timeout_seconds=30),
        cache=CachePolicy(mode="isolated"),
        admission=StartupAdmission(enabled=False),
    )
    assert HarnessSettings().timeouts.startup_seconds is UNSET
    assert config.servers["slow"].timeouts.startup_seconds == 75
    assert "profile" not in str(config.redacted_projection().get("native_settings"))
    path = tmp_path / "run.toml"
    path.write_text("[tool.m3.harness_timeouts]\nstartup_seconds=90\n")
    resolved = load_config(config_file=path, env={})
    assert resolved.harness_timeouts.startup_seconds == 90
```

`config.sources` tracks every leaf. Dots and backslashes in mapping keys are escaped in provenance paths. `config.redacted_projection()` preserves the two existing SDK diagnostics and lists only the field and source of explicitly configured native settings; it omits native values, aliases, permission identifiers and file paths from those added diagnostics. Full `model_dump()` output is a configuration export, not a safe log projection.
