"""Durable state for human responses to managed MCP elicitation rounds.

This module deliberately does not know how an execution is run.  It stores a
single pending round, its ownership lease, and the delivery state needed by a
later execution handle to decide whether a response may safely be delivered.
The response payload is retained exactly for an operational retry; a separate
redacted projection is persisted for diagnostics.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, cast, overload

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import (  # type: ignore[import-untyped]
    SchemaError,
    ValidationError,
)
from pydantic import ConfigDict, Field, model_validator
from referencing import Registry
from referencing.exceptions import Unresolvable

from .._types.base import FrozenModel
from ..elicitation import (
    ElicitationResponse,
    FormElicitationRequest,
    PendingElicitationRound,
)
from ..errors import (
    ManagedInputConflict,
    ManagedInputRecoveryError,
    ManagedInputStateError,
    ManagedInputValidationError,
)
from ..trace.redaction import redact_for_persistence

ManagedInputStatus = Literal[
    "pending",
    "response_validated",
    "delivery_started",
    "delivered",
    "resolved",
    "failed",
]

_MIGRATION_LOCK = threading.Lock()


class ManagedInputLease(FrozenModel):
    """The compare-and-set token currently allowed to mutate a round."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_id: str = Field(min_length=1)
    lease_token: str = Field(min_length=1)
    expires_at: datetime


class ManagedInputRecord(FrozenModel):
    """Immutable view of one durable managed-input round."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pending: PendingElicitationRound
    round_index: int = Field(ge=0)
    round_limit: int = Field(gt=0)
    status: ManagedInputStatus
    lease: ManagedInputLease
    responses: Mapping[str, ElicitationResponse] | None = None
    response_idempotency_key: str | None = None
    harness_session_id: str | None = None
    native_resume_token: str | None = None
    delivery_idempotency_key: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    operation_parameters: Mapping[str, object] = Field(default_factory=dict)
    delivery_attempts: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime
    response_validated_at: datetime | None = None
    delivery_started_at: datetime | None = None
    delivered_at: datetime | None = None
    resolved_at: datetime | None = None
    failed_at: datetime | None = None
    failure_code: str | None = None
    failure_message: str | None = None

    @model_validator(mode="after")
    def _validate_state(self) -> ManagedInputRecord:
        if self.round_index >= self.round_limit:
            raise ValueError("round index must be below the round limit")
        if self.status == "pending":
            if self.responses is not None or self.response_idempotency_key is not None:
                raise ValueError("pending rounds cannot contain responses")
            if self.delivery_attempts:
                raise ValueError("pending rounds cannot have delivery attempts")
        elif self.status in {
            "response_validated",
            "delivery_started",
            "delivered",
            "resolved",
        }:
            if self.responses is None or self.response_idempotency_key is None:
                raise ValueError("delivered rounds require validated responses")
        if self.status == "failed" and not self.failure_code:
            raise ValueError("failed rounds require a failure code")
        return self

    @property
    def execution_id(self) -> str:
        return self.pending.execution_id

    @property
    def round_id(self) -> str:
        return self.pending.round_id

    @property
    def request_state(self) -> str | None:
        return self.pending.request_state

    @property
    def lease_token(self) -> str:
        return self.lease.lease_token

    @property
    def owner_id(self) -> str:
        return self.lease.owner_id


class ManagedInputStore(Protocol):
    """Storage contract consumed by future execution handles."""

    def create_round(
        self,
        pending: PendingElicitationRound,
        *,
        round_index: int,
        round_limit: int,
        owner_id: str,
        lease_seconds: float,
        lease_token: str | None = None,
        harness_session_id: str | None = None,
        native_resume_token: str | None = None,
        delivery_idempotency_key: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        operation_parameters: Mapping[str, object] | None = None,
    ) -> ManagedInputRecord: ...

    def get_round(
        self, execution_id: str, round_id: str
    ) -> ManagedInputRecord | None: ...

    def list_rounds(self, execution_id: str) -> tuple[ManagedInputRecord, ...]: ...

    def claim_round(
        self,
        execution_id: str,
        round_id: str,
        *,
        owner_id: str,
        lease_seconds: float,
        expected_lease_token: str | None = None,
        delivery_state: Literal["not_started", "not_delivered", "delivered"]
        | None = None,
        idempotent_delivery: bool = False,
    ) -> ManagedInputRecord: ...

    def renew(
        self,
        execution_id: str,
        round_id: str,
        *,
        owner_id: str,
        lease_token: str,
        lease_seconds: float,
    ) -> ManagedInputRecord: ...

    def submit_responses(
        self,
        execution_id: str,
        round_id: str,
        responses: Mapping[str, ElicitationResponse],
        *,
        owner_id: str,
        lease_token: str,
        response_idempotency_key: str,
    ) -> ManagedInputRecord: ...

    def start_delivery(
        self, execution_id: str, round_id: str, *, owner_id: str, lease_token: str
    ) -> ManagedInputRecord: ...

    def mark_delivered(
        self, execution_id: str, round_id: str, *, owner_id: str, lease_token: str
    ) -> ManagedInputRecord: ...

    def resolve(
        self, execution_id: str, round_id: str, *, owner_id: str, lease_token: str
    ) -> ManagedInputRecord: ...

    def fail(
        self,
        execution_id: str,
        round_id: str,
        *,
        owner_id: str,
        lease_token: str,
        code: str,
        message: str,
    ) -> ManagedInputRecord: ...

    def fail_recovery(
        self,
        execution_id: str,
        round_id: str,
        *,
        expected_lease_token: str,
        message: str,
    ) -> ManagedInputRecord: ...

    def redacted_responses(
        self, execution_id: str, round_id: str
    ) -> Mapping[str, object] | None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ManagedInputValidationError(
            "managed input contains invalid JSON"
        ) from exc


def _new_token(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(24)}"


@overload
def _require_text(value: str | None, label: str, *, required: Literal[True]) -> str: ...


@overload
def _require_text(
    value: str | None, label: str, *, required: Literal[False] = False
) -> str | None: ...


def _require_text(
    value: str | None, label: str, *, required: bool = False
) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        raise ManagedInputValidationError(f"{label} must be a non-empty string")
    return value


def _response_payload(
    responses: Mapping[str, ElicitationResponse],
) -> dict[str, dict[str, object]]:
    return {str(key): value.model_dump(mode="json") for key, value in responses.items()}


class SQLiteManagedInputStore:
    """SQLite-backed managed-input storage sharing a database path safely."""

    def __init__(
        self,
        database: str | Path,
        *,
        busy_timeout_ms: int = 5000,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.database = Path(str(database).removeprefix("sqlite:///"))
        if self.database.exists() and self.database.is_symlink():
            raise ManagedInputValidationError("database path must not be a symlink")
        if self.database.exists() and not self.database.is_file():
            raise ManagedInputValidationError("database path must name a file")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        self.busy_timeout_ms = busy_timeout_ms
        self._clock = clock
        current = self._clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("clock must return timezone-aware datetimes")
        self._initialize()

    def _current_time(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ManagedInputValidationError(
                "clock must return timezone-aware datetimes"
            )
        return current.astimezone(timezone.utc)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database,
            timeout=max(self.busy_timeout_ms / 1000, 0.001),
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode=WAL")
        for path in (
            self.database,
            Path(f"{self.database}-wal"),
            Path(f"{self.database}-shm"),
        ):
            if path.exists() and not path.is_symlink():
                os.chmod(path, 0o600)
        return connection

    def _initialize(self) -> None:
        with _MIGRATION_LOCK, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                CREATE TABLE IF NOT EXISTS m3_managed_input_rounds (
                  execution_id TEXT NOT NULL,
                  round_id TEXT NOT NULL,
                  round_index INTEGER NOT NULL CHECK(round_index >= 0),
                  round_limit INTEGER NOT NULL CHECK(round_limit > 0),
                  pending_json TEXT NOT NULL,
                  status TEXT NOT NULL CHECK(status IN ('pending','response_validated','delivery_started','delivered','resolved','failed')),
                  responses_json TEXT,
                  response_idempotency_key TEXT,
                  owner_id TEXT NOT NULL,
                  lease_token TEXT NOT NULL,
                  lease_expires_at TEXT NOT NULL,
                  harness_session_id TEXT,
                  native_resume_token TEXT,
                  delivery_idempotency_key TEXT,
                  session_id TEXT,
                  turn_id TEXT,
                  operation_parameters_json TEXT NOT NULL,
                  delivery_attempts INTEGER NOT NULL DEFAULT 0 CHECK(delivery_attempts >= 0),
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  response_validated_at TEXT,
                  delivery_started_at TEXT,
                  delivered_at TEXT,
                  resolved_at TEXT,
                  failed_at TEXT,
                  failure_code TEXT,
                  failure_message TEXT,
                  PRIMARY KEY(execution_id, round_id),
                  UNIQUE(execution_id, round_index)
                );
                """
                )
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(m3_managed_input_rounds)"
                    ).fetchall()
                }
                migrations = {
                    "execution_id": "TEXT NOT NULL DEFAULT ''",
                    "round_id": "TEXT NOT NULL DEFAULT ''",
                    "round_index": "INTEGER NOT NULL DEFAULT 0",
                    "round_limit": "INTEGER NOT NULL DEFAULT 1",
                    "pending_json": "TEXT NOT NULL DEFAULT '{}'",
                    "status": "TEXT NOT NULL DEFAULT 'pending'",
                    "responses_json": "TEXT",
                    "response_idempotency_key": "TEXT",
                    "owner_id": "TEXT NOT NULL DEFAULT ''",
                    "lease_token": "TEXT NOT NULL DEFAULT ''",
                    "lease_expires_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
                    "harness_session_id": "TEXT",
                    "native_resume_token": "TEXT",
                    "delivery_idempotency_key": "TEXT",
                    "session_id": "TEXT",
                    "turn_id": "TEXT",
                    "operation_parameters_json": "TEXT NOT NULL DEFAULT '{}'",
                    "delivery_attempts": "INTEGER NOT NULL DEFAULT 0",
                    "created_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
                    "updated_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
                    "response_validated_at": "TEXT",
                    "delivery_started_at": "TEXT",
                    "delivered_at": "TEXT",
                    "resolved_at": "TEXT",
                    "failed_at": "TEXT",
                    "failure_code": "TEXT",
                    "failure_message": "TEXT",
                }
                for name, definition in migrations.items():
                    if name not in columns:
                        connection.execute(
                            f"ALTER TABLE m3_managed_input_rounds ADD COLUMN {name} {definition}"
                        )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS m3_managed_input_pending ON m3_managed_input_rounds(execution_id, status, round_index)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS m3_managed_input_diagnostics (execution_id TEXT NOT NULL, round_id TEXT NOT NULL, responses_redacted_json TEXT NOT NULL, PRIMARY KEY(execution_id, round_id))"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS m3_managed_input_schema (version INTEGER PRIMARY KEY CHECK(version > 0))"
                )
                connection.execute(
                    "INSERT INTO m3_managed_input_schema(version) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM m3_managed_input_schema)"
                )
                connection.execute("COMMIT")
            except BaseException:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    @contextmanager
    def _write(self) -> Any:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    @staticmethod
    def _row(
        connection: sqlite3.Connection, execution_id: str, round_id: str
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT * FROM m3_managed_input_rounds WHERE execution_id=? AND round_id=?",
                (execution_id, round_id),
            ).fetchone(),
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> ManagedInputRecord:
        try:
            pending_payload = json.loads(row["pending_json"])
            if not isinstance(pending_payload, Mapping):
                raise ValueError("pending JSON must be an object")
            pending = PendingElicitationRound.model_validate(pending_payload)
            raw_responses = (
                json.loads(row["responses_json"])
                if row["responses_json"] is not None
                else None
            )
            if raw_responses is not None and not isinstance(raw_responses, Mapping):
                raise ValueError("responses JSON must be an object")
            operation_parameters = json.loads(row["operation_parameters_json"])
            if not isinstance(operation_parameters, Mapping):
                raise ValueError("operation parameters JSON must be an object")
            responses = (
                {
                    str(key): ElicitationResponse.model_validate(value)
                    for key, value in raw_responses.items()
                }
                if raw_responses is not None
                else None
            )
            return ManagedInputRecord(
                pending=pending,
                round_index=int(row["round_index"]),
                round_limit=int(row["round_limit"]),
                status=cast(ManagedInputStatus, str(row["status"])),
                lease=ManagedInputLease(
                    owner_id=str(row["owner_id"]),
                    lease_token=str(row["lease_token"]),
                    expires_at=_parse_dt(str(row["lease_expires_at"])),
                ),
                responses=responses,
                response_idempotency_key=row["response_idempotency_key"],
                harness_session_id=row["harness_session_id"],
                native_resume_token=row["native_resume_token"],
                delivery_idempotency_key=row["delivery_idempotency_key"],
                session_id=row["session_id"],
                turn_id=row["turn_id"],
                operation_parameters=operation_parameters,
                delivery_attempts=int(row["delivery_attempts"]),
                created_at=_parse_dt(str(row["created_at"])),
                updated_at=_parse_dt(str(row["updated_at"])),
                response_validated_at=(
                    _parse_dt(str(row["response_validated_at"]))
                    if row["response_validated_at"]
                    else None
                ),
                delivery_started_at=(
                    _parse_dt(str(row["delivery_started_at"]))
                    if row["delivery_started_at"]
                    else None
                ),
                delivered_at=(
                    _parse_dt(str(row["delivered_at"])) if row["delivered_at"] else None
                ),
                resolved_at=(
                    _parse_dt(str(row["resolved_at"])) if row["resolved_at"] else None
                ),
                failed_at=(
                    _parse_dt(str(row["failed_at"])) if row["failed_at"] else None
                ),
                failure_code=row["failure_code"],
                failure_message=row["failure_message"],
            )
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            raise ManagedInputValidationError(
                "stored managed-input record is invalid"
            ) from exc

    def _get(self, execution_id: str, round_id: str) -> ManagedInputRecord | None:
        with self._connect() as connection:
            row = self._row(connection, execution_id, round_id)
        return self._record(row) if row is not None else None

    def get_round(self, execution_id: str, round_id: str) -> ManagedInputRecord | None:
        execution = _require_text(execution_id, "execution_id", required=True)
        round_key = _require_text(round_id, "round_id", required=True)
        return self._get(execution, round_key)

    def list_rounds(self, execution_id: str) -> tuple[ManagedInputRecord, ...]:
        key = _require_text(execution_id, "execution_id", required=True)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM m3_managed_input_rounds WHERE execution_id=? ORDER BY round_index",
                (key,),
            ).fetchall()
        return tuple(self._record(cast(sqlite3.Row, row)) for row in rows)

    def create_round(
        self,
        pending: PendingElicitationRound,
        *,
        round_index: int,
        round_limit: int,
        owner_id: str,
        lease_seconds: float,
        lease_token: str | None = None,
        harness_session_id: str | None = None,
        native_resume_token: str | None = None,
        delivery_idempotency_key: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        operation_parameters: Mapping[str, object] | None = None,
    ) -> ManagedInputRecord:
        if not isinstance(pending, PendingElicitationRound) or not pending.requests:
            raise ManagedInputValidationError(
                "managed input requires a non-empty elicitation round"
            )
        if (
            not isinstance(round_index, int)
            or isinstance(round_index, bool)
            or round_index < 0
        ):
            raise ManagedInputValidationError("round_index must be non-negative")
        if (
            not isinstance(round_limit, int)
            or isinstance(round_limit, bool)
            or round_limit <= 0
        ):
            raise ManagedInputValidationError("round_limit must be positive")
        if round_index >= round_limit:
            raise ManagedInputValidationError("round_index exceeds round limit")
        owner = _require_text(owner_id, "owner_id", required=True)
        if (
            not isinstance(lease_seconds, (int, float))
            or isinstance(lease_seconds, bool)
            or lease_seconds <= 0
        ):
            raise ManagedInputValidationError("lease_seconds must be positive")
        token = _require_text(lease_token, "lease_token") or _new_token("lease")
        for label, value in (
            ("harness_session_id", harness_session_id),
            ("native_resume_token", native_resume_token),
            ("delivery_idempotency_key", delivery_idempotency_key),
            ("session_id", session_id),
            ("turn_id", turn_id),
        ):
            _require_text(value, label)
        parameters = dict(operation_parameters or {})
        pending_json = _json(pending.model_dump(mode="json"))
        parameters_json = _json(parameters)
        now = self._current_time()
        expires = now + timedelta(seconds=float(lease_seconds))
        values = (
            pending.execution_id,
            pending.round_id,
            round_index,
            round_limit,
            pending_json,
            "pending",
            None,
            None,
            owner,
            token,
            _iso(expires),
            harness_session_id,
            native_resume_token,
            delivery_idempotency_key,
            session_id,
            turn_id,
            parameters_json,
            0,
            _iso(now),
            _iso(now),
        )
        try:
            with self._write() as connection:
                existing = self._row(connection, pending.execution_id, pending.round_id)
                if existing is not None:
                    current = self._record(existing)
                    if (
                        current.pending.model_dump(mode="json")
                        == pending.model_dump(mode="json")
                        and current.round_index == round_index
                        and current.round_limit == round_limit
                        and current.owner_id == owner
                        and current.lease_token == token
                        and current.harness_session_id == harness_session_id
                        and current.native_resume_token == native_resume_token
                        and current.delivery_idempotency_key == delivery_idempotency_key
                        and current.session_id == session_id
                        and current.turn_id == turn_id
                        and dict(current.operation_parameters) == parameters
                    ):
                        return current
                    raise ManagedInputConflict(
                        "managed-input round already exists with different data"
                    )
                connection.execute(
                    "INSERT INTO m3_managed_input_rounds(execution_id,round_id,round_index,round_limit,pending_json,status,responses_json,response_idempotency_key,owner_id,lease_token,lease_expires_at,harness_session_id,native_resume_token,delivery_idempotency_key,session_id,turn_id,operation_parameters_json,delivery_attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
        except sqlite3.IntegrityError as exc:
            raise ManagedInputConflict(
                "managed-input round conflicts with an existing round"
            ) from exc
        result = self._get(pending.execution_id, pending.round_id)
        if result is None:
            raise ManagedInputValidationError(
                "managed-input round disappeared after creation"
            )
        return result

    def claim_round(
        self,
        execution_id: str,
        round_id: str,
        *,
        owner_id: str,
        lease_seconds: float,
        expected_lease_token: str | None = None,
        delivery_state: Literal["not_started", "not_delivered", "delivered"]
        | None = None,
        idempotent_delivery: bool = False,
    ) -> ManagedInputRecord:
        execution = _require_text(execution_id, "execution_id", required=True)
        round_key = _require_text(round_id, "round_id", required=True)
        owner = _require_text(owner_id, "owner_id", required=True)
        expected = _require_text(expected_lease_token, "expected_lease_token")
        if (
            not isinstance(lease_seconds, (int, float))
            or isinstance(lease_seconds, bool)
            or lease_seconds <= 0
        ):
            raise ManagedInputValidationError("lease_seconds must be positive")
        now = self._current_time()
        with self._write() as connection:
            row = self._row(connection, execution, round_key)
            if row is None:
                raise ManagedInputConflict("managed-input round does not exist")
            current = self._record(row)
            if current.status == "failed":
                if current.failure_code == "recovery_unavailable":
                    raise ManagedInputRecoveryError(
                        current.failure_message or "managed input cannot be recovered"
                    )
                raise ManagedInputStateError(
                    "failed managed-input round cannot be claimed"
                )
            if current.status == "resolved":
                raise ManagedInputStateError(
                    "resolved managed-input round cannot be claimed"
                )
            if delivery_state is None:
                raise ManagedInputRecoveryError(
                    "replacement claims require adapter delivery-state evidence"
                )
            if current.status == "delivery_started":
                if delivery_state == "not_delivered" and not idempotent_delivery:
                    raise ManagedInputRecoveryError(
                        "not_delivered recovery requires idempotent delivery proof"
                    )
                if delivery_state not in {"not_delivered", "delivered"}:
                    raise ManagedInputRecoveryError(
                        "delivery_started round has ambiguous delivery state"
                    )
            else:
                expected_state = {
                    "pending": "not_started",
                    "response_validated": "not_delivered",
                    "delivered": "delivered",
                }.get(current.status)
                if expected_state != delivery_state:
                    raise ManagedInputRecoveryError(
                        "adapter delivery-state evidence does not match the persisted round"
                    )
            if current.status == "response_validated" and not idempotent_delivery:
                raise ManagedInputRecoveryError(
                    "validated responses require idempotent delivery proof"
                )
            if current.lease.expires_at > now:
                raise ManagedInputConflict("managed-input round lease is still held")
            if expected is None or expected != current.lease_token:
                raise ManagedInputConflict("expected lease token does not match")
            token = _new_token("lease")
            expires = now + timedelta(seconds=float(lease_seconds))
            recovered_status = current.status
            delivered_at = current.delivered_at
            if current.status == "delivery_started":
                if delivery_state == "not_delivered":
                    recovered_status = "response_validated"
                else:
                    recovered_status = "delivered"
                    delivered_at = now
            connection.execute(
                "UPDATE m3_managed_input_rounds SET status=?,delivered_at=?,owner_id=?,lease_token=?,lease_expires_at=?,updated_at=? WHERE execution_id=? AND round_id=? AND owner_id=? AND lease_token=? AND lease_expires_at<=?",
                (
                    recovered_status,
                    _iso(delivered_at) if delivered_at is not None else None,
                    owner,
                    token,
                    _iso(expires),
                    _iso(now),
                    execution,
                    round_key,
                    current.owner_id,
                    current.lease_token,
                    _iso(now),
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ManagedInputConflict(
                    "managed-input lease was changed concurrently"
                )
        result = self._get(execution, round_key)
        if result is None:
            raise ManagedInputConflict("managed-input round disappeared after claim")
        return result

    def renew(
        self,
        execution_id: str,
        round_id: str,
        *,
        owner_id: str,
        lease_token: str,
        lease_seconds: float,
    ) -> ManagedInputRecord:
        """Renew an active local wait without changing its compare-and-set token."""

        execution = _require_text(execution_id, "execution_id", required=True)
        round_key = _require_text(round_id, "round_id", required=True)
        owner = _require_text(owner_id, "owner_id", required=True)
        token = _require_text(lease_token, "lease_token", required=True)
        if (
            not isinstance(lease_seconds, (int, float))
            or isinstance(lease_seconds, bool)
            or lease_seconds <= 0
        ):
            raise ManagedInputValidationError("lease_seconds must be positive")
        now = self._current_time()
        expires = now + timedelta(seconds=float(lease_seconds))
        with self._write() as connection:
            row = self._row(connection, execution, round_key)
            current = self._record(row) if row is not None else None
            if current is None:
                raise ManagedInputConflict("managed-input round does not exist")
            self._authorize(current, owner_id=owner, lease_token=token)
            if current.status not in {
                "pending",
                "response_validated",
                "delivery_started",
            }:
                raise ManagedInputStateError(
                    "terminal managed-input round cannot renew"
                )
            cursor = connection.execute(
                "UPDATE m3_managed_input_rounds SET lease_expires_at=?,updated_at=? WHERE execution_id=? AND round_id=? AND owner_id=? AND lease_token=? AND status IN ('pending','response_validated','delivery_started')",
                (_iso(expires), _iso(now), execution, round_key, owner, token),
            )
            if cursor.rowcount != 1:
                raise ManagedInputConflict(
                    "managed-input lease renewal raced with another owner"
                )
        result = self._get(execution, round_key)
        if result is None:
            raise ManagedInputConflict("managed-input round disappeared after renewal")
        return result

    def _authorize(
        self,
        current: ManagedInputRecord,
        *,
        owner_id: str,
        lease_token: str,
        allow_expired: bool = False,
    ) -> None:
        owner = _require_text(owner_id, "owner_id", required=True)
        token = _require_text(lease_token, "lease_token", required=True)
        if current.owner_id != owner or current.lease_token != token:
            raise ManagedInputConflict("managed-input round is owned by another lease")
        if not allow_expired and current.lease.expires_at <= self._current_time():
            raise ManagedInputConflict("managed-input lease has expired")

    @staticmethod
    def _validate_responses(
        pending: PendingElicitationRound,
        responses: Mapping[str, ElicitationResponse],
    ) -> dict[str, ElicitationResponse]:
        if not isinstance(responses, Mapping):
            raise ManagedInputValidationError("responses must be a mapping")
        actual = set(responses)
        expected = set(pending.requests)
        if actual != expected:
            extra = actual - expected
            if extra:
                raise ManagedInputValidationError(
                    "responses contain unexpected request keys"
                )
            raise ManagedInputValidationError(
                "responses must contain exactly the pending request keys"
            )
        normalized: dict[str, ElicitationResponse] = {}
        for key, request in pending.requests.items():
            response = responses.get(key)
            if not isinstance(response, ElicitationResponse):
                raise ManagedInputValidationError(
                    "responses must contain ElicitationResponse values"
                )
            if (
                isinstance(request, FormElicitationRequest)
                and response.action == "accept"
            ):
                if response.content is None:
                    raise ManagedInputValidationError(
                        "form acceptance requires content"
                    )
                try:
                    validator = Draft202012Validator(
                        dict(request.requested_schema), registry=Registry()
                    )
                    validator.validate(dict(response.content or {}))
                except (SchemaError, ValidationError, Unresolvable) as exc:
                    raise ManagedInputValidationError(
                        "response content does not match the request schema"
                    ) from exc
            if (
                not isinstance(request, FormElicitationRequest)
                and response.content is not None
            ):
                raise ManagedInputValidationError(
                    "URL responses cannot contain form content"
                )
            normalized[key] = ElicitationResponse.model_validate(
                response.model_dump(mode="json")
            )
        return normalized

    def submit_responses(
        self,
        execution_id: str,
        round_id: str,
        responses: Mapping[str, ElicitationResponse],
        *,
        owner_id: str,
        lease_token: str,
        response_idempotency_key: str,
    ) -> ManagedInputRecord:
        execution = _require_text(execution_id, "execution_id", required=True)
        round_key = _require_text(round_id, "round_id", required=True)
        idem = _require_text(
            response_idempotency_key, "response_idempotency_key", required=True
        )
        with self._write() as connection:
            row = self._row(connection, execution, round_key)
            current = self._record(row) if row is not None else None
            if current is None:
                raise ManagedInputConflict("managed-input round does not exist")
            if current.status == "failed":
                raise ManagedInputStateError(
                    "failed managed-input round cannot accept responses"
                )
            self._authorize(
                current,
                owner_id=owner_id,
                lease_token=lease_token,
                allow_expired=current.status != "pending",
            )
            response_map = self._validate_responses(current.pending, responses)
            payload = _response_payload(response_map)
            payload_json = _json(payload)
            if current.status != "pending":
                current_payload = _json(_response_payload(current.responses or {}))
                if (
                    current.response_idempotency_key == idem
                    and current_payload == payload_json
                ):
                    return current
                raise ManagedInputConflict(
                    "response idempotency key conflicts with an accepted response"
                )
            redacted_json = _json(
                redact_for_persistence(payload, path="$.managed_input.responses")
            )
            now = self._current_time()
            connection.execute(
                "UPDATE m3_managed_input_rounds SET status='response_validated',responses_json=?,response_idempotency_key=?,response_validated_at=?,updated_at=?,failure_code=NULL,failure_message=NULL WHERE execution_id=? AND round_id=? AND owner_id=? AND lease_token=? AND status='pending' AND lease_expires_at>?",
                (
                    payload_json,
                    idem,
                    _iso(now),
                    _iso(now),
                    execution,
                    round_key,
                    current.owner_id,
                    current.lease_token,
                    _iso(now),
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ManagedInputConflict(
                    "managed-input response submission raced with another owner"
                )
            connection.execute(
                "INSERT INTO m3_managed_input_diagnostics(execution_id,round_id,responses_redacted_json) VALUES(?,?,?) ON CONFLICT(execution_id,round_id) DO UPDATE SET responses_redacted_json=excluded.responses_redacted_json",
                (execution, round_key, redacted_json),
            )
        result = self._get(execution, round_key)
        if result is None:
            raise ManagedInputConflict("managed-input round disappeared after response")
        return result

    def _transition(
        self,
        execution_id: str,
        round_id: str,
        *,
        owner_id: str,
        lease_token: str,
        expected: str,
        target: str,
        timestamp_column: str,
        increment_attempt: bool = False,
    ) -> ManagedInputRecord:
        execution = _require_text(execution_id, "execution_id", required=True)
        round_key = _require_text(round_id, "round_id", required=True)
        now = self._current_time()
        with self._write() as connection:
            row = self._row(connection, execution, round_key)
            current = self._record(row) if row is not None else None
            if current is None:
                raise ManagedInputConflict("managed-input round does not exist")
            self._authorize(current, owner_id=owner_id, lease_token=lease_token)
            if current.status == target:
                return current
            if current.status != expected:
                raise ManagedInputStateError(
                    f"managed-input transition requires {expected}, got {current.status}"
                )
            assignments = ["status=?", f"{timestamp_column}=?", "updated_at=?"]
            values: list[Any] = [target, _iso(now), _iso(now)]
            if increment_attempt:
                assignments.append("delivery_attempts=delivery_attempts+1")
            values.extend(
                [
                    execution,
                    round_key,
                    current.owner_id,
                    current.lease_token,
                    expected,
                    _iso(now),
                ]
            )
            cursor = connection.execute(
                f"UPDATE m3_managed_input_rounds SET {','.join(assignments)} WHERE execution_id=? AND round_id=? AND owner_id=? AND lease_token=? AND status=? AND lease_expires_at>?",
                tuple(values),
            )
            if cursor.rowcount != 1:
                raise ManagedInputConflict(
                    "managed-input transition raced with another owner"
                )
        result = self._get(execution, round_key)
        if result is None:
            raise ManagedInputConflict(
                "managed-input round disappeared after transition"
            )
        return result

    def start_delivery(
        self, execution_id: str, round_id: str, *, owner_id: str, lease_token: str
    ) -> ManagedInputRecord:
        return self._transition(
            execution_id,
            round_id,
            owner_id=owner_id,
            lease_token=lease_token,
            expected="response_validated",
            target="delivery_started",
            timestamp_column="delivery_started_at",
            increment_attempt=True,
        )

    def mark_delivered(
        self, execution_id: str, round_id: str, *, owner_id: str, lease_token: str
    ) -> ManagedInputRecord:
        return self._transition(
            execution_id,
            round_id,
            owner_id=owner_id,
            lease_token=lease_token,
            expected="delivery_started",
            target="delivered",
            timestamp_column="delivered_at",
        )

    def resolve(
        self, execution_id: str, round_id: str, *, owner_id: str, lease_token: str
    ) -> ManagedInputRecord:
        return self._transition(
            execution_id,
            round_id,
            owner_id=owner_id,
            lease_token=lease_token,
            expected="delivered",
            target="resolved",
            timestamp_column="resolved_at",
        )

    def fail(
        self,
        execution_id: str,
        round_id: str,
        *,
        owner_id: str,
        lease_token: str,
        code: str,
        message: str,
    ) -> ManagedInputRecord:
        failure_code = _require_text(code, "code", required=True)
        failure_message = _require_text(message, "message", required=True)
        execution = _require_text(execution_id, "execution_id", required=True)
        round_key = _require_text(round_id, "round_id", required=True)
        now = self._current_time()
        with self._write() as connection:
            row = self._row(connection, execution, round_key)
            current = self._record(row) if row is not None else None
            if current is None:
                raise ManagedInputConflict("managed-input round does not exist")
            self._authorize(current, owner_id=owner_id, lease_token=lease_token)
            if current.status == "resolved":
                raise ManagedInputStateError("resolved managed-input round cannot fail")
            if current.status == "failed":
                if (
                    current.failure_code == failure_code
                    and current.failure_message == failure_message
                ):
                    return current
                raise ManagedInputConflict("managed-input failure is already recorded")
            cursor = connection.execute(
                "UPDATE m3_managed_input_rounds SET status='failed',failed_at=?,failure_code=?,failure_message=?,updated_at=? WHERE execution_id=? AND round_id=? AND owner_id=? AND lease_token=? AND status<>? AND lease_expires_at>?",
                (
                    _iso(now),
                    failure_code,
                    failure_message,
                    _iso(now),
                    execution,
                    round_key,
                    current.owner_id,
                    current.lease_token,
                    "resolved",
                    _iso(now),
                ),
            )
            if cursor.rowcount != 1:
                raise ManagedInputConflict(
                    "managed-input failure raced with another owner"
                )
        result = self._get(execution, round_key)
        if result is None:
            raise ManagedInputConflict("managed-input round disappeared after failure")
        return result

    def fail_recovery(
        self,
        execution_id: str,
        round_id: str,
        *,
        expected_lease_token: str,
        message: str,
    ) -> ManagedInputRecord:
        execution = _require_text(execution_id, "execution_id", required=True)
        round_key = _require_text(round_id, "round_id", required=True)
        expected = _require_text(
            expected_lease_token, "expected_lease_token", required=True
        )
        failure_message = _require_text(message, "message", required=True)
        now = self._current_time()
        with self._write() as connection:
            row = self._row(connection, execution, round_key)
            current = self._record(row) if row is not None else None
            if current is None:
                raise ManagedInputConflict("managed-input round does not exist")
            if current.lease_token != expected:
                raise ManagedInputConflict("expected lease token does not match")
            if current.status == "failed":
                if current.failure_code == "recovery_unavailable":
                    return current
                raise ManagedInputStateError(
                    "managed-input round has another terminal failure"
                )
            if current.status not in {
                "pending",
                "response_validated",
                "delivery_started",
                "delivered",
            }:
                raise ManagedInputStateError(
                    "only unresolved rounds require recovery failure"
                )
            if current.lease.expires_at > now:
                raise ManagedInputConflict("managed-input lease is still held")
            cursor = connection.execute(
                "UPDATE m3_managed_input_rounds SET status='failed',failed_at=?,failure_code='recovery_unavailable',failure_message=?,updated_at=? WHERE execution_id=? AND round_id=? AND lease_token=? AND status IN ('pending','response_validated','delivery_started','delivered') AND lease_expires_at<=?",
                (
                    _iso(now),
                    failure_message,
                    _iso(now),
                    execution,
                    round_key,
                    expected,
                    _iso(now),
                ),
            )
            if cursor.rowcount != 1:
                raise ManagedInputConflict(
                    "managed-input recovery decision raced with another owner"
                )
        result = self._get(execution, round_key)
        if result is None:
            raise ManagedInputConflict(
                "managed-input round disappeared after recovery failure"
            )
        return result

    def redacted_responses(
        self, execution_id: str, round_id: str
    ) -> Mapping[str, object] | None:
        execution = _require_text(execution_id, "execution_id", required=True)
        round_key = _require_text(round_id, "round_id", required=True)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT responses_redacted_json FROM m3_managed_input_diagnostics WHERE execution_id=? AND round_id=?",
                (execution, round_key),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(str(row[0]))
            if not isinstance(value, Mapping):
                raise ValueError("redacted response JSON must be an object")
            return cast(Mapping[str, object], value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ManagedInputValidationError(
                "stored redacted response projection is invalid"
            ) from exc


__all__ = [
    "ManagedInputLease",
    "ManagedInputRecord",
    "ManagedInputStatus",
    "ManagedInputStore",
    "SQLiteManagedInputStore",
]
