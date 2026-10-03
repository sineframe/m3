---
title: "Configuration"
description: "Project identity and environment-file loading behavior."
---

# Configuration

## Project identity

`m3.toml` stores project identity. Keep its generated project ID stable; renaming a project changes its name, not that identity.

## Environment files

The CLI automatically loads `.env` from the project root (`--project-root`, or the current directory) when that file exists. Pass `--env-file PATH` to load a custom file instead; a missing explicit file is an error. Ambient environment values take precedence, including empty values. File values are not interpolated. The SDK reads from the process environment or a resolver supplied by the application; it does not load dotenv files.

See the [credential reference](credentials.md) for credential destinations and their resolution behavior.

## SDK settings

The SDK resolves two settings. Each can come from an explicit argument, an `M3_*` environment variable, or a `[tool.m3]` table in `pyproject.toml`.

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

The report's `configuration.settings` object has this shape; `origin` for a `pyproject.toml` value is `pyproject:` followed by the absolute file path:

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
