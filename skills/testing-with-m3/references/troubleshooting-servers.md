<!-- Generated from docs/site/troubleshooting/servers.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Server startup and connection failures

## Stdio process exits during initialization

Run the configured command directly from the configured working directory.
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

Classify a public or trusted private endpoint explicitly. Omitting trust is
accepted only for addresses that resolve exclusively to loopback.

A nonlocal HTTP agent server defaults to `untrusted`, which cannot be exposed
to an agent. Use `TrustLevel.PUBLIC` or `TRUSTED_PRIVATE`. A direct public
endpoint may keep the default.
