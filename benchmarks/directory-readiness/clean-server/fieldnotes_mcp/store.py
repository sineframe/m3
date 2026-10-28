"""In-memory Fieldnotes workspace used by the example server.

Writes change this process's copy only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA = json.loads((Path(__file__).parent / "seed.json").read_text(encoding="utf-8"))


def notebooks() -> list[dict[str, Any]]:
    return [{"id": nb["id"], "name": nb["name"]} for nb in DATA["notebooks"]]


def note(note_id: str) -> dict[str, Any] | None:
    return next((row for row in DATA["notes"] if row["id"] == note_id), None)


def search(query: str, notebook_id: str | None, limit: int) -> list[dict[str, Any]]:
    words = query.lower().split()
    hits = [
        row
        for row in DATA["notes"]
        if (notebook_id is None or row["notebook_id"] == notebook_id)
        and all(word in (row["title"] + " " + row["body"]).lower() for word in words)
    ]
    return [
        {"id": row["id"], "title": row["title"], "notebook_id": row["notebook_id"]}
        for row in hits[:limit]
    ]


def create(notebook_id: str, title: str, body: str) -> dict[str, Any]:
    highest = max(
        (int(row["id"].removeprefix("note_")) for row in DATA["notes"]), default=0
    )
    row = {
        "id": f"note_{highest + 1:03d}",
        "notebook_id": notebook_id,
        "title": title,
        "body": body,
    }
    DATA["notes"].append(row)
    return row


def replace_body(note_id: str, body: str) -> dict[str, Any] | None:
    row = note(note_id)
    if row is not None:
        row["body"] = body
    return row


def delete(note_id: str) -> bool:
    before = len(DATA["notes"])
    DATA["notes"] = [row for row in DATA["notes"] if row["id"] != note_id]
    return len(DATA["notes"]) < before
