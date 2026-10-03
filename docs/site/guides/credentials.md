---
title: "Configure credentials"
description: "Choose a credential source and pass it only to the M3 process that needs it."
---

# Configure credentials

Credentials enter M3 under a source name and reach a child process under the name that process expects. In the mapping `OPENAI_API_KEY=MY_OPENAI_KEY`, M3 reads `MY_OPENAI_KEY` and sets `OPENAI_API_KEY` for the agent.

## Credential destinations

| Process | Destination | How to configure it |
| --- | --- | --- |
| stdio MCP server | A variable the server expects, such as `DEMO_SERVICE_TOKEN` | Map the source value into the server's configured environment. The value goes to the server, not the agent calling its tools. |
| HTTP MCP server | An authorization header or bearer token accepted by the endpoint | Configure authentication on the HTTP server connection. Configure the agent's model-provider key on its harness. |
| Native agent harness | A provider variable, such as `OPENAI_API_KEY` | Set `credential_env` on the `kit.agents(...)` selection. Each native adapter isolates its child environment. Codex may reuse eligible host authentication when no explicit mapping is set; Pi, Claude Code, and OpenCode do not copy host login files into their temporary homes. |
| ACP agent | A variable the ACP process expects | Add an exact `${SOURCE_NAME}` reference to the ACP manifest's `env` mapping. M3 starts ACP with an isolated environment containing its configured entries. |
| LLM judge | `M3_JUDGE_API_KEY` by default, or the key variable configured for a custom endpoint | Set the key in the M3 process environment or map a source with the `judge:` scope. A judge does not borrow an agent key. |
| M3 run upload | Saved CLI credential or `M3_ACCESS_TOKEN` | For local uploads, run `m3 auth login`, then start tests with `m3 test --upload`. For CI, create a CI token on your organization's **CI tokens** page in the [M3 account console](https://auth.sineframe.com/account) and store it as `M3_ACCESS_TOKEN` in the CI secret store. See [Manage M3 access](ci/access.md#create-a-ci-token). |

`M3_ACCESS_TOKEN` authenticates M3 uploads. `m3 test` and `m3 ci test` remove it from the pytest child environment, and M3 rejects that exact variable name in agent and judge mappings or ACP manifests. Agent providers and judges need their own credentials.

## Credential sources

The SDK reads the process environment and does not load dotenv files. The CLI also reads the process environment and automatically loads `.env` from the project root (`--project-root`, or the nearest `m3.toml` at or above the current directory, stopping at the Git root, otherwise the current directory) when that file exists. Pass `--env-file PATH` to load a custom file instead.

When an environment file is loaded, values already present in the process environment take precedence, including empty values. File values fill names absent from the process environment. Values in the file are not interpolated. If an ambient variable is set but empty, M3 will not replace it with the non-empty value from the file.

For local uploads, `m3 auth login` uses device authorization in the M3 account console and saves a 30-day CLI credential in a supported operating-system credential store. An environment-provided `M3_ACCESS_TOKEN` always takes precedence. If `CI`, `GITHUB_ACTIONS`, or `GITLAB_CI` has a non-empty value, M3 requires `M3_ACCESS_TOKEN` and never reads the interactive credential store. Without one of those markers, commands can use the saved CLI credential when `M3_ACCESS_TOKEN` is absent, including `m3 ci test`. A selected `--env-file` can provide values to CLI test commands. Do not commit files containing live credentials.

## Common configuration mistakes

- In `TARGET=SOURCE`, the child reads the target name and M3 reads the source name from its environment.
- M3 loads `.env` only from the project root, not from parent directories or the test directory. Pass `--env-file PATH` for a file elsewhere, or export the values into the process environment.
- An empty ambient variable still overrides a non-empty value from the environment file.
- Configure a key on the process that uses it: the MCP server, agent harness, or judge.
- Host login behavior differs between native harnesses. Use an explicit mapping when an isolated child process needs a provider key.
- `M3_ACCESS_TOKEN` cannot replace a model-provider or judge credential. Give each one its own source variable and credential.

## Next steps

- [Pass a credential to a stdio MCP server](credentials/endpoints.md)
- [Choose an agent harness](agents/harnesses.md) or [configure an ACP agent](agents/acp.md)
- [Evaluate a response with an LLM judge](evaluations/judges.md)
- [Manage M3 access](ci/access.md) or [run M3 in GitHub Actions](ci/github-actions.md)
- [Credential reference](../reference/credentials.md) for HTTP authentication, exact resolution, and failure behavior
