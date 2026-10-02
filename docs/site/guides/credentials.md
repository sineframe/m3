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
| M3 run upload | `M3_ACCESS_TOKEN` | Store the token in the CI secret store. For local uploads, `m3 auth login` can save it in the operating-system credential store. |

`M3_ACCESS_TOKEN` authenticates M3 uploads. M3 removes it from the pytest child environment and rejects that exact variable name in agent and judge mappings or ACP manifests. Agent providers and judges need their own credentials.

## Credential sources

The SDK reads the process environment and does not load dotenv files. The CLI also reads the process environment. It loads a dotenv file only when the command includes `--env-file PATH`; it never searches for `.env`.

When an explicit environment file is selected, values already present in the process environment take precedence, including empty values. File values fill names absent from the process environment. Values in the file are not interpolated. If an ambient variable is set but empty, M3 will not replace it with the non-empty value from the file.

For local uploads, `m3 auth login` saves an M3 token in a supported operating-system credential store. The CLI uses the saved token when `M3_ACCESS_TOKEN` is absent; an environment token takes precedence. CI uploads require `M3_ACCESS_TOKEN` and do not read an interactive local credential store. A selected `--env-file` can provide values to CLI test commands. Do not commit files containing live credentials.

## Common configuration mistakes

- In `TARGET=SOURCE`, the child reads the target name and M3 reads the source name from its environment.
- M3 does not discover `.env`. Pass `--env-file PATH` to the CLI command or export the values into the process environment.
- An empty ambient variable still overrides a non-empty value from the environment file.
- Configure a key on the process that uses it: the MCP server, agent harness, or judge.
- Host login behavior differs between native harnesses. Use an explicit mapping when an isolated child process needs a provider key.
- `M3_ACCESS_TOKEN` cannot replace a model-provider or judge credential. Give each one its own source variable and credential.

## Next steps

- [Pass a credential to a stdio MCP server](credentials/endpoints.md)
- [Choose an agent harness](agents/harnesses.md) or [configure an ACP agent](agents/acp.md)
- [Evaluate a response with an LLM judge](evaluations/judges.md)
- [Manage M3 access tokens](ci/access.md) or [run M3 in GitHub Actions](ci/github-actions.md)
- [Credential reference](../reference/credentials.md) for HTTP authentication, exact resolution, and failure behavior
