"""Small, allowlisted CI context included with published feedback."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from .errors import CLIError

FIELDS = frozenset(
    {
        "provider",
        "repository",
        "commit",
        "ref",
        "pr_number",
        "workflow",
        "job",
        "workflow_run_id",
        "attempt",
        "job_url",
    }
)


def resolve_ci_metadata(
    environment: Mapping[str, str], path: Path | None = None
) -> dict[str, str]:
    values: dict[str, str] = {}
    if environment.get("GITHUB_ACTIONS") == "true":
        names = {
            "repository": "GITHUB_REPOSITORY",
            "commit": "GITHUB_SHA",
            "ref": "GITHUB_REF",
            "workflow": "GITHUB_WORKFLOW",
            "job": "GITHUB_JOB",
            "workflow_run_id": "GITHUB_RUN_ID",
            "attempt": "GITHUB_RUN_ATTEMPT",
        }
        values["provider"] = "github"
        values.update(
            {
                target: environment[source]
                for target, source in names.items()
                if environment.get(source)
            }
        )
        if repository := environment.get("GITHUB_REPOSITORY"):
            if run_id := environment.get("GITHUB_RUN_ID"):
                values["job_url"] = (
                    f"https://github.com/{repository}/actions/runs/{run_id}"
                )
    if path is not None:
        try:
            overrides = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CLIError("CI metadata file is unreadable or invalid") from exc
        if not isinstance(overrides, dict) or set(overrides) - FIELDS:
            raise CLIError("CI metadata contains unsupported fields")
        for key, value in overrides.items():
            if not isinstance(value, (str, int)) or isinstance(value, bool):
                raise CLIError("CI metadata values must be strings or integers")
            values[key] = str(value)
    if any(not value or len(value) > 512 for value in values.values()):
        raise CLIError("CI metadata values must be nonempty and at most 512 characters")
    return values


__all__ = ["FIELDS", "resolve_ci_metadata"]
