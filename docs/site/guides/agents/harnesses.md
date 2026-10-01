---
title: "Choose an agent harness"
description: "Choose between M3 native harness adapters and ACP based on the agent process you want to run and the evidence it can provide."
---

# Choose an agent harness

Choose a native adapter when you want M3 to launch a supported coding-agent
CLI and collect its native session evidence. Choose ACP when your existing
agent speaks Agent Client Protocol or you can provide a process that does. M3
does not download or manage an ACP executable.

| Integration | M3 launches | Useful when | Important limit |
| --- | --- | --- | --- |
| Codex CLI | Native Codex app-server session | You want Codex-specific session and tool evidence. | Requires a configured Codex login/model; approval behavior belongs to Codex. |
| Pi | Native Pi RPC session | You want Pi's provider/model selection and RPC trace. | Provider configuration and model syntax are Pi-specific. |
| Claude Code | Native Claude Code stream-json session | You want Claude Code's native messages and MCP trace. | M3 does not support its exact per-tool restrictions; keep a one-tool server or use server-scope policy. |
| OpenCode | Native OpenCode HTTP session | You want to select an OpenCode provider/model. | Use `provider/model` or a matching explicit provider and model. |
| ACP | A command and arguments from the ACP manifest | Your agent already implements ACP or you can wrap its interface. | The executable, credentials, and provider setup remain your responsibility; ACP is excluded from managed runtime acquisition. |

All integrations have different setup, authentication, approval, and evidence
boundaries. A completed assistant message is not proof of a server call; check
the structured tool-call result in the execution trace. For one unchanged
pytest test selected across four native agents, see [run the same test across
agent harnesses](multiple-harnesses.md). For a local ACP example, see
[connect an ACP-compatible agent](acp-connect.md) or
[expose a custom agent through ACP](acp-wrapper.md). For native version pins,
see [managed runtimes](managed-runtimes.md) and [compare versions](versions.md).
The [harness compatibility reference](../../reference/compatibility.md) lists
feature-specific tested limits; do not infer equivalence from a shared API.
