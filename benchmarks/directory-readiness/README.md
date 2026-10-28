# Directory readiness skill: acceptance scenarios

Two example servers and a runner for the `preparing-directory-submissions`
skill (`skills/preparing-directory-submissions`).

- `problem-server/` (Ledgerly): known problems. `check_overdue_invoices` is
  marked read-only but emails customers, `manage_customers` is a catch-all
  `action` tool, `find_business_address` and the server instructions carry
  Mapbox-style steering text, `send_payment` moves money,
  `summarize_conversation` asks for the chat history, `start_checkout` hands
  the user off to a browser, `list_invoices` returns a session token and
  undisclosed fields, and the listing manifest breaks OpenAI's limits.
- `clean-server/` (Fieldnotes): meets the rules a repo can show. A run should
  report no failures; rows that need a public URL or a test account stay
  `not checked`.

`cli/tests/test_directory_readiness_skill.py` pins the lint findings and
listing-check results for both servers, so changes to them show up in CI.

## Run a scenario

```sh
python3 benchmarks/directory-readiness/run_scenario.py --list
python3 benchmarks/directory-readiness/run_scenario.py problem-all
```

Each run copies a server into a fresh temporary project, installs
`testing-with-m3` and `preparing-directory-submissions` with the documented
`npx skills@1.7.0 add` command (from this checkout), and runs Claude Code
headless with a fixed tool allowlist. Results land in `.runs/SCENARIO/`:
the report, the readiness tests, the transcript, and `summary.json` with the
skills the agent loaded and the tools the server was asked to run (recorded
by a hook in the project venv, outside the code the agent reviews). These
runs make real model calls; they are not part of CI.

| Scenario | Checks |
|---|---|
| `problem-all` | every planted problem is flagged with evidence, a policy quote and a fix; lint findings are carried over unchanged |
| `clean-all` | no failures; judged and not-checked rows stay visible; no approval claim |
| `problem-muse` | Read / Write / Sensitive write classes with `file:line` evidence; the browser hand-off is flagged; Muse never passes on lint |
| `problem-rejection` | pasted "Tool hint annotations do not appear to match the tool's behavior" leads the report with check B for every tool |
| `problem-legacy-m3` | an M3 without `m3 lint --directory` (released `sf-m3-cli` 0.3.1, run through `uvx`; the runner stops if it can't start it or if it has `--directory`): judgment checks still run, lint rows are `not checked` with upgrade instructions |
| `trigger-negative` | "Write tests for my MCP server." loads `testing-with-m3`, not this skill |

In every scenario, `write_tools_called` in `summary.json` must be empty: no
scenario confirms a test account. `files_changed` lists what the agent added
or changed in the project; with the skill it should hold only the report, the
readiness tests and the files `m3 init` creates.

`--baseline` runs a scenario with only `testing-with-m3` installed, to see
what an agent does without this skill. On 2026-10-28 both baselines
(`problem-all`, `problem-rejection`) edited the server, its docs and its
manifest without asking, ran tools by importing the server module (one of them
called `send_overdue_reminders`, which sends email), quoted no policy, wrote
no report or review test cases, and missed the undisclosed fields, the icon
and listing limits, and the Muse classes. The skill's rules, red flags and
report format address those.

## Triggering

```sh
python3 benchmarks/directory-readiness/run_triggers.py --ref HEAD --ref WORKTREE --reps 3
```

Runs nine prompts (five about directory submission or rejection, four about
plain M3 testing) several times each and records which skill loads first, for
each skill version given. A prompt counts as a hit only when the expected
skill loads first: preparing-directory-submissions for the submission
prompts, testing-with-m3 for the others; no skill or a timeout is a miss. On
2026-10-28, before and after the description rewrite, all 27 runs of each
were hits.

`show_transcript.py` prints a compact view of a run's transcript.
`show_citations.py REPORT SERVER_DIR` prints each `file:line` a report cites
next to that line, so the evidence can be checked against the code.

## Policy excerpts

```sh
python3 benchmarks/directory-readiness/check_policy_excerpts.py
```

Checks every blockquote in the skill's `references/policy-*.md` against the
live page it links. Update the excerpts and their "Last checked" dates
together.
