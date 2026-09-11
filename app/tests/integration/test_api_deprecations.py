from pathlib import Path

from mcp_pal_app.api import create_app
from mcp_pal_app.settings import Settings

DEPRECATED_V1_OPERATIONS = {
    ("post", "/api/v1/runs"): "POST /api/v2/executions",
    ("get", "/api/v1/runs"): "GET /api/v2/executions",
    ("get", "/api/v1/runs/{run_id}"): "GET /api/v2/executions/{execution_id}",
    ("post", "/api/v1/runs/{run_id}/cancel"): (
        "POST /api/v2/executions/{execution_id}/cancel"
    ),
    ("delete", "/api/v1/runs/{run_id}"): "DELETE /api/v2/executions/{execution_id}",
    ("get", "/api/v1/runs/{run_id}/report"): (
        "GET /api/v2/executions/{execution_id}/report"
    ),
}


def test_only_v1_operations_with_v2_replacements_are_deprecated(tmp_path):
    app = create_app(
        Settings(database_path=str(Path(tmp_path).resolve() / "deprecations.sqlite"))
    )
    paths = app.openapi()["paths"]

    deprecated_v1_operations = {
        (method, path)
        for path, operations in paths.items()
        if path.startswith("/api/v1/")
        for method, operation in operations.items()
        if method in {"get", "post", "delete", "patch", "put"}
        and operation.get("deprecated", False)
    }
    assert deprecated_v1_operations == set(DEPRECATED_V1_OPERATIONS)

    for operation_key, replacement in DEPRECATED_V1_OPERATIONS.items():
        method, path = operation_key
        operation = paths[path][method]
        assert operation["deprecated"] is True
        assert replacement in operation["description"]

    assert paths["/api/v1/runs"]["get"]["description"] == (
        "Deprecated for new executions; use GET /api/v2/executions; legacy v1 "
        "history and the full legacy filter set are not exposed through v2."
    )

    # These routes have active callers or no v2 replacement and must remain
    # available without suggesting a migration path that does not exist.
    for path, method in (
        ("/api/v1/health", "get"),
        ("/api/v1/capabilities", "get"),
        ("/api/v1/profiles", "get"),
        ("/api/v1/profiles/{profile_id}", "get"),
        ("/api/v1/runs/{run_id}/events", "get"),
        ("/api/v1/runs/{run_id}/clone", "post"),
        ("/api/v1/runs", "delete"),
    ):
        assert paths[path][method].get("deprecated", False) is False
