# M3 documentation index

<!-- Generated from docs/site/navigation.json by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

## Getting started

- [Write your first MCP test](getting-started.md): This walkthrough starts a small MCP server as a local process, discovers its shipping_quote tool, calls it, and checks the structured response. You will also make the assertion fail, restore it, and open the saved run.

## Start

- [Install and update M3](start-install.md): Install the M3 CLI and the project SDK separately. The CLI runs m3 and includes the local results viewer. The SDK runs inside the Python project being tested.
- [Test your own server](start-your-server.md): Use a StdioServer when M3 should start your local server process for each test connection. Use an HTTPServer when the MCP endpoint is already running. Both direct paths can run without an agent provider.

## Concepts

- [Evidence and traces](concepts-evidence.md): M3 records what it can observe at the transport and harness boundaries. A recorded tool call is evidence that the call crossed an observed boundary; an agent's prose saying that it used a tool is not.
- [Runs, suites, cases, and executions](concepts-identity.md): A run is one invocation of pytest or one explicit SDK run ID. The CLI prints its run ID and writes the corresponding feedback under .m3/reports/<run-id>/feedback.json.
- [Isolation and repeatability](concepts-isolation.md): Each execution receives its own identity and trace. Trials are independent executions; they are not automatic retries. A server subprocess can still retain state for the lifetime of one connection, and an external HTTP service can retain state beyond the test.
- [Runtime and connection lifecycle](concepts-lifecycle.md): MCPTestKit owns the surrounding runtime. A direct client owns one initialized MCP connection. Entering the client starts its transport; leaving it closes the connection and any subprocess that client started.
- [Test, execution, and evaluation outcomes](concepts-outcomes.md): M3 keeps three judgments separate:
- [Direct tests and agent tests](concepts-testing-model.md): M3 supports two different questions.

## Guides

- [Use the async SDK](guides-advanced-async.md): Use AsyncMCPTestKit when the surrounding test is asynchronous. Await direct operations instead of creating another event loop.
- [Use mocks and replay](guides-advanced-mocks-replay.md): Use a mock to test client behavior against a controlled MCP exchange.
- [Run M3 without pytest](guides-advanced-scripts.md): The SDK can run in a normal Python program. Persistence is in memory unless you select a store.
- [Snapshot stable values](guides-advanced-snapshots.md): Use snapshot for a public value whose full stable projection is meaningful to review. Prefer field assertions when only a few behaviors matter.
- [Bring your own agent](guides-agents-acp.md): Connect an existing ACP-compatible agent or expose a custom agent through ACP.
- [Connect an ACP-compatible agent](guides-agents-acp-connect.md): Launch your existing ACP agent with M3 and check the MCP server result.
- [Expose your custom agent through ACP](guides-agents-acp-wrapper.md): Use the ACP SDK to wrap agent logic and check its captured MCP response.
- [Test an agent’s tool use](guides-agents-first-test.md): This test starts a local MCP server, gives Codex access to one named tool, and checks the recorded call. It verifies the interaction M3 observed; it does not prove that the agent’s final prose is correct.
- [Choose an agent harness](guides-agents-harnesses.md): M3 connects to agent harnesses through native adapters or an ACP manifest. Choose the integration your agent provides, then check the capabilities and version your test needs.
- [Run a pinned agent harness](guides-agents-managed-runtimes.md): Acquire an explicit native harness version, run an agent test, and check the requested and resolved runtime identity recorded by M3.
- [Run agent cases as a matrix](guides-agents-matrices.md): Use HarnessMatrix to run the same MCP cases across harnesses and trials. A matrix expands test work; it does not make model behavior deterministic.
- [Test a multi-turn agent session](guides-agents-sessions.md): An agent session keeps one conversation open across turns. Use it when later prompts depend on earlier work, and assert evidence against the turn that produced it.
- [Compare agent harnesses and versions](guides-agents-versions.md): Run the same test across pinned agent harnesses in isolated runtimes and compare each execution's recorded tool-call result.
- [Manage M3 access](guides-ci-access.md): Use device authorization for local uploads, or create and store a separate CI token for automated uploads.
- [Run M3 in GitHub Actions](guides-ci-github-actions.md): Run M3 tests in a consumer repository with a credential-free pull request workflow and an optional trusted upload workflow.
- [Publish or retry a run](guides-ci-publish.md): Publishing is opt-in. Add --upload to m3 test after m3 auth login, or to m3 ci test with M3_ACCESS_TOKEN in CI.
- [Run M3 tests in CI](guides-ci-run.md): m3 ci test uses the normal project Python, storage, harness, and pytest selection. It excludes tests whose nearest M3 marker sets ci=False.
- [Configure credentials](guides-credentials.md): Choose a credential source and pass it only to the M3 process that needs it.
- [Pass a credential to a stdio MCP server](guides-credentials-endpoints.md): Map a parent environment variable to a stdio server and verify its tool result.
- [Submit input to a paused execution](guides-elicitation-managed-input.md): Managed input lets an agent execution pause while it waits for a person’s response. The caller reads the persisted request, submits a response keyed by the request name, then waits for the worker to finish.
- [Plan answers to elicitation requests](guides-elicitation-plans.md): An elicitation plan describes the requests an operation may make and the response M3 should submit. Attach the plan to the action that can trigger those requests, then assert the action’s result.
- [Aggregate saved evaluations](guides-evaluations-aggregate.md): Aggregate records from one reader-created run so older rows in the same database do not alter the result.
- [Assert the evidence that matters](guides-evaluations-assertions.md): Use ordinary Python assertions for direct operation results. Use expect when you need to ask a question about a complete M3 execution or agent turn.
- [Write a custom evaluator](guides-evaluations-custom.md): A custom evaluator turns an explicit subject into a named evaluation result. It does not run automatically because an execution completed.
- [Evaluate a response with an LLM judge](guides-evaluations-judges.md): An LLM judge sends the selected subject text to its configured endpoint. Use it for criteria that cannot be expressed as deterministic assertions, and avoid sending private data unless that transfer is intended.
- [Compare a run with a baseline](guides-results-baselines.md): --baseline compares a new feedback bundle with an earlier run in the same results database. Capture the ID produced by your own first run; IDs printed in documentation do not exist in your database.
- [Save and reopen executions](guides-results-persistence.md): Direct SDK use keeps data in memory unless you choose a persistent store. The CLI chooses SQLite automatically.
- [Find slow steps in a test run](guides-results-timings.md): Set M3_TIMINGS=1 to record where time goes in an m3 test run: a summary table, a per-step JSON file, and a trace you can open in Perfetto.
- [Read a finalized trace](guides-results-traces.md): Use the operation result for the immediate response and the finalized trace for the complete observed execution.
- [Open saved test results](guides-results-viewer.md): The standalone CLI includes a local viewer for runs saved in the default project database.
- [Test tool errors and input schemas](guides-servers-errors-schemas.md): An MCP tool can report an expected application error as a normal call result. When schema validation is enabled, M3 can also reject test arguments that do not match the tool's advertised input schema before sending the call.
- [Test a Streamable HTTP server](guides-servers-http.md): Use HTTPServer for an MCP endpoint that is already running. The direct test below uses a local deterministic service, so it does not depend on a public endpoint or network service.
- [Test resources and prompts](guides-servers-resources-prompts.md): Use resource operations to read server data. Use prompt operations to retrieve a named message template and supply its declared arguments. These operations do not require an agent harness.
- [Test state across tool calls](guides-servers-stateful-tests.md): Keep dependent calls inside one direct-client context when a later operation uses state created by an earlier operation. This example captures the server-returned customer and order identifiers and passes them to subsequent calls.
- [Test a stdio server](guides-servers-stdio.md): Use StdioServer when M3 should launch a local MCP server process for the test. M3 starts it for the connection and closes the process when the direct client closes.
- [Test MCP tools](guides-servers-tools.md): Test a tool in two steps: discover its advertised schema, then call it with known arguments and check the result that matters to your application.

## Reference

- [Reference](reference.md): Use reference pages when you already know what you need to look up. For a working sequence, start with a guide instead.
- [ACP reference](reference-acp.md): Manifest, launch, session, policy, evidence, and failure behavior for ACP agents.
- [CLI reference](reference-cli.md): The standalone m3 command selects the project Python and coordinates pytest, saved history, the local viewer, managed harnesses, authentication, and report publishing. Options after -- are passed to pytest unchanged.
- [Compatibility and current boundaries](reference-compatibility.md): These docs describe the release shown in the site header. The SDK requires Python 3.10 or newer. A CLI-managed project must use the matching SDK version.
- [Configuration](reference-configuration.md): Project identity and environment-file loading behavior.
- [Credential reference](reference-credentials.md): Resolution and validation behavior for endpoint, agent, judge, ACP, and upload credentials.
- [Managed harness runtimes](reference-managed-runtimes.md): Reference for native harness selection, version resolution, executable acquisition, provenance, cache configuration, leases, and failure behavior.
- [CLI output and trace limitations](reference-output.md): What each M3 line printed after a pytest run means, and which trace limitations are routine.
- [pytest integration](reference-pytest.md): Install sf-m3[pytest] or run m3 setup. The plugin adds M3 fixtures, selection, storage integration, and feedback generation while leaving ordinary pytest selection after -- intact.
- [Python SDK reference](reference-python.md): Start with the small package-root surface for ordinary synchronous tests:
- [Core execution API](reference-python-m3-core.md): The synchronous runtime and cleanup boundary. Construct it with optional environment, store, evaluator, runtime, and harness-cache configuration. Enter it before opening direct clients or agent sessions and close it after all work.
- [Elicitation and managed-input API](reference-python-m3-elicitation.md): ElicitationPlan describes expected form or URL input requests and the responses M3 may submit. Build plans with:
- [Exceptions](reference-python-m3-errors.md): Normal MCP tool errors usually remain typed tool results rather than raised exceptions. Test result.is_error when the server intentionally returned a tool-level failure.
- [Evaluation API](reference-python-m3-evaluations.md): An Evaluator or AsyncEvaluator receives EvaluationContext and returns an EvaluationDecision. EvaluatorCallable is the accepted callback union.
- [Matchers](reference-python-m3-matchers.md): expect(subject) creates an Expectation whose methods raise AssertionError when captured evidence does not satisfy the predicate. check(subject) records failures through a CheckGroup, allowing several related checks to be reported together when the plugin has a recording binding.
- [Matrix API](reference-python-m3-matrix.md): ToolCase names one tool invocation and its arguments. ServerCase groups tool cases under the server that owns them. ToolMatrix validates and expands those cases without starting processes or network work.
- [Observability models](reference-python-m3-observability.md): TraceView is the stable typed projection for reading a finalized trace. Its entries preserve whether a value was observed, reported, inferred, redacted, or unavailable.
- [Storage API](reference-python-m3-storage.md): InMemoryExecutionStore is the default direct-SDK execution store. SQLiteExecutionStore persists execution specs, snapshots, traces, sessions, turns, evaluations, profiles, and test-run records when the storage extra is installed.
- [Testing utilities](reference-python-m3-testing.md): MockMCPServer and ExpectedCall define a controlled protocol interaction. Unexpected calls raise MockExpectationError; invalid mock protocol behavior raises MockProtocolError.
- [Public value types](reference-python-m3-types.md): Public values are immutable, serializable contracts. Construct server and operation specifications as inputs; treat result, trace, evidence, and report models as outputs.

## Troubleshooting

- [Troubleshooting](troubleshooting.md): Start with the symptom:
- [Agent readiness, approvals, and timeouts](troubleshooting-agents.md): Use m3 doctor --require harness:NAME for a system runtime, or select a managed runtime/version supported on the current OS and CPU.
- [CI credentials and uploads](troubleshooting-ci.md): An ambient variable takes precedence even when its value is empty. Unset it if the file should supply the value. M3 does not interpolate dotenv values.
- [Installation and project Python](troubleshooting-install.md): Run m3 setup from the project root. It installs the SDK version matching the standalone CLI into the selected isolated environment. If M3 selected the wrong environment, pass --python PATH to setup and doctor.
- [Missing traces or saved results](troubleshooting-results.md): Use m3 test, or configure SQLiteExecutionStore explicitly in direct SDK code. Plain pytest without the storage plugin keeps SDK executions in memory.
- [Server startup and connection failures](troubleshooting-servers.md): Run the configured command directly from the configured working directory. Check that its stdout contains only MCP protocol traffic; write diagnostics to stderr. Confirm every argument is a separate StdioServer.args value.

## CLI

- [M3 CLI](cli.md): Install and use the standalone M3 command to initialize projects, run pytest with selected agents and servers, and inspect saved results.
