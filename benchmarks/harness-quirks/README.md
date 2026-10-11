# Harness Quirks Matrix

M3 tests that check how coding agents handle unusual but legal MCP features. Each folder is one eval: a small synthetic MCP server (`server.py`), the prompt and expected tool call (`quirk.yaml`), and a pytest test. Every run also starts `_control/`, a plain `echo` server, so a broken setup fails the control instead of looking like an agent bug.

This is a standalone uv project pinned to the released `sf-m3==0.2.38`, not the SDK source in this repository.

## Run

```sh
cd benchmarks/harness-quirks
uv sync --locked
uv tool install sf-m3-cli==0.2.38

m3 test --runtime=managed \
  --harness claude_code@2.1.287=claude-sonnet-5-5 \
  --harness codex@0.162.0=gpt-5.6-sol \
  --trials 3 --execution-timeout 300 -- .
```

Claude Code needs `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`. Codex needs `OPENAI_API_KEY` or a login in `~/.codex/auth.json`. OpenCode runs with the keyless model: `--harness opencode@1.18.25=opencode/big-pickle`. To run one eval, add a pytest filter after the path, for example `-- . -k q05`.

A test passes when the call reached the server with the expected arguments and status, and any expected marker reached the final answer. A failing test is a finding about that agent version, not a bug in the suite.

## Results

On 9 October 2026, three trials per cell:

| Eval | Claude Code 2.1.287 | Codex 0.162.0 |
|---|---|---|
| Q05 argument named `ids[]` | fails 0/3 | passes 3/3 |
| Q20 marker in the middle of a 12,000-character tool error | fails 0/3 | passes 3/3 |
| Q26 text and `structuredContent` markers | fails 0/3 | passes 3/3 |
| Q27 tool added after a catalog change | passes 3/3 | fails 0/3 |
| Q28 required nested fields in a 14 KB schema | passes 3/3 | fails 0/3 |
| Q01, Q07, Q08, Q09, Q11 | passes 3/3 | passes 3/3 |

The other ten evals (Q02, Q04, Q23, Q29, Q35, Q37, Q38, Q44, Q45, Q47) came from direct probes of the agent binaries and have not been run under M3 yet.
