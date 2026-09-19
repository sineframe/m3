from pathlib import Path

import pytest
from _local_client import TestClient
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
