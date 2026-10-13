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

The SDK resolves the settings below. Each can come from an explicit argument or a `[tool.m3]` table in `pyproject.toml`; `artifact_policy` and `protocol_revision` can also come from an `M3_*` environment variable. The other settings are nested tables in `[tool.m3]` and have no environment variable.

| Setting | Environment variable | `[tool.m3]` key | Values | Default | Effect |
| --- | --- | --- | --- | --- | --- |
| `artifact_policy` | `M3_ARTIFACT_POLICY` | `artifact_policy` | `failed`, `always`, `never` (lowercase only) | `failed` | Recorded in the resolved configuration only. No SDK runtime path reads it; agent artifact retention is set per agent with `AgentSpec(artifact_policy=...)`, which takes the same values. |
| `protocol_revision` | `M3_PROTOCOL_REVISION` | `protocol_revision` | `auto`, or a revision identifier made of letters, digits, `.`, `_`, and `-` that starts with a letter or digit | `auto` | Default MCP protocol revision for `kit.direct(...)` when the call passes no `protocol=` argument. `auto` and the revision of the bundled MCP client are accepted; any other revision makes `direct(...)` raise `UnsupportedFeature`. |
| `cli.project_python_timeout_seconds` | — | `[tool.m3.cli]` `project_python_timeout_seconds` | number greater than 0, at most 3600 | `15` | Seconds `m3 setup`, `m3 test`, `m3 ci test`, and `m3 doctor` wait for the project-Python check, and `m3 test` and `m3 ci test` wait for the `--baseline` lookup. `m3 setup` reads it from `pyproject.toml` only; it has no `--m3-config`. |
| `results.busy_timeout_seconds` | — | `[tool.m3.results]` `busy_timeout_seconds` | number from 0 to 3600 | `5` | SQLite lock wait for the stores the pytest plugin opens. The run manifest and feedback-export stores use the run's value; the default store of each kit uses that kit's resolved value, so `config={...}` or `cwd=` on a kit changes it. A store passed explicitly with `store=` keeps its own `busy_timeout_ms`. |
| `harnesses.codex.timeouts.initialize_seconds` | — | `[tool.m3.harnesses.codex.timeouts]` `initialize_seconds` | number greater than 0, at most 3600 | `30` | Seconds the Codex App Server has to answer `initialize`. |
| `harnesses.codex.timeouts.startup_seconds` | — | `[tool.m3.harnesses.codex.timeouts]` `startup_seconds` | number greater than 0, at most 3600 | `120` | Seconds Codex has to answer `thread/start`. Codex answers only after it has started the session's MCP servers, so this limit contains their MCP startup budget. One deadline covers the whole request; other frames Codex sends meanwhile do not extend it. |
| `harnesses.codex.timeouts.account_seconds` | — | `[tool.m3.harnesses.codex.timeouts]` `account_seconds` | number greater than 0, at most 3600 | `15` | Seconds Codex has to answer each startup account request (`account/login/start`, `account/read`). One deadline per request; other frames do not extend it. |
| `harnesses.opencode.timeouts.startup_seconds` | — | `[tool.m3.harnesses.opencode.timeouts]` `startup_seconds` | number greater than 0, at most 3600 | `60` | Seconds `opencode serve` has to print its endpoint. |
| `harnesses.opencode.timeouts.session_creation_seconds` | — | `[tool.m3.harnesses.opencode.timeouts]` `session_creation_seconds` | number greater than 0, at most 3600 | `60` | Seconds OpenCode has to answer `POST /session`. |
| `harnesses.pi.timeouts.initialize_seconds` | — | `[tool.m3.harnesses.pi.timeouts]` `initialize_seconds` | number greater than 0, at most 3600 | `15` | Seconds Pi has to answer `get_state`. |
| `harnesses.pi.timeouts.extension_connect_seconds` | — | `[tool.m3.harnesses.pi.timeouts]` `extension_connect_seconds` | number greater than 0, at most 3600 | `5` | Seconds m3's Pi extension has to connect back to m3 after `get_state`. |
| `harness_probes.timeout_seconds` | — | `[tool.m3.harness_probes]` `timeout_seconds` | number greater than 0, at most 3600 | `30` | Seconds each attempt of a harness capability probe (`--help`, `--version`) may run. A timed-out probe is retried once. |
| `mcp_timeouts.startup_seconds` | — | `[tool.m3.mcp_timeouts]` `startup_seconds` | number greater than 0, at most 3600 | unset | MCP startup budget passed to the harness's own MCP client for every server. Unset keeps each harness's native default. See [MCP startup budgets](#mcp-startup-budgets). |
| `startup_admission.enabled` | — | `[tool.m3.startup_admission]` `enabled` | `true` or `false` | `false` | Limits how many native harness sessions start at the same time on this host. See [Startup admission](#startup-admission). |
| `startup_admission.slots` | — | `[tool.m3.startup_admission]` `slots` | integer from 1 to 256 | `4` | Native startups allowed at once, across every m3 process of the current user. |
| `startup_admission.queue_timeout_seconds` | — | `[tool.m3.startup_admission]` `queue_timeout_seconds` | number greater than 0, at most 3600 | `300` | Seconds a session may wait for a slot (and for the load threshold) before startup fails. |
| `startup_admission.max_load_per_cpu` | — | `[tool.m3.startup_admission]` `max_load_per_cpu` | number greater than 0, at most 64 | unset | When set, a session that holds a slot also waits until the 1-minute load average divided by the CPU count is at or below this value. |

For each setting the highest-precedence source that provides a value wins:

1. An explicit argument: `load_config(artifact_policy="always")`, `load_config({"protocol_revision": "auto"})`, or a mapping passed as `MCPTestKit(config={...})`, including nested form such as `MCPTestKit(config={"results": {"busy_timeout_seconds": 30}})` or `MCPTestKit(config={"harnesses": {"codex": {"timeouts": {"startup_seconds": 90}}}})`.
2. The process environment. `MCPTestKit(env={...})` replaces it, so `MCPTestKit(env={})` ignores ambient `M3_*` variables.
3. The `[tool.m3]` table of the nearest `pyproject.toml`, searching from the working directory (or `MCPTestKit(cwd=...)`) up through its parents. For `m3 test` and `m3 ci test` the working directory is the directory pytest runs in: the project root when `--project-root` is given, otherwise the directory you run the command from. Only the first `pyproject.toml` found is read, even if it has no `[tool.m3]` table. `--m3-config PATH` (on `m3 test`, `m3 ci test`, `m3 doctor`, and `pytest -p m3.pytest_plugin`) reads that file's `[tool.m3]` table instead, and `pyproject.toml` is not read. A relative path is relative to the directory you run the command from. A missing file, or a file without `[tool.m3]`, is an `invalid_project` error.
4. The default.

Passing a ready-made `Config` object to `MCPTestKit` uses it as is, without reading the environment or `pyproject.toml`. A setting that is present but invalid is an error, not a fallback to the next source, and so is an unknown key in `[tool.m3]`. The resolved `Config` records where each value came from in `config.sources` (`explicit`, `environment`, `project`, or `default`).

**Inside a pytest run.** The pytest plugin resolves settings once when pytest starts, so an invalid setting stops the run before collection, even if no test uses M3; `pytest --help` ignores project settings. pytest-xdist workers receive that resolved copy and do not read `pyproject.toml`, the `--m3-config` file, or `M3_*` again, so editing or deleting the file during a run has no effect. `MCPTestKit()` and `AsyncMCPTestKit()` created without `env=` or `cwd=` use that copy, with any `config={...}` mapping applied on top. Passing `env=` or `cwd=` resolves on its own, as outside pytest.

```toml
# pyproject.toml
[tool.m3]
protocol_revision = "auto"

[tool.m3.cli]
project_python_timeout_seconds = 60

[tool.m3.results]
busy_timeout_seconds = 30

[tool.m3.harnesses.codex.timeouts]
startup_seconds = 90

[tool.m3.mcp_timeouts]
startup_seconds = 60
```

### Harness startup limits

The `harnesses.<name>.timeouts` settings bound how long m3 waits for a native harness to start a session. A typed harness can override them for its own sessions with `timeouts=HarnessTimeouts(...)`; fields left unset keep the resolved setting. Codex accepts `initialize_seconds`, `account_seconds`, and `startup_seconds`; OpenCode accepts `startup_seconds` and `session_creation_seconds`; Pi accepts `initialize_seconds` and `extension_connect_seconds`. Any other field is a validation error when the harness value is built. Agent selections accept the same value as a `timeouts` key for `codex`, `opencode`, and `pi`.

```python
from m3.types import Codex, HarnessTimeouts

harness = Codex(model="gpt-6.1-sol", timeouts=HarnessTimeouts(startup_seconds=180))
```

When a limit runs out, session startup fails with a cause that names the phase, the limit, and the setting that controls it, for example `Codex thread/start did not finish within 120s (harnesses.codex.timeouts.startup_seconds)`.

### MCP startup budgets

`mcp_timeouts.startup_seconds` and `McpTimeouts(startup_seconds=...)` on a `StdioServer` or `HTTPServer` set the budget each harness's own MCP client has to start a server. A value on the server wins over the setting. When neither is set, m3 writes nothing and the harness's native default applies.

| Harness | Native field m3 writes | Default when unset | Notes |
| --- | --- | --- | --- |
| Codex | `startup_timeout_sec` for each server in `config.toml` | Codex's own (30 seconds in Codex 0.162.1) | Per server. |
| OpenCode | `timeout` (milliseconds) for each server in its config | OpenCode's own | Per server. OpenCode applies the same value to requests made to the server, not only to connecting. |
| Claude Code | `MCP_TIMEOUT` (milliseconds) in its environment | Claude Code's own | One value for every server. Servers that resolve to different budgets, including one that keeps the native default, fail startup with `Claude Code applies one MCP startup timeout to every server`. |

For Codex, MCP startup happens inside `thread/start`, so the earlier of `harnesses.codex.timeouts.startup_seconds` and the MCP budget ends a slow startup.

```python
from m3.types import McpTimeouts, StdioServer

server = StdioServer(name="slow", command="python", args=("slow_mcp.py",), timeouts=McpTimeouts(startup_seconds=75))
```

### Startup admission

Many sessions starting at once (pytest-xdist workers, or several `m3 test` processes) can overload a host so that healthy harnesses miss their startup limits. With `[tool.m3.startup_admission] enabled = true`, a session waits for one of `slots` host-wide slots before it starts anything: capability probes, MCP servers, and the native harness. It keeps the slot until the harness has started, or, if startup fails, until startup cleanup has finished. Sessions are admitted in arrival order.

```toml
[tool.m3.startup_admission]
enabled = true
slots = 4
queue_timeout_seconds = 300
max_load_per_cpu = 1.5
```

- Slots are shared by every m3 process of the current OS user on the host. Queue state lives in the user cache directory (`~/Library/Caches/m3/admission` on macOS, `$XDG_CACHE_HOME/m3/admission` or `~/.cache/m3/admission` elsewhere). A process that dies frees its slot.
- Time spent waiting does not count toward the harness startup limits, which start when the harness starts. It does count toward outer limits such as a test or execution timeout.
- Claude Code initializes during its first turn, so its slot covers only starting the process.
- When the wait exceeds `queue_timeout_seconds`, startup fails with `m3 startup admission did not finish within 300s (startup_admission.queue_timeout_seconds)`; if the load threshold kept it waiting, the message also reports the load.
- Admission is supported on macOS and Linux. Enabling it on Windows fails session startup.

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
  "harnesses": {
    "codex": {"timeouts": {"initialize_seconds": 30.0, "startup_seconds": 120.0, "account_seconds": 15.0}},
    "opencode": {"timeouts": {"startup_seconds": 60.0, "session_creation_seconds": 60.0}},
    "pi": {"timeouts": {"initialize_seconds": 15.0, "extension_connect_seconds": 5.0}}
  },
  "mcp_timeouts": {"startup_seconds": null},
  "harness_probes": {"timeout_seconds": 30.0},
  "startup_admission": {"enabled": false, "slots": 4, "queue_timeout_seconds": 300.0, "max_load_per_cpu": null},
  "sources": {
    "artifact_policy": {"source": "environment", "origin": "env:M3_ARTIFACT_POLICY"},
    "protocol_revision": {"source": "default", "origin": "default"},
    "cli.project_python_timeout_seconds": {"source": "default", "origin": "default"},
    "results.busy_timeout_seconds": {"source": "default", "origin": "default"},
    "harnesses.codex.timeouts.initialize_seconds": {"source": "default", "origin": "default"},
    "harnesses.codex.timeouts.startup_seconds": {"source": "default", "origin": "default"},
    "harnesses.codex.timeouts.account_seconds": {"source": "default", "origin": "default"},
    "harnesses.opencode.timeouts.startup_seconds": {"source": "default", "origin": "default"},
    "harnesses.opencode.timeouts.session_creation_seconds": {"source": "default", "origin": "default"},
    "harnesses.pi.timeouts.initialize_seconds": {"source": "default", "origin": "default"},
    "harnesses.pi.timeouts.extension_connect_seconds": {"source": "default", "origin": "default"},
    "mcp_timeouts.startup_seconds": {"source": "default", "origin": "default"},
    "harness_probes.timeout_seconds": {"source": "default", "origin": "default"},
    "startup_admission.enabled": {"source": "default", "origin": "default"},
    "startup_admission.slots": {"source": "default", "origin": "default"},
    "startup_admission.queue_timeout_seconds": {"source": "default", "origin": "default"},
    "startup_admission.max_load_per_cpu": {"source": "default", "origin": "default"}
  }
}
```

`configuration.digest` is `sha256:` plus a hash of the effective values (sources excluded). The same value is stored as `configuration_digest` in the run manifest. Human output prints it after `config:` and lists each non-default setting with its origin.

The doctor also reads `M3_*` values from the dotenv file described in [Environment files](#environment-files); ambient variables still win. `--require config:FIELD` accepts `artifact_policy` or `protocol_revision`, but it does not narrow the check: every setting is resolved and validated, so an invalid `protocol_revision` fails `config:artifact_policy` too. Any other field name exits 2 with `invalid requirement`. Running `m3 doctor` with no `--require` checks `config` and `storage:memory`. `m3 doctor` reads `cli.project_python_timeout_seconds` on every run, and a configuration error fails the doctor even without a `config` requirement.

An invalid setting exits 2 and names the code, field, origin, and reason without echoing the value. With `--json` the error is printed to standard output as `{"ready": false, "error": {...}}`; otherwise the message goes to standard error:

```text
m3 doctor: configuration error (code=invalid_configuration field=artifact_policy origin=env:M3_ARTIFACT_POLICY reason=must be one of failed, always, or never)
```

The `code` is `invalid_configuration` for a bad value, `unknown_setting` for an unrecognized `[tool.m3]` key or nested key (the field is the dotted name, such as `results.foo` or `harnesses.claude_code`), and `invalid_project` for a `pyproject.toml` or `--m3-config` file that is not valid TOML or whose `[tool.m3]` is not a table, an `--m3-config` file that does not exist (`field=config_file`), or one without a `[tool.m3]` table. Exit 1 means the settings resolved but another requirement, such as the project Python environment, is not ready.
