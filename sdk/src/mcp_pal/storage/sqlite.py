"""Fresh SQLite persistence for the v0.2 SDK.

This module is intentionally imported lazily by :mod:`mcp_pal.storage`.  The
in-memory stores remain the zero-dependency default; selecting a SQLite store
is an explicit application decision.  The schema is deliberately namespaced
with ``v2_`` and is created from scratch.  There is no schema inspection,
version detector, migration path, or legacy import in this module.

SQLite stores redacted stable values only.  Large artifact values are
written atomically to a content-addressed filesystem directory before their
metadata reference is committed.  A failed metadata transaction leaves an
unreferenced file which is safe for the explicit garbage collector to remove.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
import warnings
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, cast

from pydantic import TypeAdapter, ValidationError

from ..aggregations import EvaluationQuery, EvaluationReport, aggregate_evaluations
from ..domain.validation import validate_mcp_config
from ..errors import (
    RawEvidenceIntegrityError,
    RawEvidenceUnavailable,
    TraceNotFinalized,
    TraceUnavailable,
)
from ..harness.manifest import validate_manifest
from ..observability import (
    CaptureOptions,
    EvidenceCapture,
    RawEvidence,
    TraceView,
)
from ..services.acp_probes import ACPProbeDimension, ACPProbeResult
from ..suites import Suite, generated_suite_id, normalize_suite_name
from ..trace.redaction import (
    RedactionConfig,
    redact_artifact_bytes,
    redact_for_persistence,
    redact_model_json,
)
from ..types import (
    ArtifactId,
    ArtifactRef,
    EvaluationRecord,
    EvaluationResult,
    Event,
    EventId,
    EventKind,
    EvidenceRef,
    ExecutionId,
    ExecutionOutcome,
    ExecutionPage,
    ExecutionReport,
    ExecutionSpec,
    ExecutionState,
    ExecutionStatus,
    PayloadRef,
    ProjectId,
    RevisionId,
    RevisionSelection,
    RunId,
    SessionId,
    SuiteId,
    TraceId,
    TraceResult,
    TurnId,
    TurnResult,
    TurnState,
)
from .blobs import FilesystemBlobStore
from .ephemeral import (
    ArtifactNotFound,
    BlobIntegrityError,
    EventCallback,
    ExecutionTransaction,
    StorageConflict,
    StorageError,
    _execution_key,
    _report_fields,
)
from .evidence import (
    evidence_id_for as _evidence_id_for,
)
from .evidence import (
    make_capture as _make_evidence_capture,
)
from .evidence import (
    make_ref as _make_evidence_ref,
)
from .evidence import (
    make_result as _make_evidence_result,
)
from .evidence import (
    prepare_evidence as _prepare_evidence,
)
from .evidence import (
    validate_evidence_id as _validate_evidence_id,
)
from .evidence import (
    verify_reference as _verify_evidence_reference,
)
from .serialization import serialize_durable


def _sqlalchemy() -> Any:
    """Load SQLAlchemy only when the optional SQLite backend is selected."""
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
    except ImportError as exc:  # pragma: no cover - exercised in a minimal env
        raise ModuleNotFoundError(
            "SQLite storage requires the optional dependency; install mcp-pal[storage]"
        ) from exc
    return create_engine, NullPool


def _is_database_error(exc: BaseException) -> bool:
    """Recognize SQLAlchemy's DBAPI wrappers without importing it eagerly."""
    return isinstance(exc, OSError) or exc.__class__.__module__.startswith(
        "sqlalchemy."
    )


def _is_integrity_error(exc: BaseException) -> bool:
    return exc.__class__.__name__ == "IntegrityError" and _is_database_error(exc)


class _CompatRow:
    """Small sqlite3.Row-compatible view over a SQLAlchemy row."""

    def __init__(self, row: Any) -> None:
        self._row = row

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, str):
            return self._row._mapping[key]
        return self._row[key]


class _CompatResult:
    def __init__(self, result: Any) -> None:
        self._result = result

    def fetchone(self) -> Any:
        row = self._result.fetchone()
        return None if row is None else _CompatRow(row)

    def fetchall(self) -> list[_CompatRow]:
        return [_CompatRow(row) for row in self._result.fetchall()]

    def __iter__(self) -> Iterator[_CompatRow]:
        return iter(self.fetchall())

    @property
    def rowcount(self) -> int:
        return int(self._result.rowcount)


class _CompatConnection:
    """Keep the existing private SQL helpers while routing through SQLAlchemy."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, statement: str, parameters: Sequence[Any] = ()) -> _CompatResult:
        return _CompatResult(
            self._connection.exec_driver_sql(statement, tuple(parameters))
        )

    def executescript(self, script: str) -> None:
        # The schema is a controlled, fresh-only script containing no string
        # literals with semicolons. Execute each statement through SQLAlchemy's
        # driver API rather than taking ownership of sqlite3's connection.
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)

    @property
    def in_transaction(self) -> bool:
        return bool(self._connection.in_transaction())

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> _CompatConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return _utcnow()
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _loads(value: str | None, default: Any = None) -> Any:
    return default if value is None else json.loads(value)


def _upgrade_persisted_execution_spec(value: Any) -> Any:
    """Fill fields introduced after the first durable execution-spec schema."""
    if not isinstance(value, Mapping):
        return value
    servers = value.get("servers")
    if not isinstance(servers, (list, tuple)):
        return value
    upgraded = dict(value)
    upgraded_servers: list[Any] = []
    for binding in servers:
        if not isinstance(binding, Mapping):
            upgraded_servers.append(binding)
            continue
        upgraded_binding = dict(binding)
        profile = binding.get("profile")
        if isinstance(profile, Mapping) and "server_name" not in profile:
            profile_id = profile.get("profile_id")
            if isinstance(profile_id, str) and profile_id:
                upgraded_profile = dict(profile)
                # Before server_name became explicit, the profile ID was the
                # stable selector used by persisted specs.
                upgraded_profile["server_name"] = profile_id
                upgraded_binding["profile"] = upgraded_profile
        upgraded_servers.append(upgraded_binding)
    upgraded["servers"] = upgraded_servers
    return upgraded


def _evaluation_subject_kind(subject: Any) -> str:
    if subject is None:
        return "unknown"
    kind = getattr(subject, "kind", None)
    if isinstance(kind, str) and kind:
        return kind
    name = type(subject).__name__
    return name if name not in {"dict", "list", "tuple"} else "json"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor) if path.is_absolute() else Path(".")
    for component in path.parts:
        if path.is_absolute() and component == path.anchor:
            continue
        current /= component
        if current.is_symlink():
            return True
    return False


@dataclass(frozen=True)
class ProfileRevisionRecord:
    id: RevisionId
    profile_id: str
    revision_number: int
    value: Mapping[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class ProfileRecord:
    id: str
    kind: str
    name: str
    description: str
    archived: bool
    current_revision_id: RevisionId | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Lease:
    execution_id: ExecutionId
    owner_id: str
    lease_token: str
    expires_at: datetime


@dataclass(frozen=True)
class Command:
    id: str
    execution_id: ExecutionId
    kind: str
    status: str
    payload: Mapping[str, Any]
    session_id: SessionId | None = None
    turn_id: TurnId | None = None


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS v2_server_profiles (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
  archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1)), current_revision_id TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(current_revision_id) REFERENCES v2_server_profile_revisions(id)
);
CREATE TABLE IF NOT EXISTS v2_projects (
  id TEXT PRIMARY KEY, project_name TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v2_server_profile_revisions (
  id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES v2_server_profiles(id) ON DELETE CASCADE,
  revision_number INTEGER NOT NULL CHECK (revision_number > 0), value_json TEXT NOT NULL,
  created_at TEXT NOT NULL, UNIQUE(profile_id, revision_number), UNIQUE(profile_id, id)
);
CREATE TABLE IF NOT EXISTS v2_harness_profiles (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
  archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1)), current_revision_id TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(current_revision_id) REFERENCES v2_harness_profile_revisions(id)
);
CREATE TABLE IF NOT EXISTS v2_harness_profile_revisions (
  id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES v2_harness_profiles(id) ON DELETE CASCADE,
  revision_number INTEGER NOT NULL CHECK (revision_number > 0), value_json TEXT NOT NULL,
  created_at TEXT NOT NULL, UNIQUE(profile_id, revision_number), UNIQUE(profile_id, id)
);
CREATE TABLE IF NOT EXISTS v2_acp_probes (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES v2_harness_profiles(id) ON DELETE CASCADE,
  revision_id TEXT NOT NULL REFERENCES v2_harness_profile_revisions(id) ON DELETE CASCADE,
  probe_type TEXT NOT NULL CHECK(probe_type IN ('protocol','full')),
  transport TEXT NOT NULL, agent_mode_id TEXT, session_config_json TEXT NOT NULL,
  dimension_key TEXT NOT NULL, status TEXT NOT NULL,
  agent_identity_json TEXT, agent_capabilities_json TEXT NOT NULL,
  agent_modes_json TEXT NOT NULL, current_agent_mode_id TEXT,
  config_options_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
  diagnostics TEXT, error TEXT, created_at TEXT NOT NULL, started_at TEXT,
  finished_at TEXT, duration_ms REAL
);
CREATE INDEX IF NOT EXISTS v2_acp_probes_dimension ON v2_acp_probes(dimension_key, created_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS v2_suites (
  id INTEGER PRIMARY KEY AUTOINCREMENT, suite_name TEXT NOT NULL,
  project_id TEXT REFERENCES v2_projects(id)
);
CREATE TABLE IF NOT EXISTS v2_executions (
  id TEXT PRIMARY KEY, snapshot_json TEXT NOT NULL, specification_json TEXT,
  provenance_json TEXT, parent_execution_id TEXT REFERENCES v2_executions(id),
  created_at TEXT NOT NULL, deleted_at TEXT, run_id TEXT, suite_id INTEGER REFERENCES v2_suites(id),
  project_id TEXT REFERENCES v2_projects(id)
);
CREATE TABLE IF NOT EXISTS v2_execution_server_bindings (
  execution_id TEXT NOT NULL REFERENCES v2_executions(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0), profile_id TEXT, revision_id TEXT,
  binding_json TEXT NOT NULL, PRIMARY KEY(execution_id, ordinal)
);
CREATE TABLE IF NOT EXISTS v2_execution_harness_bindings (
  execution_id TEXT PRIMARY KEY REFERENCES v2_executions(id) ON DELETE CASCADE,
  profile_id TEXT, revision_id TEXT, binding_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v2_sessions (
  id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES v2_executions(id) ON DELETE CASCADE,
  state TEXT NOT NULL, created_at TEXT NOT NULL, closed_at TEXT
);
CREATE TABLE IF NOT EXISTS v2_turns (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES v2_sessions(id) ON DELETE CASCADE,
  number INTEGER NOT NULL CHECK (number > 0), snapshot_json TEXT NOT NULL,
  result_json TEXT, created_at TEXT NOT NULL, UNIQUE(session_id, number)
);
CREATE TABLE IF NOT EXISTS v2_events (
  id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES v2_executions(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL CHECK (sequence >= 0), event_json TEXT NOT NULL,
  timestamp TEXT NOT NULL, UNIQUE(execution_id, sequence)
);
CREATE TABLE IF NOT EXISTS v2_evaluations (
  id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES v2_executions(id) ON DELETE CASCADE,
  turn_id TEXT REFERENCES v2_turns(id) ON DELETE SET NULL, result_json TEXT NOT NULL,
  created_at TEXT NOT NULL, evaluator_name TEXT, status TEXT, score REAL, run_id TEXT
);
CREATE TABLE IF NOT EXISTS v2_blobs (
  sha256 TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
  compressed_size INTEGER NOT NULL CHECK (compressed_size >= 0), media_type TEXT,
  storage_key TEXT NOT NULL UNIQUE, ref_count INTEGER NOT NULL DEFAULT 0 CHECK (ref_count >= 0),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v2_event_blobs (
  event_id TEXT NOT NULL REFERENCES v2_events(id) ON DELETE CASCADE,
  sha256 TEXT NOT NULL REFERENCES v2_blobs(sha256),
  role TEXT NOT NULL CHECK (role IN ('payload','raw_evidence')),
  media_type TEXT,
  evidence_id TEXT UNIQUE,
  CHECK ((role = 'payload' AND evidence_id IS NULL AND media_type IS NULL) OR
    (role = 'raw_evidence' AND evidence_id IS NOT NULL AND
     media_type IS NOT NULL AND length(media_type) > 0)),
  PRIMARY KEY(event_id, role)
);
CREATE TABLE IF NOT EXISTS v2_artifacts (
  id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES v2_executions(id) ON DELETE CASCADE,
  name TEXT NOT NULL, media_type TEXT, size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
  sha256 TEXT NOT NULL REFERENCES v2_blobs(sha256), redacted INTEGER NOT NULL CHECK (redacted=1),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v2_leases (
  execution_id TEXT PRIMARY KEY REFERENCES v2_executions(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL, lease_token TEXT NOT NULL UNIQUE, acquired_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v2_cancellation (
  execution_id TEXT PRIMARY KEY REFERENCES v2_executions(id) ON DELETE CASCADE,
  requested_at TEXT NOT NULL, reason TEXT
);
CREATE TABLE IF NOT EXISTS v2_commands (
  id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES v2_executions(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('queued','claimed','done','failed','cancelled','interrupted')),
  payload_json TEXT NOT NULL, session_id TEXT, turn_id TEXT, created_at TEXT NOT NULL,
  claimed_at TEXT, owner_id TEXT
);
CREATE TABLE IF NOT EXISTS v2_test_runs (
  run_id TEXT PRIMARY KEY, record_json TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  project_id TEXT REFERENCES v2_projects(id)
);
CREATE TABLE IF NOT EXISTS v2_test_results (
  run_id TEXT NOT NULL REFERENCES v2_test_runs(run_id) ON DELETE CASCADE,
  attempt_id TEXT NOT NULL, record_json TEXT NOT NULL, suite_id INTEGER REFERENCES v2_suites(id),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY(run_id, attempt_id)
);
CREATE INDEX IF NOT EXISTS v2_test_results_run_node ON v2_test_results(run_id, attempt_id);
CREATE INDEX IF NOT EXISTS v2_commands_fifo ON v2_commands(status, created_at, id);
CREATE UNIQUE INDEX IF NOT EXISTS v2_commands_turn_unique
  ON v2_commands(execution_id, session_id, turn_id)
  WHERE turn_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS v2_sequence_reservations (
  execution_id TEXT NOT NULL REFERENCES v2_executions(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL CHECK(sequence >= 0), PRIMARY KEY(execution_id, sequence)
);
"""


class _SqliteBase:
    _migrate_profiles_enabled = False
    _redaction_config: RedactionConfig

    def __init__(
        self,
        database: str | Path,
        *,
        busy_timeout_ms: int = 5000,
        wal: bool = True,
    ) -> None:
        raw = str(database)
        if raw.startswith("sqlite:///"):
            raw = raw.removeprefix("sqlite:///")
        self.database = Path(raw).expanduser()
        if _has_symlink_component(self.database):
            raise StorageError("database path must not contain symlinks")
        if self.database.exists() and not self.database.is_file():
            raise StorageError("database path must name a regular file")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.wal_requested = wal
        self.journal_mode = "delete"
        self._init_lock = threading.Lock()
        create_engine, null_pool = _sqlalchemy()
        # SQLAlchemy is the owner of the SQLite DBAPI connection. AUTOCOMMIT
        # keeps PRAGMA setup and our explicit BEGIN/COMMIT boundaries intact;
        # write transactions still use SQLite's native BEGIN IMMEDIATE below.
        self._engine = create_engine(
            f"sqlite+pysqlite:///{self.database}",
            connect_args={
                "timeout": max(self.busy_timeout_ms / 1000, 0.001),
                "check_same_thread": False,
            },
            isolation_level="AUTOCOMMIT",
            pool_pre_ping=True,
            # Store connections are short-lived and the application may
            # construct several isolated stores during tests. Avoid retaining
            # idle descriptors in per-store pools; SQLite still serializes
            # writes through its normal locking semantics.
            poolclass=null_pool,
        )
        self._initialize()

    def _connect(self) -> _CompatConnection:
        # SQLite follows a database symlink (and WAL/SHM symlinks) before this
        # method gets a chance to write.  Refuse those paths before opening.
        if _has_symlink_component(self.database) or any(
            Path(f"{self.database}{suffix}").is_symlink() for suffix in ("-wal", "-shm")
        ):
            raise StorageError("database or journal path must not contain symlinks")
        connection: _CompatConnection | None = None
        try:
            connection = _CompatConnection(self._engine.connect())
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            if self.wal_requested:
                result = connection.execute("PRAGMA journal_mode=WAL").fetchone()
                if result:
                    self.journal_mode = str(result[0]).lower()
            else:
                result = connection.execute("PRAGMA journal_mode").fetchone()
                if result:
                    self.journal_mode = str(result[0]).lower()
            # Metadata and WAL/SHM files can contain sensitive redacted
            # evidence.  Tighten modes on every open, including files created
            # by SQLite after the connection was established.
            for path in (
                self.database,
                Path(f"{self.database}-wal"),
                Path(f"{self.database}-shm"),
            ):
                if path.is_symlink():
                    raise StorageError("database or journal path must not be a symlink")
                if path.exists():
                    if not path.is_file():
                        raise StorageError(
                            "database or journal path must name a regular file"
                        )
                    path.chmod(0o600)
            return connection
        except StorageError:
            if connection is not None:
                connection.close()
            raise
        except Exception as exc:
            if connection is not None:
                connection.close()
            if _is_database_error(exc):
                raise StorageError("database is unavailable") from None
            raise

    def _initialize(self) -> None:
        try:
            with self._init_lock, self._connect() as connection:
                connection.executescript(SCHEMA)
                self._migrate_suite_uniqueness(connection)
                # CREATE TABLE IF NOT EXISTS deliberately does not evolve an
                # existing database.  Keep migrations small, idempotent, and
                # local to the store so old SDK databases remain readable.
                for table, column, definition in (
                    ("v2_executions", "run_id", "TEXT"),
                    ("v2_executions", "deleted_at", "TEXT"),
                    ("v2_executions", "suite_id", "INTEGER"),
                    ("v2_evaluations", "evaluator_name", "TEXT"),
                    ("v2_evaluations", "status", "TEXT"),
                    ("v2_evaluations", "score", "REAL"),
                    ("v2_evaluations", "run_id", "TEXT"),
                    ("v2_test_results", "suite_id", "INTEGER"),
                    ("v2_suites", "project_id", "TEXT"),
                    ("v2_executions", "project_id", "TEXT"),
                    ("v2_test_runs", "project_id", "TEXT"),
                ):
                    columns = connection.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                    if not any(str(row[1]) == column for row in columns):
                        connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                        )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS v2_evaluations_execution_turn ON v2_evaluations(execution_id, turn_id, created_at, id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS v2_evaluations_run_evaluator ON v2_evaluations(run_id, evaluator_name, created_at, id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS v2_evaluations_evaluator ON v2_evaluations(evaluator_name, created_at, id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS v2_executions_created_at ON v2_executions(created_at, id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS v2_executions_suite ON v2_executions(suite_id, created_at, id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS v2_executions_project ON v2_executions(project_id, created_at, id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS v2_suites_project ON v2_suites(project_id, suite_name)"
                )
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS v2_suites_project_name ON v2_suites(project_id, suite_name) WHERE project_id IS NOT NULL"
                )
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS v2_suites_legacy_name ON v2_suites(suite_name) WHERE project_id IS NULL"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS v2_test_results_suite ON v2_test_results(suite_id, run_id, attempt_id)"
                )
                migrate = getattr(self, "_migrate_legacy_evaluations", None)
                if callable(migrate):
                    migrate(connection)
                profile_migrate = getattr(self, "_migrate_legacy_profiles", None)
                if self._migrate_profiles_enabled and callable(profile_migrate):
                    profile_migrate(connection)
        except StorageError:
            raise
        except Exception as exc:
            if _is_database_error(exc):
                raise StorageError("database initialization failed") from None
            raise

    @staticmethod
    def _migrate_suite_uniqueness(connection: _CompatConnection) -> None:
        """Remove the legacy database-wide suite-name constraint safely."""
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='v2_suites'"
        ).fetchone()
        definition = "" if row is None else str(row[0] or "")
        if "suite_name text not null unique" not in definition.lower():
            return
        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE v2_suites_mcp_pal_new ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, suite_name TEXT NOT NULL, "
                "project_id TEXT REFERENCES v2_projects(id))"
            )
            columns = connection.execute("PRAGMA table_info(v2_suites)").fetchall()
            has_project = any(str(item[1]) == "project_id" for item in columns)
            if has_project:
                connection.execute(
                    "INSERT INTO v2_suites_mcp_pal_new(id,suite_name,project_id) "
                    "SELECT id,suite_name,project_id FROM v2_suites"
                )
            else:
                connection.execute(
                    "INSERT INTO v2_suites_mcp_pal_new(id,suite_name) "
                    "SELECT id,suite_name FROM v2_suites"
                )
            connection.execute("DROP TABLE v2_suites")
            connection.execute("ALTER TABLE v2_suites_mcp_pal_new RENAME TO v2_suites")
            connection.execute("COMMIT")
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise StorageError("suite schema migration left invalid foreign keys")

    def _migrate_legacy_evaluations(self, connection: _CompatConnection) -> None:
        """Best-effort projection of pre-v2.1 evaluation JSON."""
        rows = connection.execute(
            "SELECT id,execution_id,result_json,created_at,evaluator_name,status,score,run_id "
            "FROM v2_evaluations WHERE evaluator_name IS NULL OR status IS NULL"
        ).fetchall()
        for row in rows:
            try:
                value = json.loads(str(row["result_json"]))
                if not isinstance(value, Mapping):
                    continue
                raw_context = value.get("context")
                context = dict(raw_context) if isinstance(raw_context, Mapping) else {}
                raw_metadata = context.get("metadata")
                metadata = (
                    dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
                )
                execution = connection.execute(
                    "SELECT run_id FROM v2_executions WHERE id=?",
                    (str(row["execution_id"]),),
                ).fetchone()
                execution_run = execution[0] if execution is not None else None
                run_value = (
                    value.get("run_id")
                    or metadata.get("mcp_pal.run_id")
                    or row["run_id"]
                    or execution_run
                )
                subject = context.get("subject")
                config = getattr(self, "_redaction_config", RedactionConfig())
                subject_json = _json(
                    redact_for_persistence(
                        subject, config=config, path="$.evaluation.subject"
                    )
                )
                digest = value.get("subject_digest")
                if not isinstance(digest, str) or len(digest) != 64:
                    digest = hashlib.sha256(subject_json.encode()).hexdigest()
                subject_kind = value.get("subject_kind")
                if not isinstance(subject_kind, str) or not subject_kind:
                    subject_kind = _evaluation_subject_kind(subject)
                name = str(value.get("name") or row["evaluator_name"] or "")
                status = str(value.get("status") or row["status"] or "error")
                score = (
                    value.get("score")
                    if value.get("score") is not None
                    else row["score"]
                )
                compact = {
                    "evaluation_id": str(value.get("evaluation_id") or row["id"]),
                    "name": name,
                    "status": status,
                    "required": bool(value.get("required", False)),
                    "message": value.get("message"),
                    "score": score,
                    "rationale": value.get("rationale"),
                    "metrics": value.get("metrics", {}),
                    "provenance": value.get("provenance"),
                    "context": {
                        "execution_id": context.get("execution_id")
                        or str(row["execution_id"]),
                        "turn_id": context.get("turn_id"),
                        "goal": context.get("goal"),
                        "artifacts": context.get("artifacts", []),
                        "metadata": metadata,
                    },
                    "subject_kind": subject_kind,
                    "subject_digest": digest,
                    "run_id": str(run_value) if run_value is not None else None,
                    "created_at": str(value.get("created_at") or row["created_at"]),
                }
                candidate = dict(compact)
                candidate_context = candidate.pop("context")
                candidate.update(
                    execution_id=str(
                        candidate_context.get("execution_id") or row["execution_id"]
                    ),
                    turn_id=candidate_context.get("turn_id"),
                    goal=candidate_context.get("goal"),
                    metadata=candidate_context.get("metadata", {}),
                )
                EvaluationRecord.model_validate(candidate)
                connection.execute(
                    "UPDATE v2_evaluations SET result_json=?, evaluator_name=?, status=?, score=?, run_id=? WHERE id=?",
                    (
                        _json(compact),
                        name,
                        status,
                        score,
                        str(run_value) if run_value is not None else None,
                        str(row["id"]),
                    ),
                )
            except Exception:
                # Malformed legacy input must not make the database unusable.
                continue

    def _migrate_legacy_profiles(self, connection: _CompatConnection) -> None:
        self._begin(connection, immediate=True)
        try:
            self._migrate_legacy_profiles_unlocked(connection)
            self._commit(connection)
        except BaseException:
            self._rollback(connection)
            raise

    def _migrate_legacy_profiles_unlocked(self, connection: _CompatConnection) -> None:
        """Import legacy profile rows into v2 exactly once.

        Runs/events and probe attestations intentionally remain legacy-only.
        A v2 row wins both ID and name conflicts; conflicting legacy data is
        left untouched and surfaced as a warning for operators.
        """
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        families = (
            ("server", "mcp_profiles", "mcp_profile_revisions", "mcp_json"),
            ("harness", "harness_profiles", "harness_profile_revisions", None),
        )
        for kind, profile_table, revision_table, value_column in families:
            if profile_table not in tables or revision_table not in tables:
                continue
            profiles = connection.execute(f"SELECT * FROM {profile_table}").fetchall()
            for profile in profiles:
                profile_id = str(profile["id"])
                # Legacy metadata crossed the old persistence boundary without
                # the redaction applied by v2 create/update.  Project it
                # before using it for either conflict checks or insertion.
                try:
                    name_value = redact_for_persistence(
                        str(profile["name"]),
                        config=self._redaction_config,
                        path="$.legacy_profile.name",
                    )
                    description_value = redact_for_persistence(
                        str(profile["description"] or ""),
                        config=self._redaction_config,
                        path="$.legacy_profile.description",
                    )
                except Exception:
                    warnings.warn(
                        f"skipping legacy {kind} profile: metadata is invalid",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    continue
                if not isinstance(name_value, str) or not isinstance(
                    description_value, str
                ):
                    warnings.warn(
                        f"skipping legacy {kind} profile: metadata is invalid",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    continue
                name = name_value
                description = description_value
                v2_table = f"v2_{kind}_profiles"
                existing_id = connection.execute(
                    f"SELECT id FROM {v2_table} WHERE id=?", (profile_id,)
                ).fetchone()
                existing_name = connection.execute(
                    f"SELECT id FROM {v2_table} WHERE name=?", (name,)
                ).fetchone()
                revisions = connection.execute(
                    f"SELECT * FROM {revision_table} WHERE profile_id=? ORDER BY revision_number,id",
                    (profile_id,),
                ).fetchall()
                rev_table = f"v2_{kind}_profile_revisions"
                legacy_values: list[tuple[Any, Any]] = []
                for revision in revisions:
                    try:
                        if kind == "server":
                            value = revision[value_column]  # type: ignore[index]
                            if isinstance(value, str):
                                value = _loads(value)
                            if not isinstance(value, Mapping):
                                raise ValueError("MCP revision is not an object")
                            validate_mcp_config(dict(value))
                            value = dict(value)
                        else:
                            manifest = revision["manifest"]
                            if isinstance(manifest, str):
                                manifest = _loads(manifest)
                            if not isinstance(manifest, Mapping):
                                raise ValueError("harness manifest is not an object")
                            value = {
                                "manifest": validate_manifest(dict(manifest))[
                                    "manifest"
                                ],
                                "trusted_unsandboxed": bool(
                                    revision["trusted_unsandboxed"]
                                ),
                            }
                        safe_value = serialize_durable(
                            value,
                            config=self._redaction_config,
                            path=f"$.legacy_profile.{kind}.revision",
                        )
                    except Exception:
                        warnings.warn(
                            f"skipping legacy {kind} profile revision: malformed",
                            RuntimeWarning,
                            stacklevel=2,
                        )
                        continue
                    legacy_values.append((revision, safe_value))
                if revisions and not legacy_values:
                    # A profile with no usable revision cannot be selected or
                    # executed.  Keep the legacy source untouched and avoid
                    # importing an unusable v2 shell; profiles with no legacy
                    # revision rows at all remain importable for diagnostics.
                    warnings.warn(
                        f"skipping legacy {kind} profile: no valid revisions",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    continue
                valid_current = {str(revision["id"]) for revision, _ in legacy_values}
                legacy_current = profile["current_revision_id"]
                if (
                    legacy_current is not None
                    and str(legacy_current) not in valid_current
                ):
                    if revisions:
                        warnings.warn(
                            f"legacy {kind} profile has no valid current revision",
                            RuntimeWarning,
                            stacklevel=2,
                        )
                    legacy_current = None
                if existing_id is not None or existing_name is not None:
                    same = False
                    if existing_id is not None:
                        current = connection.execute(
                            f"SELECT * FROM {v2_table} WHERE id=?", (profile_id,)
                        ).fetchone()
                        same = bool(
                            current is not None
                            and str(current["name"]) == name
                            and str(current["description"] or "") == description
                            and bool(current["archived"]) == bool(profile["archived"])
                            and str(current["current_revision_id"] or "")
                            == str(legacy_current or "")
                            and str(current["created_at"]) == str(profile["created_at"])
                            and str(current["updated_at"]) == str(profile["updated_at"])
                        )
                        if same:
                            rows = connection.execute(
                                f"SELECT id,revision_number,value_json,created_at FROM {rev_table} WHERE profile_id=? ORDER BY revision_number,id",
                                (profile_id,),
                            ).fetchall()
                            same = len(rows) == len(legacy_values) and all(
                                str(row["id"]) == str(revision["id"])
                                and int(row["revision_number"])
                                == int(revision["revision_number"])
                                and _json(_loads(row["value_json"], {})) == _json(value)
                                and str(row["created_at"])
                                == str(revision["created_at"])
                                for row, (revision, value) in zip(
                                    rows, legacy_values, strict=True
                                )
                            )
                    if not same:
                        warnings.warn(
                            f"v2 {kind} profile wins legacy ID/name conflict",
                            RuntimeWarning,
                            stacklevel=2,
                        )
                    continue
                if any(
                    connection.execute(
                        f"SELECT 1 FROM {rev_table} WHERE id=?", (str(rev["id"]),)
                    ).fetchone()
                    is not None
                    for rev, _value in legacy_values
                ):
                    warnings.warn(
                        f"skipping legacy {kind} profile: revision ID conflicts with v2",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    continue
                if len(
                    {
                        int(revision["revision_number"])
                        for revision, _value in legacy_values
                    }
                ) != len(legacy_values):
                    warnings.warn(
                        f"skipping legacy {kind} profile: duplicate revision number",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    continue
                created = str(profile["created_at"])
                updated = str(profile["updated_at"])
                connection.execute(
                    f"INSERT INTO {v2_table}(id,name,description,archived,current_revision_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        profile_id,
                        name,
                        description,
                        int(bool(profile["archived"])),
                        None,
                        created,
                        updated,
                    ),
                )
                for revision, value in legacy_values:
                    connection.execute(
                        f"INSERT INTO {rev_table}(id,profile_id,revision_number,value_json,created_at) VALUES(?,?,?,?,?)",
                        (
                            str(revision["id"]),
                            profile_id,
                            int(revision["revision_number"]),
                            _json(value),
                            str(revision["created_at"]),
                        ),
                    )
                if legacy_current is not None:
                    connection.execute(
                        f"UPDATE {v2_table} SET current_revision_id=? WHERE id=?",
                        (str(legacy_current), profile_id),
                    )

    @staticmethod
    def _begin(connection: _CompatConnection, *, immediate: bool = False) -> None:
        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")

    @staticmethod
    def _commit(connection: _CompatConnection) -> None:
        connection.execute("COMMIT")

    @staticmethod
    def _rollback(connection: _CompatConnection) -> None:
        if connection.in_transaction:
            connection.execute("ROLLBACK")

    def close(self) -> None:
        """Dispose SQLAlchemy's pool and release all DBAPI connections."""
        self._engine.dispose()


class SQLiteArtifactStore(_SqliteBase):
    """Filesystem-backed content-addressed artifact store with SQLite refs."""

    def __init__(
        self, database: str | Path, blob_root: str | Path | None = None, **kwargs: Any
    ) -> None:
        config = kwargs.pop("config", None)
        super().__init__(database, **kwargs)
        self._blob_store = FilesystemBlobStore(blob_root or f"{self.database}.blobs")
        self.blob_root = self._blob_store.root
        self._redaction_config = config or RedactionConfig.from_environment()

    def put(
        self,
        execution_id: ExecutionId | str,
        name: str,
        content: bytes,
        *,
        media_type: str | None = None,
    ) -> ArtifactRef:
        if not isinstance(content, bytes):
            raise TypeError("artifact content must be bytes")
        if not name:
            raise ValueError("artifact name must not be empty")
        safe_content = redact_artifact_bytes(
            content, config=self._redaction_config
        ).data
        safe_name = redact_for_persistence(
            name, config=self._redaction_config, path="$.artifact.name"
        )
        safe_media_type = redact_for_persistence(
            media_type, config=self._redaction_config, path="$.artifact.media_type"
        )
        if not isinstance(safe_name, str) or (
            safe_media_type is not None and not isinstance(safe_media_type, str)
        ):
            raise StorageError("artifact metadata could not be redacted")
        execution_key = _execution_key(execution_id)
        blob = self._blob_store.put(safe_content)
        digest = blob.sha256
        path = blob.path
        artifact_id = ArtifactId(_new_id("artifact"))
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            if (
                connection.execute(
                    "SELECT 1 FROM v2_executions WHERE id=?", (execution_key,)
                ).fetchone()
                is None
            ):
                raise StorageConflict("execution does not exist")
            connection.execute(
                "INSERT INTO v2_blobs(sha256,size_bytes,compressed_size,media_type,storage_key,ref_count,created_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(sha256) DO UPDATE SET ref_count=ref_count+1",
                (
                    digest,
                    len(safe_content),
                    blob.compressed_size_bytes,
                    safe_media_type,
                    str(path.relative_to(self.blob_root)),
                    1,
                    _iso(_utcnow()),
                ),
            )
            # The upsert above increments existing rows but initializes a new
            # row with one reference.  New artifact rows are always one ref.
            connection.execute(
                "INSERT INTO v2_artifacts(id,execution_id,name,media_type,size_bytes,sha256,redacted,created_at) VALUES(?,?,?,?,?,?,1,?)",
                (
                    str(artifact_id.root),
                    execution_key,
                    safe_name,
                    safe_media_type,
                    len(safe_content),
                    digest,
                    _iso(_utcnow()),
                ),
            )
            self._commit(connection)
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()
        return ArtifactRef(
            artifact_id=artifact_id,
            execution_id=ExecutionId(execution_key),
            name=safe_name,
            media_type=safe_media_type,
            size_bytes=len(safe_content),
            sha256=digest,
            redacted=True,
        )

    def _resolve(self, artifact: ArtifactRef | ArtifactId | str) -> _CompatRow:
        key = str(
            artifact.artifact_id.root
            if isinstance(artifact, ArtifactRef)
            else artifact.root
            if isinstance(artifact, ArtifactId)
            else artifact
        )
        with self._connect() as connection:
            row = connection.execute(
                "SELECT a.*, b.storage_key, b.size_bytes AS blob_size, b.media_type AS blob_media_type "
                "FROM v2_artifacts a JOIN v2_blobs b ON b.sha256=a.sha256 WHERE a.id=?",
                (key,),
            ).fetchone()
        if row is None:
            raise ArtifactNotFound("artifact not found")
        return cast(_CompatRow, row)

    def get(self, artifact: ArtifactRef | ArtifactId | str) -> bytes:
        row = self._resolve(artifact)
        try:
            return self._blob_store.read(
                str(row["sha256"]), size_bytes=int(row["blob_size"])
            )
        except (ArtifactNotFound, BlobIntegrityError):
            raise

    def get_ref(self, artifact_id: ArtifactId | str) -> ArtifactRef:
        row = self._resolve(artifact_id)
        return ArtifactRef(
            artifact_id=ArtifactId(str(row["id"])),
            execution_id=ExecutionId(str(row["execution_id"])),
            name=str(row["name"]),
            media_type=row["media_type"],
            size_bytes=int(row["size_bytes"]),
            sha256=str(row["sha256"]),
            redacted=True,
        )

    def iter_refs(
        self, execution_id: ExecutionId | str | None = None
    ) -> Iterator[ArtifactRef]:
        with self._connect() as connection:
            if execution_id is None:
                rows = connection.execute(
                    "SELECT * FROM v2_artifacts ORDER BY created_at,id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM v2_artifacts WHERE execution_id=? ORDER BY created_at,id",
                    (_execution_key(execution_id),),
                ).fetchall()
        return iter(
            ArtifactRef(
                artifact_id=ArtifactId(str(row["id"])),
                execution_id=ExecutionId(str(row["execution_id"])),
                name=str(row["name"]),
                media_type=row["media_type"],
                size_bytes=int(row["size_bytes"]),
                sha256=str(row["sha256"]),
                redacted=True,
            )
            for row in rows
        )

    def delete(self, artifact: ArtifactRef | ArtifactId | str) -> None:
        key = str(
            artifact.artifact_id.root
            if isinstance(artifact, ArtifactRef)
            else artifact.root
            if isinstance(artifact, ArtifactId)
            else artifact
        )
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            row = connection.execute(
                "SELECT sha256 FROM v2_artifacts WHERE id=?", (key,)
            ).fetchone()
            if row is None:
                raise ArtifactNotFound("artifact not found")
            digest = str(row["sha256"])
            connection.execute("DELETE FROM v2_artifacts WHERE id=?", (key,))
            connection.execute(
                "UPDATE v2_blobs SET ref_count=ref_count-1 WHERE sha256=?", (digest,)
            )
            blob = connection.execute(
                "SELECT storage_key FROM v2_blobs WHERE sha256=? AND ref_count=0",
                (digest,),
            ).fetchone()
            if blob is not None:
                connection.execute("DELETE FROM v2_blobs WHERE sha256=?", (digest,))
            self._commit(connection)
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()
        self.cleanup()

    def cleanup(self) -> None:
        with self._connect() as connection:
            referenced = {
                str(row[0]): int(row[1])
                for row in connection.execute("SELECT sha256,ref_count FROM v2_blobs")
            }
        self._blob_store.garbage_collect(referenced)

    @property
    def blob_store(self) -> FilesystemBlobStore:
        """The app-owned content-addressed store used by metadata rows."""
        return self._blob_store


class _SqliteBatch(AbstractContextManager["_SqliteBatch"]):
    def __init__(self, store: SQLiteExecutionStore, execution_id: str) -> None:
        self.store = store
        self.execution_id = execution_id
        self.events: list[Event] = []
        self.done = False

    def append(self, events: Sequence[Event]) -> None:
        if self.done:
            raise StorageConflict("transaction is already closed")
        if any(
            _execution_key(event.execution_id) != self.execution_id for event in events
        ):
            raise StorageConflict(
                "all events in a transaction must belong to its execution"
            )
        self.events.extend(events)

    def commit(self) -> None:
        if not self.done:
            self.store._append(self.execution_id, tuple(self.events))
            self.done = True

    def rollback(self) -> None:
        self.events.clear()
        self.done = True

    def __enter__(self) -> _SqliteBatch:
        if self.done:
            raise StorageConflict("transaction is already closed")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return None


class SQLiteExecutionStore(_SqliteBase):
    """SQLite implementation of the public :class:`ExecutionStore` protocol."""

    # These are the only limitations accepted by TraceResult and by the
    # recorder's terminal payload contract.  Store-owned terminalization (for
    # example queued cancellation or lease interruption) has no provider
    # cleanup/capture phase to certify, so it must be explicitly partial.
    _TERMINAL_LIMITATIONS = frozenset(
        {"cleanup_failed", "persistence_failed", "capture_incomplete", "partial_trace"}
    )
    _migrate_profiles_enabled = True

    def __init__(
        self,
        database: str | Path,
        *,
        blob_root: str | Path | None = None,
        config: RedactionConfig | None = None,
        capture_config: CaptureOptions | None = None,
        payload_blob_threshold: int = 64 * 1024,
        **kwargs: Any,
    ) -> None:
        if payload_blob_threshold < 0:
            raise ValueError("payload_blob_threshold must be non-negative")
        # Store initialization performs legacy migration before returning, so
        # migration must see the same explicit redaction policy used by all
        # later profile and execution writes.
        self._redaction_config = config or RedactionConfig.from_environment()
        super().__init__(database, **kwargs)
        self._capture_config = (
            capture_config if capture_config is not None else CaptureOptions()
        )
        self.artifacts = SQLiteArtifactStore(
            database,
            blob_root,
            config=self._redaction_config,
            busy_timeout_ms=self.busy_timeout_ms,
            wal=False,
        )
        self.payload_blob_threshold = payload_blob_threshold
        self._callbacks: dict[str, list[EventCallback]] = {}
        self._callback_lock = threading.RLock()

    def ensure_project(self, project_id: str, project_name: str) -> tuple[str, str]:
        """Register a stable project identity and refresh its display name."""
        from .._types.base import ProjectId

        identifier = ProjectId(project_id).root
        name = str(project_name).strip()
        if not name or len(name) > 256:
            raise ValueError("project_name must contain 1-256 characters")
        now = _iso(_utcnow())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO v2_projects(id,project_name,created_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET project_name=excluded.project_name, updated_at=excluded.updated_at",
                (identifier, name, now, now),
            )
        return identifier, name

    def get_project(self, project_id: str) -> tuple[str, str] | None:
        from .._types.base import ProjectId

        identifier = ProjectId(project_id).root
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,project_name FROM v2_projects WHERE id=?", (identifier,)
            ).fetchone()
        return None if row is None else (str(row[0]), str(row[1]))

    def ensure_suite(self, suite_name: str, project_id: str | None = None) -> Suite:
        normalized = normalize_suite_name(suite_name)
        from ..types import ProjectId

        project = None if project_id is None else ProjectId(project_id)
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            row = connection.execute(
                "SELECT id,suite_name,project_id FROM v2_suites WHERE suite_name=? AND (project_id IS ? OR project_id=?)",
                (
                    normalized,
                    None if project is None else project.root,
                    None if project is None else project.root,
                ),
            ).fetchone()
            if row is None:
                row_max = connection.execute(
                    "SELECT COALESCE(MAX(id),0)+1 FROM v2_suites"
                ).fetchone()
                suite_id = generated_suite_id(int(row_max[0]))
                connection.execute(
                    "INSERT INTO v2_suites(id,suite_name,project_id) VALUES(?,?,?)",
                    (
                        suite_id.root,
                        normalized,
                        None if project is None else project.root,
                    ),
                )
                result = Suite(suite_id, normalized, normalized, project)
            else:
                result = Suite(SuiteId(int(row[0])), str(row[1]), str(row[1]), project)
            self._commit(connection)
            return result
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    @staticmethod
    def _ensure_suite_connection(
        connection: _CompatConnection, suite_name: str, project_id: str | None = None
    ) -> Suite:
        normalized = normalize_suite_name(suite_name)
        from ..types import ProjectId

        project = None if project_id is None else ProjectId(project_id)
        row = connection.execute(
            "SELECT id,suite_name,project_id FROM v2_suites WHERE suite_name=? AND (project_id IS ? OR project_id=?)",
            (
                normalized,
                None if project is None else project.root,
                None if project is None else project.root,
            ),
        ).fetchone()
        if row is not None:
            return Suite(SuiteId(int(row[0])), str(row[1]), str(row[1]), project)
        next_id = int(
            connection.execute(
                "SELECT COALESCE(MAX(id),0)+1 FROM v2_suites"
            ).fetchone()[0]
        )
        connection.execute(
            "INSERT INTO v2_suites(id,suite_name,project_id) VALUES(?,?,?)",
            (next_id, normalized, None if project is None else project.root),
        )
        return Suite(SuiteId(next_id), normalized, normalized, project)

    def get_suite(self, suite_id: SuiteId | str) -> Suite | None:
        raw_id = suite_id.root if isinstance(suite_id, SuiteId) else suite_id
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,suite_name,project_id FROM v2_suites WHERE id=?",
                (int(raw_id),),
            ).fetchone()
        return (
            None
            if row is None
            else Suite(
                SuiteId(int(row[0])),
                str(row[1]),
                str(row[1]),
                ProjectId(str(row[2])) if row[2] else None,
            )
        )

    def get_suite_by_name(
        self, suite_name: str, project_id: str | None = None
    ) -> Suite | None:
        normalized = normalize_suite_name(suite_name)
        project = ProjectId(project_id).root if project_id is not None else None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,suite_name,project_id FROM v2_suites WHERE suite_name=? AND project_id IS ?",
                (normalized, project),
            ).fetchone()
        return (
            None
            if row is None
            else Suite(
                SuiteId(int(row[0])),
                str(row[1]),
                str(row[1]),
                ProjectId(str(row[2])) if row[2] else None,
            )
        )

    def list_suites(self) -> tuple[Suite, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id,suite_name,project_id FROM v2_suites ORDER BY suite_name,project_id"
            ).fetchall()
        return tuple(
            Suite(
                SuiteId(int(row[0])),
                str(row[1]),
                str(row[1]),
                ProjectId(str(row[2])) if row[2] else None,
            )
            for row in rows
        )

    def create(
        self,
        snapshot: ExecutionState,
        *,
        specification: Mapping[str, Any] | None = None,
        provenance: Mapping[str, Any] | None = None,
        server_bindings: Sequence[Mapping[str, Any]] = (),
        harness_binding: Mapping[str, Any] | None = None,
        parent_execution_id: ExecutionId | str | None = None,
        run_id: RunId | str | None = None,
    ) -> None:
        suite_id = None
        suite_name = (
            str(specification["suite_name"]).strip()
            if specification is not None and specification.get("suite_name")
            else snapshot.suite_name
        )
        key = _execution_key(snapshot.execution_id)
        try:
            from .._test_runs import associate_execution

            associate_execution(snapshot.execution_id, run_id=run_id or snapshot.run_id)
        except Exception:
            # Test recording is observational and must never break execution.
            pass
        snapshot_run = snapshot.run_id.root if snapshot.run_id is not None else None
        fallback_run = run_id.root if isinstance(run_id, RunId) else run_id
        run_key = snapshot_run or fallback_run
        if run_key is not None and snapshot.run_id is None:
            snapshot = snapshot.model_copy(update={"run_id": RunId(run_key)})
        safe_snapshot = snapshot.model_dump(mode="json")
        # Specifications and bindings are later rehydrated and may be
        # executed. Evidence redaction would turn a typed SecretReference
        # under (for example) ``Authorization`` into a runnable
        # ``[REDACTED]`` literal, so use the dedicated durable projection.
        safe_spec = (
            serialize_durable(
                specification,
                config=self._redaction_config,
                path="$.execution.specification",
            )
            if specification is not None
            else None
        )
        safe_provenance = (
            redact_for_persistence(
                provenance, config=self._redaction_config, path="$.execution.provenance"
            )
            if provenance is not None
            else None
        )
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            suite = (
                self._ensure_suite_connection(
                    connection,
                    suite_name,
                    snapshot.project_id.root
                    if snapshot.project_id is not None
                    else None,
                )
                if suite_name
                else None
            )
            suite_id = suite.id.root if suite else None
            if suite is not None or snapshot.suite_id is not None:
                snapshot = snapshot.model_copy(
                    update={
                        "suite_id": suite.id if suite else None,
                        "suite_name": suite.name if suite else suite_name,
                    }
                )
                safe_snapshot = snapshot.model_dump(mode="json")
            parent_key = (
                _execution_key(parent_execution_id)
                if parent_execution_id is not None
                else None
            )
            connection.execute(
                "INSERT INTO v2_executions(id,snapshot_json,specification_json,provenance_json,parent_execution_id,created_at,run_id,suite_id,project_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    _json(safe_snapshot),
                    _json(safe_spec) if safe_spec is not None else None,
                    _json(safe_provenance) if safe_provenance is not None else None,
                    parent_key,
                    _iso(snapshot.created_at),
                    run_key,
                    suite_id,
                    snapshot.project_id.root
                    if snapshot.project_id is not None
                    else None,
                ),
            )
            for ordinal, binding in enumerate(server_bindings):
                value = dict(binding)
                connection.execute(
                    "INSERT INTO v2_execution_server_bindings(execution_id,ordinal,profile_id,revision_id,binding_json) VALUES(?,?,?,?,?)",
                    (
                        key,
                        ordinal,
                        value.get("profile_id"),
                        value.get("revision_id"),
                        _json(
                            serialize_durable(
                                value,
                                config=self._redaction_config,
                                path="$.execution.server_binding",
                            )
                        ),
                    ),
                )
            if harness_binding is not None:
                value = dict(harness_binding)
                connection.execute(
                    "INSERT INTO v2_execution_harness_bindings(execution_id,profile_id,revision_id,binding_json) VALUES(?,?,?,?)",
                    (
                        key,
                        value.get("profile_id"),
                        value.get("revision_id"),
                        _json(
                            serialize_durable(
                                value,
                                config=self._redaction_config,
                                path="$.execution.harness_binding",
                            )
                        ),
                    ),
                )
            self._commit(connection)
        except Exception as exc:
            self._rollback(connection)
            if not _is_integrity_error(exc):
                raise
            if "foreign key" in str(exc).lower():
                raise StorageError(
                    "execution references an unregistered project or parent"
                ) from exc
            raise StorageConflict("execution already exists") from exc
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    create_execution = create

    def save_test_run(self, run_id: str, value: Mapping[str, object]) -> None:
        key = str(getattr(run_id, "root", run_id))
        safe = redact_for_persistence(
            dict(value), config=self._redaction_config, path="$.test_run"
        )
        if not isinstance(safe, Mapping):
            raise StorageError("test run manifest could not be redacted")
        now = _iso(_utcnow())
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            connection.execute(
                "INSERT INTO v2_test_runs(run_id,record_json,created_at,updated_at,project_id) VALUES(?,?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET record_json=excluded.record_json,updated_at=excluded.updated_at,project_id=excluded.project_id",
                (key, _json(safe), now, now, safe.get("project_id")),
            )
            self._commit(connection)
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    def get_test_run(self, run_id: str) -> Mapping[str, object] | None:
        key = str(getattr(run_id, "root", run_id))
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_json FROM v2_test_runs WHERE run_id=?", (key,)
            ).fetchone()
        if row is None:
            return None
        value = _loads(row[0])
        return dict(value) if isinstance(value, Mapping) else None

    def list_test_runs(self) -> tuple[Mapping[str, object], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT record_json FROM v2_test_runs ORDER BY created_at,run_id"
            ).fetchall()
        values: list[Mapping[str, object]] = []
        for row in rows:
            value = _loads(row[0])
            if isinstance(value, Mapping):
                values.append(dict(value))
        return tuple(values)

    def save_test_result(
        self, run_id: str, attempt_id: str, value: Mapping[str, object]
    ) -> None:
        key = str(getattr(run_id, "root", run_id))
        safe = redact_for_persistence(
            dict(value), config=self._redaction_config, path="$.test_result"
        )
        if not isinstance(safe, Mapping):
            raise StorageError("test result could not be redacted")
        suite_ref = None
        now = _iso(_utcnow())
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            suite = (
                self._ensure_suite_connection(
                    connection,
                    str(safe["suite_name"]),
                    str(safe.get("project_id")) if safe.get("project_id") else None,
                )
                if safe.get("suite_name")
                else None
            )
            suite_ref = suite.id.root if suite else None
            if suite is not None:
                safe = dict(safe)
                safe["suite_id"] = suite.id.root
                safe["suite_name"] = suite.name
            # xdist workers can publish their first attempt before the
            # controller's manifest update reaches the database.
            if (
                connection.execute(
                    "SELECT 1 FROM v2_test_runs WHERE run_id=?", (key,)
                ).fetchone()
                is None
            ):
                connection.execute(
                    "INSERT INTO v2_test_runs(run_id,record_json,created_at,updated_at,project_id) VALUES(?,?,?,?,?)",
                    (
                        key,
                        _json(
                            {
                                "schema_version": 1,
                                "run_id": key,
                                "status": "running",
                                "created_at": now,
                                "finished_at": None,
                            }
                        ),
                        now,
                        now,
                        safe.get("project_id"),
                    ),
                )
            connection.execute(
                "INSERT INTO v2_test_results(run_id,attempt_id,record_json,created_at,updated_at,suite_id) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(run_id,attempt_id) DO UPDATE SET record_json=excluded.record_json,updated_at=excluded.updated_at,suite_id=excluded.suite_id",
                (key, str(attempt_id), _json(safe), now, now, suite_ref),
            )
            self._commit(connection)
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    def list_test_results(self, run_id: str) -> tuple[Mapping[str, object], ...]:
        key = str(getattr(run_id, "root", run_id))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT record_json FROM v2_test_results WHERE run_id=? ORDER BY attempt_id",
                (key,),
            ).fetchall()
        values: list[Mapping[str, object]] = []
        for row in rows:
            value = _loads(row[0])
            if isinstance(value, Mapping):
                values.append(dict(value))
        return tuple(values)

    def get_snapshot(self, execution_id: ExecutionId | str) -> ExecutionState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM v2_executions WHERE id=? AND deleted_at IS NULL",
                (_execution_key(execution_id),),
            ).fetchone()
        return ExecutionState.model_validate(_loads(row[0])) if row else None

    def get_execution_spec(
        self, execution_id: ExecutionId | str
    ) -> ExecutionSpec | None:
        """Return the immutable typed submission spec, if one was saved."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT specification_json FROM v2_executions WHERE id=? AND deleted_at IS NULL",
                (_execution_key(execution_id),),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        value = _upgrade_persisted_execution_spec(_loads(row[0]))
        try:
            return TypeAdapter(ExecutionSpec).validate_python(value)
        except (TypeError, ValueError, ValidationError) as exc:
            raise StorageError("persisted execution specification is invalid") from exc

    get_spec = get_execution_spec

    def list_executions(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        lifecycle: ExecutionStatus | str | None = None,
        outcome: ExecutionOutcome | str | None = None,
        run_id: str | None = None,
        suite_id: int | None = None,
        project_id: str | None = None,
    ) -> ExecutionPage:
        page = ExecutionPage(limit=limit, offset=offset)
        lifecycle_value = ExecutionStatus(lifecycle) if lifecycle is not None else None
        outcome_value = ExecutionOutcome(outcome) if outcome is not None else None
        clauses = ["deleted_at IS NULL"]
        parameters: list[Any] = []
        if lifecycle_value is not None:
            clauses.append("json_extract(snapshot_json, '$.lifecycle')=?")
            parameters.append(lifecycle_value.value)
        if outcome_value is not None:
            clauses.append("json_extract(snapshot_json, '$.outcome')=?")
            parameters.append(outcome_value.value)
        if run_id is not None:
            clauses.append("run_id=?")
            parameters.append(str(getattr(run_id, "root", run_id)))
        if suite_id is not None:
            clauses.append("suite_id=?")
            parameters.append(int(suite_id))
        if project_id is not None:
            clauses.append("project_id=?")
            parameters.append(str(project_id))
        where = " AND ".join(clauses)
        with self._connect() as connection:
            total_row = connection.execute(
                f"SELECT COUNT(*) FROM v2_executions WHERE {where}", tuple(parameters)
            ).fetchone()
            rows = connection.execute(
                f"SELECT snapshot_json FROM v2_executions WHERE {where} ORDER BY json_extract(snapshot_json, '$.created_at') DESC, id DESC LIMIT ? OFFSET ?",
                (*tuple(parameters), limit, offset),
            ).fetchall()
        snapshots = [ExecutionState.model_validate(_loads(row[0])) for row in rows]
        return page.model_copy(
            update={
                "items": tuple(snapshots),
                "total": int(total_row[0]) if total_row else 0,
            }
        )

    def get_report(
        self,
        execution_id: ExecutionId | str,
        *,
        after_sequence: int = -1,
        event_limit: int | None = None,
        artifact_limit: int | None = None,
    ) -> ExecutionReport | None:
        snapshot = self.get_snapshot(execution_id)
        if snapshot is None:
            return None
        if (
            after_sequence < -1
            or (event_limit is not None and event_limit < 1)
            or (artifact_limit is not None and artifact_limit < 1)
        ):
            raise ValueError("invalid report event cursor or limit")
        if event_limit is None:
            all_events = self.events(execution_id)
            events, event_count = all_events, len(all_events)
        else:
            page, event_count = self._events_page(
                _execution_key(execution_id),
                after_sequence=after_sequence,
                event_limit=event_limit,
            )
            events = page[:event_limit]
        key = _execution_key(execution_id)
        with self._connect() as connection:
            terminal_row = connection.execute(
                "SELECT event_json FROM v2_events WHERE execution_id=? AND json_extract(event_json, '$.kind')=? ORDER BY sequence DESC LIMIT 1",
                (key, EventKind.EXECUTION_FINISHED.value),
            ).fetchone()
        terminal_events = (
            (self._restore_event(_loads(terminal_row[0])),) if terminal_row else events
        )
        direct_result, error, evidence = _report_fields(terminal_events)
        all_artifacts = tuple(self.artifacts.iter_refs(execution_id))
        artifacts = (
            all_artifacts if artifact_limit is None else all_artifacts[:artifact_limit]
        )
        return ExecutionReport(
            snapshot=snapshot,
            events=events,
            artifacts=artifacts,
            direct_result=direct_result,
            error=error,
            evidence=evidence,
            turns=tuple(
                result
                for _snapshot, result in self.turns(execution_id)
                if result is not None
            ),
            evaluations=self.persisted_evaluations(execution_id),
            event_count=event_count,
            events_truncated=event_limit is not None and event_count > event_limit,
            next_after_sequence=events[-1].sequence if events else after_sequence,
            artifact_count=len(all_artifacts),
            artifacts_truncated=artifact_limit is not None
            and len(all_artifacts) > artifact_limit,
        )

    def get_trace(self, execution_id: ExecutionId | str) -> TraceResult | None:
        snapshot = self.get_snapshot(execution_id)
        if snapshot is None:
            return None
        events = self._events(_execution_key(execution_id))
        created_events = [
            event for event in events if event.kind is EventKind.EXECUTION_CREATED
        ]
        if (
            not events
            or events[0].sequence != 0
            or events[0].kind is not EventKind.EXECUTION_CREATED
            or len(created_events) != 1
        ):
            raise TraceUnavailable("persisted execution.created evidence is malformed")
        trace_id = events[0].payload.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id:
            raise TraceUnavailable("trace identity evidence is unavailable")
        try:
            typed_trace_id = TraceId(trace_id)
        except ValueError:
            raise TraceUnavailable("trace identity evidence is invalid") from None
        if typed_trace_id.root != trace_id:
            raise TraceUnavailable("trace identity evidence is not stable")
        terminal = [
            event for event in events if event.kind is EventKind.EXECUTION_FINISHED
        ]
        if not terminal:
            raise TraceNotFinalized("execution has not been finalized")
        if len(terminal) != 1 or terminal[0] is not events[-1]:
            raise TraceUnavailable("persisted trace terminal evidence is malformed")
        final = terminal[-1]
        outcome = final.payload.get("outcome")
        completeness = final.payload.get("completeness")
        raw_limitations = final.payload.get("limitations")
        if (
            not isinstance(outcome, str)
            or outcome not in {item.value for item in ExecutionOutcome}
            or completeness not in {"complete", "partial"}
            or not isinstance(raw_limitations, (list, tuple))
            or any(
                not isinstance(item, str) or not item.strip()
                for item in raw_limitations
            )
        ):
            raise TraceUnavailable("persisted execution.finished evidence is malformed")
        limitations = tuple(raw_limitations)
        try:
            typed_outcome = ExecutionOutcome(outcome)
        except ValueError:
            raise TraceUnavailable("persisted execution outcome is invalid") from None
        if (
            snapshot.lifecycle is not ExecutionStatus.FINISHED
            or snapshot.outcome != typed_outcome
        ):
            raise TraceUnavailable(
                "persisted snapshot outcome conflicts with terminal evidence"
            )
        try:
            return TraceResult(
                trace_id=typed_trace_id,
                execution_id=snapshot.execution_id,
                completeness=cast(Literal["complete", "partial"], completeness),
                highest_sequence=events[-1].sequence if events else 0,
                events=events,
                limitations=limitations,
            )
        except (TypeError, ValueError):
            raise TraceUnavailable("persisted trace evidence is malformed") from None

    def get_trace_view(self, execution_id: ExecutionId | str) -> TraceView | None:
        trace = self.get_trace(execution_id)
        return trace.view() if trace is not None else None

    def save_snapshot(self, snapshot: ExecutionState) -> None:
        key = _execution_key(snapshot.execution_id)
        actual = self.get_snapshot(key)
        if actual is None:
            raise StorageConflict("execution does not exist")
        derived = self._derive_snapshot(key)
        if snapshot != derived:
            raise StorageConflict("snapshot is derived from committed events")
        with self._connect() as connection:
            connection.execute(
                "UPDATE v2_executions SET snapshot_json=? WHERE id=?",
                (_json(snapshot.model_dump(mode="json")), key),
            )

    update_snapshot = save_snapshot

    def _events(self, execution_id: str) -> tuple[Event, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_json FROM v2_events WHERE execution_id=? ORDER BY sequence",
                (execution_id,),
            ).fetchall()
        return tuple(self._restore_event(_loads(row[0])) for row in rows)

    def _restore_event(self, value: Mapping[str, Any]) -> Event:
        event = Event.model_validate(value)
        marker = event.payload.get("__mcp_pal_blob__")
        if isinstance(marker, Mapping) and event.payload_ref is not None:
            payload = self.artifacts.blob_store.read(
                event.payload_ref.sha256,
                size_bytes=event.payload_ref.size_bytes,
            )
            try:
                decoded = json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BlobIntegrityError(
                    "event payload blob is not valid JSON"
                ) from exc
            if not isinstance(decoded, Mapping):
                raise BlobIntegrityError("event payload blob is not a JSON object")
            event = event.model_copy(update={"payload": decoded})
        return event

    def _events_page(
        self, execution_id: str, *, after_sequence: int, event_limit: int
    ) -> tuple[tuple[Event, ...], int]:
        with self._connect() as connection:
            count_row = connection.execute(
                "SELECT COUNT(*) FROM v2_events WHERE execution_id=? AND sequence>?",
                (execution_id, after_sequence),
            ).fetchone()
            rows = connection.execute(
                "SELECT event_json FROM v2_events WHERE execution_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (execution_id, after_sequence, event_limit),
            ).fetchall()
        return tuple(self._restore_event(_loads(row[0])) for row in rows), int(
            count_row[0]
        ) if count_row else 0

    def _derive_snapshot(
        self, execution_id: str, events: Sequence[Event] | None = None
    ) -> ExecutionState:
        existing = self.get_snapshot(execution_id)
        if existing is None:
            raise StorageConflict("execution does not exist")
        lifecycle, outcome = existing.lifecycle, existing.outcome
        created_at, finished_at = existing.created_at, existing.finished_at
        values = tuple(events if events is not None else self._events(execution_id))
        for event in values:
            if event.kind is EventKind.EXECUTION_CREATED:
                if lifecycle is ExecutionStatus.FINISHED:
                    raise StorageConflict(
                        "terminal execution cannot receive more events"
                    )
                created_at, lifecycle, outcome, finished_at = (
                    event.timestamp,
                    ExecutionStatus.CREATED,
                    None,
                    None,
                )
            elif event.kind is EventKind.EXECUTION_STATE_CHANGED:
                if lifecycle is ExecutionStatus.FINISHED:
                    raise StorageConflict(
                        "terminal execution cannot receive more events"
                    )
                try:
                    next_lifecycle = ExecutionStatus(
                        event.payload.get("lifecycle", event.payload.get("state"))
                    )
                except (TypeError, ValueError):
                    raise StorageConflict(
                        "execution state payload is invalid"
                    ) from None
                if next_lifecycle is ExecutionStatus.FINISHED:
                    raise StorageConflict(
                        "execution state event cannot finish an execution"
                    )
                lifecycle = next_lifecycle
            elif event.kind is EventKind.EXECUTION_FINISHED:
                if lifecycle is ExecutionStatus.FINISHED:
                    raise StorageConflict(
                        "terminal execution cannot receive more events"
                    )
                try:
                    outcome = ExecutionOutcome(event.payload["outcome"])
                except (KeyError, TypeError, ValueError):
                    raise StorageConflict(
                        "execution terminal payload is invalid"
                    ) from None
                lifecycle, finished_at = ExecutionStatus.FINISHED, event.timestamp
        return ExecutionState(
            execution_id=ExecutionId(execution_id),
            project_id=existing.project_id,
            run_id=existing.run_id,
            suite_id=existing.suite_id,
            suite_name=existing.suite_name,
            lifecycle=lifecycle,
            outcome=outcome,
            sequence=values[-1].sequence if values else existing.sequence,
            created_at=created_at,
            finished_at=finished_at,
            provenance=existing.provenance,
        )

    def _safe_event(self, event: Event) -> Event:
        projected = redact_model_json(
            event, config=self._redaction_config, path="$.event"
        )
        try:
            safe = Event.model_validate(projected)
        except Exception:
            raise StorageError("event projection is invalid") from None
        for field in (
            "event_id",
            "execution_id",
            "sequence",
            "kind",
            "session_id",
            "turn_id",
            "server_binding",
            "connection_id",
            "correlation",
            "lifecycle_phase",
            "payload_ref",
            "raw_evidence_ref",
            "reasoning",
        ):
            if getattr(safe, field) != getattr(event, field):
                raise StorageError("event identity changed during redaction")
        return safe

    def _safe_reason(self, reason: str | None, *, path: str) -> str | None:
        projected = redact_for_persistence(
            reason, config=self._redaction_config, path=path
        )
        if projected is not None and not isinstance(projected, str):
            raise StorageError("reason could not be redacted")
        return projected

    def _terminal_payload(
        self,
        outcome: ExecutionOutcome,
        *,
        reason: str | None = None,
        reason_path: str = "$.execution.terminal.reason",
        limitations: Sequence[str] = ("capture_incomplete",),
    ) -> dict[str, Any]:
        """Build the stable payload for a store-owned terminal event.

        SQLite can close an execution without owning the provider adapter's
        cleanup or evidence capture.  Such terminal events are therefore
        partial, and only the bounded limitation vocabulary is persisted.
        Reasons are included only when supplied and are passed through the
        store's redaction policy before becoming durable evidence.
        """
        safe_limitations = tuple(
            sorted({item for item in limitations if item in self._TERMINAL_LIMITATIONS})
        )
        if not safe_limitations:
            # A store-owned terminal event cannot honestly claim complete
            # provider evidence; retain an explicit bounded limitation.
            safe_limitations = ("capture_incomplete",)
        payload: dict[str, Any] = {
            "outcome": outcome.value,
            "completeness": "partial",
            "limitations": list(safe_limitations),
        }
        safe_reason = self._safe_reason(reason, path=reason_path)
        if safe_reason:
            payload["reason"] = safe_reason
        return payload

    @staticmethod
    def _next_event_position(
        connection: _CompatConnection, execution_id: str
    ) -> tuple[int, float]:
        """Return the next sequence and a nondecreasing trace offset."""
        row = connection.execute(
            "SELECT sequence,event_json FROM v2_events WHERE execution_id=? ORDER BY sequence DESC LIMIT 1",
            (execution_id,),
        ).fetchone()
        if row is None:
            return 0, 0.0
        latest = Event.model_validate(_loads(row["event_json"]))
        return int(row["sequence"]) + 1, latest.monotonic_offset_ms

    def _ensure_created_event(
        self, connection: _CompatConnection, execution_id: str
    ) -> None:
        """Ensure store-owned terminalization has stable trace identity.

        Worker-owned cancellation/lease paths can run before a provider has
        opened a recorder.  They still need to produce the same immutable
        trace boundary as a normal recorder.  Existing events are never
        repaired here: malformed or conflicting identity is rejected so a
        terminal snapshot cannot make an invalid trace appear readable.
        """
        rows = connection.execute(
            "SELECT event_json FROM v2_events WHERE execution_id=? ORDER BY sequence",
            (execution_id,),
        ).fetchall()
        if rows:
            events = tuple(Event.model_validate(_loads(row[0])) for row in rows)
            created = tuple(
                event for event in events if event.kind is EventKind.EXECUTION_CREATED
            )
            if (
                events[0].sequence != 0
                or events[0].kind is not EventKind.EXECUTION_CREATED
                or len(created) != 1
            ):
                raise StorageConflict(
                    "persisted execution.created evidence is malformed"
                )
            trace_id = events[0].payload.get("trace_id")
            if not isinstance(trace_id, str) or not trace_id:
                raise StorageConflict("persisted trace ID is unavailable")
            try:
                typed_trace_id = TraceId(trace_id)
            except ValueError:
                raise StorageConflict("persisted trace ID is invalid") from None
            if typed_trace_id.root != trace_id:
                raise StorageConflict("persisted trace ID is not stable")
            return
        event = Event(
            event_id=EventId(_new_id("event")),
            execution_id=ExecutionId(execution_id),
            sequence=0,
            kind=EventKind.EXECUTION_CREATED,
            monotonic_offset_ms=0.0,
            payload={
                "lifecycle": ExecutionStatus.CREATED.value,
                "trace_id": f"trace-{uuid.uuid4().hex}",
            },
        )
        connection.execute(
            "INSERT INTO v2_events(id,execution_id,sequence,event_json,timestamp) VALUES(?,?,?,?,?)",
            (
                str(event.event_id.root),
                execution_id,
                event.sequence,
                _json(event.model_dump(mode="json", by_alias=True)),
                _iso(event.timestamp),
            ),
        )

    def _append(
        self,
        execution_id: str,
        events: Sequence[Event],
        *,
        raw_evidence: tuple[EvidenceRef, Any] | None = None,
    ) -> None:
        if not events:
            return
        safe_events = tuple(self._safe_event(event) for event in events)
        if any(
            _execution_key(event.execution_id) != execution_id for event in safe_events
        ):
            raise StorageConflict(
                "all events in an append must belong to one execution"
            )
        # Keep the full semantic payload in a verified compressed blob once it
        # crosses the configured threshold.  SQLite retains a small searchable
        # marker and the typed reference; readers restore the original payload
        # transparently.  A failed metadata transaction leaves only an
        # unreferenced, explicitly-GC-able file.
        persisted_events: list[Event] = []
        payload_blobs: list[tuple[Event, Any]] = []
        for event in safe_events:
            encoded_payload = _json(event.model_dump(mode="json")["payload"]).encode(
                "utf-8"
            )
            if len(encoded_payload) <= self.payload_blob_threshold:
                persisted_events.append(event)
                continue
            blob = self.artifacts.blob_store.put(encoded_payload)
            reference = PayloadRef(
                blob_id=f"event-payload-{event.event_id.root}",
                sha256=blob.sha256,
                size_bytes=blob.size_bytes,
                media_type="application/json",
                compression="gzip",
            )
            persisted = event.model_copy(
                update={
                    "payload": {
                        "__mcp_pal_blob__": {
                            "sha256": blob.sha256,
                            "size_bytes": blob.size_bytes,
                        }
                    },
                    "payload_ref": reference,
                }
            )
            persisted_events.append(persisted)
            payload_blobs.append((persisted, blob))
        connection = self._connect()
        committed: tuple[Event, ...] = ()
        try:
            self._begin(connection, immediate=True)
            if (
                connection.execute(
                    "SELECT 1 FROM v2_executions WHERE id=? AND deleted_at IS NULL",
                    (execution_id,),
                ).fetchone()
                is None
            ):
                raise StorageConflict("execution does not exist")
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence),-1) FROM v2_events WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            expected = int(row[0]) + 1
            seen: set[str] = set()
            for event in safe_events:
                if event.sequence != expected or str(event.event_id.root) in seen:
                    raise StorageConflict("event sequence or id is invalid")
                expected += 1
                seen.add(str(event.event_id.root))
            sessions = {
                str(row[0])
                for row in connection.execute(
                    "SELECT id FROM v2_sessions WHERE execution_id=?", (execution_id,)
                )
            }
            for event in safe_events:
                if event.kind is EventKind.SESSION_CREATED:
                    if (
                        event.session_id is None
                        or str(event.session_id.root) in sessions
                    ):
                        raise StorageConflict("session event is invalid")
                    sessions.add(str(event.session_id.root))
                elif event.kind is EventKind.SESSION_STATE_CHANGED and (
                    event.session_id is None
                    or str(event.session_id.root) not in sessions
                ):
                    raise StorageConflict("session does not exist")
                persisted = next(
                    item for item in persisted_events if item.event_id == event.event_id
                )
                connection.execute(
                    "INSERT INTO v2_events(id,execution_id,sequence,event_json,timestamp) VALUES(?,?,?,?,?)",
                    (
                        str(event.event_id.root),
                        execution_id,
                        event.sequence,
                        _json(persisted.model_dump(mode="json", by_alias=True)),
                        _iso(event.timestamp),
                    ),
                )
                if (
                    raw_evidence is not None
                    and event.event_id == safe_events[0].event_id
                ):
                    raw_reference, blob = raw_evidence
                    existing_blob = connection.execute(
                        "SELECT size_bytes,storage_key,compressed_size "
                        "FROM v2_blobs WHERE sha256=?",
                        (str(blob.sha256),),
                    ).fetchone()
                    if existing_blob is not None and (
                        int(existing_blob["size_bytes"]) != blob.size_bytes
                        or str(existing_blob["storage_key"])
                        != str(raw_reference.storage_key)
                        or int(existing_blob["compressed_size"])
                        != blob.compressed_size_bytes
                    ):
                        raise RawEvidenceIntegrityError(
                            "raw evidence blob metadata is inconsistent"
                        )
                    connection.execute(
                        "INSERT INTO v2_blobs(sha256,size_bytes,compressed_size,media_type,storage_key,ref_count,created_at) "
                        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(sha256) DO UPDATE SET ref_count=ref_count+1",
                        (
                            str(blob.sha256),
                            int(blob.size_bytes),
                            int(blob.compressed_size_bytes),
                            raw_reference.media_type,
                            str(raw_reference.storage_key),
                            1,
                            _iso(_utcnow()),
                        ),
                    )
                    connection.execute(
                        "INSERT INTO v2_event_blobs(event_id,sha256,role,media_type,evidence_id) VALUES(?,?,?,?,?)",
                        (
                            str(event.event_id.root),
                            str(blob.sha256),
                            "raw_evidence",
                            raw_reference.media_type,
                            raw_reference.evidence_id,
                        ),
                    )
                for blob_event, blob in payload_blobs:
                    if blob_event.event_id == event.event_id:
                        connection.execute(
                            "INSERT INTO v2_blobs(sha256,size_bytes,compressed_size,media_type,storage_key,ref_count,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(sha256) DO UPDATE SET ref_count=ref_count+1",
                            (
                                blob.sha256,
                                blob.size_bytes,
                                blob.compressed_size_bytes,
                                "application/json",
                                str(blob.path.relative_to(self.artifacts.blob_root)),
                                1,
                                _iso(_utcnow()),
                            ),
                        )
                        connection.execute(
                            "INSERT INTO v2_event_blobs(event_id,sha256,role) VALUES(?,?,?)",
                            (str(event.event_id.root), blob.sha256, "payload"),
                        )
                        break
                # A reservation is only a claim on a sequence number.  It is
                # consumed atomically with the event that uses it so stale
                # reservations cannot accumulate forever.
                connection.execute(
                    "DELETE FROM v2_sequence_reservations WHERE execution_id=? AND sequence=?",
                    (execution_id, event.sequence),
                )
                if (
                    event.kind is EventKind.SESSION_CREATED
                    and event.session_id is not None
                ):
                    connection.execute(
                        "INSERT INTO v2_sessions(id,execution_id,state,created_at) VALUES(?,?,?,?)",
                        (
                            str(event.session_id.root),
                            execution_id,
                            str(event.payload.get("state", "open")),
                            _iso(event.timestamp),
                        ),
                    )
                elif (
                    event.kind is EventKind.SESSION_STATE_CHANGED
                    and event.session_id is not None
                ):
                    state = event.payload.get("state", event.payload.get("lifecycle"))
                    if isinstance(state, str):
                        connection.execute(
                            "UPDATE v2_sessions SET state=?,closed_at=CASE WHEN ? IN ('closed','finished') THEN ? ELSE closed_at END WHERE id=?",
                            (
                                state,
                                state,
                                _iso(event.timestamp),
                                str(event.session_id.root),
                            ),
                        )
            derived = self._derive_snapshot(
                execution_id, self._events(execution_id) + safe_events
            )
            connection.execute(
                "UPDATE v2_executions SET snapshot_json=? WHERE id=?",
                (_json(derived.model_dump(mode="json")), execution_id),
            )
            self._commit(connection)
            committed = safe_events
        except Exception as exc:
            if not _is_integrity_error(exc):
                raise
            self._rollback(connection)
            raise StorageConflict("event identity or sequence conflicts") from exc
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()
        with self._callback_lock:
            callbacks = tuple(self._callbacks.get(execution_id, ()))
        for event in committed:
            for callback in callbacks:
                try:
                    callback(event.model_copy())
                except Exception:
                    pass

    def append_events(self, events: Sequence[Event]) -> None:
        batch = tuple(events)
        if batch:
            self._append(_execution_key(batch[0].execution_id), batch)

    append = append_events

    def append_event(self, event: Event, content: bytes, *, media_type: str) -> Event:
        """Commit an event and its raw blob in one SQLite transaction."""
        if event.raw_evidence_ref is not None:
            raise StorageConflict("raw evidence reference must be store-owned")
        execution_id = _execution_key(event.execution_id)
        safe_media_type = redact_for_persistence(
            media_type, config=self._redaction_config, path="$.raw_evidence.media_type"
        )
        if (
            not isinstance(safe_media_type, str)
            or not safe_media_type
            or len(safe_media_type) > 256
        ):
            raise ValueError("raw evidence media_type must be 1-256 characters")
        with self._connect() as connection:
            used_row = connection.execute(
                "SELECT COALESCE(SUM(b.size_bytes),0) FROM v2_event_blobs eb "
                "JOIN v2_events e ON e.id=eb.event_id JOIN v2_blobs b ON b.sha256=eb.sha256 "
                "WHERE e.execution_id=? AND eb.role='raw_evidence'",
                (execution_id,),
            ).fetchone()
        used = int(used_row[0]) if used_row is not None else 0
        prepared = _prepare_evidence(
            content,
            config=self._capture_config,
            redaction_config=self._redaction_config,
            remaining_bytes=max(self._capture_config.raw_execution_bytes - used, 0),
        )
        try:
            # Blob materialization and metadata publication have one failure
            # boundary.  A blob writer may fail after creating a temporary
            # file, so cleanup also runs when ``put`` itself raises.
            blob = self.artifacts.blob_store.put(prepared.content)
            storage_key = str(blob.path.relative_to(self.artifacts.blob_root))
            ref = _make_evidence_ref(
                event.event_id, prepared.content, media_type=safe_media_type
            ).model_copy(update={"storage_key": storage_key})
            capture = _make_evidence_capture(ref, prepared)
            event_payload = {
                **dict(event.payload),
                "raw_capture": capture.model_dump(mode="json"),
            }
            self._append(
                execution_id,
                (
                    event.model_copy(
                        update={"raw_evidence_ref": ref, "payload": event_payload}
                    ),
                ),
                raw_evidence=(ref, blob),
            )
        except BaseException:
            # The DB transaction did not publish a reference.  The explicit
            # store GC removes this unreferenced, verified content-addressed
            # blob; existing shared blobs are retained.
            try:
                self.artifacts.cleanup()
            except Exception:
                pass
            raise
        return self._events(execution_id)[-1]

    def iter_events(
        self, execution_id: ExecutionId | str, *, after_sequence: int = -1
    ) -> Iterator[Event]:
        if after_sequence < -1:
            raise ValueError("after_sequence must be >= -1")
        events = self._events(_execution_key(execution_id))
        return iter(event for event in events if event.sequence > after_sequence)

    def events(
        self, execution_id: ExecutionId | str, *, after_sequence: int = -1
    ) -> tuple[Event, ...]:
        return tuple(self.iter_events(execution_id, after_sequence=after_sequence))

    # -- sessions, turns, evaluations ------------------------------------
    def create_session(
        self,
        execution_id: ExecutionId | str,
        session_id: SessionId | str,
        *,
        state: str = "open",
    ) -> SessionId:
        execution_key, session_key = (
            _execution_key(execution_id),
            str(session_id.root if isinstance(session_id, SessionId) else session_id),
        )
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            existing = connection.execute(
                "SELECT execution_id FROM v2_sessions WHERE id=?", (session_key,)
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != execution_key:
                    self._rollback(connection)
                    raise StorageConflict(
                        "session identity belongs to another execution"
                    )
                self._commit(connection)
                return SessionId(session_key)
            connection.execute(
                "INSERT INTO v2_sessions(id,execution_id,state,created_at) VALUES(?,?,?,?)",
                (session_key, execution_key, state, _iso(_utcnow())),
            )
            self._commit(connection)
        except Exception as exc:
            if not _is_integrity_error(exc):
                raise
            self._rollback(connection)
            raise StorageConflict(
                "session already exists or execution does not exist"
            ) from exc
        finally:
            connection.close()
        return SessionId(session_key)

    def close_session(self, session_id: SessionId | str) -> None:
        key = str(session_id.root if isinstance(session_id, SessionId) else session_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE v2_sessions SET state='closed', closed_at=? WHERE id=?",
                (_iso(_utcnow()), key),
            )

    def save_turn(
        self, snapshot: TurnState, result: TurnResult | Mapping[str, Any] | None = None
    ) -> None:
        session_key = str(snapshot.session_id.root)
        if isinstance(result, TurnResult) and (
            result.snapshot.turn_id != snapshot.turn_id
            or result.snapshot.session_id != snapshot.session_id
            or result.snapshot.number != snapshot.number
        ):
            raise StorageConflict("turn result does not match turn snapshot")
        raw_result = (
            result.model_dump(mode="json")
            if isinstance(result, TurnResult)
            else dict(result)
            if result is not None
            else None
        )
        result_json = (
            redact_for_persistence(
                raw_result, config=self._redaction_config, path="$.turn.result"
            )
            if raw_result is not None
            else None
        )
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            session = connection.execute(
                "SELECT 1 FROM v2_sessions WHERE id=?", (session_key,)
            ).fetchone()
            if session is None:
                raise StorageConflict("session does not exist")
            # A turn id is globally unique.  On an idempotent update, retain
            # the original session/ordinal rather than allowing a caller to
            # mutate ownership through the snapshot JSON.
            existing = connection.execute(
                "SELECT session_id,number FROM v2_turns WHERE id=?",
                (str(snapshot.turn_id.root),),
            ).fetchone()
            if existing is not None and (
                str(existing["session_id"]) != session_key
                or int(existing["number"]) != snapshot.number
            ):
                raise StorageConflict(
                    "turn identity cannot move between sessions or ordinals"
                )
            connection.execute(
                "INSERT INTO v2_turns(id,session_id,number,snapshot_json,result_json,created_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET snapshot_json=excluded.snapshot_json,result_json=excluded.result_json",
                (
                    str(snapshot.turn_id.root),
                    session_key,
                    snapshot.number,
                    _json(snapshot.model_dump(mode="json")),
                    _json(result_json) if result_json is not None else None,
                    _iso(snapshot.created_at),
                ),
            )
            self._commit(connection)
        except Exception as exc:
            if not _is_integrity_error(exc):
                raise
            self._rollback(connection)
            raise StorageConflict("turn order or identity conflicts") from exc
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    append_turn = save_turn

    def turns(
        self, execution_id: ExecutionId | str
    ) -> tuple[tuple[TurnState, TurnResult | None], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT t.snapshot_json,t.result_json FROM v2_turns t JOIN v2_sessions s ON s.id=t.session_id WHERE s.execution_id=? ORDER BY s.created_at,t.number",
                (_execution_key(execution_id),),
            ).fetchall()
        return tuple(
            (
                TurnState.model_validate(_loads(row[0])),
                TurnResult.model_validate(_loads(row[1])) if row[1] else None,
            )
            for row in rows
        )

    def save_evaluation(
        self,
        execution_id: ExecutionId | str,
        result: Mapping[str, Any] | Any,
        *,
        evaluation_id: str | None = None,
        turn_id: TurnId | str | None = None,
    ) -> str:
        runtime_subject = getattr(getattr(result, "context", None), "subject", None)
        value = (
            result.model_dump(mode="json")
            if hasattr(result, "model_dump")
            else dict(result)
        )
        identifier = evaluation_id or str(
            value.get("evaluation_id") or _new_id("evaluation")
        )
        raw_context = value.get("context")
        context: Mapping[str, Any] = (
            raw_context if isinstance(raw_context, Mapping) else {}
        )
        subject = context.get("subject")
        subject_digest = hashlib.sha256(
            _json(
                redact_for_persistence(
                    subject, config=self._redaction_config, path="$.evaluation.subject"
                )
            ).encode()
        ).hexdigest()
        created_at = _utcnow()
        run_id = value.get("run_id") or context.get("metadata", {}).get(
            "mcp_pal.run_id"
        )
        if run_id is None:
            snapshot = self.get_snapshot(execution_id)
            run_id = (
                snapshot.run_id.root
                if snapshot is not None and snapshot.run_id is not None
                else None
            )
        snapshot = self.get_snapshot(execution_id)
        compact = {
            "evaluation_id": identifier,
            "name": value.get("name", ""),
            "status": value.get("status", "error"),
            "required": bool(value.get("required", False)),
            "message": value.get("message"),
            "score": value.get("score"),
            "rationale": value.get("rationale"),
            "metrics": value.get("metrics", {}),
            "provenance": value.get("provenance"),
            "details": value.get("details", {}),
            "context": {
                "execution_id": context.get("execution_id")
                or _execution_key(execution_id),
                "suite_id": context.get("suite_id")
                or (
                    snapshot.suite_id.root
                    if snapshot is not None and snapshot.suite_id is not None
                    else None
                ),
                "suite_name": context.get("suite_name")
                or (snapshot.suite_name if snapshot is not None else None),
                "turn_id": context.get("turn_id")
                or (
                    str(turn_id.root if isinstance(turn_id, TurnId) else turn_id)
                    if turn_id
                    else None
                ),
                "case_id": context.get("case_id") or value.get("case_id"),
                "goal": context.get("goal"),
                "artifacts": context.get("artifacts", []),
                "metadata": context.get("metadata", {}),
            },
            "subject_kind": context.get("subject_kind")
            or self._subject_kind(
                runtime_subject if runtime_subject is not None else subject
            ),
            "subject_digest": subject_digest,
            "run_id": str(run_id) if run_id is not None else None,
            "created_at": _iso(created_at),
        }
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            if turn_id is not None:
                turn_key = str(turn_id.root if isinstance(turn_id, TurnId) else turn_id)
                if (
                    connection.execute(
                        "SELECT 1 FROM v2_turns t JOIN v2_sessions s ON s.id=t.session_id WHERE t.id=? AND s.execution_id=?",
                        (turn_key, _execution_key(execution_id)),
                    ).fetchone()
                    is None
                ):
                    raise StorageConflict("turn does not belong to execution")
            connection.execute(
                "INSERT INTO v2_evaluations(id,execution_id,turn_id,result_json,created_at,evaluator_name,status,score,run_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    _execution_key(execution_id),
                    str(turn_id.root if isinstance(turn_id, TurnId) else turn_id)
                    if turn_id
                    else None,
                    _json(
                        redact_for_persistence(
                            compact, config=self._redaction_config, path="$.evaluation"
                        )
                    ),
                    _iso(created_at),
                    str(value.get("name") or ""),
                    str(value.get("status") or "error"),
                    value.get("score"),
                    str(run_id) if run_id is not None else None,
                ),
            )
            self._commit(connection)
            return identifier
        except Exception as exc:
            if not _is_integrity_error(exc):
                raise
            self._rollback(connection)
            raise StorageConflict(
                "evaluation already exists or execution does not exist"
            ) from exc
        finally:
            connection.close()

    @staticmethod
    def _subject_kind(subject: Any) -> str:
        return _evaluation_subject_kind(subject)

    def save(self, result: EvaluationResult) -> None:
        context = result.context
        if context is None or context.execution_id is None:
            raise StorageConflict("SQLite evaluations require an execution identity")
        self.save_evaluation(context.execution_id, result, turn_id=context.turn_id)

    @staticmethod
    def _evaluation_model(value: Mapping[str, Any]) -> EvaluationResult:
        # Durable-only identity fields belong to EvaluationRecord,
        # while the runtime result remains backwards-compatible.
        projected = dict(value)
        for key in ("subject_kind", "subject_digest", "created_at", "run_id"):
            projected.pop(key, None)
        return EvaluationResult.model_validate(projected)

    def get(self, evaluation_id: str) -> EvaluationResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM v2_evaluations WHERE id=?",
                (str(evaluation_id),),
            ).fetchone()
        return self._evaluation_model(_loads(row[0], {})) if row else None

    def all(self) -> tuple[EvaluationResult, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT result_json FROM v2_evaluations ORDER BY created_at,id"
            ).fetchall()
        return tuple(self._evaluation_model(_loads(row[0], {})) for row in rows)

    def evaluations(
        self, execution_id: ExecutionId | str, *, turn_id: TurnId | str | None = None
    ) -> tuple[EvaluationRecord, ...]:
        with self._connect() as connection:
            query = "SELECT result_json FROM v2_evaluations WHERE execution_id=?"
            params: list[Any] = [_execution_key(execution_id)]
            if turn_id is not None:
                query += " AND turn_id=?"
                params.append(
                    str(turn_id.root if isinstance(turn_id, TurnId) else turn_id)
                )
            query += " ORDER BY created_at,id"
            rows = connection.execute(query, params).fetchall()
        records: list[EvaluationRecord] = []
        for row in rows:
            try:
                value = _loads(row[0], {})
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(value, Mapping):
                continue
            raw_context = value.get("context")
            context: Mapping[str, Any] = (
                raw_context if isinstance(raw_context, Mapping) else {}
            )
            projected = dict(value)
            projected["execution_id"] = _execution_key(execution_id)
            projected["turn_id"] = context.get("turn_id")
            projected["suite_id"] = context.get("suite_id")
            projected["suite_name"] = context.get("suite_name")
            projected["case_id"] = context.get("case_id") or projected.get("case_id")
            projected["goal"] = context.get("goal")
            projected["metadata"] = context.get("metadata", {})
            projected.pop("context", None)
            try:
                records.append(EvaluationRecord.model_validate(projected))
            except (TypeError, ValueError, ValidationError):
                # A malformed legacy row remains opaque and is excluded from
                # typed reports/aggregates rather than breaking the report.
                continue

        return tuple(records)

    def aggregate_evaluations(self, query: EvaluationQuery) -> EvaluationReport:
        """Calculate summaries from persisted evaluations and execution traces."""
        if not isinstance(query, EvaluationQuery):
            query = EvaluationQuery.model_validate(query)
        where = ["x.deleted_at IS NULL"]
        params: list[Any] = []
        if query.start is not None:
            where.append("x.created_at >= ?")
            params.append(_iso(query.start.astimezone(timezone.utc)))
        if query.to is not None:
            where.append("x.created_at < ?")
            params.append(_iso(query.to.astimezone(timezone.utc)))
        for label, column in (
            ("evaluator", "e.evaluator_name"),
            ("run_id", "COALESCE(e.run_id, x.run_id)"),
            ("suite_name", "s.suite_name"),
            ("project_id", "x.project_id"),
            ("project_name", "p.project_name"),
        ):
            values = query.filters.get(label)
            if values:
                placeholders = ",".join("?" for _ in values)
                where.append(f"{column} IN ({placeholders})")
                params.extend(str(value) for value in values)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT e.execution_id,e.result_json,x.snapshot_json,x.specification_json,x.project_id,p.project_name "
                "FROM v2_evaluations e JOIN v2_executions x ON x.id=e.execution_id LEFT JOIN v2_suites s ON s.id=x.suite_id LEFT JOIN v2_projects p ON p.id=x.project_id WHERE "
                + " AND ".join(where)
                + " ORDER BY x.created_at,e.created_at,e.id",
                params,
            ).fetchall()
        records: list[EvaluationRecord] = []
        snapshots: dict[str, ExecutionState] = {}
        specifications: dict[str, ExecutionSpec] = {}
        traces: dict[str, TraceView] = {}
        loaded_executions: set[str] = set()
        attempted_traces: set[str] = set()
        for row in rows:
            execution_id = str(row[0])
            try:
                if execution_id not in loaded_executions:
                    loaded_executions.add(execution_id)
                    snapshots[execution_id] = ExecutionState.model_validate(
                        _loads(row[2])
                    )
                    if row[4] and snapshots[execution_id].project_id is None:
                        snapshots[execution_id] = snapshots[execution_id].model_copy(
                            update={"project_id": ProjectId(str(row[4]))}
                        )
                    if row[3] is not None:
                        specifications[execution_id] = TypeAdapter(
                            ExecutionSpec
                        ).validate_python(_loads(row[3]))
                value = _loads(row[1], {})
                raw_context = (
                    value.get("context") if isinstance(value, Mapping) else None
                )
                context: Mapping[str, Any] = (
                    raw_context if isinstance(raw_context, Mapping) else {}
                )
                projected = dict(value) if isinstance(value, Mapping) else {}
                projected.update(
                    {
                        "execution_id": execution_id,
                        "turn_id": context.get("turn_id"),
                        "case_id": context.get("case_id") or projected.get("case_id"),
                        "goal": context.get("goal"),
                        "metadata": {
                            **(
                                dict(context.get("metadata", {}))
                                if isinstance(context.get("metadata", {}), Mapping)
                                else {}
                            ),
                            "project_name": row[5],
                        },
                    }
                )
                projected.pop("context", None)
                records.append(EvaluationRecord.model_validate(projected))
            except (TypeError, ValueError, ValidationError, json.JSONDecodeError):
                continue
            if execution_id not in attempted_traces:
                attempted_traces.add(execution_id)
                try:
                    trace = self.get_trace_view(execution_id)
                except (TraceUnavailable, TraceNotFinalized, StorageError, ValueError):
                    trace = None
                if trace is not None:
                    traces[execution_id] = trace
        return aggregate_evaluations(
            query,
            records,
            snapshots=snapshots,
            specifications=specifications,
            traces=traces,
        )

    persisted_evaluations = evaluations

    def evaluation_json(
        self, execution_id: ExecutionId | str, *, turn_id: TurnId | str | None = None
    ) -> tuple[Mapping[str, Any], ...]:
        with self._connect() as connection:
            if turn_id is None:
                rows = connection.execute(
                    "SELECT result_json FROM v2_evaluations WHERE execution_id=? ORDER BY created_at,id",
                    (_execution_key(execution_id),),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT result_json FROM v2_evaluations WHERE execution_id=? AND turn_id=? ORDER BY created_at,id",
                    (
                        _execution_key(execution_id),
                        str(turn_id.root if isinstance(turn_id, TurnId) else turn_id),
                    ),
                ).fetchall()
        values: list[Mapping[str, Any]] = []
        for row in rows:
            try:
                value = _loads(row[0], {})
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(value, Mapping):
                values.append(value)
        return tuple(values)

    def transaction(self, execution_id: ExecutionId | str) -> ExecutionTransaction:
        if self.get_snapshot(execution_id) is None:
            raise StorageConflict("execution does not exist")
        return _SqliteBatch(self, _execution_key(execution_id))

    def allocate(
        self, execution_id: ExecutionId | str, *, count: int = 1
    ) -> tuple[int, ...]:
        if count < 1:
            raise ValueError("count must be positive")
        key = _execution_key(execution_id)
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            if (
                connection.execute(
                    "SELECT 1 FROM v2_executions WHERE id=?", (key,)
                ).fetchone()
                is None
            ):
                raise StorageConflict("execution does not exist")
            max_row = connection.execute(
                "SELECT COALESCE(MAX(sequence),-1) FROM v2_events WHERE execution_id=?",
                (key,),
            ).fetchone()
            candidate = int(max_row[0]) + 1
            reserved: list[int] = []
            while len(reserved) < count:
                try:
                    connection.execute(
                        "INSERT INTO v2_sequence_reservations(execution_id,sequence) VALUES(?,?)",
                        (key, candidate),
                    )
                    reserved.append(candidate)
                except Exception as exc:
                    if not _is_integrity_error(exc):
                        raise
                candidate += 1
            self._commit(connection)
            return tuple(reserved)
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    allocate_sequence = lambda self, execution_id: self.allocate(execution_id, count=1)[
        0
    ]
    allocate_sequences = allocate

    def release(
        self, execution_id: ExecutionId | str, sequences: Sequence[int]
    ) -> None:
        key = _execution_key(execution_id)
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            for sequence in sequences:
                connection.execute(
                    "DELETE FROM v2_sequence_reservations WHERE execution_id=? AND sequence=?",
                    (key, sequence),
                )
            self._commit(connection)
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    release_sequences = release

    def subscribe(
        self, execution_id: ExecutionId | str, callback: EventCallback
    ) -> Callable[[], None]:
        key = _execution_key(execution_id)
        if self.get_snapshot(key) is None:
            raise StorageConflict("execution does not exist")
        with self._callback_lock:
            self._callbacks.setdefault(key, []).append(callback)

        def unsubscribe() -> None:
            with self._callback_lock:
                values = self._callbacks.get(key, [])
                if callback in values:
                    values.remove(callback)

        return unsubscribe

    # -- Profiles ---------------------------------------------------------
    # -- ACP probes -------------------------------------------------------
    def save_acp_probe(self, result: ACPProbeResult) -> ACPProbeResult:
        """Persist one redacted ACP probe result and return its safe copy."""
        from ..services.acp_probes import redact_probe

        safe = redact_probe(result, self._redaction_config)
        # Probe history is tied to a real harness profile revision.  Keeping
        # this check at the durable boundary prevents forged/stale dimensions
        # from becoming selectable readiness evidence.
        profile = self.get_profile(safe.profile_id, kind="harness")
        revision = self.get_revision(safe.revision_id, kind="harness")
        if (
            profile is None
            or profile.kind != "harness"
            or revision is None
            or revision.profile_id != safe.profile_id
        ):
            raise StorageConflict("ACP probe profile revision does not exist")
        value = safe.model_dump(mode="json")
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            existing = connection.execute(
                "SELECT dimension_key,created_at FROM v2_acp_probes WHERE id=?",
                (safe.id,),
            ).fetchone()
            if existing is not None and (
                str(existing["dimension_key"]) != safe.stable_key
                or str(existing["created_at"]) != str(value["created_at"])
            ):
                raise StorageConflict(
                    "ACP probe id was already used for another dimension"
                )
            connection.execute(
                "INSERT INTO v2_acp_probes(id,profile_id,revision_id,probe_type,transport,agent_mode_id,session_config_json,dimension_key,status,agent_identity_json,agent_capabilities_json,agent_modes_json,current_agent_mode_id,config_options_json,evidence_json,diagnostics,error,created_at,started_at,finished_at,duration_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status,agent_identity_json=excluded.agent_identity_json,agent_capabilities_json=excluded.agent_capabilities_json,agent_modes_json=excluded.agent_modes_json,current_agent_mode_id=excluded.current_agent_mode_id,config_options_json=excluded.config_options_json,evidence_json=excluded.evidence_json,diagnostics=excluded.diagnostics,error=excluded.error,started_at=excluded.started_at,finished_at=excluded.finished_at,duration_ms=excluded.duration_ms",
                (
                    safe.id,
                    safe.profile_id,
                    safe.revision_id,
                    safe.probe_type.value,
                    safe.transport,
                    safe.agent_mode_id,
                    _json(value["session_config"]),
                    safe.stable_key,
                    safe.status.value,
                    _json(value["agent_identity"])
                    if safe.agent_identity is not None
                    else None,
                    _json(value["agent_capabilities"]),
                    _json(value["agent_modes"]),
                    safe.current_agent_mode_id,
                    _json(value["config_options"]),
                    _json(value["evidence"]),
                    safe.diagnostics,
                    safe.error,
                    value["created_at"],
                    value.get("started_at"),
                    value.get("finished_at"),
                    safe.duration_ms,
                ),
            )
            self._commit(connection)
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()
        return safe

    @staticmethod
    def _acp_probe_row(row: Any) -> ACPProbeResult:
        return ACPProbeResult.model_validate(
            {
                "id": row["id"],
                "profile_id": row["profile_id"],
                "revision_id": row["revision_id"],
                "probe_type": row["probe_type"],
                "transport": row["transport"],
                "agent_mode_id": row["agent_mode_id"],
                "session_config": _loads(row["session_config_json"], {}),
                "status": row["status"],
                "agent_identity": _loads(row["agent_identity_json"], None),
                "agent_capabilities": _loads(row["agent_capabilities_json"], {}),
                "agent_modes": tuple(_loads(row["agent_modes_json"], [])),
                "current_agent_mode_id": row["current_agent_mode_id"],
                "config_options": tuple(_loads(row["config_options_json"], [])),
                "evidence": _loads(row["evidence_json"], {}),
                "diagnostics": row["diagnostics"],
                "error": row["error"],
                "created_at": row["created_at"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "duration_ms": row["duration_ms"],
            }
        )

    def get_acp_probe(self, probe_id: str) -> ACPProbeResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM v2_acp_probes WHERE id=?", (str(probe_id),)
            ).fetchone()
        return None if row is None else self._acp_probe_row(row)

    def list_acp_probes(
        self,
        dimension: ACPProbeDimension | None = None,
        *,
        include_inflight: bool = True,
    ) -> tuple[ACPProbeResult, ...]:
        parameters: list[Any] = []
        clauses: list[str] = []
        if dimension is not None:
            clauses.append("dimension_key=?")
            parameters.append(dimension.stable_key)
        if not include_inflight:
            clauses.append("status NOT IN ('queued','running')")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM v2_acp_probes{where} ORDER BY created_at DESC,id DESC",
                tuple(parameters),
            ).fetchall()
        return tuple(self._acp_probe_row(row) for row in rows)

    def latest_acp_probe(self, dimension: ACPProbeDimension) -> ACPProbeResult | None:
        values = self.list_acp_probes(dimension, include_inflight=False)
        return values[0] if values else None

    def create_profile(
        self,
        kind: str,
        name: str,
        value: Mapping[str, Any],
        *,
        description: str = "",
        profile_id: str | None = None,
        revision_id: str | None = None,
    ) -> ProfileRecord:
        if kind not in {"server", "harness"}:
            raise ValueError("profile kind must be server or harness")
        profile_kind = cast(Literal["server", "harness"], kind)
        safe_name = redact_for_persistence(
            name, config=self._redaction_config, path="$.profile.name"
        )
        safe_description = redact_for_persistence(
            description, config=self._redaction_config, path="$.profile.description"
        )
        if not isinstance(safe_name, str) or not isinstance(safe_description, str):
            raise StorageError("profile metadata could not be redacted")
        now = _iso(_utcnow())
        pid, rid, table, revision_table = (
            profile_id or _new_id(f"{kind}-profile"),
            revision_id or _new_id(f"{kind}-revision"),
            f"v2_{profile_kind}_profiles",
            f"v2_{profile_kind}_profile_revisions",
        )
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            connection.execute(
                f"INSERT INTO {table}(id,name,description,archived,current_revision_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (pid, safe_name, safe_description, 0, None, now, now),
            )
            connection.execute(
                f"INSERT INTO {revision_table}(id,profile_id,revision_number,value_json,created_at) VALUES(?,?,?,?,?)",
                (
                    rid,
                    pid,
                    1,
                    _json(
                        serialize_durable(
                            dict(value), config=self._redaction_config, path="$.profile"
                        )
                    ),
                    now,
                ),
            )
            connection.execute(
                f"UPDATE {table} SET current_revision_id=? WHERE id=?", (rid, pid)
            )
            self._commit(connection)
        except Exception as exc:
            self._rollback(connection)
            if not _is_integrity_error(exc):
                raise
            raise StorageConflict("profile name or id already exists") from exc
        finally:
            connection.close()
        return self.get_profile(pid, kind=profile_kind)  # type: ignore[return-value]

    def create_server_profile(
        self, name: str, value: Mapping[str, Any], **kwargs: Any
    ) -> ProfileRecord:
        return self.create_profile("server", name, value, **kwargs)

    def create_harness_profile(
        self, name: str, value: Mapping[str, Any], **kwargs: Any
    ) -> ProfileRecord:
        return self.create_profile("harness", name, value, **kwargs)

    def get_profile(
        self,
        profile_id: str,
        *,
        kind: Literal["server", "harness"] | None = None,
    ) -> ProfileRecord | None:
        with self._connect() as connection:
            if kind is not None and kind not in {"server", "harness"}:
                raise ValueError("profile kind must be server or harness")
            for profile_kind in (kind,) if kind is not None else ("server", "harness"):
                row = connection.execute(
                    f"SELECT * FROM v2_{profile_kind}_profiles WHERE id=?",
                    (profile_id,),
                ).fetchone()
                if row:
                    return ProfileRecord(
                        str(row["id"]),
                        profile_kind,
                        str(row["name"]),
                        str(row["description"]),
                        bool(row["archived"]),
                        RevisionId(str(row["current_revision_id"]))
                        if row["current_revision_id"]
                        else None,
                        _parse_dt(row["created_at"]),
                        _parse_dt(row["updated_at"]),
                    )
        return None

    def resolve_profile(
        self,
        profile_id: str,
        selection: RevisionSelection,
        *,
        kind: Literal["server", "harness"],
    ) -> tuple[ProfileRecord | None, ProfileRevisionRecord | None]:
        """Read one profile and its selected immutable revision.

        This is intentionally a read-only composition boundary used by the
        SDK runtime.  Kind is checked here so an ID collision across the two
        profile families cannot silently resolve to the wrong descriptor.
        """
        if kind not in {"server", "harness"}:
            raise ValueError("profile kind must be server or harness")
        connection = self._connect()
        try:
            # Keep the profile pointer and selected revision in one SQLite
            # snapshot.  In particular, latest must not observe an archive or
            # add_revision committed between two independent connections.
            self._begin(connection)
            try:
                row = connection.execute(
                    f"SELECT * FROM v2_{kind}_profiles WHERE id=?", (profile_id,)
                ).fetchone()
                if row is None:
                    # Return a same-ID record from the other family from this
                    # same snapshot so wrong-kind diagnostics are consistent.
                    other_kind = "harness" if kind == "server" else "server"
                    other = connection.execute(
                        f"SELECT * FROM v2_{other_kind}_profiles WHERE id=?",
                        (profile_id,),
                    ).fetchone()
                    self._commit(connection)
                    if other is None:
                        return None, None
                    return (
                        ProfileRecord(
                            str(other["id"]),
                            other_kind,
                            str(other["name"]),
                            str(other["description"]),
                            bool(other["archived"]),
                            RevisionId(str(other["current_revision_id"]))
                            if other["current_revision_id"]
                            else None,
                            _parse_dt(other["created_at"]),
                            _parse_dt(other["updated_at"]),
                        ),
                        None,
                    )
                profile = ProfileRecord(
                    str(row["id"]),
                    kind,
                    str(row["name"]),
                    str(row["description"]),
                    bool(row["archived"]),
                    RevisionId(str(row["current_revision_id"]))
                    if row["current_revision_id"]
                    else None,
                    _parse_dt(row["created_at"]),
                    _parse_dt(row["updated_at"]),
                )
                if profile.archived:
                    self._commit(connection)
                    return profile, None
                selected = (
                    selection
                    if isinstance(selection, RevisionSelection)
                    else RevisionSelection(
                        mode=cast(Literal["latest", "pinned"], selection)
                    )
                )
                revisions_table = f"v2_{kind}_profile_revisions"
                if selected.mode == "latest":
                    revision_row = connection.execute(
                        f"SELECT * FROM {revisions_table} WHERE id=? AND profile_id=?",
                        (
                            profile.current_revision_id.root
                            if profile.current_revision_id
                            else "",
                            profile_id,
                        ),
                    ).fetchone()
                else:
                    revision_row = connection.execute(
                        f"SELECT * FROM {revisions_table} WHERE id=? AND profile_id=? AND revision_number=?",
                        (
                            selected.revision_id.root if selected.revision_id else "",
                            profile_id,
                            selected.revision_number,
                        ),
                    ).fetchone()
                self._commit(connection)
                if revision_row is None:
                    return profile, None
                return (
                    profile,
                    ProfileRevisionRecord(
                        RevisionId(str(revision_row["id"])),
                        profile_id,
                        int(revision_row["revision_number"]),
                        _loads(revision_row["value_json"], {}),
                        _parse_dt(revision_row["created_at"]),
                    ),
                )
            except BaseException:
                self._rollback(connection)
                raise
        finally:
            connection.close()

    def list_profiles(
        self, kind: str, *, include_archived: bool = False
    ) -> tuple[ProfileRecord, ...]:
        """List one profile family in stable name/id order.

        Profile families are explicit so callers cannot accidentally combine
        server and harness descriptors.  Archived records are opt-in because
        normal selection should not offer them.
        """
        if kind not in {"server", "harness"}:
            raise ValueError("profile kind must be server or harness")
        where = "" if include_archived else " WHERE archived=0"
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM v2_{kind}_profiles{where} ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
        return tuple(
            ProfileRecord(
                str(row["id"]),
                kind,
                str(row["name"]),
                str(row["description"]),
                bool(row["archived"]),
                RevisionId(str(row["current_revision_id"]))
                if row["current_revision_id"]
                else None,
                _parse_dt(row["created_at"]),
                _parse_dt(row["updated_at"]),
            )
            for row in rows
        )

    def list_profile_revisions(
        self,
        profile_id: str,
        *,
        kind: Literal["server", "harness"] | None = None,
    ) -> tuple[ProfileRevisionRecord, ...]:
        """Return every revision ordered by revision number then id."""
        profile = self.get_profile(profile_id, kind=kind)
        if profile is None:
            raise StorageConflict("profile does not exist")
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM v2_{profile.kind}_profile_revisions "
                "WHERE profile_id=? ORDER BY revision_number, id",
                (profile_id,),
            ).fetchall()
        return tuple(
            ProfileRevisionRecord(
                RevisionId(str(row["id"])),
                profile_id,
                int(row["revision_number"]),
                _loads(row["value_json"], {}),
                _parse_dt(row["created_at"]),
            )
            for row in rows
        )

    def update_profile(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        kind: Literal["server", "harness"] | None = None,
    ) -> ProfileRecord:
        """Update mutable metadata without changing the immutable revision."""
        if kind is not None and kind not in {"server", "harness"}:
            raise ValueError("profile kind must be server or harness")
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            profile: ProfileRecord | None = None
            kinds = (kind,) if kind is not None else ("server", "harness")
            for profile_kind in kinds:
                row = connection.execute(
                    f"SELECT * FROM v2_{profile_kind}_profiles WHERE id=?",
                    (profile_id,),
                ).fetchone()
                if row is not None:
                    profile = ProfileRecord(
                        str(row["id"]),
                        profile_kind,
                        str(row["name"]),
                        str(row["description"]),
                        bool(row["archived"]),
                        RevisionId(str(row["current_revision_id"]))
                        if row["current_revision_id"]
                        else None,
                        _parse_dt(row["created_at"]),
                        _parse_dt(row["updated_at"]),
                    )
                    break
            if profile is None:
                raise StorageConflict("profile does not exist")
            if name is None and description is None:
                self._commit(connection)
                return profile
            safe_name = (
                redact_for_persistence(
                    name, config=self._redaction_config, path="$.profile.name"
                )
                if name is not None
                else profile.name
            )
            safe_description = (
                redact_for_persistence(
                    description,
                    config=self._redaction_config,
                    path="$.profile.description",
                )
                if description is not None
                else profile.description
            )
            if not isinstance(safe_name, str) or not isinstance(safe_description, str):
                raise StorageError("profile metadata could not be redacted")
            connection.execute(
                f"UPDATE v2_{profile.kind}_profiles SET name=?,description=?,updated_at=? WHERE id=?",
                (safe_name, safe_description, _iso(_utcnow()), profile_id),
            )
            self._commit(connection)
        except Exception as exc:
            self._rollback(connection)
            if _is_integrity_error(exc):
                raise StorageConflict("profile name already exists") from exc
            raise
        finally:
            connection.close()
        profile_kind = cast(Literal["server", "harness"], profile.kind)
        return self.get_profile(profile_id, kind=profile_kind)  # type: ignore[return-value]

    def add_revision(
        self,
        profile_id: str,
        value: Mapping[str, Any],
        *,
        revision_id: str | None = None,
        kind: Literal["server", "harness"] | None = None,
    ) -> ProfileRevisionRecord:
        if kind is not None and kind not in {"server", "harness"}:
            raise ValueError("profile kind must be server or harness")
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            found: tuple[str, _CompatRow] | None = None
            kinds = (kind,) if kind is not None else ("server", "harness")
            for profile_kind in kinds:
                row = connection.execute(
                    f"SELECT * FROM v2_{profile_kind}_profiles WHERE id=?",
                    (profile_id,),
                ).fetchone()
                if row:
                    found = (profile_kind, row)
                    break
            if found is None:
                raise StorageConflict("profile does not exist")
            profile_kind = found[0]
            if bool(found[1]["archived"]):
                raise StorageConflict("profile is archived")
            table = f"v2_{profile_kind}_profile_revisions"
            number = int(
                connection.execute(
                    f"SELECT COALESCE(MAX(revision_number),0)+1 FROM {table} WHERE profile_id=?",
                    (profile_id,),
                ).fetchone()[0]
            )
            rid = revision_id or _new_id(f"{profile_kind}-revision")
            created = _iso(_utcnow())
            safe = serialize_durable(
                dict(value), config=self._redaction_config, path="$.profile.revision"
            )
            connection.execute(
                f"INSERT INTO {table}(id,profile_id,revision_number,value_json,created_at) VALUES(?,?,?,?,?)",
                (rid, profile_id, number, _json(safe), created),
            )
            connection.execute(
                f"UPDATE v2_{profile_kind}_profiles SET current_revision_id=?,updated_at=? WHERE id=?",
                (rid, created, profile_id),
            )
            self._commit(connection)
            return ProfileRevisionRecord(
                RevisionId(rid),
                profile_id,
                number,
                safe if isinstance(safe, Mapping) else {},
                _parse_dt(created),
            )
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    def archive_profile(
        self,
        profile_id: str,
        *,
        kind: Literal["server", "harness"] | None = None,
    ) -> ProfileRecord:
        return self._set_archived(profile_id, True, kind=kind)

    def restore_profile(
        self,
        profile_id: str,
        *,
        kind: Literal["server", "harness"] | None = None,
    ) -> ProfileRecord:
        return self._set_archived(profile_id, False, kind=kind)

    def _set_archived(
        self,
        profile_id: str,
        value: bool,
        *,
        kind: Literal["server", "harness"] | None = None,
    ) -> ProfileRecord:
        if kind is not None and kind not in {"server", "harness"}:
            raise ValueError("profile kind must be server or harness")
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            kinds = (kind,) if kind is not None else ("server", "harness")
            profile_kind: Literal["server", "harness"] | None = None
            for candidate in kinds:
                row = connection.execute(
                    f"SELECT id FROM v2_{candidate}_profiles WHERE id=?",
                    (profile_id,),
                ).fetchone()
                if row is not None:
                    profile_kind = cast(Literal["server", "harness"], candidate)
                    break
            if profile_kind is None:
                raise StorageConflict("profile does not exist")
            connection.execute(
                f"UPDATE v2_{profile_kind}_profiles SET archived=?,updated_at=? WHERE id=?",
                (int(value), _iso(_utcnow()), profile_id),
            )
            self._commit(connection)
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()
        return self.get_profile(profile_id, kind=profile_kind)  # type: ignore[return-value]

    def resolve_revision(
        self,
        profile_id: str,
        selection: RevisionSelection | str = "latest",
        *,
        kind: Literal["server", "harness"] | None = None,
    ) -> ProfileRevisionRecord:
        if kind is not None and kind not in {"server", "harness"}:
            raise ValueError("profile kind must be server or harness")
        profile = self.get_profile(profile_id, kind=kind)
        if profile is None:
            raise StorageConflict("profile does not exist")
        selected = (
            selection
            if isinstance(selection, RevisionSelection)
            else RevisionSelection(mode=cast(Literal["latest", "pinned"], selection))
        )
        table = f"v2_{profile.kind}_profile_revisions"
        with self._connect() as connection:
            if selected.mode == "latest":
                row = connection.execute(
                    f"SELECT * FROM {table} WHERE id=? AND profile_id=?",
                    (
                        profile.current_revision_id.root
                        if profile.current_revision_id
                        else "",
                        profile_id,
                    ),
                ).fetchone()
            else:
                row = connection.execute(
                    f"SELECT * FROM {table} WHERE id=? AND profile_id=? AND revision_number=?",
                    (
                        selected.revision_id.root if selected.revision_id else "",
                        profile_id,
                        selected.revision_number,
                    ),
                ).fetchone()
        if row is None:
            raise StorageConflict("profile revision does not exist")
        return ProfileRevisionRecord(
            RevisionId(str(row["id"])),
            profile_id,
            int(row["revision_number"]),
            _loads(row["value_json"], {}),
            _parse_dt(row["created_at"]),
        )

    def get_revision(
        self,
        revision_id: RevisionId | str,
        *,
        kind: Literal["server", "harness"] | None = None,
    ) -> ProfileRevisionRecord | None:
        key = str(
            revision_id.root if isinstance(revision_id, RevisionId) else revision_id
        )
        with self._connect() as connection:
            if kind is not None and kind not in {"server", "harness"}:
                raise ValueError("profile kind must be server or harness")
            for profile_kind in (kind,) if kind is not None else ("server", "harness"):
                row = connection.execute(
                    f"SELECT * FROM v2_{profile_kind}_profile_revisions WHERE id=?",
                    (key,),
                ).fetchone()
                if row:
                    return ProfileRevisionRecord(
                        RevisionId(str(row["id"])),
                        str(row["profile_id"]),
                        int(row["revision_number"]),
                        _loads(row["value_json"], {}),
                        _parse_dt(row["created_at"]),
                    )
        return None

    # -- leases, queue, cancellation -------------------------------------
    def enqueue_command(
        self,
        execution_id: ExecutionId | str,
        kind: str = "execution",
        payload: Mapping[str, Any] | None = None,
        *,
        command_id: str | None = None,
        session_id: SessionId | str | None = None,
        turn_id: TurnId | str | None = None,
    ) -> Command:
        command = command_id or _new_id("command")
        execution_key = _execution_key(execution_id)
        session_key = (
            str(session_id.root if isinstance(session_id, SessionId) else session_id)
            if session_id
            else None
        )
        turn_key = (
            str(turn_id.root if isinstance(turn_id, TurnId) else turn_id)
            if turn_id
            else None
        )
        safe_payload = serialize_durable(
            dict(payload or {}), config=self._redaction_config, path="$.command.payload"
        )
        payload_json = _json(safe_payload)
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            # A caller may retry a submit after losing its response.  Stable
            # command IDs make that retry idempotent when the normalized
            # request is identical, while rejecting accidental key reuse.
            existing = connection.execute(
                "SELECT * FROM v2_commands WHERE id=?", (command,)
            ).fetchone()
            if existing is not None:
                same = (
                    str(existing["execution_id"]) == execution_key
                    and str(existing["kind"]) == kind
                    and str(existing["payload_json"]) == payload_json
                    and existing["session_id"] == session_key
                    and existing["turn_id"] == turn_key
                )
                if not same:
                    raise StorageConflict(
                        "command id was already used for a different request"
                    )
                self._rollback(connection)
                return self.get_command(command)  # type: ignore[return-value]
            connection.execute(
                "INSERT INTO v2_commands(id,execution_id,kind,status,payload_json,session_id,turn_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    command,
                    execution_key,
                    kind,
                    "queued",
                    payload_json,
                    session_key,
                    turn_key,
                    _iso(_utcnow()),
                ),
            )
            self._commit(connection)
        except Exception as exc:
            self._rollback(connection)
            if not _is_integrity_error(exc):
                raise
            raise StorageConflict(
                "command already exists or execution does not exist"
            ) from exc
        finally:
            connection.close()
        return self.get_command(command)  # type: ignore[return-value]

    enqueue = enqueue_command

    def get_command(self, command_id: str) -> Command | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM v2_commands WHERE id=?", (command_id,)
            ).fetchone()
        if row is None:
            return None
        return Command(
            str(row["id"]),
            ExecutionId(str(row["execution_id"])),
            str(row["kind"]),
            str(row["status"]),
            _loads(row["payload_json"], {}),
            SessionId(str(row["session_id"])) if row["session_id"] else None,
            TurnId(str(row["turn_id"])) if row["turn_id"] else None,
        )

    def claim_next(
        self, owner_id: str, *, lease_seconds: float = 30.0
    ) -> tuple[Command, Lease] | None:
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            row = connection.execute(
                "SELECT c.* FROM v2_commands c JOIN v2_executions e ON e.id=c.execution_id "
                "LEFT JOIN v2_cancellation x ON x.execution_id=c.execution_id "
                "WHERE c.status IN ('queued','claimed') "
                "AND (c.status='claimed' OR x.execution_id IS NULL) "
                "AND json_extract(e.snapshot_json,'$.lifecycle') <> 'finished' "
                "ORDER BY c.created_at,c.id LIMIT 1"
            ).fetchone()
            if row is None:
                self._rollback(connection)
                return None
            execution_id = str(row["execution_id"])
            now = _utcnow()
            current = connection.execute(
                "SELECT * FROM v2_leases WHERE execution_id=?", (execution_id,)
            ).fetchone()
            if current and _parse_dt(current["expires_at"]) > now:
                self._rollback(connection)
                return None
            if row["status"] == "claimed" and (
                current is None or _parse_dt(current["expires_at"]) <= now
            ):
                # A stale owner is never resumed.  Persist the interruption
                # while the compare-and-set transaction still owns the
                # database lock, then discard its lease and claimed command.
                execution = connection.execute(
                    "SELECT snapshot_json FROM v2_executions WHERE id=?",
                    (execution_id,),
                ).fetchone()
                self._ensure_created_event(connection, execution_id)
                sequence, monotonic_offset_ms = self._next_event_position(
                    connection, execution_id
                )
                event = Event(
                    event_id=EventId(_new_id("event")),
                    execution_id=ExecutionId(execution_id),
                    sequence=sequence,
                    kind=EventKind.EXECUTION_FINISHED,
                    monotonic_offset_ms=monotonic_offset_ms,
                    payload=self._terminal_payload(
                        ExecutionOutcome.INTERRUPTED,
                        reason="worker lease expired",
                        reason_path="$.lease.reason",
                    ),
                )
                if execution is not None:
                    snapshot = ExecutionState.model_validate(
                        _loads(execution["snapshot_json"])
                    )
                    interrupted = snapshot.model_copy(
                        update={
                            "lifecycle": ExecutionStatus.FINISHED,
                            "outcome": ExecutionOutcome.INTERRUPTED,
                            "sequence": sequence,
                            "finished_at": event.timestamp,
                        }
                    )
                    connection.execute(
                        "INSERT INTO v2_events(id,execution_id,sequence,event_json,timestamp) VALUES(?,?,?,?,?)",
                        (
                            str(event.event_id.root),
                            execution_id,
                            sequence,
                            _json(event.model_dump(mode="json", by_alias=True)),
                            _iso(event.timestamp),
                        ),
                    )
                    connection.execute(
                        "UPDATE v2_executions SET snapshot_json=? WHERE id=?",
                        (_json(interrupted.model_dump(mode="json")), execution_id),
                    )
                connection.execute(
                    "UPDATE v2_commands SET status='interrupted' WHERE execution_id=? AND status IN ('queued','claimed')",
                    (execution_id,),
                )
                connection.execute(
                    "DELETE FROM v2_leases WHERE execution_id=?", (execution_id,)
                )
                self._commit(connection)
                return None
            token, expires = _new_id("lease"), now + timedelta(seconds=lease_seconds)
            connection.execute(
                "INSERT INTO v2_leases(execution_id,owner_id,lease_token,acquired_at,heartbeat_at,expires_at) VALUES(?,?,?,?,?,?) ON CONFLICT(execution_id) DO UPDATE SET owner_id=excluded.owner_id,lease_token=excluded.lease_token,acquired_at=excluded.acquired_at,heartbeat_at=excluded.heartbeat_at,expires_at=excluded.expires_at",
                (execution_id, owner_id, token, _iso(now), _iso(now), _iso(expires)),
            )
            connection.execute(
                "UPDATE v2_commands SET status='claimed',claimed_at=?,owner_id=? WHERE id=? AND status='queued'",
                (_iso(now), owner_id, str(row["id"])),
            )
            self._commit(connection)
            command = self.get_command(str(row["id"]))
            if command is None:
                raise StorageError(
                    "claimed command disappeared before it could be returned"
                )
            return command, Lease(ExecutionId(execution_id), owner_id, token, expires)
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    claim = claim_next

    def heartbeat(
        self,
        lease: Lease | str,
        *,
        owner_id: str | None = None,
        lease_seconds: float = 30.0,
    ) -> bool:
        token = lease.lease_token if isinstance(lease, Lease) else lease
        owner = lease.owner_id if isinstance(lease, Lease) else owner_id
        if not owner:
            raise ValueError("owner_id is required")
        now, expires = _utcnow(), _utcnow() + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE v2_leases SET heartbeat_at=?,expires_at=? WHERE lease_token=? AND owner_id=? AND expires_at>?",
                (_iso(now), _iso(expires), token, owner, _iso(now)),
            )
            return cursor.rowcount == 1

    renew_lease = heartbeat

    def mark_interrupted_if_lease_lost(
        self,
        lease: Lease,
        *,
        reason: str = "worker lease lost",
    ) -> bool:
        """Atomically close work whose owner can no longer renew its lease.

        This is intentionally compare-and-set on the owner and token.  A
        replacement worker can never have its newer lease or events replaced
        by the old worker after a heartbeat failure.
        """
        execution_id = str(lease.execution_id.root)
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            owned = connection.execute(
                "SELECT 1 FROM v2_leases WHERE execution_id=? AND owner_id=? AND lease_token=?",
                (execution_id, lease.owner_id, lease.lease_token),
            ).fetchone()
            if owned is None:
                self._rollback(connection)
                return False
            execution = connection.execute(
                "SELECT snapshot_json FROM v2_executions WHERE id=? AND deleted_at IS NULL",
                (execution_id,),
            ).fetchone()
            if execution is None:
                connection.execute(
                    "DELETE FROM v2_leases WHERE execution_id=?", (execution_id,)
                )
                self._commit(connection)
                return False
            snapshot = ExecutionState.model_validate(_loads(execution["snapshot_json"]))
            if snapshot.lifecycle is not ExecutionStatus.FINISHED:
                self._ensure_created_event(connection, execution_id)
                sequence, monotonic_offset_ms = self._next_event_position(
                    connection, execution_id
                )
                event = Event(
                    event_id=EventId(_new_id("event")),
                    execution_id=ExecutionId(execution_id),
                    sequence=sequence,
                    kind=EventKind.EXECUTION_FINISHED,
                    monotonic_offset_ms=monotonic_offset_ms,
                    payload=self._terminal_payload(
                        ExecutionOutcome.INTERRUPTED,
                        reason=reason,
                        reason_path="$.lease.reason",
                    ),
                )
                interrupted = snapshot.model_copy(
                    update={
                        "lifecycle": ExecutionStatus.FINISHED,
                        "outcome": ExecutionOutcome.INTERRUPTED,
                        "sequence": sequence,
                        "finished_at": event.timestamp,
                    }
                )
                connection.execute(
                    "INSERT INTO v2_events(id,execution_id,sequence,event_json,timestamp) VALUES(?,?,?,?,?)",
                    (
                        str(event.event_id.root),
                        execution_id,
                        sequence,
                        _json(event.model_dump(mode="json", by_alias=True)),
                        _iso(event.timestamp),
                    ),
                )
                connection.execute(
                    "UPDATE v2_executions SET snapshot_json=? WHERE id=?",
                    (_json(interrupted.model_dump(mode="json")), execution_id),
                )
            connection.execute(
                "UPDATE v2_commands SET status='interrupted' WHERE execution_id=? AND status IN ('queued','claimed')",
                (execution_id,),
            )
            connection.execute(
                "DELETE FROM v2_leases WHERE execution_id=?", (execution_id,)
            )
            self._commit(connection)
            return True
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    def release_lease(self, lease: Lease | str, *, owner_id: str | None = None) -> bool:
        token = lease.lease_token if isinstance(lease, Lease) else lease
        owner = lease.owner_id if isinstance(lease, Lease) else owner_id
        if not owner:
            raise ValueError("owner_id is required")
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM v2_leases WHERE lease_token=? AND owner_id=?",
                (token, owner),
            )
            return cursor.rowcount == 1

    def complete_command(
        self, command_id: str, *, owner_id: str, lease_token: str, status: str = "done"
    ) -> bool:
        """Mark one claimed command terminal under its current lease."""
        if status not in {"done", "cancelled", "failed"}:
            raise ValueError("command status must be done, cancelled, or failed")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT c.execution_id FROM v2_commands c JOIN v2_leases l ON l.execution_id=c.execution_id "
                "WHERE c.id=? AND c.status='claimed' AND c.owner_id=? AND l.lease_token=? AND l.expires_at>?",
                (str(command_id), owner_id, lease_token, _iso(_utcnow())),
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise StorageConflict("command is no longer owned")
            connection.execute(
                "UPDATE v2_commands SET status=? WHERE id=? AND status='claimed'",
                (status, str(command_id)),
            )
            connection.execute("COMMIT")
        return True

    def request_cancel(
        self, execution_id: ExecutionId | str, reason: str | None = None
    ) -> bool:
        key = _execution_key(execution_id)
        safe_reason = self._safe_reason(reason, path="$.cancellation.reason")
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            execution = connection.execute(
                "SELECT snapshot_json FROM v2_executions WHERE id=? AND deleted_at IS NULL",
                (key,),
            ).fetchone()
            if execution is None:
                self._rollback(connection)
                return False
            connection.execute(
                "INSERT INTO v2_cancellation(execution_id,requested_at,reason) VALUES(?,?,?) "
                "ON CONFLICT(execution_id) DO NOTHING",
                (key, _iso(_utcnow()), safe_reason),
            )
            connection.execute(
                "UPDATE v2_commands SET status='cancelled' WHERE execution_id=? AND status='queued'",
                (key,),
            )
            # A queued execution has no owner that can perform its normal
            # finalization path.  Close it here, atomically with the durable
            # cancellation flag, so a restart cannot leave it indefinitely in
            # the created/queued state.
            active_lease = connection.execute(
                "SELECT 1 FROM v2_leases WHERE execution_id=?",
                (key,),
            ).fetchone()
            snapshot = ExecutionState.model_validate(_loads(execution["snapshot_json"]))
            if (
                active_lease is None
                and snapshot.lifecycle is not ExecutionStatus.FINISHED
            ):
                self._ensure_created_event(connection, key)
                sequence, monotonic_offset_ms = self._next_event_position(
                    connection, key
                )
                event = Event(
                    event_id=EventId(_new_id("event")),
                    execution_id=ExecutionId(key),
                    sequence=sequence,
                    kind=EventKind.EXECUTION_FINISHED,
                    monotonic_offset_ms=monotonic_offset_ms,
                    payload=self._terminal_payload(
                        ExecutionOutcome.CANCELLED,
                        reason=safe_reason or "cancelled",
                        reason_path="$.cancellation.reason",
                    ),
                )
                cancelled = snapshot.model_copy(
                    update={
                        "lifecycle": ExecutionStatus.FINISHED,
                        "outcome": ExecutionOutcome.CANCELLED,
                        "sequence": sequence,
                        "finished_at": event.timestamp,
                    }
                )
                connection.execute(
                    "INSERT INTO v2_events(id,execution_id,sequence,event_json,timestamp) VALUES(?,?,?,?,?)",
                    (
                        str(event.event_id.root),
                        key,
                        sequence,
                        _json(event.model_dump(mode="json", by_alias=True)),
                        _iso(event.timestamp),
                    ),
                )
                connection.execute(
                    "UPDATE v2_executions SET snapshot_json=? WHERE id=?",
                    (_json(cancelled.model_dump(mode="json")), key),
                )
            self._commit(connection)
            return True
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    cancel = request_cancel

    def finalize_cancelled(
        self, execution_id: ExecutionId | str, *, reason: str = "cancelled"
    ) -> bool:
        """Persist a cancellation terminal event when a worker observed it.

        Runtime runners may already have written their terminal event; in that
        case this operation is an idempotent no-op.
        """
        key = _execution_key(execution_id)
        safe_reason = (
            self._safe_reason(reason, path="$.cancellation.reason") or "cancelled"
        )
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            row = connection.execute(
                "SELECT snapshot_json FROM v2_executions WHERE id=? AND deleted_at IS NULL",
                (key,),
            ).fetchone()
            if row is None:
                self._rollback(connection)
                return False
            snapshot = ExecutionState.model_validate(_loads(row["snapshot_json"]))
            if snapshot.lifecycle is ExecutionStatus.FINISHED:
                self._rollback(connection)
                return False
            self._ensure_created_event(connection, key)
            sequence, monotonic_offset_ms = self._next_event_position(connection, key)
            event = Event(
                event_id=EventId(_new_id("event")),
                execution_id=ExecutionId(key),
                sequence=sequence,
                kind=EventKind.EXECUTION_FINISHED,
                monotonic_offset_ms=monotonic_offset_ms,
                payload=self._terminal_payload(
                    ExecutionOutcome.CANCELLED,
                    reason=safe_reason,
                    reason_path="$.cancellation.reason",
                ),
            )
            cancelled = snapshot.model_copy(
                update={
                    "lifecycle": ExecutionStatus.FINISHED,
                    "outcome": ExecutionOutcome.CANCELLED,
                    "sequence": sequence,
                    "finished_at": event.timestamp,
                }
            )
            connection.execute(
                "INSERT INTO v2_events(id,execution_id,sequence,event_json,timestamp) VALUES(?,?,?,?,?)",
                (
                    str(event.event_id.root),
                    key,
                    sequence,
                    _json(event.model_dump(mode="json", by_alias=True)),
                    _iso(event.timestamp),
                ),
            )
            connection.execute(
                "UPDATE v2_executions SET snapshot_json=? WHERE id=?",
                (_json(cancelled.model_dump(mode="json")), key),
            )
            self._commit(connection)
            return True
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    def cancellation_requested(self, execution_id: ExecutionId | str) -> bool:
        with self._connect() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM v2_cancellation WHERE execution_id=?",
                    (_execution_key(execution_id),),
                ).fetchone()
                is not None
            )

    def mark_stale_interrupted(
        self, *, now: datetime | None = None
    ) -> tuple[ExecutionId, ...]:
        moment = now or _utcnow()
        changed: list[ExecutionId] = []
        connection = self._connect()
        try:
            # Select and transition stale owners under one write transaction.
            # A heartbeat cannot race between the stale check and interruption.
            self._begin(connection, immediate=True)
            rows = connection.execute(
                "SELECT execution_id FROM v2_leases WHERE expires_at<=?",
                (_iso(moment),),
            ).fetchall()
            for row in rows:
                key = str(row[0])
                execution = connection.execute(
                    "SELECT snapshot_json FROM v2_executions WHERE id=? AND deleted_at IS NULL",
                    (key,),
                ).fetchone()
                if execution is None:
                    connection.execute(
                        "DELETE FROM v2_leases WHERE execution_id=?", (key,)
                    )
                    continue
                snapshot = ExecutionState.model_validate(
                    _loads(execution["snapshot_json"])
                )
                if snapshot.lifecycle is ExecutionStatus.FINISHED:
                    connection.execute(
                        "DELETE FROM v2_leases WHERE execution_id=?", (key,)
                    )
                    continue
                self._ensure_created_event(connection, key)
                sequence, monotonic_offset_ms = self._next_event_position(
                    connection, key
                )
                event = Event(
                    event_id=EventId(_new_id("event")),
                    execution_id=ExecutionId(key),
                    sequence=sequence,
                    kind=EventKind.EXECUTION_FINISHED,
                    monotonic_offset_ms=monotonic_offset_ms,
                    payload=self._terminal_payload(
                        ExecutionOutcome.INTERRUPTED,
                        reason="worker lease expired",
                        reason_path="$.lease.reason",
                    ),
                )
                interrupted = snapshot.model_copy(
                    update={
                        "lifecycle": ExecutionStatus.FINISHED,
                        "outcome": ExecutionOutcome.INTERRUPTED,
                        "sequence": sequence,
                        "finished_at": event.timestamp,
                    }
                )
                connection.execute(
                    "INSERT INTO v2_events(id,execution_id,sequence,event_json,timestamp) VALUES(?,?,?,?,?)",
                    (
                        str(event.event_id.root),
                        key,
                        sequence,
                        _json(event.model_dump(mode="json", by_alias=True)),
                        _iso(event.timestamp),
                    ),
                )
                connection.execute(
                    "UPDATE v2_executions SET snapshot_json=? WHERE id=?",
                    (_json(interrupted.model_dump(mode="json")), key),
                )
                connection.execute(
                    "UPDATE v2_commands SET status='interrupted' WHERE execution_id=? AND status IN ('queued','claimed')",
                    (key,),
                )
                connection.execute("DELETE FROM v2_leases WHERE execution_id=?", (key,))
                changed.append(ExecutionId(key))
            self._commit(connection)
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()
        return tuple(changed)

    def delete_execution(self, execution_id: ExecutionId | str) -> None:
        key = _execution_key(execution_id)
        snapshot = self.get_snapshot(key)
        if snapshot is None:
            raise StorageConflict("execution does not exist")
        if snapshot.lifecycle is not ExecutionStatus.FINISHED:
            raise StorageConflict("active execution cannot be deleted")
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            digests = [
                str(row[0])
                for row in connection.execute(
                    "SELECT sha256 FROM v2_artifacts WHERE execution_id=?", (key,)
                ).fetchall()
            ]
            digests.extend(
                str(row[0])
                for row in connection.execute(
                    "SELECT eb.sha256 FROM v2_event_blobs eb JOIN v2_events e ON e.id=eb.event_id WHERE e.execution_id=?",
                    (key,),
                ).fetchall()
            )
            connection.execute("DELETE FROM v2_executions WHERE id=?", (key,))
            for digest in digests:
                # Cascading artifact deletion removes metadata rows but does
                # not know about content-addressed reference counts.  Apply
                # each decrement in the same transaction as the deletion.
                connection.execute(
                    "UPDATE v2_blobs SET ref_count=ref_count-1 WHERE sha256=?",
                    (digest,),
                )
                row = connection.execute(
                    "SELECT ref_count,storage_key FROM v2_blobs WHERE sha256=?",
                    (digest,),
                ).fetchone()
                if row and int(row["ref_count"]) <= 0:
                    connection.execute("DELETE FROM v2_blobs WHERE sha256=?", (digest,))
            self._commit(connection)
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()
        self.artifacts.cleanup()

    delete = delete_execution

    def put_raw_evidence(
        self, event_id: EventId | str, content: bytes, *, media_type: str
    ) -> EvidenceCapture:
        """Redact, bound, and durably associate evidence with one event."""

        event_key = str(event_id.root if isinstance(event_id, EventId) else event_id)
        safe_media_type = redact_for_persistence(
            media_type, config=self._redaction_config, path="$.raw_evidence.media_type"
        )
        if (
            not isinstance(safe_media_type, str)
            or not safe_media_type
            or len(safe_media_type) > 256
        ):
            raise ValueError("raw evidence media_type must be 1-256 characters")
        connection = self._connect()
        try:
            self._begin(connection, immediate=True)
            event = connection.execute(
                "SELECT execution_id FROM v2_events WHERE id=?", (event_key,)
            ).fetchone()
            if event is None:
                raise RawEvidenceUnavailable("raw evidence event does not exist")
            execution_key = str(event[0])
            used_row = connection.execute(
                "SELECT COALESCE(SUM(b.size_bytes),0) "
                "FROM v2_event_blobs eb JOIN v2_events e ON e.id=eb.event_id "
                "JOIN v2_blobs b ON b.sha256=eb.sha256 "
                "WHERE e.execution_id=? AND eb.role='raw_evidence'",
                (execution_key,),
            ).fetchone()
            used = int(used_row[0]) if used_row is not None else 0
            prepared = _prepare_evidence(
                content,
                config=self._capture_config,
                redaction_config=self._redaction_config,
                remaining_bytes=max(self._capture_config.raw_execution_bytes - used, 0),
            )
            blob = self.artifacts.blob_store.put(prepared.content)
            existing_blob = connection.execute(
                "SELECT size_bytes,storage_key,compressed_size "
                "FROM v2_blobs WHERE sha256=?",
                (blob.sha256,),
            ).fetchone()
            storage_key = str(blob.path.relative_to(self.artifacts.blob_root))
            if existing_blob is not None and (
                int(existing_blob["size_bytes"]) != blob.size_bytes
                or str(existing_blob["storage_key"]) != storage_key
                or int(existing_blob["compressed_size"]) != blob.compressed_size_bytes
            ):
                raise RawEvidenceIntegrityError(
                    "raw evidence blob metadata is inconsistent"
                )
            ref = _make_evidence_ref(
                event_key, prepared.content, media_type=safe_media_type
            ).model_copy(update={"storage_key": storage_key})
            connection.execute(
                "INSERT INTO v2_blobs(sha256,size_bytes,compressed_size,media_type,"
                "storage_key,ref_count,created_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(sha256) "
                "DO UPDATE SET ref_count=ref_count+1",
                (
                    blob.sha256,
                    blob.size_bytes,
                    blob.compressed_size_bytes,
                    safe_media_type,
                    ref.storage_key,
                    1,
                    _iso(_utcnow()),
                ),
            )
            connection.execute(
                "INSERT INTO v2_event_blobs(event_id,sha256,role,media_type,"
                "evidence_id) VALUES(?,?,?,?,?)",
                (
                    event_key,
                    blob.sha256,
                    "raw_evidence",
                    safe_media_type,
                    ref.evidence_id,
                ),
            )
            self._commit(connection)
            return _make_evidence_capture(ref, prepared)
        except (BlobIntegrityError, ValueError):
            self._rollback(connection)
            raise RawEvidenceIntegrityError(
                "raw evidence blob could not be published"
            ) from None
        except Exception as exc:
            self._rollback(connection)
            if _is_integrity_error(exc):
                raise StorageConflict("raw evidence already exists for event") from None
            raise
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    def read_raw_evidence(
        self, reference: EvidenceRef, *, max_bytes: int = 1_048_576
    ) -> RawEvidence:
        if not isinstance(reference, EvidenceRef):
            raise RawEvidenceUnavailable("raw evidence reference is invalid")
        _validate_evidence_id(reference.evidence_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT eb.event_id,eb.evidence_id,eb.media_type,"
                "b.sha256,b.size_bytes,b.storage_key "
                "FROM v2_event_blobs eb JOIN v2_blobs b ON b.sha256=eb.sha256 "
                "WHERE eb.evidence_id=? AND eb.role='raw_evidence'",
                (reference.evidence_id,),
            ).fetchone()
        if row is None:
            raise RawEvidenceUnavailable("raw evidence is unavailable")
        try:
            recomputed_evidence_id = _evidence_id_for(str(row["event_id"]))
        except (RawEvidenceUnavailable, TypeError, ValueError):
            raise RawEvidenceIntegrityError(
                "raw evidence evidence_id binding is invalid"
            ) from None
        if str(row["evidence_id"]) != recomputed_evidence_id:
            raise RawEvidenceIntegrityError(
                "raw evidence evidence_id binding is invalid"
            )
        expected = EvidenceRef(
            evidence_id=recomputed_evidence_id,
            sha256=str(row["sha256"]),
            size_bytes=int(row["size_bytes"]),
            media_type=(
                str(row["media_type"]) if row["media_type"] is not None else None
            ),
            storage_key=str(row["storage_key"]),
        )
        _verify_evidence_reference(reference, expected)
        if expected.sha256 is None or expected.storage_key is None:
            raise RawEvidenceIntegrityError("raw evidence metadata is incomplete")
        try:
            expected_path = str(
                self.artifacts.blob_store.path_for(expected.sha256).relative_to(
                    self.artifacts.blob_root
                )
            )
        except (StorageError, ValueError):
            raise RawEvidenceIntegrityError(
                "raw evidence storage metadata is invalid"
            ) from None
        if expected.storage_key != expected_path:
            raise RawEvidenceIntegrityError("raw evidence storage metadata is invalid")
        try:
            content = self.artifacts.blob_store.read(
                expected.sha256, size_bytes=expected.size_bytes
            )
        except ArtifactNotFound:
            raise RawEvidenceUnavailable("raw evidence is unavailable") from None
        except (BlobIntegrityError, StorageError, ValueError):
            raise RawEvidenceIntegrityError(
                "raw evidence integrity verification failed"
            ) from None
        except OSError:
            raise RawEvidenceUnavailable("raw evidence is unavailable") from None
        return _make_evidence_result(expected, content, max_bytes=max_bytes)

    def clone_execution(
        self, execution_id: ExecutionId | str, *, use_latest: bool = False
    ) -> ExecutionId:
        source = _execution_key(execution_id)
        original = self.get_snapshot(source)
        if original is None:
            raise StorageConflict("execution does not exist")
        new_id = ExecutionId(_new_id("execution"))
        with self._connect() as connection:
            row = connection.execute(
                "SELECT specification_json,provenance_json FROM v2_executions WHERE id=?",
                (source,),
            ).fetchone()
            bindings = connection.execute(
                "SELECT ordinal,profile_id,revision_id,binding_json FROM v2_execution_server_bindings WHERE execution_id=? ORDER BY ordinal",
                (source,),
            ).fetchall()
            harness = connection.execute(
                "SELECT profile_id,revision_id,binding_json FROM v2_execution_harness_bindings WHERE execution_id=?",
                (source,),
            ).fetchone()
        server_values = [
            {"profile_id": row[1], "revision_id": row[2], **_loads(row[3], {})}
            for row in bindings
        ]
        harness_value = (
            {
                "profile_id": harness[0],
                "revision_id": harness[1],
                **_loads(harness[2], {}),
            }
            if harness
            else None
        )
        if use_latest:
            for item in server_values:
                if item.get("profile_id"):
                    item["revision_id"] = str(
                        self.resolve_revision(
                            str(item["profile_id"]), kind="server"
                        ).id.root
                    )
            if harness_value and harness_value.get("profile_id"):
                harness_value["revision_id"] = str(
                    self.resolve_revision(
                        str(harness_value["profile_id"]), kind="harness"
                    ).id.root
                )
        provenance = {
            "clone_of": source,
            "revision_selection": "latest" if use_latest else "original",
        }
        self.create(
            original.model_copy(
                update={
                    "execution_id": new_id,
                    "lifecycle": ExecutionStatus.CREATED,
                    "outcome": None,
                    "sequence": 0,
                    "finished_at": None,
                }
            ),
            specification=_loads(row[0]) if row and row[0] else None,
            provenance=provenance,
            server_bindings=server_values,
            harness_binding=harness_value,
            parent_execution_id=source,
        )
        return new_id

    clone = clone_execution

    def resolved_bindings(self, execution_id: ExecutionId | str) -> Mapping[str, Any]:
        """Return the immutable, submission-time profile binding snapshot."""
        key = _execution_key(execution_id)
        with self._connect() as connection:
            servers = connection.execute(
                "SELECT ordinal,binding_json FROM v2_execution_server_bindings WHERE execution_id=? ORDER BY ordinal",
                (key,),
            ).fetchall()
            harness = connection.execute(
                "SELECT binding_json FROM v2_execution_harness_bindings WHERE execution_id=?",
                (key,),
            ).fetchone()
            execution = connection.execute(
                "SELECT provenance_json FROM v2_executions WHERE id=?", (key,)
            ).fetchone()
        return {
            "servers": tuple(_loads(row["binding_json"], {}) for row in servers),
            "harness": _loads(harness[0], {}) if harness else None,
            "provenance": _loads(execution[0], {})
            if execution and execution[0]
            else None,
        }

    def close(self) -> None:
        with self._callback_lock:
            self._callbacks.clear()
        self.artifacts.close()
        super().close()


SQLiteStore = SQLiteExecutionStore
PersistentExecutionStore = SQLiteExecutionStore

__all__ = [
    "Command",
    "Lease",
    "PersistentExecutionStore",
    "ProfileRecord",
    "ProfileRevisionRecord",
    "SQLiteArtifactStore",
    "SQLiteExecutionStore",
    "SQLiteStore",
]
