#!/usr/bin/env python3
"""Validate M3 skills as self-contained packages for the skills CLI."""

from __future__ import annotations

import re
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER = re.compile(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n")
FIELD = re.compile(r"^([a-z][a-z0-9_-]*):\s*(.+)$", re.IGNORECASE)
LINK = re.compile(r"!?\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)")


def parse_frontmatter(source: str, label: str, errors: list[str]) -> dict[str, str]:
    match = FRONTMATTER.match(source)
    if not match:
        errors.append(f"{label}: missing YAML frontmatter")
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        field = FIELD.match(line)
        if field:
            values[field.group(1)] = field.group(2).strip().strip("'\"")
    return values


def validate_skill(skill: Path, errors: list[str]) -> None:
    relative = skill.relative_to(ROOT).as_posix()
    source_path = skill / "SKILL.md"
    source = source_path.read_text(encoding="utf-8")
    fields = parse_frontmatter(source, f"{relative}/SKILL.md", errors)
    if fields.get("name") != skill.name:
        errors.append(f"{relative}/SKILL.md: name must match directory {skill.name!r}")
    if not fields.get("description"):
        errors.append(f"{relative}/SKILL.md: description must be non-empty")
    if not NAME.fullmatch(skill.name):
        errors.append(f"{relative}: directory name is not a valid skill name")

    for path in skill.rglob("*"):
        if path.is_symlink():
            errors.append(f"{path.relative_to(ROOT)}: symlinks are not publishable")
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        body = path.read_text(encoding="utf-8")
        for match in LINK.finditer(body):
            destination = match.group(1).split("#", 1)[0]
            if (
                not destination
                or urlsplit(destination).scheme
                or destination.startswith("/")
            ):
                continue
            parts = PurePosixPath(path.relative_to(skill).parent, destination).parts
            normalized: list[str] = []
            escaped = False
            for part in parts:
                if part in {"", "."}:
                    continue
                if part == "..":
                    if not normalized:
                        escaped = True
                        break
                    normalized.pop()
                else:
                    normalized.append(part)
            label = path.relative_to(ROOT).as_posix()
            if escaped:
                errors.append(
                    f"{label}: relative link escapes the installed skill: {destination}"
                )
                continue
            target = skill.joinpath(*normalized)
            if not target.is_file():
                errors.append(
                    f"{label}: relative link target is missing: {destination}"
                )


def main() -> int:
    errors: list[str] = []
    skills = sorted(path.parent for path in SKILLS.glob("*/SKILL.md"))
    if not skills:
        errors.append("skills/: no installable */SKILL.md packages found")
    for skill in skills:
        validate_skill(skill, errors)
    if errors:
        print("Skill validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Validated {len(skills)} installable skill package(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
