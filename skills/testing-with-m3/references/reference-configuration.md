<!-- Generated from docs/site/reference/configuration.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Configuration

## Project identity

`m3.toml` stores project identity. Keep its generated project ID stable; renaming a project changes its name, not that identity.

## Environment files

The CLI automatically loads `.env` from the project root (`--project-root`, or the nearest `m3.toml` at or above the current directory, stopping at the Git root, otherwise the current directory) when that file exists. Pass `--env-file PATH` to load a custom file instead; a missing explicit file is an error. Ambient environment values take precedence, including empty values. File values are not interpolated. The SDK reads from the process environment or a resolver supplied by the application; it does not load dotenv files.

See the [credential reference](reference-credentials.md) for credential destinations and their resolution behavior.

## Diagnostics

`M3_TIMINGS` turns on step timings for a run, and `M3_LOG_LEVEL` and `M3_LOG_FILE` control the local app's API request log. They are environment variables only; there is no `[tool.m3]` key and no SDK argument.

| Variable | Values | Default | Effect |
| --- | --- | --- | --- |
| `M3_TIMINGS` | `1`, `true`, `yes`, `on` (case-insensitive) | unset | Records step timings under `.m3/reports/<run-id>/timings/` during `m3 test`, `m3 ci test`, and `pytest`, and prints a summary after the run. Any other value, or unset, records and prints nothing. |
| `M3_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` (case-insensitive) | `INFO` | Level of the local app's API request log. `DEBUG` adds a per-store-method breakdown for each request. Any other value means `INFO`. |
| `M3_LOG_FILE` | a file path | `.m3/logs/api.log` under the app's working directory | Where the local app writes its API request log. |

See [Find slow steps in a test run](guides-results-timings.md), which also covers [API request logs](guides-results-timings.md#api-request-logs).

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

`Config` also parses typed native timeout, permission, cache and admission values. These values are a configuration foundation: constructing them or loading them does not yet apply them to a running harness, server, cache or scheduler. Native capability checks and runtime application are separate.

`load_config(config_file=path)` replaces project discovery with an explicit TOML file containing the same `[tool.m3]` table. It adds no environment variables or CLI flags. Invalid fields fail during parsing; no native process is started by this API. Both file forms merge defaults, file values, existing configuration environment values, and explicit Python values in that order. Alias settings describe existing server bindings and never create servers.

Every native phase budget is a positive finite number of seconds. `session_seconds` bounds native session creation, not session lifetime or a turn. `request_seconds` on `HarnessTimeouts` bounds a native startup protocol request. `total_seconds` is an optional total startup budget. Omitted native leaves use `UNSET` and inherit. Only `total_seconds` and `normalized_load_threshold` accept Python `None` or TOML `false` to disable a budget. Required phases reject `None`; numeric fields reject booleans and strings.

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

Caches default to `isolated`. `immutable-seed` requests opt-in seed reuse; this type does not materialize a cache. Startup admission defaults to disabled, with four slots and a 300-second queue budget when enabled. Worker bootstrap defaults to one total 30-second budget. Native phase defaults remain unset so the runtime can retain its existing limits.

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
