---
title: "Configure credentials"
description: "Choose the credential path for an MCP endpoint, agent, judge, or uploaded run."
---

# Configure credentials

Choose the task and continue to its guide:

| Task | Credential path | Next guide |
| --- | --- | --- |
| Pass a credential to an MCP endpoint | `SecretReference` in the endpoint definition | [Pass a credential to a stdio MCP server](credentials/endpoints.md) |
| Authenticate a native agent | `credential_env` or the harness's normal login | [Configure agent harnesses](agents/harnesses.md) |
| Pass an environment value to an ACP agent | `${ENV_NAME}` in the ACP manifest | [Configure ACP agents](agents/acp.md) |
| Authenticate an LLM judge | `M3_JUDGE_API_KEY` or a judge-scoped mapping | [Use an LLM judge](evaluations/judges.md) |
| Upload an M3 run | `M3_ACCESS_TOKEN` in an explicitly trusted job | [Run M3 in GitHub Actions](ci/github-actions.md) |

For the exact resolution and failure behavior, see the [credential reference](../reference/credentials.md). Project environment-file behavior is in [configuration](../reference/configuration.md).
