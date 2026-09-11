"""Durable specification/profile SecretReference regressions."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_pal.events import EventFactory
from mcp_pal.storage import (
    DurableSerializationError,
    SQLiteExecutionStore,
    serialize_durable,
)
from mcp_pal.trace.redaction import RedactionConfig
from mcp_pal.types import (
    DirectSpec,
    EventKind,
    ExecutionId,
    ExecutionState,
    Ping,
    SecretReference,
    ServerBinding,
    StdioServer,
)

pytestmark = [pytest.mark.e2e, pytest.mark.process_lifecycle]


def _spec() -> DirectSpec:
    return DirectSpec(
        servers=(
            ServerBinding(
                server=StdioServer(
                    name="secret-server",
                    command="mcp-server",
                    environment={
                        "SERVICE_TOKEN": SecretReference(
                            source="environment", name="SERVICE_TOKEN"
                        )
                    },
                )
            ),
        ),
        operation=Ping(server="secret-server"),
    )


def test_secret_reference_survives_sqlite_process_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "secret-references.sqlite"
    blob_root = tmp_path / "blobs"
    canary = "resolved-service-canary"
    store = SQLiteExecutionStore(
        database,
        blob_root=blob_root,
        config=RedactionConfig(secrets=frozenset({canary}), include_environment=False),
    )
    reference = SecretReference(source="environment", name="SERVICE_TOKEN")
    profile = store.create_server_profile(
        "secret-profile",
        {
            "mcpServers": {
                "secret-server": {
                    "command": "mcp-server",
                    "env": {"SERVICE_TOKEN": reference},
                },
                "secret-http": {
                    "type": "http",
                    "url": "https://example.test/mcp",
                    "headers": {"Authorization": reference},
                },
            }
        },
    )
    # Exercise immutable saved revisions as well as initial profile creation;
    # the child process reopens and validates the latest descriptor below. It
    # intentionally does not resolve a credential.
    store.add_revision(
        profile.id,
        {
            "mcpServers": {
                "secret-server": {
                    "command": "mcp-server",
                    "env": {"SERVICE_TOKEN": reference},
                },
                "secret-http": {
                    "type": "http",
                    "url": "https://example.test/mcp",
                    "headers": {"Authorization": reference},
                },
            }
        },
    )
    harness_profile = store.create_harness_profile(
        "secret-harness",
        {"command": "harness", "env": {"SERVICE_TOKEN": reference}},
    )
    spec = _spec().model_copy(update={"metadata": {"note": canary}})
    execution_id = ExecutionId("secret-execution")
    store.create(
        ExecutionState(execution_id=execution_id),
        specification=spec.model_dump(mode="json"),
        server_bindings=[spec.servers[0].model_dump(mode="json")],
    )
    command = store.enqueue_command(
        execution_id,
        payload={
            "spec": spec.model_dump(mode="json"),
            "bearer_token": reference,
            "note": canary,
        },
    )
    # Force a content-addressed evidence blob so the scan covers both SQLite
    # rows and blob bytes, while keeping the canary in a non-sensitive field.
    store.append_events(
        [
            EventFactory(execution_id).create(
                EventKind.DIAGNOSTIC, payload={"note": canary * 4000}
            )
        ]
    )
    clone = store.clone_execution(execution_id)
    store.close()

    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json, sqlite3, sys
from pathlib import Path
from mcp_pal.types import DirectSpec, StdioServer, HTTPServer, SecretReference
from mcp_pal.storage import SQLiteExecutionStore
db, blobs, profile_id, harness_profile_id, command_id, clone_id = sys.argv[1:]
store = SQLiteExecutionStore(db, blob_root=blobs)
profile = store.get_revision(store.resolve_revision(profile_id).id)
with sqlite3.connect(db) as connection:
    spec_json = connection.execute("SELECT specification_json FROM v2_executions WHERE id='secret-execution'").fetchone()[0]
    command_json = connection.execute("SELECT payload_json FROM v2_commands WHERE id=?", (command_id,)).fetchone()[0]
spec = DirectSpec.model_validate(json.loads(spec_json))
profile_server = profile.value["mcpServers"]["secret-server"]
server = StdioServer.model_validate({"name": "secret-server", "command": profile_server["command"], "environment": profile_server["env"]})
http_server = HTTPServer.model_validate({"name": "secret-http", "url": profile.value["mcpServers"]["secret-http"]["url"], "headers": profile.value["mcpServers"]["secret-http"]["headers"]})
harness = store.get_revision(store.resolve_revision(harness_profile_id).id)
clone_bindings = store.resolved_bindings(clone_id)["servers"]
print(json.dumps({
  "spec": spec.servers[0].server.environment["SERVICE_TOKEN"].model_dump(mode="json"),
  "profile": server.environment["SERVICE_TOKEN"].model_dump(mode="json"),
  "http": http_server.headers["Authorization"].model_dump(mode="json"),
  "harness": harness.value["env"]["SERVICE_TOKEN"],
  "command": json.loads(command_json)["bearer_token"],
  "clone": clone_bindings[0]["server"]["environment"]["SERVICE_TOKEN"],
  "canary_redacted": "[REDACTED]" in spec_json,
  "canary_leaked": any(b"resolved-service-canary" in path.read_bytes() for path in [Path(db), *Path(blobs).rglob('*')] if path.is_file()),
}))
store.close()
""",
            str(database),
            str(blob_root),
            profile.id,
            harness_profile.id,
            command.id,
            clone.root,
        ],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    observed = json.loads(child.stdout)
    expected = {"source": "environment", "name": "SERVICE_TOKEN"}
    assert observed == {
        "spec": expected,
        "profile": expected,
        "http": expected,
        "harness": expected,
        "command": expected,
        "clone": expected,
        "canary_redacted": True,
        "canary_leaked": False,
    }


def test_durable_credential_literals_are_rejected_instead_of_redacted(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(
        tmp_path / "reject-secret.sqlite",
        blob_root=tmp_path / "blobs",
        config=RedactionConfig(
            sensitive_keys=frozenset({"private_marker"}), include_environment=False
        ),
    )
    with pytest.raises(DurableSerializationError, match="requires a SecretReference"):
        store.create_server_profile(
            "literal-profile",
            {
                "mcpServers": {
                    "server": {
                        "type": "http",
                        "url": "https://example.test",
                        "headers": {"Authorization": "literal-token"},
                    }
                }
            },
        )
    with pytest.raises(DurableSerializationError, match="requires a SecretReference"):
        store.create_server_profile(
            "configured-key", {"private_marker": "literal-token"}
        )
    configured = store.create_server_profile(
        "configured-reference",
        {
            "private_marker": SecretReference(
                source="environment", name="PRIVATE_MARKER"
            )
        },
    )
    assert store.resolve_revision(configured.id).value["private_marker"] == {
        "source": "environment",
        "name": "PRIVATE_MARKER",
    }
    nested = store.create_harness_profile(
        "nested-provider-reference",
        {
            "credentials": {
                "provider": {
                    "api_key": SecretReference(
                        source="provider", name="provider-api-key"
                    ),
                    "bearer_token": SecretReference(
                        source="provider", name="provider-bearer"
                    ),
                }
            }
        },
    )
    assert store.resolve_revision(nested.id).value["credentials"]["provider"] == {
        "api_key": {"source": "provider", "name": "provider-api-key"},
        "bearer_token": {"source": "provider", "name": "provider-bearer"},
    }
    with pytest.raises(DurableSerializationError, match="requires a SecretReference"):
        store.create_harness_profile(
            "nested-literal",
            {"credentials": {"provider": {"api_key": "literal-token"}}},
        )
    with sqlite3.connect(tmp_path / "reject-secret.sqlite") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM v2_server_profiles WHERE name='literal-profile'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM v2_harness_profiles WHERE name='nested-literal'"
            ).fetchone()[0]
            == 0
        )
    store.create(ExecutionState(execution_id=ExecutionId("literal-execution")))
    with pytest.raises(DurableSerializationError, match="requires a SecretReference"):
        store.enqueue_command(
            "literal-execution", payload={"headers": {"Authorization": "literal-token"}}
        )
    with sqlite3.connect(tmp_path / "reject-secret.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM v2_commands").fetchone()[0] == 0
    store.close()


def test_durable_provenance_remains_redacted_metadata(tmp_path: Path) -> None:
    canary = "provenance-canary"
    store = SQLiteExecutionStore(
        tmp_path / "provenance.sqlite",
        blob_root=tmp_path / "blobs",
        config=RedactionConfig(secrets=frozenset({canary}), include_environment=False),
    )
    execution_id = ExecutionId("provenance-execution")
    store.create(
        ExecutionState(execution_id=execution_id),
        provenance={"secret": canary, "credential": "literal-metadata"},
    )
    with sqlite3.connect(tmp_path / "provenance.sqlite") as connection:
        value = connection.execute(
            "SELECT provenance_json FROM v2_executions WHERE id=?", (execution_id.root,)
        ).fetchone()[0]
    assert canary not in value
    assert "[REDACTED]" in value
    store.close()


@pytest.mark.parametrize(
    "boundary", ["specification", "server_bindings", "harness_binding"]
)
def test_durable_credential_rejection_is_atomic(tmp_path: Path, boundary: str) -> None:
    store = SQLiteExecutionStore(
        tmp_path / f"atomic-{boundary}.sqlite", blob_root=tmp_path / f"blobs-{boundary}"
    )
    execution_id = ExecutionId(f"atomic-{boundary}")
    kwargs = {
        "specification": {"headers": {"Authorization": "literal-token"}},
        "server_bindings": [
            {"server": {"headers": {"Authorization": "literal-token"}}}
        ],
        "harness_binding": {"credentials": {"Authorization": "literal-token"}},
    }
    with pytest.raises(DurableSerializationError, match="requires a SecretReference"):
        store.create(
            ExecutionState(execution_id=execution_id), **{boundary: kwargs[boundary]}
        )
    assert store.get_snapshot(execution_id) is None
    store.close()


@pytest.mark.parametrize(
    "value",
    [
        "literal-secret ${IGNORED}",
        "prefix-${TOKEN}",
        "Bearer ${TOKEN}-suffix",
        "bearer ${TOKEN}",
    ],
)
def test_legacy_placeholder_bypass_rejects_literal_suffixes(
    tmp_path: Path, value: str
) -> None:
    store = SQLiteExecutionStore(
        tmp_path / "placeholder.sqlite", blob_root=tmp_path / "blobs"
    )
    with pytest.raises(DurableSerializationError, match="requires a SecretReference"):
        store.create_server_profile(
            "placeholder", {"headers": {"Authorization": value}}
        )
    store.close()


@pytest.mark.parametrize("value", ["${TOKEN}", "Bearer ${TOKEN}"])
def test_legacy_placeholder_forms_are_explicitly_accepted(
    tmp_path: Path, value: str
) -> None:
    store = SQLiteExecutionStore(
        tmp_path / "accepted-placeholder.sqlite", blob_root=tmp_path / "blobs"
    )
    profile = store.create_server_profile(
        "placeholder", {"headers": {"Authorization": value}}
    )
    assert store.resolve_revision(profile.id).value["headers"]["Authorization"] == value
    store.close()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/mcp?token=literal-token",
        "https://user:password@example.test/mcp",
    ],
)
def test_durable_urls_reject_embedded_credentials(tmp_path: Path, url: str) -> None:
    store = SQLiteExecutionStore(
        tmp_path / "credential-url.sqlite", blob_root=tmp_path / "blobs"
    )
    with pytest.raises(
        DurableSerializationError, match="URL cannot contain credentials"
    ):
        store.create_server_profile("credential-url", {"url": url})
    store.close()


def test_durable_serialization_rejects_cycles_and_excessive_nesting_atomically(
    tmp_path: Path,
) -> None:
    cyclic_mapping: dict[str, object] = {}
    cyclic_mapping["self"] = cyclic_mapping
    cyclic_list: list[object] = []
    cyclic_list.append(cyclic_list)
    with pytest.raises(DurableSerializationError, match="cycle"):
        serialize_durable(cyclic_mapping)
    with pytest.raises(DurableSerializationError, match="cycle"):
        serialize_durable(cyclic_list)

    deeply_nested: dict[str, object] = {}
    cursor = deeply_nested
    for _index in range(65):
        child: dict[str, object] = {}
        cursor["next"] = child
        cursor = child
    store = SQLiteExecutionStore(
        tmp_path / "depth.sqlite", blob_root=tmp_path / "blobs"
    )
    with pytest.raises(DurableSerializationError, match="maximum nesting depth"):
        store.create(
            ExecutionState(execution_id=ExecutionId("depth-execution")),
            specification=deeply_nested,
        )
    assert store.get_snapshot("depth-execution") is None
    store.close()
