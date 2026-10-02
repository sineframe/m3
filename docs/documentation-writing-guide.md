# M3 documentation writing guide

This is the contract for new and updated product documentation. It applies to
human authors and coding agents.

## Start from a reader task

Name the reader, the task, and the observable result before writing. Prefer one
task per guide. Put conceptual background in `docs/site/concepts` and exact
contracts in `docs/site/reference`; do not make a guide carry both manuals.

Use the existing terminology consistently: run, suite, case, execution, turn,
evaluation, trace, direct test, and agent test are distinct concepts.

## Practical guide order

Every practical guide uses this sequence:

1. State the task and result.
2. List requirements, including relevant compatibility.
3. Show the complete minimum example.
4. Give the working directory and exact command.
5. Show the stable result and explain what it proves.
6. Add a focused variation only when it teaches another useful choice.
7. Explain the failure or limitation readers are likely to meet.
8. Link to the next task.

Do not postpone prerequisites until after the command. Do not make readers
assemble a working program from unrelated fragments.

## Full examples and snippets

A **complete example** supplies every file, dependency, fixture, service, and
command needed to run it. A **complete file** can replace its named file
verbatim: include imports and configuration; exclude ellipses, hidden helpers,
and unexplained TODOs.

A **snippet** is an excerpt or replacement from a tested complete example.
State its filename and exact insertion or replacement point. If several edits
must work together, show the resulting complete file.

Put the main example inside the guide that teaches it. Source links and
downloads support the guide; they are not substitutes for its task-bearing
code. Concept pages may use clearly labeled illustrative fragments. Reference
pages use focused examples with their imports and required context.

Keep runnable guide projects under `sdk/examples/docs/<guide-id>`. Each project
has a manifest recording its owning page, files, dependencies, working
directory, commands, expected exit codes, stable assertions, compatibility,
external requirements, and verification mode. The displayed source is
synchronized from these files. Test every documented executable variation.
Run `python3 scripts/render_docs_examples.py` after editing source. Then run
`python3 scripts/validate_docs_examples.py` to reject unmapped Python blocks,
source drift, invalid manifests, and syntax errors. After changing any page
under `docs/site`, run `python3 scripts/render_skill_references.py` and commit
the regenerated `skills/testing-with-m3/references/`.

## Runtime-created state

Never put an author's run ID, execution ID, profile ID, local path, or database
record ID into a command the reader should run. Teach the reader to capture the
identity produced by their own command and reuse it. Literal IDs belong only in
clearly labeled sample output, with a note that the reader's value differs.

Stateful examples must create their own prerequisites. An aggregation example
creates evaluations before querying them; a baseline example captures its
first run before comparing the second; an upload retry uses the failed local
run created in that sequence.

## Compatibility and limitations

State a limitation where it changes the reader's action:

- Use a short “Works with” requirement for the main compatibility boundary.
- Put a narrow constraint beside the affected parameter or variation.
- Link to the compatibility reference for the full matrix.
- Use warning callouts only for material risk such as data loss or credential
  exposure.

Distinguish supported, unsupported, and not verified. Absence of a test is not
proof of unsupported behavior. For elicitation, distinguish direct SDK
operations from agent-driven paths: current agent support is Codex and Pi, but
direct elicitation does not require either harness.

## Voice

Write as a developer explaining behavior they checked. Start with the answer.
Use concrete nouns, active verbs, exact commands, and the conditions under
which a claim holds.

Avoid marketing adjectives and filler: “seamless,” “powerful,” “robust,”
“effortless,” “production-ready,” “simply,” “just,” “unlock,” and “leverage.”
Do not use a feature list where the reader needs an explanation. Do not say a
tool “ensures correctness”; say which evidence the test checks.

## Editorial cleanup

State the fact without a staged opener, invented objection, or closing sentence
that repeats the paragraph. Keep a contrast only when both outcomes affect what
the reader should do. Use bold for meaningful emphasis, not labels such as
`**Note:**` or `**Compatibility:**`.

Do not use em or en dashes as general-purpose sentence connectors. Choose the
punctuation that describes the relationship between clauses. Dashes and hyphens
inside code, commands, paths, and URLs keep their exact spelling.

Generated reference text must add information about the symbol. When a field or
method has no description, show its name, type, signature, and default without
inserting a generic description. Keep generator instructions out of published
reader content.

Output blocks must be captured from a verified scenario. Normalize only named
variable fields such as a temporary path, run ID, or elapsed time. Never invent
a transcript or normalize away a failure.

## Evidence and review

Read implementation and applicable tests before documenting behavior. Existing
documentation is material to audit, not independent evidence. Record important
claims with their release, source symbol, test or observation, status, and
limits.

Verification proceeds in this order:

1. Displayed code matches its source.
2. Imports, syntax, configuration, and public names are valid.
3. The complete project runs in a clean directory outside the repository.
4. Assertions prove the described behavior.
5. The reader's sequence works in order.
6. Rendered code remains correct and copyable.
7. The published download runs independently.

Do not count syntax checks, collection, skips, mocks, or a fixture-backed
integration as proof of a different live behavior. Record model, provider,
harness version, OS, and date for live verification.

Every change receives technical review first, then editorial review, then an
integration review for navigation, duplication, terminology, and links.
Factual changes made during editing must be technically rechecked.

## Publication

The site publishes an exact M3 release. Development previews must identify
themselves as development content. Before release, validate source links,
render the combined site, run guide examples against the candidate, and repeat
the clean-install path against published packages before updating the landing
site's release pin.
