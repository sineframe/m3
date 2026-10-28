---
name: preparing-directory-submissions
description: Use when an MCP server, connector, app or plugin is being prepared for, submitted to, or reviewed by the Claude connector directory, the OpenAI plugin directory (ChatGPT apps, Codex plugins) or the Muse connector directory (Meta); when a directory submission was rejected, flagged, held or sent back, including pasted rejection emails, reviewer feedback and portal or scan findings; or when someone asks whether a server is ready to list, publish or resubmit. Not for testing an MCP server, writing or debugging M3 tests, or setting up M3 when no directory submission is in view; testing-with-m3 covers those.
license: Apache-2.0
---

# Preparing a directory submission

This skill checks an MCP server against the published review rules of three
directories and writes one report:

- **claude**: the Claude connector directory;
- **openai**: the OpenAI plugin directory, shared by ChatGPT and Codex;
- **muse**: the Muse connector directory (Meta).

M3 provides the deterministic part: `m3 lint --directory` checks tool
metadata, and M3 direct tests call tools and record what they return. Neither
runs a model. You make every judgment call, and you back each one with
evidence.

The policy excerpts in `references/` were checked on **2026-10-28**.

## Rules

- Quote the policy line, with its link, behind every failure. Take quotes
  from the policy references, verbatim. No quote, no failure: without one,
  report a warning and say what is unclear.
- Take every `file:line` from a numbered view of the file (`grep -n`,
  `nl -ba`, or a read that shows line numbers). Never estimate a line number;
  if you can't get it, cite the function name instead.
- Never say or imply that the submission will be approved, is ready, or
  passes review. Report what was checked and what was found.
- Never edit the server without asking. Propose fixes as diffs in the
  report. Adding the readiness test file and the report is fine; say so.
- Never upload, submit or publish anything, and never contact a directory's
  portal.
- Never ask for, store or print credentials, tokens or personal data. Name
  fields, not their values.
- Never call a tool you classified as a write or sensitive write unless the
  user confirmed, in this conversation, that the server points at a
  dedicated test account. That covers every way of running it: an MCP call,
  an M3 test, or importing the server's module and calling the function.
  Never call a money-movement, trading or media-generation tool at all.
  Classify from the code, not from the hints.
- Carry `m3 lint` findings into the report unchanged. Don't reinterpret,
  upgrade or downgrade their severities.
- Never report Muse as passing because lint ran. Lint reports Muse as not
  checked; every Muse row is judged or test.

## Red flags

Stop and re-read the rules if you are about to:

- write or edit a file of the server, its docs, its privacy policy or its
  manifest;
- import the server's module and call a tool function, or run a test that
  calls a tool you haven't classified as a read;
- write a policy rule from memory ("as far as I know, OpenAI restricts…");
- write "passes", "ready" or "will be approved" about a directory;
- cite a line number you didn't read from a numbered view;
- mark a Muse row `pass` because lint ran.

| Thought | Reality |
|---|---|
| "The fixes are obvious, I'll apply them." | The user asked whether the server is ready, not for changes. Put each fix in the report as a diff and ask. |
| "I'll call the function directly to check my fix works." | Calling a write tool's function runs the write: the email goes out, the payment is recorded. An in-process call is still a call. |
| "readOnlyHint is true, so calling it is safe." | The hint is what you are checking. A tool marked read-only can send email. |
| "Lint is clean, so the metadata is fine." | Lint covers the rules metadata can decide. Muse isn't checked by lint at all, and steering, hints against behavior and privacy need your judgment. |
| "The live page has sentences my excerpt doesn't, so the policy changed." | Excerpts quote parts of sections. Only changed or missing excerpt text is a change. |
| "There's no live URL yet, so connectivity fails." | Undeployed is `not checked`. Fail only a URL that is supposed to be live. |

## 1. Preflight

Do these in order, and keep notes for the report header.

1. **Check M3.** Run `m3 --version` and `m3 lint --help`. Lint has directory
   checks when the help lists `--directory`.
   - If `m3` is missing, tell the user to install it with
     `uv tool install sf-m3-cli` (or
     `curl -LsSf https://m3.sineframe.com/install.sh | sh`).
   - If `m3 lint` or its `--directory` option is missing, tell the user to
     upgrade with
     `uv tool upgrade sf-m3-cli` (or rerun the installer) and then
     `m3 setup` in the project.
   - In both cases, carry on with the judgment checks and mark the lint rows
     `not checked`, as [Report format](references/report-format.md#lint-rows)
     describes. If `m3` is missing, test rows are `not checked` too.
2. **Choose the directories.** Use what the user named. Otherwise infer it
   from the repo: `.codex-plugin/plugin.json`, or a `plugin.json` with
   `extensions.com.openai`, means openai; docs that mention the Claude
   directory mean claude; docs that mention Muse mean muse. Ask only when
   nothing points anywhere, and then offer `all`.
   - A `.claude-plugin/` directory means a Claude *plugin* submission, which
     has its own checklist that this skill doesn't cover
     (https://claude.com/docs/plugins/pre-submission-checklist). Say so. If
     the plugin points at a remote MCP server, Claude asks for that server to
     be submitted as a connector too (`claude-plugin-points-at-server`), and
     this skill checks the server.
   - An MCP bundle (`manifest.json` for MCPB) can't be listed: the directory
     no longer accepts local servers packaged as MCPB
     (`claude-submission-targets`). Tell the user it has to go in a plugin,
     and check the server only if it will also run remotely.
3. **Find the server.** Look for a public URL (README, `.mcp.json`,
   `mcp.json`, the manifest) and a local start command (README,
   `pyproject.toml` scripts, `package.json` scripts, a `Dockerfile`). Lint
   and tests can use the local command; connectivity needs the public URL.
   If you find neither, ask.
4. **Map pasted feedback.** If the user pasted rejection feedback, split it
   into points and map each one to checks with
   [Rejection reasons](references/rejection-reasons.md). Those checks run
   first, for every tool, and their rows go first in the report.
5. **Read the material once**: the tool list, each tool's implementation,
   the docs, the privacy policy and the listing manifest, as
   [Judgment checks](references/checks.md#gather-the-tool-inventory-first)
   describes.

## Re-read the live policy

When you have web access, fetch the pages listed at the top of each policy
reference and compare the excerpts you are about to quote with the live text.
Claude's docs pages are also served as Markdown at the same URL plus `.md`.
Prefer a plain download (`curl -sL URL`) and search it for the excerpt's
words; a fetch tool that summarizes pages can't confirm exact wording, so
say so if that is all you have.
In the report header, record the date you re-read them and either "no
change" or each excerpt that changed, with the new text. An excerpt changed
only when its words are no longer on the page or now read differently.
Excerpts quote parts of sections on purpose, so page text around an excerpt
is not a change. Quote the live text
when it differs. If you can't fetch a page, write "could not re-read" and use
the bundled excerpt.

## 2. Run lint

For a local server, pass its start command after `--`; for a remote one, the
URL:

```sh
m3 lint --directory claude --directory openai --json -- python -m my_server
m3 lint https://mcp.example.com/mcp --directory all --json
```

Pass `--header NAME:VALUE` only if the user gives you a header for a test
account; lint never prints header values. Read the JSON from the command's
output; don't add it to the project. Exit status 0 means no error findings
(warnings may still be listed; read `findings`), 1 means at least one error
finding, and 2 means a usage error or that nothing could be checked (fix the
start command, or run `m3 lint` without `--directory` to see the listing
problem).

Every finding has `rule`, `location`, `detail`, `fix` and a `directories`
list with each directory's `severity`, `quote`, `policy_url` and
`policy_date`. Copy them into the report as
[Report format](references/report-format.md#lint-rows) describes. The
directory readiness reference in the M3 docs lists every rule
(https://m3.sineframe.com/docs/reference/directory-readiness).

## 3. Do the judgment checks

Follow [Judgment checks](references/checks.md). Run each check that applies
to the chosen directories:

| Check | Covers |
|---|---|
| A. Steering language | preferring this tool, avoiding others or web search, call-first or call-last, overriding or hiding, ranking and promotion |
| B. Hints against behavior | read, write or sensitive write from the code, against readOnlyHint, destructiveHint, openWorldHint, names and titles |
| C. Tool design | catch-all `action` tools, free-form API tools without an API reference |
| D. Data requests | conversation history, credentials, payment data, government IDs, precise location |
| E. Use case | money or crypto movement, trades, AI media generation, digital-goods commerce |
| F. Privacy | returned data types against the privacy policy; secrets and internal ids in output |
| G. Listing details | names, descriptions, links and icons against each directory's limits |
| H. Reviewer access | a populated test account without MFA, codes or magic links (ask; never collect) |
| I. Connectivity | public https, 401 with sign-in metadata, directory origins, OpenAI domain challenge |
| J. Muse | tool classes, hidden actions, completion inside Muse, minimum data, read-only access, docs, test account |

The bundled scripts are in this skill's `scripts/` directory; call them by
their path inside the installed skill (shown as `SKILL_DIR` in the
references).

## 4. Draft the test cases

Follow [Review test cases](references/test-cases.md): draft the cases each
directory asks for, write one M3 direct test per positive case, run the tests
for read tools only (unless the user confirms a test account), and give the
cases in each directory's form layout.

## 5. Write the report

Write `directory-readiness-report.md` at the project root with
[Report format](references/report-format.md). Then tell the user, in a few
lines: where the report is, the fail and warning counts per directory, the
top failures, and what you need from them (test-account confirmation, a
public URL, listing text). Ask before applying any proposed change.

## References

| Need | Reference |
|---|---|
| How to run and decide each judgment check | [Judgment checks](references/checks.md) |
| Claude policy text to quote | [Claude excerpts](references/policy-claude.md) |
| OpenAI policy text to quote | [OpenAI excerpts](references/policy-openai.md) |
| Muse policy text to quote | [Muse excerpts](references/policy-muse.md) |
| Mapping rejection feedback to checks | [Rejection reasons](references/rejection-reasons.md) |
| Test cases, direct tests, form layouts | [Review test cases](references/test-cases.md) |
| Report skeleton and row rules | [Report format](references/report-format.md) |
| A finished report for a server with problems | [Example report](references/example-report.md) |
| A finished set of test cases | [Example test cases](references/example-test-cases.md) |
