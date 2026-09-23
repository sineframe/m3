import warnings
from pathlib import Path

import pytest

from m3 import (
    HarnessProfileRef,
    HTTPServer,
    RevisionSelection,
    ServerProfileRef,
    StdioServer,
)
from m3.services.profiles import (
    ProfileResolutionError,
    resolve_harness_reference,
    resolve_server_reference,
)
from m3.storage import SQLiteExecutionStore
from m3.trace.redaction import RedactionConfig


def _store(tmp_path: Path) -> SQLiteExecutionStore:
    return SQLiteExecutionStore(tmp_path.resolve() / "profiles.sqlite")


def test_saved_server_profile_resolves_full_mcp_document_and_env_ref(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        store.create_server_profile(
            "servers",
            {"mcpServers": {"echo": {"command": "echo", "env": {"TOKEN": "${TOKEN}"}}}},
            profile_id="server-1",
            revision_id="server-rev-1",
        )
        resolved = resolve_server_reference(
            ServerProfileRef(
                profile_id="server-1",
                server_name="echo",
                revision=RevisionSelection(mode="latest"),
            ),
            store,
        )
        assert resolved.value.name == "echo"
        assert resolved.value.environment["TOKEN"].name == "TOKEN"
        assert resolved.provenance["revision_id"] == "server-rev-1"
    finally:
        store.close()


def test_saved_server_profile_uses_app_transport_and_trust_semantics(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        store.create_server_profile(
            "servers",
            {
                "mcpServers": {
                    "stdio": {
                        "command": "agent",
                        "args": ["--safe"],
                        "cwd": "/tmp/project",
                        "trust": "public",
                    },
                    "http": {
                        "type": "http",
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": "${TOKEN}"},
                    },
                }
            },
            profile_id="server-transports",
        )
        stdio = resolve_server_reference(
            ServerProfileRef(
                profile_id="server-transports",
                server_name="stdio",
                revision=RevisionSelection(mode="latest"),
            ),
            store,
        ).value
        http = resolve_server_reference(
            ServerProfileRef(
                profile_id="server-transports",
                server_name="http",
                revision=RevisionSelection(mode="latest"),
            ),
            store,
        ).value
        assert isinstance(stdio, StdioServer)
        assert stdio.args == ("--safe",)
        assert stdio.cwd == "/tmp/project"
        assert stdio.trust.value == "public"
        assert isinstance(http, HTTPServer)
        assert http.headers["Authorization"].name == "TOKEN"
    finally:
        store.close()


def test_resolve_profile_reads_latest_in_one_store_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    try:
        store.create_server_profile(
            "snapshot",
            {"mcpServers": {"echo": {"command": "echo"}}},
            profile_id="snapshot-profile",
            revision_id="snapshot-revision",
        )

        def unexpected_delegation(*args: object, **kwargs: object) -> object:
            raise AssertionError(
                "resolve_profile must not delegate to resolve_revision"
            )

        monkeypatch.setattr(store, "resolve_revision", unexpected_delegation)
        profile, revision = store.resolve_profile(
            "snapshot-profile", RevisionSelection(mode="latest"), kind="server"
        )
        assert profile is not None
        assert revision is not None
        assert revision.id.root == "snapshot-revision"
    finally:
        store.close()


def test_legacy_profiles_are_imported_idempotently_with_pointers(
    tmp_path: Path,
) -> None:
    """The first v2 store open imports both legacy profile families exactly once."""
    import sqlite3

    path = tmp_path.resolve() / "legacy.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE mcp_profiles (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
            archived BOOLEAN, current_revision_id TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE mcp_profile_revisions (
            id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
            revision_number INTEGER NOT NULL, mcp_json TEXT NOT NULL,
            created_at TEXT
        );
        CREATE TABLE harness_profiles (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
            archived BOOLEAN, current_revision_id TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE harness_profile_revisions (
            id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
            revision_number INTEGER NOT NULL, manifest TEXT NOT NULL,
            trusted_unsandboxed BOOLEAN, created_at TEXT
        );
        CREATE TABLE harness_probes (id TEXT PRIMARY KEY, revision_id TEXT);
        CREATE TABLE runs (id TEXT PRIMARY KEY, profile_revision_id TEXT);
        INSERT INTO mcp_profiles VALUES
          ('legacy-server', 'legacy-server', 'old', 0, 'server-rev-2',
           '2024-01-01T00:00:00+00:00', '2024-01-02T00:00:00+00:00');
        INSERT INTO mcp_profiles VALUES
          ('legacy-incomplete', 'legacy-incomplete', 'partial', 0, 'missing-rev',
           '2024-01-01T00:00:00+00:00', '2024-01-02T00:00:00+00:00');
        INSERT INTO mcp_profile_revisions VALUES
          ('server-rev-1', 'legacy-server', 1,
           '{"mcpServers":{"echo":{"command":"printf"}}}',
           '2024-01-01T00:00:00+00:00');
        INSERT INTO mcp_profile_revisions VALUES
          ('server-rev-2', 'legacy-server', 2,
           '{"mcpServers":{"echo":{"command":"echo"}}}',
           '2024-01-02T00:00:00+00:00');
        INSERT INTO harness_profiles VALUES
          ('legacy-harness', 'legacy-harness', 'old harness', 1, 'harness-rev-1',
           '2024-01-01T00:00:00+00:00', '2024-01-03T00:00:00+00:00');
        INSERT INTO harness_profile_revisions VALUES
          ('harness-rev-1', 'legacy-harness', 1,
           '{"command":"agent"}', 1, '2024-01-03T00:00:00+00:00');
        INSERT INTO harness_probes VALUES ('probe-1', 'harness-rev-1');
        INSERT INTO runs VALUES ('run-1', 'server-rev-2');
        """
    )
    connection.commit()
    connection.close()

    store = SQLiteExecutionStore(path)
    try:
        server = store.resolve_profile(
            "legacy-server", RevisionSelection(mode="latest"), kind="server"
        )
        assert server[0].id == "legacy-server"
        assert server[0].current_revision_id.root == "server-rev-2"
        assert server[1].id.root == "server-rev-2"
        assert server[1].revision_number == 2
        incomplete = store.resolve_profile(
            "legacy-incomplete", RevisionSelection(mode="latest"), kind="server"
        )
        assert incomplete[0].id == "legacy-incomplete"
        assert incomplete[0].current_revision_id is None
        assert incomplete[1] is None
        harness = store.resolve_profile(
            "legacy-harness", RevisionSelection(mode="latest"), kind="harness"
        )
        assert harness[0].archived is True
        assert harness[1] is None
        assert store.get_revision("harness-rev-1").id.root == "harness-rev-1"
        with store._connect() as check:
            assert (
                check.execute("SELECT COUNT(*) FROM v2_acp_probes").fetchone()[0] == 0
            )
            assert (
                check.execute("SELECT COUNT(*) FROM v2_executions").fetchone()[0] == 0
            )
    finally:
        store.close()

    # Re-opening an unchanged legacy database is a silent no-op and retains
    # the same IDs/revisions rather than warning about its own imported rows.
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        reopened = SQLiteExecutionStore(path)
    try:
        assert not captured
        with reopened._connect() as check:
            assert (
                check.execute(
                    "SELECT COUNT(*) FROM v2_server_profile_revisions"
                ).fetchone()[0]
                == 2
            )
            assert (
                check.execute(
                    "SELECT COUNT(*) FROM v2_harness_profile_revisions"
                ).fetchone()[0]
                == 1
            )
    finally:
        reopened.close()


def test_saved_harness_profile_resolves_trusted_acp_manifest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        store.create_harness_profile(
            "agent",
            {"manifest": {"command": "agent"}, "trusted_unsandboxed": True},
            profile_id="harness-1",
            revision_id="harness-rev-1",
        )
        resolved = resolve_harness_reference(
            HarnessProfileRef(
                profile_id="harness-1", revision=RevisionSelection(mode="latest")
            ),
            store,
        )
        assert resolved.value.kind == "acp"
        assert resolved.value.executable == "agent"
    finally:
        store.close()


def test_legacy_profile_migration_redacts_and_skips_malformed_revisions(
    tmp_path: Path,
) -> None:
    import sqlite3

    path = tmp_path.resolve() / "legacy-safety.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE mcp_profiles (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
            archived BOOLEAN, current_revision_id TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE mcp_profile_revisions (
            id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
            revision_number INTEGER NOT NULL, mcp_json TEXT NOT NULL,
            created_at TEXT
        );
        INSERT INTO mcp_profiles VALUES
          ('legacy-safe', 'legacy-safe', 'ordinary', 0, 'safe-rev',
           '2024-01-01T00:00:00+00:00', '2024-01-01T00:00:00+00:00'),
          ('legacy-bad', 'legacy-bad', 'ordinary', 0, 'bad-rev',
           '2024-01-01T00:00:00+00:00', '2024-01-01T00:00:00+00:00');
        INSERT INTO mcp_profile_revisions VALUES
          ('safe-rev', 'legacy-safe', 1,
           '{"mcpServers":{"echo":{"command":"migration-secret"}}}',
           '2024-01-01T00:00:00+00:00'),
          ('bad-rev', 'legacy-bad', 1, 'not-json',
           '2024-01-01T00:00:00+00:00');
        """
    )
    connection.commit()
    connection.close()

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        store = SQLiteExecutionStore(
            path,
            config=RedactionConfig(
                secrets=frozenset({"migration-secret"}),
                include_environment=False,
            ),
        )
    try:
        safe = store.resolve_revision("legacy-safe", kind="server")
        assert safe.value["mcpServers"]["echo"]["command"] == "[REDACTED]"
        assert "migration-secret" not in repr(safe.value)
        assert store.get_profile("legacy-bad", kind="server") is None
        messages = [str(item.message) for item in captured]
        assert any("malformed" in message for message in messages)
        assert all("not-json" not in message for message in messages)
        with store._connect() as check:
            assert (
                check.execute(
                    "SELECT COUNT(*) FROM v2_server_profile_revisions WHERE profile_id=?",
                    ("legacy-bad",),
                ).fetchone()[0]
                == 0
            )
    finally:
        store.close()


def test_archived_saved_profile_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        store.create_server_profile(
            "archived",
            {"mcpServers": {"echo": {"command": "echo"}}},
            profile_id="server-archived",
        )
        store.archive_profile("server-archived")
        with pytest.raises(ProfileResolutionError) as error:
            resolve_server_reference(
                ServerProfileRef(
                    profile_id="server-archived",
                    server_name="echo",
                    revision=RevisionSelection(mode="latest"),
                ),
                store,
            )
        assert error.value.details["reason"] == "archived"
    finally:
        store.close()


def test_v2_profiles_win_divergent_legacy_id_and_name_conflicts(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path.resolve() / "conflicts.sqlite"
    store = SQLiteExecutionStore(path)
    store.create_server_profile(
        "v2-id", {"mcpServers": {"echo": {"command": "v2"}}}, profile_id="same-id"
    )
    store.create_server_profile(
        "shared-name", {"mcpServers": {"echo": {"command": "v2"}}}, profile_id="v2-name"
    )
    store.close()

    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE mcp_profiles (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
            archived BOOLEAN, current_revision_id TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE mcp_profile_revisions (
            id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
            revision_number INTEGER NOT NULL, mcp_json TEXT NOT NULL,
            created_at TEXT
        );
        INSERT INTO mcp_profiles VALUES
          ('same-id', 'legacy-id', 'legacy', 0, 'legacy-rev-id',
           '2020-01-01T00:00:00+00:00', '2020-01-01T00:00:00+00:00'),
          ('legacy-name', 'shared-name', 'legacy', 0, NULL,
           '2020-01-01T00:00:00+00:00', '2020-01-01T00:00:00+00:00');
        INSERT INTO mcp_profile_revisions VALUES
          ('legacy-rev-id', 'same-id', 1,
           '{"mcpServers":{"echo":{"command":"legacy"}}}',
           '2020-01-01T00:00:00+00:00');
        """
    )
    connection.commit()
    connection.close()

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        reopened = SQLiteExecutionStore(path)
    try:
        messages = [str(item.message) for item in captured]
        assert len(messages) == 2
        assert all("wins legacy ID/name conflict" in message for message in messages)
        assert {profile.id for profile in reopened.list_profiles("server")} == {
            "same-id",
            "v2-name",
        }
        revision = reopened.resolve_revision(
            "same-id", RevisionSelection(mode="latest")
        )
        assert revision.value["mcpServers"]["echo"]["command"] == "v2"
    finally:
        reopened.close()
