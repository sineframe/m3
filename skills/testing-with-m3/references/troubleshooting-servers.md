<!-- Generated from docs/site/troubleshooting/servers.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Server startup and connection failures

## Stdio process exits during initialization

The test fails with `TransportError: MCP transport operation failed:
initialize`, followed by the next step. M3 discards the server's stderr and
never stores it, so the test output cannot show the server's own error.

Run the configured command directly from the configured working directory.
If it reports a missing module, install your server into the environment that
runs the tests: `uv pip install --python .venv/bin/python -e .` from the
server's project root. `m3 setup` installs only the M3 SDK. See
[test your own server](start-your-server.md#make-your-server-launchable).

`TransportStartupError: stdio MCP server startup failed` means the command
itself could not be started. Check that it exists and is on `PATH`.

Check that its stdout contains only MCP protocol traffic; write diagnostics to
stderr. Confirm every argument is a separate `StdioServer.args` value.

Resolve server and manifest paths from `Path(__file__).resolve()`, because the
subprocess may run from another working directory. `-m package.module` is
also stable.

## HTTP connection fails

Start the endpoint separately and verify the exact Streamable HTTP URL. A URL
to the service root may not be the MCP route. Add endpoint credentials through
`HTTPServer.headers`.

## Nonlocal HTTP trust error

Classify a public or trusted private endpoint explicitly. A direct
`HTTPServer(name=..., url=...)` defaults to `TrustLevel.UNTRUSTED`, so
`kit.direct(...)` raises `EndpointTrustError` ("untrusted MCP endpoint resolved
to a private or local address") for a loopback or private URL. Pass
`trust=TrustLevel.TRUSTED_PRIVATE`, as in
[Test a Streamable HTTP server](guides-servers-http.md).

Omitting trust is accepted only for servers selected through
`servers=[{"type": "http", ...}]` in the `m3` marker or through
`m3 test --server http --url URL` without `--trust`, and only when the URL host
is `localhost` or a loopback IP address. The connection is then treated as
`TRUSTED_PRIVATE` and must resolve exclusively to loopback. Supplying `trust`
in either form skips this inference.

A nonlocal HTTP agent server defaults to `untrusted`, which cannot be exposed
to an agent. Use `TrustLevel.PUBLIC` or `TRUSTED_PRIVATE`. A direct public
endpoint may keep the default.
