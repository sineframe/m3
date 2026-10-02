<!-- Generated from docs/site/reference/python/m3/elicitation.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Elicitation and managed-input API

`ElicitationPlan` describes expected form or URL input requests and the
responses M3 may submit. Build plans with:

- `expect_form` / `maybe_form`
- `expect_url` / `maybe_url`
- `sequence` for later rounds
- `round_of` for requests in one round
- `one_of` for alternatives
- `optional` for an optional branch

`ElicitationResponse` carries accept, decline, or cancel semantics.
`FormElicitationRequest` and `UrlElicitationRequest` expose the validated
request. `PendingElicitationRound` represents a managed round awaiting input.

Attach a plan to the action that may elicit: a direct call/prompt/resource
operation, `agent.run`, `agent.submit`, or `session.send` on a supported path.
A plan does not belong to the creation of a long-lived session.

Direct sync and async operations do not require an agent harness. M3 has tested
agent-driven elicitation through Codex and Pi, with different boundaries. See
[Compatibility](reference-compatibility.md) before choosing a harness.

Managed handles expose pending input, submission, cancellation, and terminal
results. A stale round, invalid response, unused required plan, round overflow,
or ambiguous request association fails explicitly.

The [Python API inventory](https://m3.sineframe.com/docs/reference/python/api) lists every public helper and
model.
