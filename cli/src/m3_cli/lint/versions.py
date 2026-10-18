"""Harness versions and the half-open version ranges rules apply to."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from . import LintUsageError

HarnessKind = Literal["claude_code", "codex"]

# The newest version of each harness that the rule data was checked against.
KNOWN_LATEST: dict[HarnessKind, str] = {"claude_code": "2.1.302", "codex": "0.163.0"}

_VERSION = re.compile(r"\d+\.\d+\.\d+", re.ASCII)
_KIND_ALIASES: dict[str, HarnessKind] = {
    "claude_code": "claude_code",
    "claude": "claude_code",
    "codex": "codex",
}


def parse_version(text: str) -> tuple[int, int, int]:
    value = text[1:] if text.startswith("v") else text
    if _VERSION.fullmatch(value) is None:
        raise LintUsageError("invalid harness version in --harness")
    major, minor, patch = (int(part) for part in value.split("."))
    return major, minor, patch


@dataclass(frozen=True)
class VersionRange:
    """Versions from ``introduced`` (inclusive) up to ``fixed`` (exclusive)."""

    introduced: str | None
    fixed: str | None

    def contains(self, version: str) -> bool:
        current = parse_version(version)
        if self.introduced is not None and current < parse_version(self.introduced):
            return False
        return self.fixed is None or current < parse_version(self.fixed)

    def label(self, *, unicode: bool = True) -> str:
        if self.introduced is None and self.fixed is None:
            return "all versions"
        at_least, through = ("≥", "–") if unicode else (">=", "-")  # noqa: RUF001
        if self.fixed is None:
            return f"{at_least}{self.introduced}"
        if self.introduced is None:
            return f"<{self.fixed}"
        return f"{self.introduced}{through}<{self.fixed}"


@dataclass(frozen=True)
class Target:
    harness: HarnessKind
    version: str
    beyond_known: bool


def _target(kind: HarnessKind, version: str) -> Target:
    beyond = parse_version(version) > parse_version(KNOWN_LATEST[kind])
    return Target(kind, version, beyond)


def parse_targets(values: list[str]) -> list[Target]:
    if not values:
        return [_target(kind, version) for kind, version in KNOWN_LATEST.items()]
    chosen: dict[HarnessKind, str] = {}
    for value in values:
        raw_kind, separator, version = value.partition("@")
        kind = _KIND_ALIASES.get(raw_kind.strip().lower().replace("-", "_"))
        if kind is None:
            raise LintUsageError("m3 lint supports claude_code and codex")
        if not separator:
            version = KNOWN_LATEST[kind]
        parse_version(version)
        chosen.pop(kind, None)
        chosen[kind] = version.removeprefix("v")
    return [_target(kind, version) for kind, version in chosen.items()]
