"""Fieldnotes MCP server: search, read and edit notes in a Fieldnotes workspace."""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import store

INSTRUCTIONS = (
    "Fieldnotes holds the user's own notes, grouped into notebooks. Note ids "
    "come from search_notes results; notebook ids come from list_notebooks."
)

READ = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, open_world_hint=False
)

mcp = MCPServer("fieldnotes", instructions=INSTRUCTIONS, version="1.2.0")


@mcp.tool(title="List notebooks", annotations=READ)
def list_notebooks() -> dict[str, Any]:
    """List the notebooks in the user's Fieldnotes workspace, with their ids and names."""
    return {"notebooks": store.notebooks()}


@mcp.tool(title="Search notes", annotations=READ)
def search_notes(
    query: str, notebook_id: str | None = None, limit: int = 10
) -> dict[str, Any]:
    """Search the user's notes for words in the title or body. Returns up to
    `limit` matches (1-50) with id, title and notebook id; use read_note for the
    full text. Pass notebook_id to search one notebook."""
    if not query.strip():
        raise ToolError("query must contain at least one word")
    if not 1 <= limit <= 50:
        raise ToolError("limit must be between 1 and 50")
    return {"notes": store.search(query, notebook_id, limit)}


@mcp.tool(title="Read note", annotations=READ)
def read_note(note_id: str) -> dict[str, Any]:
    """Return one note's title, notebook id and full text."""
    row = store.note(note_id)
    if row is None:
        raise ToolError(f"no note with id {note_id!r}; find ids with search_notes")
    return {"note": row}


@mcp.tool(
    title="Create note",
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, open_world_hint=False
    ),
)
def create_note(notebook_id: str, title: str, body: str) -> dict[str, Any]:
    """Create a new note in one of the user's notebooks. Adds a note and changes
    nothing else; returns the new note with its id."""
    if notebook_id not in {nb["id"] for nb in store.notebooks()}:
        raise ToolError(f"no notebook with id {notebook_id!r}; see list_notebooks")
    return {"note": store.create(notebook_id, title, body)}


@mcp.tool(
    title="Replace note text",
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=True, open_world_hint=False
    ),
)
def replace_note_text(note_id: str, body: str) -> dict[str, Any]:
    """Replace a note's full text with `body`. The previous text is overwritten
    and can't be recovered."""
    row = store.replace_body(note_id, body)
    if row is None:
        raise ToolError(f"no note with id {note_id!r}; find ids with search_notes")
    return {"note": row}


@mcp.tool(
    title="Delete note",
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=True, open_world_hint=False
    ),
)
def delete_note(note_id: str) -> dict[str, Any]:
    """Permanently delete one note. This can't be undone."""
    if not store.delete(note_id):
        raise ToolError(f"no note with id {note_id!r}; find ids with search_notes")
    return {"deleted": note_id}


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
