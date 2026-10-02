<!-- Generated from docs/site/reference/python/m3/evaluations.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Evaluation API

An `Evaluator` or `AsyncEvaluator` receives `EvaluationContext` and returns an
`EvaluationDecision`. `EvaluatorCallable` is the accepted callback union.

`EvaluatorRegistry` stores callbacks by stable name.
`register_builtin_evaluators(registry)` installs the built-in completed,
successful-tool-call, and non-empty-output evaluators.

`EvaluationRunner` combines a registry with an `EvaluationStore`, executes one
named evaluator, validates the decision, and persists the result.
`InMemoryEvaluationStore` is suitable for isolated tests.

`EvaluatorRegistration` declares an evaluator expected by an execution. Its
`required` setting is separate from registering the callback. Missing or
non-passing required evidence can raise `RequiredEvaluationError` after the
result has been persisted.

`EvaluationVerdict` is the callback-facing status input; public durable values
use `EvaluationStatus`. See [Custom evaluators](guides-evaluations-custom.md),
[Judges](guides-evaluations-judges.md), and
[Aggregation](guides-evaluations-aggregate.md).
