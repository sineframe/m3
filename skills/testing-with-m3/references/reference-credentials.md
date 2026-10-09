<!-- Generated from docs/site/reference/credentials.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Credential reference

## Credential references

`SecretReference(source, name)` stores a source and a variable or provider entry name, not the resolved value. The type is immutable, accepts `source="environment"` or `source="provider"`, and does not read dotenv files. Endpoint definitions accept references in stdio environment values, HTTP headers, and the direct HTTP bearer-token option.

```python
from m3.types import SecretReference

endpoint_key = SecretReference(source="environment", name="MCP_ENDPOINT_KEY")
```

## Direct MCP endpoints

For direct HTTP, the default environment resolver treats a missing or empty environment value as an authentication failure: `TransportConnectionError(transport="streamable_http", phase="authentication")`. A custom resolver controls its own lookup behavior. Header names and values containing CR or LF also fail during authentication setup.

For stdio, an environment `SecretReference` fails with `TransportStartupError` when its source name is absent. A present empty value is copied to the child as an empty string. A custom resolver determines its own behavior.

Bearer authentication uses `kit.direct(server, bearer_token=SecretReference(...))`.

## Native harnesses

In `kit.agents(...)`, set `credential_env={"OPENAI_API_KEY": "MY_OPENAI_KEY"}` to pass the parent process's `MY_OPENAI_KEY` value as `OPENAI_API_KEY` in the agent process. For pytest-selected agents, use `--credential-env codex:OPENAI_API_KEY=MY_OPENAI_KEY`. See [Choose an agent harness](guides-agents-harnesses.md) for a complete SDK example.

When constructing `Codex`, `Pi`, `ClaudeCode`, or `OpenCode` directly, use `credential_references`. Its keys are agent environment variable names; its values are `SecretReference` objects naming the credential source.

M3 also selects non-empty standard credential variables for the chosen harness and provider, such as `OPENAI_API_KEY` for Codex and `ANTHROPIC_API_KEY` for Claude Code. These defaults apply only when the selection has no `credential_env`. An explicit `credential_env` is the complete set of credentials the agent receives, so `credential_env={"CLAUDE_CODE_OAUTH_TOKEN": "MY_CLAUDE_TOKEN"}` passes only `CLAUDE_CODE_OAUTH_TOKEN`, even when `ANTHROPIC_API_KEY` is set in the parent. Set `credential_env={}` to forward no credentials at all; with Codex, this lets the agent reuse the host's ChatGPT login even when `OPENAI_API_KEY` is set.

For OpenCode and Pi, M3 selects a credential based on the model prefix:
`opencode/` uses `OPENCODE_API_KEY`, `openai/` uses `OPENAI_API_KEY`, and
`anthropic/` uses `ANTHROPIC_API_KEY`. Pi's `openai-codex/` prefix instead
uses `PI_CODING_AGENT_DIR`.

With `kit.agents(...)`, a missing mapped source raises `ValueError` when you start the run, before launching the agent. A present empty source passes that check, but launch requires a non-empty value and otherwise raises `HarnessStartupError`.

Codex, Claude Code, Pi, and OpenCode isolate their child environments. Codex may copy a host authentication file only when there are no explicit credential references. The other native adapters use selected references and do not copy host login files into their temporary homes.

Agent and judge mappings reject the exact name `M3_ACCESS_TOKEN` as a target or environment source. This validation does not detect the same token copied into another variable.

## ACP environment

An ACP manifest `env` value must be an exact `${ENV_NAME}` reference. M3 resolves it from the parent environment before starting the ACP process. A missing reference causes `HarnessStartupError`; a present empty value is forwarded as empty. The child receives the configured environment entries and an isolated HOME, not the caller's full environment. ACP manifest validation rejects `M3_ACCESS_TOKEN` as a child target or as the referenced source name.

## LLM judge

`LLMJudge` uses `M3_JUDGE_API_KEY` by default for the default endpoint. A custom endpoint requires an explicit `response_mode`; with `auth="env"`, it also requires an explicit `api_key_env`. The exact `M3_ACCESS_TOKEN` name is not accepted for `api_key_env`.

`auth="none"` is allowed only for `localhost`, `127.0.0.1`, or `::1`. A remote endpoint with no authentication is rejected. A missing or empty judge key returns an evaluation with status `ERROR` and code `judge_credentials_missing`; it does not fail harness startup or borrow an agent key.

## M3 upload

`M3_ACCESS_TOKEN` is the environment variable for an access token used to upload an M3 run. It has the form `m3pat_<22 base64url characters>.<43 base64url characters>`; an explicitly empty or malformed token is a CLI configuration error. `m3 test` and `m3 ci test` remove the token from the pytest child environment, whether or not `--upload` is present. When this variable is present, its value takes precedence over the saved CLI credential, including when the value is empty. If `CI`, `GITHUB_ACTIONS`, or `GITLAB_CI` has a non-empty value, M3 requires the environment variable and never reads the interactive operating-system credential store. Without one of those markers, commands can fall back to the saved CLI credential when the variable is absent, including `m3 test --upload` and `m3 ci test`.
