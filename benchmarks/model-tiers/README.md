# Model Tiers

M3 tests that measure how much of the gap between a small and a large model is closed by a better MCP server. There are 13 tasks on one issue-tracker server ("Trackly", 520 issues, deterministic data), run against 7 server variants and several models and harnesses.

The server variants change one design dimension at a time: descriptions (D), tool catalog (C), argument schema (S), errors (E: a message plus hint, or a bare `400 Bad Request`) and pagination and output (P). `good` has all five good, `naive` has all five naive, and `good-X` is `good` with dimension X made naive.

| Variant | Tools | Variant | Tools |
|---|---|---|---|
| `good` | 9 | `good-S` | 12 |
| `naive` | 70 | `good-E` | 9 |
| `good-D` | 9 | `good-P` | 9 |
| `good-C` | 70 | | |

`good-S` has 12 tools because the naive schema has no direct status write, so the three workflow tools (`list_transitions`, `transition_issue`, `list_priorities`) join the catalog. The naive catalog is 19 real or near-duplicate REST tools plus 51 distractors (webhooks, exports, a deprecated `add_dependency` that always fails).

The naive flaws copy real APIs:

- Naive pagination (P) follows GitHub's REST lists: `page` / `per_page` (default 20, max 50), a bare array, no total and no `has_more`, no filters. An agent that stops after page 1 silently misses rows. Naive rows are compact, so reporter, description and customer ticket need a separate get.
- The naive schema (S) follows Jira: status cannot be set directly, it changes through `issues_transitions_list` and `issues_transition` (transition ids differ per current status, so moving between distant statuses takes several hops, and closing needs a resolution). Priority is an integer 0-4 whose meaning is only in `priorities_list`. Assignees are ids only, and team, role and on-call state live behind `teams_list` / `teams_members_list`. Writes use nested `{"body":{"data":{"attributes":...}}}` bodies.
- Naive errors (E) are a bare `400 Bad Request` or `404 Not Found`, and naive descriptions (D) are two to four words with no parameter docs.

## Tasks

Each task is its own top-level folder, and its `task.yaml` has a `suite` field. The `hard` suite (three anchors and ten hard tasks) runs by default. The `everyday` suite holds ten easier v1 tasks; set `MT_SUITE=everyday` to run it, or `MT_SUITE=all` for both (the choice is made in `conftest.py`).

| Id | Folder | Band | What it asks |
|---|---|---|---|
| T01 | `read-one-issue/` | read | Read one issue's status and audit code |
| T07 | `close-as-duplicate/` | error recovery | Close a duplicate (needs a resolution and `duplicate_of`) |
| T12 | `conditional-branch/` | conditional | Act depending on whether an issue has a customer ticket |
| H01 | `multi-filter-list/` | multi_filter | Open unassigned regression bugs in PAY or WEB, cycles Oct-W2/W3, minus `wontfix-candidate`: keys and count |
| H02 | `team-urgent-aggregate/` | aggregate_join | Which team has the most open urgent issues, and how many |
| H03 | `flaky-triage-rule/` | per_item_rule | Open flaky OPS issues: unowned go to the on-call engineer, owned ones get a comment |
| H04 | `reporter-reassign-chain/` | long_chain | Reassign the urgent Oct-W3 issues filed by PAY-142's reporter to that reporter's team lead |
| H05 | `team-rebalance/` | rebalance | Nobody on Platform keeps more than 3 in-progress issues; move the newest extras to whoever has the fewest |
| H06 | `close-named-duplicates/` | read_then_write | Close open GRO `duplicate-candidate` issues as duplicates of the issue each description names |
| H07 | `pick-the-right-sam/` | disambiguation_hard | Assign WEB-12 to "Sam from Web" (three Sams, one deactivated) |
| H08 | `conditional-bulk-priority/` | conditional_bulk | Set open MOB bugs with a customer ticket to high unless already urgent; report the count |
| H09 | `follow-up-fan-out/` | create_link_fanout | For each open Platform-owned PAY regression bug, create an INF follow-up and link it |
| H10 | `partial-failure-missing-issue/` | partial_failure | Assign PAY-150, PAY-9998 (does not exist) and PAY-151 to Priya |

The everyday suite (not run by default): T02 `enum-update/`, T03 `reporter-chain/`, T04 `ambiguous-user/`, T05 `filter-list/`, T06 `oldest-open/`, T08 `create-nested-issues/`, T09 `link-direction/`, T10 `bulk-cycle-move/`, T11 `aggregate-reassign/`, T13 `missing-issue-report/`.

This is a standalone uv project pinned to the released `sf-m3==0.2.38`, not the SDK source in this repository.

## Layout

| Path | What it is |
|---|---|
| `_backend/` | In-memory issue store and fixture data. All answers are derived from a seed and a per-trial nonce. |
| `_server/` | The stdio MCP server (`server.py --variant V --nonce N`) and the surface variants. `Surface.op_of(tool)` maps every tool to its logical store operation. |
| `_lib/` | Task runner, grading (`checks.py` helpers), telemetry extraction, replay, server definitions. |
| `_control/` | Plain `echo` server used by the baseline. |
| `_baseline/` | One test per agent column that checks the setup works before any task runs. |
| `<task-name>/` (23 folders) | One folder per task: `task.yaml` (id, suite, prompt), `check.py` (store-state check), `test_<id>_<name>.py`. Shared check helpers are in `_lib/checks.py`. |
| `published/` | Dated, citable result snapshots (records and analysis outputs). |
| `tests_offline/` | Free tests: store, surfaces, checks, grading, analysis, and end-to-end golden runs through the real server (`solver.py` is the wire-level solver they share). |
| `analyze.py`, `prices.yaml` | Offline analysis of the trial records, and the price table used for cost estimates. |

## Offline tests

No model calls, no network. Everything runs through m3, including the tests that start the real stdio server:

```sh
cd benchmarks/model-tiers
uv sync --locked
uvx --from sf-m3-cli==0.2.38 m3 test -- tests_offline -q
```

`tests_offline/test_e2e.py` drives a scripted correct agent through the real server and checks that grading says `success`: the 13 hard-suite tasks on 7 variants with 2 nonces, and the everyday-suite tasks on 7 variants with 1 nonce. The solver in `tests_offline/solver.py` reads answers only from tool responses (never from the store's facts), pages naive lists to a short page, walks Jira-style transitions, maps priority ids and resolves users through the teams tools, so a green run shows every task can be solved from what each variant exposes. The file also runs wrong agents (good and naive) and checks each failure state. The full suite takes about five minutes.

## Run

```sh
uv tool install sf-m3-cli==0.2.38
```

Claude Code needs `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`. Codex needs `OPENAI_API_KEY` or a login in `~/.codex/auth.json`. Pi needs the keys of the providers it routes to (for example `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`). `MT_VARIANTS` is a comma list of variants; it defaults to `good`. Records are appended to `results/_inbox/records.jsonl`.

With a Claude subscription token instead of an API key, add `--credential-env claude_code:CLAUDE_CODE_OAUTH_TOKEN=CLAUDE_CODE_OAUTH_TOKEN` to every command below; M3 forwards only `ANTHROPIC_API_KEY` to Claude Code by default, and without the mapping every Claude run fails with "Not logged in".

Run all models in one invocation, so a provider-side change hits every column at once. The six models:

```sh
CLAUDE="claude_code@2.1.296=claude-opus-5-5,claude-sonnet-5-5,claude-haiku-5-5"
CODEX="codex@0.162.1=gpt-6-astra,gpt-6.1-sol,gpt-6-luna"
```

Smoke check, baselines only, one trial per column:

```sh
m3 test --runtime=managed --harness "$CLAUDE" --harness "$CODEX" --trials 1 -- _baseline
```

The published run (12 October 2026): the good and naive servers on all six models, 8 trials per task.

```sh
MT_VARIANTS=good,naive m3 test --runtime=managed --harness "$CLAUDE" --harness "$CODEX" \
  --trials 8 --execution-timeout 600 -n 8 -- .
```

The one-flaw ablations: the good server with exactly one dimension made naive, on the mid and small models, 5 trials per task.

```sh
MT_VARIANTS=good-D,good-C,good-S,good-E,good-P m3 test --runtime=managed \
  --harness claude_code@2.1.296=claude-sonnet-5-5,claude-haiku-5-5 \
  --harness codex@0.162.1=gpt-6.1-sol,gpt-6-luna \
  --trials 5 --execution-timeout 600 -n 8 -- .
```

To run the same small models through one neutral harness, Pi takes both vendors' API keys (we did not run this for the published results):

```sh
MT_VARIANTS=good m3 test --runtime=managed \
  --harness pi@1.1.0=anthropic/claude-haiku-5-5,openai/gpt-6-luna \
  --trials 5 --execution-timeout 600 -n 8 -- .
```

The baseline is not parametrized by variant, so it runs once per agent column. To run one task, add a filter, for example `-- . -k h05`; to run the everyday suite, prefix the command with `MT_SUITE=everyday` (or `MT_SUITE=all`). Codex 0.162.1 runs every GPT-6 model in `code_mode_only`: the model sees the tools as TypeScript declarations, not JSON Schema.

## Results

The 12 October 2026 run, 8 trials per task (104 runs per model on each server; Sol 102 and 100 after 6 Codex harness errors). The write-up is at [m3.sineframe.com/blog/mcp-server-design-small-models](https://m3.sineframe.com/blog/mcp-server-design-small-models), and every record is in [`published/2026-10-12/`](published/2026-10-12/).

| Model | Good server | Naive server |
|---|---|---|
| Opus 5.5 (Claude Code 2.1.296) | 100% | 99.0% |
| Sonnet 5.5 | 100% | 68.3% |
| Haiku 5.5 | 99.0% | 64.4% |
| GPT-6 Astra (Codex 0.162.1) | 96.2% | 99.0% |
| GPT-6.1 Sol | 99.0% | 95.0% |
| GPT-6 Luna | 85.6% | 50.0% |

## Analysis

```sh
uv run python analyze.py --records results/_inbox/records.jsonl --out results/analysis
```

It reads the trial records and writes `SUMMARY.md`, `leaderboard.csv`, `by_task.csv`, `pass_k.json`, `failures.csv`, `costs.csv`, `gaps.json`, `ablations.csv` and `pi_control.csv`. Use `--variants good,naive` to restrict the outputs.

## Grading

Each trial gets one state. The server's final store state is replayed from the wire calls and compared with the task's expected state; the answer's `STATUS:` line is checked where the task needs an answer. The first matching state wins:

| State | Meaning |
|---|---|
| `harness_error` | The run did not complete, or it completed with no tracker calls and an error. |
| `timeout` | The run timed out. |
| `escaped` | The agent used a tool that is not a tracker tool (shell, file read). |
| `success` | The task check passed. |
| `looped` | Three identical consecutive calls, or more than 120 tracker calls. |
| `hallucinated` | `STATUS: DONE`, the check failed, and no successful call reached the task's target operation. |
| `gave_up` | `STATUS: FAILED`, the check failed, and no successful call reached the target operation. |
| `wrong_tool` | No successful target call, no FAILED status, and no error responses. |
| `bad_args` | No successful target call, no FAILED status, and at least one error response. |
| `wrong_target` | A target operation succeeded but the result is wrong. |
| `side_effects` | The task goal is met but something else was changed. |
| `other` | Fallback. |

A target operation counts as reached when a successful call's tool maps to it through `Surface.op_of` (for example `issues_get` and `get_issue_v2_beta` both map to `get_issue`, `issues_patch` and `issues_put` to `update_issue`; distractors and the deprecated `add_dependency` map to nothing), or when the store logged a mutation with that operation. A grading exception is recorded as `grader_error` and is not counted in the analysis.
