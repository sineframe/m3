"""Shared suite name normalization and lightweight registry records."""

from __future__ import annotations

from dataclasses import dataclass

from ._types.base import ProjectId, SuiteId


def normalize_suite_name(value: str) -> str:
    """Normalize a user supplied suite name for uniqueness and lookup."""
    if not isinstance(value, str):
        raise TypeError("suite_name must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("suite_name must not be empty")
    if len(normalized) > 256:
        raise ValueError("suite_name must be at most 256 characters")
    return normalized


def generated_suite_id(value: int) -> SuiteId:
    return SuiteId(value)


@dataclass(frozen=True)
class Suite:
    id: SuiteId
    name: str
    normalized_name: str
    project_id: ProjectId | None = None
