# Server startup and connection failures

## Stdio process exits during initialization

Run the configured command directly from the configured working directory.
Check that its stdout contains only MCP protocol traffic; write diagnostics to
stderr. Confirm every argument is a separate `StdioServer.args` value.

## HTTP connection fails

Start the endpoint separately and verify the exact Streamable HTTP URL. A URL
to the service root may not be the MCP route. Add endpoint credentials through
`HTTPServer.headers`.

## Nonlocal HTTP trust error

Classify a public or trusted private endpoint explicitly. Omitting trust is
accepted only for addresses that resolve exclusively to loopback.
