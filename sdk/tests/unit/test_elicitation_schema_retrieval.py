"""Form schemas from MCP servers must never cause network retrieval."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from m3._mrtr import validate_response
from m3.elicitation import (
    ElicitationResponse,
    FormElicitationRequest,
    PendingElicitationRound,
)
from m3.errors import ElicitationExpectationError, ManagedInputValidationError
from m3.storage import SQLiteManagedInputStore


@contextmanager
def _loopback_schema_server() -> Iterator[tuple[str, list[str]]]:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            body = b'{"type":"object"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/schema.json", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_direct_form_validation_rejects_remote_ref_without_request() -> None:
    with _loopback_schema_server() as (schema_url, requests):
        request = FormElicitationRequest(
            request_key="form",
            message="Fill in the form",
            requested_schema={"$ref": schema_url},
        )
        response = ElicitationResponse(action="accept", content={})

        with pytest.raises(ElicitationExpectationError):
            validate_response(request, response)

        assert requests == []


def test_managed_form_validation_rejects_remote_ref_without_request(
    tmp_path: Path,
) -> None:
    with _loopback_schema_server() as (schema_url, requests):
        request = FormElicitationRequest(
            request_key="form",
            message="Fill in the form",
            requested_schema={"$ref": schema_url},
        )
        pending = PendingElicitationRound(
            round_id="round-1",
            execution_id="execution-1",
            logical_operation_id="operation-1",
            server="loopback",
            operation_kind="tool",
            operation_name="submit",
            requests={"form": request},
            created_at=datetime.now(timezone.utc),
        )
        store = SQLiteManagedInputStore(tmp_path / "managed-input.sqlite")
        created = store.create_round(
            pending,
            round_index=0,
            round_limit=1,
            owner_id="worker",
            lease_seconds=30,
        )

        with pytest.raises(ManagedInputValidationError, match="schema"):
            store.submit_responses(
                "execution-1",
                "round-1",
                {"form": ElicitationResponse(action="accept", content={})},
                owner_id="worker",
                lease_token=created.lease_token,
                response_idempotency_key="response-1",
            )

        assert requests == []


def test_local_form_refs_validate_in_direct_and_managed_paths(tmp_path: Path) -> None:
    schema = {
        "$defs": {"answer": {"type": "string"}},
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"$ref": "#/$defs/answer"}},
    }
    request = FormElicitationRequest(
        request_key="form", message="Fill in the form", requested_schema=schema
    )
    response = ElicitationResponse(
        action="accept", content={"answer": "local reference works"}
    )

    validate_response(request, response)

    pending = PendingElicitationRound(
        round_id="round-1",
        execution_id="execution-1",
        logical_operation_id="operation-1",
        server="loopback",
        operation_kind="tool",
        operation_name="submit",
        requests={"form": request},
        created_at=datetime.now(timezone.utc),
    )
    store = SQLiteManagedInputStore(tmp_path / "managed-input.sqlite")
    created = store.create_round(
        pending,
        round_index=0,
        round_limit=1,
        owner_id="worker",
        lease_seconds=30,
    )
    submitted = store.submit_responses(
        "execution-1",
        "round-1",
        {"form": response},
        owner_id="worker",
        lease_token=created.lease_token,
        response_idempotency_key="response-1",
    )
    assert submitted.status == "response_validated"
