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

The SDK resolves four settings. Each can come from an explicit argument, an `M3_*` environment variable, or a `[tool.m3]` table in `pyproject.toml`. `cli` and `results` settings are nested tables in `[tool.m3]` and have no environment variable.

| Setting | Environment variable | `[tool.m3]` key | Values | Default | Effect |
| --- | --- | --- | --- | --- | --- |
| `artifact_policy` | `M3_ARTIFACT_POLICY` | `artifact_policy` | `failed`, `always`, `never` (lowercase only) | `failed` | Recorded in the resolved configuration only. No SDK runtime path reads it; agent artifact retention is set per agent with `AgentSpec(artifact_policy=...)`, which takes the same values. |
| `protocol_revision` | `M3_PROTOCOL_REVISION` | `protocol_revision` | `auto`, or a revision identifier made of letters, digits, `.`, `_`, and `-` that starts with a letter or digit | `auto` | Default MCP protocol revision for `kit.direct(...)` when the call passes no `protocol=` argument. `auto` and the revision of the bundled MCP client are accepted; any other revision makes `direct(...)` raise `UnsupportedFeature`. |
| `cli.project_python_timeout_seconds` | — | `[tool.m3.cli]` `project_python_timeout_seconds` | number greater than 0, at most 3600 | `15` | Seconds `m3 test`, `m3 ci test`, and `m3 doctor` wait for the project-Python check and the `--baseline` lookup. |
| `results.busy_timeout_seconds` | — | `[tool.m3.results]` `busy_timeout_seconds` | number from 0 to 3600 | `5` | SQLite lock wait for the stores the pytest plugin opens. The run manifest and feedback-export stores use the run's value; the default store of each kit uses that kit's resolved value, so `config={...}` or `cwd=` on a kit changes it. A store passed explicitly with `store=` keeps its own `busy_timeout_ms`. |

For each setting the highest-precedence source that provides a value wins:

1. An explicit argument: `load_config(artifact_policy="always")`, `load_config({"protocol_revision": "auto"})`, or a mapping passed as `MCPTestKit(config={...})`, including nested form such as `MCPTestKit(config={"results": {"busy_timeout_seconds": 30}})`.
2. The process environment. `MCPTestKit(env={...})` replaces it, so `MCPTestKit(env={})` ignores ambient `M3_*` variables.
3. The `[tool.m3]` table of the nearest `pyproject.toml`, searching from the working directory (or `MCPTestKit(cwd=...)`) up through its parents. For `m3 test` and `m3 ci test` the working directory is the directory pytest runs in: the project root when `--project-root` is given, otherwise the directory you run the command from. Only the first `pyproject.toml` found is read, even if it has no `[tool.m3]` table. `--m3-config PATH` (on `m3 test`, `m3 ci test`, `m3 doctor`, and `pytest -p m3.pytest_plugin`) reads that file's `[tool.m3]` table instead, and `pyproject.toml` is not read. A relative path is relative to the directory you run the command from. A missing file, or a file without `[tool.m3]`, is an `invalid_project` error.
4. The default.

Passing a ready-made `Config` object to `MCPTestKit` uses it as is, without reading the environment or `pyproject.toml`. A setting that is present but invalid is an error, not a fallback to the next source, and so is an unknown key in `[tool.m3]`. The resolved `Config` records where each value came from in `config.sources` (`explicit`, `environment`, `project`, or `default`).

**Inside a pytest run.** The pytest plugin resolves settings once when pytest starts. pytest-xdist workers receive that resolved copy and do not read `pyproject.toml`, the `--m3-config` file, or `M3_*` again, so editing or deleting the file during a run has no effect. `MCPTestKit()` and `AsyncMCPTestKit()` created without `env=` or `cwd=` use that copy, with any `config={...}` mapping applied on top. Passing `env=` or `cwd=` resolves on its own, as outside pytest.

```toml
# pyproject.toml
[tool.m3]
protocol_revision = "auto"

[tool.m3.cli]
project_python_timeout_seconds = 60

[tool.m3.results]
busy_timeout_seconds = 30
```

### Check the resolved settings

`m3 doctor --require config` resolves the same settings and reports them, with the source of each value. Run it from the project directory, or pass `--project-root` to choose where `pyproject.toml` discovery starts:

```sh
M3_ARTIFACT_POLICY=never m3 doctor --require config --json
```

The report's `configuration.settings` object has this shape; `origin` for a `pyproject.toml` value is `pyproject:` followed by the absolute file path, and an `--m3-config` value's origin is `config-file:` followed by the absolute path:

```json
{
  "artifact_policy": "never",
  "protocol_revision": "auto",
  "cli": {"project_python_timeout_seconds": 15.0},
  "results": {"busy_timeout_seconds": 5.0},
  "sources": {
    "artifact_policy": {"source": "environment", "origin": "env:M3_ARTIFACT_POLICY"},
    "protocol_revision": {"source": "default", "origin": "default"},
    "cli.project_python_timeout_seconds": {"source": "default", "origin": "default"},
    "results.busy_timeout_seconds": {"source": "default", "origin": "default"}
  }
}
```

`configuration.digest` is `sha256:` plus a hash of the effective values (sources excluded). The same value is stored as `configuration_digest` in the run manifest. Human output prints it after `config:` and lists each non-default setting with its origin.

The doctor also reads `M3_*` values from the dotenv file described in [Environment files](#environment-files); ambient variables still win. `--require config:FIELD` accepts any setting name above (for example `config:results.busy_timeout_seconds`), but it does not narrow the check: every setting is resolved and validated, so an invalid value in any of them fails the check. Any other field name exits 2 with `invalid requirement`. Running `m3 doctor` with no `--require` checks `config` and `storage:memory`. `m3 doctor` reads `cli.project_python_timeout_seconds` on every run, and a configuration error fails the doctor even without a `config` requirement.

An invalid setting exits 2 and names the code, field, origin, and reason without echoing the value. With `--json` the error is printed to standard output as `{"ready": false, "error": {...}}`; otherwise the message goes to standard error:

```text
m3 doctor: configuration error (code=invalid_configuration field=artifact_policy origin=env:M3_ARTIFACT_POLICY reason=must be one of failed, always, or never)
```

The `code` is `invalid_configuration` for a bad value, `unknown_setting` for an unrecognized `[tool.m3]` key or nested key (the field is the dotted name, such as `results.foo`), and `invalid_project` for a `pyproject.toml` or `--m3-config` file that is not valid TOML or whose `[tool.m3]` is not a table, an `--m3-config` file that does not exist (`field=config_file`), or one without a `[tool.m3]` table. Exit 1 means the settings resolved but another requirement, such as the project Python environment, is not ready.
