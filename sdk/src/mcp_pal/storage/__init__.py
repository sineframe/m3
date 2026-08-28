"""Ephemeral storage contracts and implementations."""

from typing import TYPE_CHECKING as _TYPE_CHECKING

from .ephemeral import (
    ArtifactNotFound,
    ArtifactStore,
    BlobIntegrityError,
    EventCallback,
    ExecutionStore,
    ExecutionTransaction,
    InMemoryArtifactStore,
    InMemoryExecutionStore,
    StorageConflict,
    StorageError,
    TemporaryArtifactStore,
)
from .blobs import BlobRecord, FilesystemBlobStore
from .serialization import DurableSerializationError, serialize_durable
from ..services.persistent import SQLiteStoreWorker
from ..services.acp_probes import ACPProbeDimension, ACPProbeResult, ACPProbeStore

if _TYPE_CHECKING:
    from .sqlite import ProfileRecord, ProfileRevisionRecord

__all__ = [
    "ArtifactNotFound",
    "ArtifactStore",
    "BlobIntegrityError",
    "EventCallback",
    "ExecutionStore",
    "ExecutionTransaction",
    "InMemoryArtifactStore",
    "InMemoryExecutionStore",
    "StorageConflict",
    "StorageError",
    "TemporaryArtifactStore",
    "BlobRecord",
    "FilesystemBlobStore",
    "DurableSerializationError",
    "serialize_durable",
    "SQLiteStoreWorker",
    "SQLiteArtifactStore",
    "SQLiteExecutionStore",
    "Command",
    "Lease",
    "ProfileRecord",
    "ProfileRevisionRecord",
    "SQLiteStore",
    "PersistentExecutionStore",
    "ACPProbeDimension", "ACPProbeResult", "ACPProbeStore",
]


def __getattr__(name: str):
    """Load the optional full execution store only on explicit use."""
    if name in {
        "SQLiteArtifactStore",
        "SQLiteExecutionStore",
        "Command",
        "Lease",
        "ProfileRecord",
        "ProfileRevisionRecord",
        "SQLiteStore",
        "PersistentExecutionStore",
    }:
        from .sqlite import (
            Command,
            Lease,
            ProfileRecord,
            ProfileRevisionRecord,
            PersistentExecutionStore,
            SQLiteArtifactStore,
            SQLiteExecutionStore,
            SQLiteStore,
        )

        return {
            "SQLiteArtifactStore": SQLiteArtifactStore,
            "SQLiteExecutionStore": SQLiteExecutionStore,
            "Command": Command,
            "Lease": Lease,
            "ProfileRecord": ProfileRecord,
            "ProfileRevisionRecord": ProfileRevisionRecord,
            "SQLiteStore": SQLiteStore,
            "PersistentExecutionStore": PersistentExecutionStore,
        }[name]
    raise AttributeError(name)
