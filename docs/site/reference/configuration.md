---
title: "Configuration and credentials"
description: "m3.toml stores project identity. Keep its generated project ID stable; renaming a project changes its name, not that identity."
---

# Configuration and credentials

`m3.toml` stores project identity. Keep its generated project ID stable;
renaming a project changes its name, not that identity.

## Environment loading

M3 reads the process environment. It loads a dotenv file only when a command
uses `--env-file PATH`. Ambient variables take precedence, including an empty
ambient value. File values are not interpolated.

Common credential variables:

| Consumer | Variable |
| --- | --- |
| OpenCode harness | `OPENCODE_API_KEY` |
| Codex harness | `OPENAI_API_KEY` |
| Claude Code harness | `ANTHROPIC_API_KEY` |
| LLM judge | `M3_JUDGE_API_KEY` |
| Report publishing | `M3_ACCESS_TOKEN` |
| GitHub managed-runtime metadata | `M3_GITHUB_TOKEN` |

Use `--credential-env KIND:TARGET=SOURCE` when CI stores a credential under a
different name. Agent, judge, and publishing credentials are separate; M3 does
not use one as a fallback for another.

Endpoint credentials belong in the server definition, such as
`HTTPServer.headers`. Keep real values outside source control.
