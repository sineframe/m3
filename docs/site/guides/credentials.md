---
title: "Configure credentials"
description: "Choose a credential source and pass it only to the M3 process that needs it."
---

# Configure credentials

Give each process the credential it needs through its own configuration. For environment mappings, M3 reads a source name from its environment and supplies that value under a destination name expected by the child process. For example, `OPENAI_API_KEY=MY_OPENAI_KEY` means the agent reads `OPENAI_API_KEY`; M3 looks up `MY_OPENAI_KEY` in its own environment.

## Choose the process that needs the credential

| Process | Destination | How to configure it |
| --- | --- | --- |
| stdio MCP server | A variable the server expects, such as `DEMO_SERVICE_TOKEN` | Map the source value into the server's configured environment. This credential authenticates the server itself, not the agent calling its tools. |
| HTTP MCP server | An authorization header or bearer token accepted by the endpoint | Configure the credential on the HTTP server connection. Keep it separate from the model-provider key used by an agent. |
| Native agent harness | A provider variable, such as `OPENAI_API_KEY` | Set `credential_env` on the `kit.agents(...)` selection. Codex, Pi, Claude Code, and OpenCode isolate their child environments. Codex may reuse eligible host authentication when no explicit credential mapping is configured; other native adapters do not copy host login files into their temporary homes. |
| ACP agent | A variable the ACP process expects | Add an exact `${SOURCE_NAME}` reference to the ACP manifest's `env` mapping. M3 starts ACP with an isolated environment containing its configured entries. |
| LLM judge | `M3_JUDGE_API_KEY` by default, or the key variable configured for a custom endpoint | Set the key in the M3 process environment or map a source with the `judge:` scope. A judge does not borrow an agent key. |
| M3 run upload | `M3_ACCESS_TOKEN` | `m3 ci test --upload` reads this token for the upload step. Keep it in the CI secret store for CI, or use `m3 auth login` to save it in the local operating-system credential store. |

M3 keeps upload authentication separate from test credentials. It removes `M3_ACCESS_TOKEN` from the pytest child environment, and rejects using that exact variable name in agent and judge mappings or ACP manifests. Store provider keys and judge keys separately from the M3 token.

## Choose where M3 reads the source value

The SDK reads the process environment. It does not load dotenv files. The CLI also uses the process environment by default; it reads a dotenv file only when the command includes `--env-file PATH`. M3 does not search for `.env` automatically.

When an explicit environment file is selected, values already present in the process environment take precedence, including empty values. File values fill names absent from the process environment. Values in the file are not interpolated. If an ambient variable is set but empty, M3 will not replace it with the non-empty value from the file.

For local uploads, `m3 auth login` saves an M3 token in a supported operating-system credential store. The CLI uses that saved token when no `M3_ACCESS_TOKEN` is set. An environment token takes precedence. In CI, provide `M3_ACCESS_TOKEN` through the CI secret store; M3 does not use an interactive local credential store there. A selected `--env-file` can provide environment values to CLI test commands, but do not commit a file containing live credentials.

## Avoid common configuration mistakes

- Reversing the mapping: the left side is the **destination** read by the child; the right side is the **source** M3 reads from its environment.
- Setting `.env` and expecting M3 to find it. Pass `--env-file PATH` to the CLI command, or export the values into the process environment.
- Setting an empty variable in the shell and expecting the environment file to replace it. Ambient values, including empty ones, take precedence.
- Giving a key to the wrong process. An MCP server key belongs in that server's environment; an agent provider key belongs in its harness mapping; a judge key belongs in the judge environment or `judge:` mapping.
- Expecting one native harness's host login behavior to apply to every harness. Use an explicit mapping when a provider key must reach an isolated child process.
- Reusing `M3_ACCESS_TOKEN` as a model-provider or judge credential. Use a separate source variable and credential for each purpose.

## Continue with a credential task

- [Pass a credential to a stdio MCP server](credentials/endpoints.md)
- [Choose an agent harness](agents/harnesses.md) or [configure an ACP agent](agents/acp.md)
- [Evaluate a response with an LLM judge](evaluations/judges.md)
- [Manage M3 access tokens](ci/access.md) or [run M3 in GitHub Actions](ci/github-actions.md)
- [Credential reference](../reference/credentials.md) for HTTP authentication, exact resolution, and failure behavior
