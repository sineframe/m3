---
title: "Compatibility and current boundaries"
description: "These docs describe the release shown in the site header. The SDK requires Python 3.10 or newer. A CLI-managed project must use the matching SDK version."
---

# Compatibility and current boundaries

These docs describe the release shown in the site header. The SDK requires
Python 3.10 or newer. A CLI-managed project must use the matching SDK version.

## Harness capabilities

| Capability | Current documented boundary |
| --- | --- |
| Direct MCP operations | Independent of an agent harness. |
| Native agent tool tests | Claude Code, OpenCode, Codex, and Pi where the selected operation is supported. |
| Bring-your-own agent | ACP v1 adapter path. |
| Agent-driven elicitation | Codex and Pi, using the tested versions and action scopes documented with the guide. |
| Direct elicitation | Sync and async SDK operations; it does not require Codex or Pi. |

Harness availability does not imply identical evidence, approval, resume,
elicitation, or cancellation behavior. Each agent guide names the tested path.

## Codex elicitation

Codex owns tool selection, dispatch, retries, and cancellation. M3 answers
Codex's tool approval for calls to bound servers from the test's tool
selection, and observes and answers supported native requests when their
association is unambiguous. Overlapping same-server approval or elicitation
cannot currently be associated reliably and is rejected. The tested Codex version
fails the action when a server asks for a tenth elicitation round, so an action can
use at most 9 rounds even when `elicitation_round_limit` is higher. M3's default
limit is 10. Prompt/resource elicitation and sampling/roots callbacks
inside a native Codex tool round are not verified supported paths.

The capture barrier cannot flush an event that has not reached the M3 process.
Codex integration uses the unmodified App Server.

## Pi elicitation

Pi supports the native request-key and round behavior used by M3's interaction
bridge. Pi has no 9-round cap: M3 enforces `elicitation_round_limit` as given,
10 by default. With managed input (`human_input="managed"`), the limit can be at
most 1024, the largest value the managed-input control channel accepts. A larger
value raises `ModelValidationError` before the turn starts. Use the Pi version
named in the elicitation guide.

Managed Pi delivery is verified for same-worker form, multi-round, and URL
rounds with `SQLiteExecutionStore`. Worker or process restart redelivery and
recovery are unsupported and terminalize when ambiguous.

## Viewer database

`m3 ui` currently opens `.m3/executions.sqlite` under the project root. It does
not accept the custom path supported by `m3 test --results-db`.

## Managed runtimes

Managed mode downloads and verifies a harness executable selected for the
current OS and CPU. It isolates that executable and writable runtime state but
is not an operating-system sandbox.
