<!-- Generated from docs/site/concepts/isolation.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Isolation and repeatability

Each execution receives its own identity and trace. Trials are independent
executions; they are not automatic retries. A server subprocess can still
retain state for the lifetime of one connection, and an external HTTP service
can retain state beyond the test.

Make direct contract tests deterministic: use fixed inputs, assert structured
results, and reset owned state. Agent tests are inherently variable, so assert
captured behavior and run multiple trials when the variance matters.

Managed harness runtimes isolate the selected executable and writable runtime
state. They are not an operating-system sandbox. Workspace, terminal, and tool
policies still define what the harness may do.
