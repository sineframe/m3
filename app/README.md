# MCP Pal app

`mcp-pal-app` is the repository's internal application package. It contains the
FastAPI viewer API, application services, and persistence adapters. It is not a
public package that MCP Pal users install directly.

The standalone [`mcp-pal-cli`](../cli/README.md) installs the matching app
runtime in its isolated environment and bundles the production frontend. The
[`mcp-pal` SDK](../sdk/README.md) remains the public testing API and owns the
execution, trace, and assertion contracts used by the app.

## Develop the app

From the repository root, install the complete workspace:

```bash
just setup
```

Start the FastAPI development server with:

```bash
just api
```

The maintained [API v2 capability guide](docs/api-v2.md) lists every route,
request and response shape, persistence behavior, and example flow.

Its OpenAPI documentation is available at `http://127.0.0.1:8000/docs` by
default. The supported browser experience is the compiled SPA bundled with the
standalone CLI.

The app exposes the read-only route
`GET /api/v2/feedback/{run_id}?baseline_run_id=...`. It reads the SDK's saved
test manifest and execution history and returns the same `Feedback` projection
used by CLI exports. No UI is implemented in this package; the production UI
is bundled separately with the CLI.

Copy [`.env.example`](../.env.example) to `.env` only for local development that
needs configured harnesses. Credentials and live-provider tests are optional.
The application does not implicitly load a working-directory `.env`; the
`just api` recipe uses ambient environment variables. To select the file
explicitly, run:

```bash
uv run --env-file .env --project app uvicorn mcp_pal_app.main:app --host 127.0.0.1 --reload
```

The exported ASGI application is an unauthenticated local service. It rejects
non-loopback peers and Host headers as well as cross-origin browser mutations,
and must remain bound to loopback. A non-loopback deployment requires a separate
authenticated gateway and is not a supported replacement for the local security
boundary.

## Test the app

Run the app suite directly:

```bash
uv run --locked --project app --group test --group typecheck \
  pytest app/tests
```

Or run all three workspace suites with `just test`. To reset the disposable
development database, use the guarded repository recipe:

```bash
just CONFIRM=reset dev-db-reset
```

Product installation and viewer troubleshooting belong in the
[CLI guide](../cli/README.md).
