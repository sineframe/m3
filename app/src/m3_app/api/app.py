"""FastAPI composition for the application-owned v2 runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib.metadata import version as distribution_version
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from m3_app.local_security import install_local_security
from m3_app.services.app_service import AppRuntimeService
from m3_app.settings import Settings, get_settings

from .v2 import install_v2


def _package_version() -> str:
    return distribution_version("m3")


def _create_app(
    settings: Settings | None = None,
    engine_override: Any = None,
    session_factory: Any = None,
    v2_store: Any = None,
    v2_kit: Any = None,
    *,
    v2_embedded_worker: bool = True,
    runtime: AppRuntimeService | None = None,
) -> FastAPI:
    """Build the API around one application runtime.

    The first two parameters are inert compatibility parameters for callers
    of the retired SQLAlchemy composition. They are intentionally unused.
    """
    del engine_override, session_factory
    settings = settings or get_settings()
    if runtime is None:
        runtime = AppRuntimeService(
            settings,
            store=v2_store,
            kit=v2_kit,
            embedded_worker=v2_embedded_worker,
        )
    elif v2_store is not None or v2_kit is not None:
        raise ValueError("runtime cannot be combined with injected v2 resources")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            application.state.runtime.close()

    app = FastAPI(
        title="Test Results API",
        version=_package_version(),
        description=(
            "Local, unauthenticated API for test execution results. "
            "Bind this service to loopback (127.0.0.1); the application "
            "enforces loopback clients, an approved Host header, and same-origin "
            "browser mutations. Use /docs for Swagger UI, /redoc for ReDoc, "
            "and /openapi.json for the generated OpenAPI 3.1 contract."
        ),
        # Relative URL keeps the schema correct for uvicorn's configurable
        # port and for the bundled CLI launcher.
        servers=[{"url": "/", "description": "Local app origin"}],
        openapi_tags=[
            {
                "name": "control-plane-v2",
                "description": "Profiles, probes, capabilities, and local readiness.",
            },
            {
                "name": "executions-v2",
                "description": "Submit, inspect, cancel, report, and delete asynchronous executions.",
            },
            {
                "name": "suites-v2",
                "description": "Saved executions belonging to a suite.",
            },
            {
                "name": "evaluations-v2",
                "description": "Read-only aggregation of saved evaluation results.",
            },
            {
                "name": "feedback-v2",
                "description": "Read-only feedback projections for saved runs.",
            },
            {
                "name": "evidence-v2",
                "description": "Read bounded, redacted evidence from saved runs.",
            },
        ],
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    # Preserve the useful injected-resource seams while making runtime the
    # sole owner of store, toolkit, and lifecycle closure.
    app.state.v2_store = runtime.store
    app.state.v2_kit = runtime.kit
    app.state.v2_kit_owned = runtime.owns_kit
    app.state.v2_store_owned = runtime.owns_store
    app.state.settings = runtime.settings
    install_v2(app, runtime, embedded_worker=v2_embedded_worker)
    return app


def create_app(
    settings: Settings | None = None,
    engine_override: Any = None,
    session_factory: Any = None,
    v2_store: Any = None,
    v2_kit: Any = None,
    *,
    v2_embedded_worker: bool = True,
    runtime: AppRuntimeService | None = None,
) -> FastAPI:
    """Create the supported local v2 API with its security boundary."""
    application = _create_app(
        settings,
        engine_override=engine_override,
        session_factory=session_factory,
        v2_store=v2_store,
        v2_kit=v2_kit,
        v2_embedded_worker=v2_embedded_worker,
        runtime=runtime,
    )
    install_local_security(application)
    return application


def create_viewer_app(
    settings: Settings | None = None,
    engine_override: Any = None,
    session_factory: Any = None,
    v2_store: Any = None,
    v2_kit: Any = None,
    *,
    runtime: AppRuntimeService | None = None,
) -> FastAPI:
    """Create the history viewer's read-only v2 application."""
    application = _create_app(
        settings,
        engine_override=engine_override,
        session_factory=session_factory,
        v2_store=v2_store,
        v2_kit=v2_kit,
        v2_embedded_worker=False,
        runtime=runtime,
    )
    allowed_post_paths = frozenset(
        {"/api/v2/evidence/read", "/api/v2/evaluations/aggregate"}
    )

    @application.middleware("http")
    async def enforce_viewer_read_only(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not (
            request.method == "POST" and request.url.path in allowed_post_paths
        ):
            return JSONResponse(
                status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
                content={"detail": "viewer API is read-only"},
                headers={"Allow": "GET, HEAD, OPTIONS"},
            )
        return await call_next(request)

    application.state.viewer_read_only = True
    install_local_security(application)
    return application


__all__ = ["create_app", "create_viewer_app"]
