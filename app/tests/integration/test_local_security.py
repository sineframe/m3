from pathlib import Path

import pytest
from _local_client import TestClient
from starlette.applications import Starlette
from starlette.websockets import WebSocketDisconnect

from m3_app.api import create_app
from m3_app.local_security import LocalSecurityMiddleware
from m3_app.settings import Settings


def test_local_app_installs_host_and_browser_mutation_boundary(tmp_path: Path) -> None:
    application = create_app(Settings(database_path=str(tmp_path / "local.sqlite")))
    assert any(
        middleware.cls is LocalSecurityMiddleware
        for middleware in application.user_middleware
    )

    with TestClient(
        application,
        base_url="http://127.0.0.1:8123",
        client=("127.0.0.1", 50000),
    ) as client:
        assert client.get("/api/v2/executions").status_code == 200
        assert (
            client.get(
                "/api/v2/executions", headers={"host": "attacker.example:8123"}
            ).status_code
            == 400
        )
        path = "/api/v2/profiles/nonexistent-profile/archive"
        assert (
            client.post(
                path,
                headers={
                    "origin": "https://attacker.example",
                    "sec-fetch-site": "cross-site",
                },
            ).status_code
            == 403
        )
        same_origin = client.post(
            path,
            headers={
                "origin": "http://127.0.0.1:8123",
                "sec-fetch-site": "same-origin",
            },
        )
        assert same_origin.status_code == 404


def test_local_app_rejects_dns_rebinding_origin_before_route_dispatch(
    tmp_path: Path,
) -> None:
    application = create_app(Settings(database_path=str(tmp_path / "rebind.sqlite")))
    with TestClient(
        application,
        base_url="http://127.0.0.1:8123",
        client=("127.0.0.1", 50000),
    ) as client:
        response = client.post(
            "/api/v2/harness-profiles",
            headers={
                "host": "attacker.example:8123",
                "origin": "http://attacker.example:8123",
                "sec-fetch-site": "same-origin",
            },
            json={
                "name": "must-not-persist",
                "trusted_unsandboxed": True,
                "manifest": {"command": "/bin/sh", "args": [], "env": {}},
            },
        )
        assert response.status_code == 400
        assert client.get("/api/v2/harness-profiles").json() == []


def test_local_app_rejects_non_loopback_peer_even_with_local_host(
    tmp_path: Path,
) -> None:
    application = create_app(Settings(database_path=str(tmp_path / "remote.sqlite")))
    with TestClient(
        application,
        base_url="http://localhost:8123",
        client=("192.0.2.10", 50000),
    ) as client:
        response = client.get("/api/v2/executions")
    assert response.status_code == 403


def test_local_app_rejects_cross_origin_websocket_handshake(tmp_path: Path) -> None:
    application = create_app(Settings(database_path=str(tmp_path / "websocket.sqlite")))
    with TestClient(
        application,
        base_url="http://localhost:8123",
        client=("127.0.0.1", 50000),
    ) as client:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(
                "/socket", headers={"origin": "https://attacker.example"}
            ):
                pass
    assert rejected.value.code == 1008


def test_token_protects_all_api_paths_but_not_public_routes(tmp_path: Path) -> None:
    application = create_app(
        Settings(database_path=str(tmp_path / "authenticated.sqlite")),
        auth_token="test-token-123",
    )

    @application.get("/public")
    def public_route() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(application) as client:
        for method, path in (
            ("GET", "/api/v2/health"),
            ("POST", "/api/v2/profiles"),
            ("GET", "/api/not-a-known-route"),
        ):
            response = client.request(method, path)
            assert response.status_code == 401
            assert response.json() == {"detail": "Unauthorized"}
            assert response.headers["www-authenticate"] == "Bearer"

        assert client.get("/public").json() == {"ok": True}
        assert (
            client.get(
                "/api/v2/health", headers={"Authorization": "Bearer test-token-123"}
            ).status_code
            == 200
        )


def test_token_protects_api_routes_when_app_is_mounted_under_prefix(
    tmp_path: Path,
) -> None:
    protected_app = create_app(
        Settings(database_path=str(tmp_path / "mounted-auth.sqlite")),
        auth_token="test-token-123",
    )
    parent = Starlette()
    parent.mount("/prefix", protected_app)

    with TestClient(parent) as client:
        for path, authorized_status in (
            ("/prefix/api/v2/health", 200),
            ("/prefix/api/v2/executions", 200),
            ("/prefix/api/not-a-known-route", 404),
        ):
            response = client.get(path)
            assert response.status_code == 401
            assert response.json() == {"detail": "Unauthorized"}

            authorized = client.get(
                path, headers={"Authorization": "Bearer test-token-123"}
            )
            assert authorized.status_code == authorized_status


@pytest.mark.parametrize(
    "authorization_values",
    [
        [],
        ["Basic test-token-123"],
        ["Bearer"],
        ["Bearer "],
        ["Bearer test-token-123 extra"],
        ["Bearer wrong-token"],
        ["Bearer test-token-123", "Bearer test-token-123"],
    ],
)
def test_token_auth_rejects_malformed_or_duplicate_headers(
    tmp_path: Path, authorization_values: list[str]
) -> None:
    application = create_app(
        Settings(database_path=str(tmp_path / "invalid-token.sqlite")),
        auth_token="test-token-123",
    )
    headers = [("authorization", value) for value in authorization_values]
    with TestClient(application) as client:
        response = client.get("/api/v2/health", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_token_auth_runs_after_existing_host_and_origin_checks(tmp_path: Path) -> None:
    application = create_app(
        Settings(database_path=str(tmp_path / "auth-order.sqlite")),
        auth_token="test-token-123",
    )
    with TestClient(application) as client:
        bad_host = client.get("/api/v2/health", headers={"host": "attacker.example"})
        assert bad_host.status_code == 400
        bad_origin = client.post(
            "/api/v2/profiles",
            headers={
                "origin": "https://attacker.example",
                "sec-fetch-site": "cross-site",
            },
        )
        assert bad_origin.status_code == 403


def test_token_security_is_documented_only_when_enabled(tmp_path: Path) -> None:
    unauthenticated = create_app(
        Settings(database_path=str(tmp_path / "openapi-public.sqlite"))
    ).openapi()
    authenticated = create_app(
        Settings(database_path=str(tmp_path / "openapi-protected.sqlite")),
        auth_token="test-token-123",
    ).openapi()

    assert "securitySchemes" not in unauthenticated.get("components", {})
    assert "security" not in unauthenticated["paths"]["/api/v2/health"]["get"]
    assert authenticated["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert authenticated["paths"]["/api/v2/health"]["get"]["security"] == [
        {"BearerAuth": []}
    ]
