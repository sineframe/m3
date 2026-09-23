# M3 app

The standalone [M3 CLI](../cli/README.md) installs this app's FastAPI viewer
API, application services, and persistence adapters in its isolated environment.
The app's PyPI distribution is `sf-m3-app`; users install the CLI instead of
this support package directly. The CLI also bundles the production frontend. The
[`m3` SDK](../sdk/README.md) remains the public testing API and owns the
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

Its generated OpenAPI 3.1 documentation is available at
`http://127.0.0.1:8000/docs` (Swagger UI), `/redoc` (ReDoc), and
`/openapi.json` (machine-readable schema). The supported browser experience is
the compiled SPA bundled with the standalone CLI. The CLI uses the full
`create_app()` API. The separate `create_viewer_app()` history viewer publishes
read operations plus the read-only evidence and evaluation POSTs; other
mutation requests are rejected with 405.

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
uv run --env-file .env --project app uvicorn m3_app.main:app --host 127.0.0.1 --reload
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
