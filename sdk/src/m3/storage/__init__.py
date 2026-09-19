"""Ephemeral storage contracts and implementations."""

from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Any as _Any

from ..services.acp_probes import ACPProbeDimension, ACPProbeResult, ACPProbeStore
from ..services.persistent import SQLiteStoreWorker
from .blobs import BlobRecord, FilesystemBlobStore
from .ephemeral import (
    ArtifactNotFound,
    ArtifactStore,
    BlobIntegrityError,
    EventCallback,
    ExecutionStore,
    ExecutionTransaction,
    InMemoryArtifactStore,
    InMemoryExecutionStore,
    ProfileResolver,
    StorageConflict,
    StorageError,
    TemporaryArtifactStore,
)
from .serialization import DurableSerializationError, serialize_durable

if _TYPE_CHECKING:
    from .sqlite import ProfileRecord, ProfileRevisionRecord

__all__ = [
    "ACPProbeDimension",
    "ACPProbeResult",
    "ACPProbeStore",
    "ArtifactNotFound",
    "ArtifactStore",
    "BlobIntegrityError",
    "BlobRecord",
    "Command",
    "DurableSerializationError",
    "EventCallback",
    "ExecutionStore",
    "ExecutionTransaction",
    "FilesystemBlobStore",
    "InMemoryArtifactStore",
    "InMemoryExecutionStore",
    "Lease",
    "PersistentExecutionStore",
    "ProfileRecord",
    "ProfileResolver",
    "ProfileRevisionRecord",
    "SQLiteArtifactStore",
    "SQLiteExecutionStore",
    "SQLiteStore",
    "SQLiteStoreWorker",
    "StorageConflict",
    "StorageError",
    "TemporaryArtifactStore",
    "serialize_durable",
]


def __getattr__(name: str) -> _Any:
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
            PersistentExecutionStore,
            ProfileRecord,
            ProfileRevisionRecord,
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
