# Evidence and traces

M3 records what it can observe at the transport and harness boundaries. A
recorded tool call is evidence that the call crossed an observed boundary; an
agent's prose saying that it used a tool is not.

Trace values distinguish an observed value from an unavailable one. Do not
turn unavailable data into an empty string, empty object, or assumed success.
Harnesses expose different evidence, so the same field may be observed in one
integration and unavailable in another.

Traces become final after the owning client or agent session closes. Before
then, assert on the immediate operation result. After finalization, use the
typed trace view for calls, results, messages, timing, and outcome.

See [Read traces](/guides/results/traces) for code and
[Compatibility](/reference/compatibility) for harness-specific boundaries.
