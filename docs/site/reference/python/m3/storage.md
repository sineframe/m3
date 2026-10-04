---
title: "Storage API"
description: "InMemoryExecutionStore is the default direct-SDK execution store. SQLiteExecutionStore persists execution specs, snapshots, traces, sessions, turns, evaluations, profiles, and test-run records when the storage extra is installed."
---

# Storage API

`InMemoryExecutionStore` is the default direct-SDK execution store.
`SQLiteExecutionStore` persists execution specs, snapshots, traces, sessions,
turns, evaluations, profiles, and test-run records when the storage extra is
installed.

Read executions with `get_snapshot`, `get_execution_spec`, `get_trace`,
`get_trace_view`, `get_report`, and paged `list_executions`. Evaluation records
support direct lookup and aggregate queries. Test-run methods keep pytest
attempt outcomes separate from execution outcomes.

Artifact contracts include `ArtifactStore`, `InMemoryArtifactStore`,
`TemporaryArtifactStore`, `FilesystemBlobStore`, and the lazy SQLite artifact
store. Integrity or missing-data conditions raise `BlobIntegrityError` or
`ArtifactNotFound`.

Managed-input contracts are `ManagedInputStore`, `ManagedInputRecord`,
`ManagedInputLease`, `ManagedInputStatus`, and `SQLiteManagedInputStore`.
Profile and ACP probe records share the SQLite persistence boundary.

Storage conflicts and operational failures raise `StorageConflict` and
`StorageError`. Appends that skip a sequence number raise `SequenceConflict`,
and appends to a finished execution raise `TerminalConflict`; both are
`StorageConflict` subclasses. `DurableSerializationError` reports a value that cannot cross
the durable boundary; `serialize_durable` performs that checked conversion.

SQLite imports remain lazy so users who do not install storage support can use
the in-memory SDK.
