from __future__ import annotations

from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from fastapi.testclient import TestClient

from mcp_pal_cli import web
from mcp_pal_cli.web import create_web_app, ui_directory

_FIXTURE_UI = Path(__file__).parent / "fixtures" / "ui"


def test_ui_directory_requires_index_and_assets(tmp_path: Path) -> None:
    try:
        ui_directory(tmp_path)
    except ValueError as error:
        assert str(error) == "bundled MCP Pal UI assets are unavailable"
    else:
        raise AssertionError("missing UI bundle was accepted")


def test_full_app_uses_same_database_and_serves_spa_without_api_fallback(
    tmp_path: Path,
) -> None:
    database = tmp_path / "shared.sqlite"
    application = create_web_app(database, ui_dir=_FIXTURE_UI)
    assert not any(
        getattr(route, "path", "").startswith("/api/cli")
        for route in application.routes
    )
    assert application.state.settings.database_path == str(database.absolute())
    assert application.state.v2_store_owned is True
    assert application.state.v2_kit._embedded_worker is True
    with TestClient(
        application,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    ) as client:
        executions = client.get("/api/v2/executions")
        assert executions.status_code == 200
        assert executions.headers["content-type"].startswith("application/json")
        assert executions.json() is not None
        assert client.get("/history").status_code == 200
        assert client.get("/playground/run/run%20id").status_code == 200
        assert client.get("/assets/app.js").status_code == 200
        unknown_api = client.get("/api/does-not-exist")
        assert unknown_api.status_code == 404
        assert unknown_api.headers["content-type"].startswith("application/json")
        assert "<!doctype html>" not in unknown_api.text.lower()


def test_local_host_and_origin_protection(tmp_path: Path) -> None:
    application = create_web_app(tmp_path / "shared.sqlite", ui_dir=_FIXTURE_UI)
    with TestClient(
        application,
        base_url="http://127.0.0.1:8123",
        client=("127.0.0.1", 50000),
    ) as client:
        assert client.get("/api/v2/executions").status_code == 200
        assert (
            client.get(
                "/api/v2/executions", headers={"host": "attacker.example"}
            ).status_code
            == 400
        )
        assert (
            client.get("/api/v2/executions", headers={"host": "[::1]"}).status_code
            == 200
        )

        # Same-origin writes are allowed to reach the application.  This
        # endpoint has no matching record, so its validation response proves
        # the security middleware did not reject it.
        same_origin = client.delete(
            "/api/v2/executions/missing",
            headers={
                "origin": "http://127.0.0.1:8123",
                "sec-fetch-site": "same-origin",
            },
        )
        assert same_origin.status_code != 403
        cross_origin = client.delete(
            "/api/v2/executions/missing", headers={"origin": "http://localhost"}
        )
        assert cross_origin.status_code == 403
        assert cross_origin.headers["content-type"].startswith("application/json")
        assert (
            client.delete(
                "/api/v2/executions/missing", headers={"origin": "https://127.0.0.1"}
            ).status_code
            == 403
        )
        assert (
            client.delete(
                "/api/v2/executions/missing", headers={"sec-fetch-site": "cross-site"}
            ).status_code
            == 403
        )
        assert (
            client.delete(
                "/api/v2/executions/missing",
                headers={"origin": "http://127.0.0.1/path"},
            ).status_code
            == 403
        )
        assert (
            client.delete(
                "/api/v2/executions/missing", headers={"origin": "http://[::1"}
            ).status_code
            == 403
        )


def test_same_origin_default_ports_are_normalized(tmp_path: Path) -> None:
    application = create_web_app(tmp_path / "shared.sqlite", ui_dir=_FIXTURE_UI)
    with TestClient(
        application,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    ) as client:
        response = client.delete(
            "/api/v2/executions/missing", headers={"origin": "http://localhost:80"}
        )
        assert response.status_code != 403


def test_cli_and_app_do_not_depend_on_removed_ui_runtime() -> None:
    root = Path(__file__).parents[2]
    with (root / "cli" / "pyproject.toml").open("rb") as stream:
        cli_metadata = tomllib.load(stream)["project"]
    with (root / "app" / "pyproject.toml").open("rb") as stream:
        app_metadata = tomllib.load(stream)["project"]

    cli_dependencies = " ".join(cli_metadata["dependencies"]).lower()
    app_dependencies = " ".join(app_metadata["dependencies"]).lower()
    assert "streamlit" not in cli_dependencies
    assert "requests" not in cli_dependencies
    assert "streamlit" not in app_dependencies
    assert "requests" not in app_dependencies
    assert "legacy-ui" not in app_metadata.get("optional-dependencies", {})


def test_web_startup_error_is_bounded_and_redacted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "provider-secret-value"
    monkeypatch.setenv("OPENCODE_API_KEY", secret)
    monkeypatch.setattr(
        web,
        "create_web_app",
        lambda _database: (_ for _ in ()).throw(RuntimeError(f"API_KEY={secret}")),
    )

    assert web.main(["--database-path", "results.sqlite", "--port", "8123"]) == 2
    error = capsys.readouterr().err
    assert "RuntimeError" in error
    assert secret not in error
