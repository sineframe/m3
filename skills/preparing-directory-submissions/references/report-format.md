# Report format

Write one Markdown file, `directory-readiness-report.md`, at the project root
(or where the user asks). Rewrite it on each run; don't append. Use this
skeleton, keeping the sections in this order and dropping a section only when
the note in brackets says so.

```markdown
# Directory readiness: SERVER_NAME

- Date: YYYY-MM-DD
- Directories: Claude, OpenAI, Muse
- Server: how it was reached (`python -m pkg.server` over stdio, or the URL)
- M3: `m3 --version` output; lint policy date from the JSON report
- Policy excerpts: checked 2026-10-27; live pages re-read YYYY-MM-DD: no change / CHANGES / could not re-read
- Tools: N (names)

This report lists what was checked and what was found. It is not a prediction
of the review outcome: reviewers also test behavior, accounts and documents
that no check here sees.

## Rejection feedback

[Only when the user pasted feedback. One row per point, in the order given.]

| # | Feedback (quoted) | Mapped to | Result |
|---|---|---|---|

## Summary

| Directory | fail | warning | pass | not checked |
|---|---|---|---|---|

## Claude connector directory

| Check | Source | Status | Evidence | Fix |
|---|---|---|---|---|

## OpenAI plugin directory (ChatGPT and Codex)

| Check | Source | Status | Evidence | Fix |
|---|---|---|---|---|

## Muse connector directory

| Check | Source | Status | Evidence | Fix |
|---|---|---|---|---|

### Muse tool classification

| Tool | Class | Evidence | Documented class |
|---|---|---|---|

## Review test cases

[The cases in each chosen directory's form layout, from test-cases.md.]

## Proposed changes

[Diffs for the fixes, grouped by file. Nothing here has been applied.]

## Not checked by anyone

- MCP Apps UI and its content security policy
- Business or developer verification
- Real sign-in with the reviewer account
- Reachability from the directory's own servers (for Claude, traffic comes from 160.79.104.0/21)
- [Anything else no check covered, with the reason]
```

## Rows

Each table has exactly one B row per tool, named `B. Hints vs behavior:
TOOL`, passing tools included. Other checks may group tools in one row.

Put rows in this order inside each directory table:

1. Rows for checks that pasted feedback mapped to, for every tool.
2. Lint rows.
3. Judged rows, in check order A to J.
4. Test rows.

Columns:

- **Check**: a short name and the subject, for example
  `B. Hints vs behavior: check_overdue_invoices`, or the lint rule and
  location, `directory-tool-title-missing: tool 'manage_customers' title`.
- **Source**: `lint`, `judged` or `test`.
- **Status**: `fail`, `warning`, `pass` or `not checked`. For lint rows, put
  lint's own severity next to it: `fail (lint error)`, `warning (lint
  warning)`.
- **Evidence**: quoted text, `file:line`, or command output. Keep it short;
  quote the phrase, not the whole description.
- **Fix**: what to change, pointing to the diff in "Proposed changes" when
  there is one. Behind every `fail`, end the fix cell with the policy line:
  `Policy: “quoted excerpt” (LINK)`.

Escape `|` in table cells as `\|`, and keep each cell on one line.

## Lint rows

Copy each `m3 lint --directory … --json` finding into the table of every
directory it lists under `directories`, without changing it:

- Check: `rule` and `location`.
- Status: from that directory's `severity`: `error` becomes `fail (lint
  error)`, `warning` becomes `warning (lint warning)`. Never move a lint
  finding to another severity, and never add a directory it doesn't list.
- Evidence: `detail`, then the `quote` in quotation marks.
- Fix: lint's `fix`, then `Policy: “quote” (policy_url, policy checked
  policy_date)`.

When lint ran and had no findings for a directory, add one row: `m3 lint
--directory NAME`, `lint`, `pass`, "no findings; lint covers metadata rules
only".

For Muse, add one row: `m3 lint --directory muse`, `lint`, `not checked`,
"lint reports Muse as not checked: no rule checks Muse metadata". Every other
Muse row is judged or test.

When `m3 lint` doesn't support `--directory`, add one row per directory:
`m3 lint --directory NAME`, `lint`, `not checked`, the version you found, and
the fix "Upgrade M3: `uv tool upgrade sf-m3-cli` (or rerun the installer),
then rerun this check."

## Summary counts

Count rows per directory by status. Lint warnings count as warnings, lint
errors as fails.

## Wording

- Never write that the server "will be approved", "is ready to submit",
  "passes review" or "meets the requirements". Write what was checked:
  "No check found a problem" or "N checks failed".
- Never put credentials, tokens, personal data or returned values in the
  report. Name fields, not their contents.
