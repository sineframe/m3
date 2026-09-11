"""Application profile service used by API and viewer composition."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from mcp_pal import RevisionSelection
from mcp_pal.domain.validation import ProfileValidationError, validate_mcp_config
from mcp_pal.harness.manifest import (
    ManifestValidationError,
    export_manifest,
    validate_manifest,
)
from mcp_pal.storage import StorageConflict
from mcp_pal_app.builtin_profiles import (
    EXCALIDRAW_MCP_CONFIG,
    EXCALIDRAW_PROFILE_ID,
    EXCALIDRAW_REVISION_ID,
)

if _TYPE_CHECKING:
    from mcp_pal.storage import ProfileRecord, ProfileRevisionRecord


class ProfileServiceError(ValueError):
    """Safe, transport-neutral profile service error."""


class BuiltinProfileError(ProfileServiceError):
    """The reserved built-in profile ID is occupied by another profile."""


class ProfileStore(Protocol):
    def create_profile(
        self,
        kind: str,
        name: str,
        value: Mapping[str, Any],
        *,
        description: str = "",
        profile_id: str | None = None,
        revision_id: str | None = None,
    ) -> ProfileRecord: ...
    def list_profiles(
        self, kind: str, *, include_archived: bool = False
    ) -> tuple[ProfileRecord, ...]: ...
    def get_profile(self, profile_id: str) -> ProfileRecord | None: ...
    def list_profile_revisions(
        self, profile_id: str
    ) -> tuple[ProfileRevisionRecord, ...]: ...
    def add_revision(
        self,
        profile_id: str,
        value: Mapping[str, Any],
        *,
        revision_id: str | None = None,
    ) -> ProfileRevisionRecord: ...
    def resolve_revision(
        self, profile_id: str, selection: RevisionSelection | str = "latest"
    ) -> ProfileRevisionRecord: ...
    def update_profile(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> ProfileRecord: ...
    def archive_profile(self, profile_id: str) -> ProfileRecord: ...
    def restore_profile(self, profile_id: str) -> ProfileRecord: ...


@dataclass(frozen=True, slots=True)
class ProfileView:
    record: ProfileRecord
    revisions: tuple[ProfileRevisionRecord, ...]

    @property
    def current_revision(self) -> ProfileRevisionRecord | None:
        if self.record.current_revision_id is None:
            return None
        return next(
            (
                item
                for item in self.revisions
                if item.id == self.record.current_revision_id
            ),
            None,
        )


class MCPProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4096)
    config: dict[str, Any]

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("profile name must not be blank")
        return value.strip()

    @field_validator("config")
    @classmethod
    def valid_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_mcp_config(value)
        return value


class HarnessProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4096)
    manifest: dict[str, Any]
    trusted_unsandboxed: bool = False

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("profile name must not be blank")
        return value.strip()

    @field_validator("manifest")
    @classmethod
    def valid_manifest(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_manifest(value).get("manifest")
        if not isinstance(validated, dict):
            raise ValueError("manifest validation returned an invalid manifest")
        return {str(key): item for key, item in validated.items()}


def _view(store: ProfileStore, record: ProfileRecord) -> ProfileView:
    return ProfileView(record, store.list_profile_revisions(record.id))


class ProfileService:
    """Profile lifecycle operations backed by the SDK v2 profile tables."""

    def __init__(self, store: ProfileStore) -> None:
        self.store = store

    def ensure_builtins(self) -> None:
        """Seed the v2 built-ins through the public SDK profile store."""
        existing = self.store.get_profile(EXCALIDRAW_PROFILE_ID)
        if existing is not None:
            if existing.kind != "server" or existing.name != "Excalidraw":
                raise BuiltinProfileError("reserved Excalidraw profile ID is occupied")
            try:
                revision = self.store.resolve_revision(EXCALIDRAW_PROFILE_ID)
            except StorageConflict as exc:
                raise BuiltinProfileError(
                    "reserved Excalidraw profile is malformed"
                ) from exc
            if revision.value != EXCALIDRAW_MCP_CONFIG:
                raise BuiltinProfileError("reserved Excalidraw profile is malformed")
            return
        try:
            self.store.create_profile(
                "server",
                "Excalidraw",
                EXCALIDRAW_MCP_CONFIG,
                description="Built-in Excalidraw MCP server over HTTP.",
                profile_id=EXCALIDRAW_PROFILE_ID,
                revision_id=EXCALIDRAW_REVISION_ID,
            )
        except StorageConflict:
            # Another application instance may win the idempotent race.
            existing = self.store.get_profile(EXCALIDRAW_PROFILE_ID)
            if existing is None:
                raise ProfileServiceError(
                    "built-in profile could not be seeded"
                ) from None
            self.ensure_builtins()

    def _get(self, profile_id: str, kind: str) -> ProfileView:
        record = self.store.get_profile(profile_id)
        if record is None or record.kind != kind:
            raise ProfileServiceError(f"{kind} profile does not exist")
        return _view(self.store, record)

    @staticmethod
    def _metadata(
        name: str | None, description: str | None
    ) -> tuple[str | None, str | None]:
        if name is not None and not name.strip():
            raise ProfileServiceError("profile name must not be blank")
        if name is not None and len(name.strip()) > 200:
            raise ProfileServiceError("profile name is too long")
        if description is not None and len(description.strip()) > 4096:
            raise ProfileServiceError("profile description is too long")
        return (
            name.strip() if name is not None else None,
            description.strip() if description is not None else None,
        )

    def create_mcp(self, value: MCPProfileInput) -> ProfileView:
        try:
            record = self.store.create_profile(
                "server", value.name, value.config, description=value.description
            )
        except StorageConflict as exc:
            raise ProfileServiceError("MCP profile already exists") from exc
        return _view(self.store, record)

    def list_mcp(self, *, include_archived: bool = False) -> tuple[ProfileView, ...]:
        return tuple(
            _view(self.store, item)
            for item in self.store.list_profiles(
                "server", include_archived=include_archived
            )
        )

    def get_mcp(self, profile_id: str) -> ProfileView:
        return self._get(profile_id, "server")

    def update_mcp(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> ProfileView:
        name, description = self._metadata(name, description)
        try:
            return _view(
                self.store,
                self.store.update_profile(
                    profile_id, name=name, description=description
                ),
            )
        except StorageConflict as exc:
            raise ProfileServiceError(
                "MCP profile metadata could not be updated"
            ) from exc

    def add_mcp_revision(
        self, profile_id: str, config: Mapping[str, Any]
    ) -> ProfileView:
        try:
            validate_mcp_config(config)
            self.store.add_revision(profile_id, config)
        except ProfileValidationError:
            raise
        except StorageConflict as exc:
            raise ProfileServiceError("MCP profile is missing or archived") from exc
        return self.get_mcp(profile_id)

    def archive_mcp(self, profile_id: str) -> ProfileView:
        try:
            return _view(self.store, self.store.archive_profile(profile_id))
        except StorageConflict as exc:
            raise ProfileServiceError("MCP profile does not exist") from exc

    def restore_mcp(self, profile_id: str) -> ProfileView:
        try:
            return _view(self.store, self.store.restore_profile(profile_id))
        except StorageConflict as exc:
            raise ProfileServiceError("MCP profile does not exist") from exc

    def create_harness(self, value: HarnessProfileInput) -> ProfileView:
        if not value.trusted_unsandboxed:
            raise ProfileServiceError("trusted unsandboxed acknowledgment is required")
        manifest = validate_manifest(value.manifest)["manifest"]
        # Trust is an explicit acknowledgement on every revision, never an
        # inferred property of the executable or the importing caller.
        stored = {
            "manifest": manifest,
            "trusted_unsandboxed": value.trusted_unsandboxed,
        }
        try:
            record = self.store.create_profile(
                "harness", value.name, stored, description=value.description
            )
        except StorageConflict as exc:
            raise ProfileServiceError("harness profile already exists") from exc
        return _view(self.store, record)

    def list_harness(
        self, *, include_archived: bool = False
    ) -> tuple[ProfileView, ...]:
        return tuple(
            _view(self.store, item)
            for item in self.store.list_profiles(
                "harness", include_archived=include_archived
            )
        )

    def get_harness(self, profile_id: str) -> ProfileView:
        return self._get(profile_id, "harness")

    def update_harness(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> ProfileView:
        name, description = self._metadata(name, description)
        try:
            return _view(
                self.store,
                self.store.update_profile(
                    profile_id, name=name, description=description
                ),
            )
        except StorageConflict as exc:
            raise ProfileServiceError(
                "harness profile metadata could not be updated"
            ) from exc

    def add_harness_revision(
        self,
        profile_id: str,
        value: HarnessProfileInput | Mapping[str, Any],
        *,
        trusted_unsandboxed: bool | None = None,
    ) -> ProfileView:
        if isinstance(value, HarnessProfileInput):
            manifest = value.manifest
            acknowledged = (
                value.trusted_unsandboxed
                if trusted_unsandboxed is None
                else trusted_unsandboxed
            )
        else:
            manifest = dict(value)
            acknowledged = bool(trusted_unsandboxed)
        if not acknowledged:
            raise ProfileServiceError("trusted unsandboxed acknowledgment is required")
        try:
            normalized = validate_manifest(manifest)["manifest"]
            self.store.add_revision(
                profile_id, {"manifest": normalized, "trusted_unsandboxed": True}
            )
        except ManifestValidationError:
            raise
        except StorageConflict as exc:
            raise ProfileServiceError("harness profile is missing or archived") from exc
        return self.get_harness(profile_id)

    def archive_harness(self, profile_id: str) -> ProfileView:
        try:
            return _view(self.store, self.store.archive_profile(profile_id))
        except StorageConflict as exc:
            raise ProfileServiceError("harness profile does not exist") from exc

    def restore_harness(self, profile_id: str) -> ProfileView:
        try:
            return _view(self.store, self.store.restore_profile(profile_id))
        except StorageConflict as exc:
            raise ProfileServiceError("harness profile does not exist") from exc

    def import_harness(self, value: Mapping[str, Any]) -> ProfileView:
        raw = (
            value.get("manifest")
            if isinstance(value.get("manifest"), Mapping)
            else value
        )
        if not isinstance(raw, Mapping):
            raise ProfileServiceError("import must contain a harness manifest")
        name = (
            str(value.get("name", "Imported harness"))
            if "manifest" in value
            else "Imported harness"
        )
        description = str(value.get("description", "")) if "manifest" in value else ""
        # Imports are always untrusted, even if a stale acknowledgement is in
        # the file.  Validation happens before durable storage.
        manifest = validate_manifest(dict(raw))["manifest"]
        try:
            record = self.store.create_profile(
                "harness",
                name,
                {"manifest": manifest, "trusted_unsandboxed": False},
                description=description,
            )
        except StorageConflict as exc:
            raise ProfileServiceError("harness profile already exists") from exc
        return _view(self.store, record)

    def export_harness(self, profile_id: str) -> str:
        view = self.get_harness(profile_id)
        revision = view.current_revision
        if revision is None:
            raise ProfileServiceError("harness profile has no revision")
        manifest = (
            revision.value.get("manifest")
            if isinstance(revision.value, Mapping)
            else None
        )
        if not isinstance(manifest, Mapping):
            raise ProfileServiceError("harness profile revision is malformed")
        return (
            json.dumps(
                {
                    "name": view.record.name,
                    "description": view.record.description,
                    "manifest": json.loads(export_manifest(manifest)),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )


__all__ = [
    "BuiltinProfileError",
    "HarnessProfileInput",
    "MCPProfileInput",
    "ProfileService",
    "ProfileServiceError",
    "ProfileStore",
    "ProfileView",
]
