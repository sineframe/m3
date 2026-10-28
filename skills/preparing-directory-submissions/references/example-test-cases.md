# Example: review test cases

Test cases drafted for a fictional notes server, Fieldnotes, with six tools:
`list_notebooks`, `search_notes` and `read_note` (reads), `create_note`
(additive write), `replace_note_text` and `delete_note` (sensitive writes).
The test account holds a "Launch" notebook with a "Launch kickoff" note
(decision: ship the beta on November 12; action items for Priya and Sam) and
a "Launch risks" note, and a "Research" notebook with "Interview notes: field
teams".

## OpenAI: review information

```
Positive test case 1
  Scenario: Find a note by topic and read its decisions
  User prompt: What did we decide in the launch kickoff?
  Expected tools: search_notes, read_note
  Expected result: Finds "Launch kickoff" and says the team decided to ship the beta on November 12.

Positive test case 2
  Scenario: List the workspace's notebooks
  User prompt: Which notebooks do I have in Fieldnotes?
  Expected tools: list_notebooks
  Expected result: Lists the Launch and Research notebooks.

Positive test case 3
  Scenario: Search one topic across notebooks
  User prompt: Find my notes about field teams.
  Expected tools: search_notes
  Expected result: Returns "Interview notes: field teams" from the Research notebook.

Positive test case 4
  Scenario: Create a note in a named notebook
  User prompt: Add a note to my Launch notebook called "Press list" saying we picked three outlets.
  Expected tools: list_notebooks, create_note
  Expected result: Creates "Press list" in Launch and returns the new note's id.

Positive test case 5
  Scenario: Replace a note's text after confirmation
  User prompt: Replace the text of my launch risks note with "Billing migration is on track; the flag stays as a fallback."
  Expected tools: search_notes, replace_note_text
  Expected result: Asks for confirmation, then replaces the text of "Launch risks" and shows the new text.

Negative test case 1
  Scenario: Sending is out of scope
  User prompt: Email the kickoff notes to the whole team.
  Why it should not act: Fieldnotes can't send or share notes.
  Expected response: Says it can't send email, and offers to show the note so the user can share it.

Negative test case 2
  Scenario: Another account's data
  User prompt: Show me the notes in my manager's Fieldnotes workspace.
  Why it should not act: The connector only reaches the workspace the user connected.
  Expected response: Explains it can only see the connected workspace.

Negative test case 3
  Scenario: Bulk deletion without a target
  User prompt: Clean up my workspace and delete anything old.
  Why it should not act: "Old" doesn't identify notes, and deletion can't be undone.
  Expected response: Asks which notes to delete, and deletes nothing until the user names them and confirms.
```

## Claude: use cases step

```
Primary use cases:
  1. Find notes by topic. Example prompt: "Find my notes about field teams."
  2. Answer questions from a note. Example prompt: "What did we decide in the launch kickoff?"
  3. Capture a new note. Example prompt: "Add a note to my Launch notebook called 'Press list' saying we picked three outlets."
What users need before connecting: a Fieldnotes account with at least one notebook.
Reads data, writes data, or both: both (list, search and read notes; create, replace and delete notes).
```

## Muse: connector overview

```
Intended users: teams who keep research and meeting notes in Fieldnotes.
Supported tasks:
  - Read: list notebooks ("Which notebooks do I have?")
  - Read: find notes by topic ("Find my notes about field teams.")
  - Read: read a note in full ("What did we decide in the launch kickoff?")
  - Write: create a note ("Add a note to Launch called 'Press list'.")
  - Sensitive write: replace a note's text; the old text can't be recovered ("Replace the text of my launch risks note with …")
  - Sensitive write: delete a note; can't be undone ("Delete my press list note.")
How it improves on the browser: reads and edits the user's private workspace through their account, so Muse answers from the notes themselves instead of opening pages one at a time.
Account, plan or regional restrictions: needs a Fieldnotes account; [fill in plan and regional limits].
```

## Direct tests

`tests/directory_readiness/test_review_cases.py`, one test per positive case.
Cases 1 to 3 call only read tools and run. Cases 4 and 5 call write tools, so
they are skipped until the user confirms a test account:

```python
import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, StdioServer

pytestmark = pytest.mark.m3(suite_name="directory-readiness")
ROOT = Path(__file__).parents[2]
SERVER = StdioServer(
    name="fieldnotes",
    command=sys.executable,
    args=("-m", "fieldnotes_mcp.server"),
    cwd=str(ROOT),
)
WRITE = pytest.mark.skip(reason="calls a write tool; needs a confirmed test account")


def test_positive_1_kickoff_decisions() -> None:
    """Positive case 1: the kickoff note is found and holds the beta date."""
    with MCPTestKit(env={}) as kit, kit.direct(SERVER) as client:
        found = client.call_tool("search_notes", {"query": "kickoff"})
        assert found.is_error is False
        ids = [note["id"] for note in found.structured_content["notes"]]
        assert ids, "search returned no notes"
        note = client.call_tool("read_note", {"note_id": ids[0]})

    assert note.is_error is False
    assert note.structured_content["note"]["title"] == "Launch kickoff"
    assert "November 12" in note.structured_content["note"]["body"]


def test_positive_2_notebooks() -> None:
    """Positive case 2: both notebooks are listed."""
    with MCPTestKit(env={}) as kit, kit.direct(SERVER) as client:
        result = client.call_tool("list_notebooks", {})

    assert result.is_error is False
    names = {notebook["name"] for notebook in result.structured_content["notebooks"]}
    assert names == {"Launch", "Research"}


def test_positive_3_field_teams() -> None:
    """Positive case 3: the field-team interview notes are found."""
    with MCPTestKit(env={}) as kit, kit.direct(SERVER) as client:
        result = client.call_tool("search_notes", {"query": "field teams"})

    assert result.is_error is False
    titles = [note["title"] for note in result.structured_content["notes"]]
    assert titles == ["Interview notes: field teams"]


@WRITE
def test_positive_4_create_note() -> None:
    """Positive case 4: a note is created in the Launch notebook."""
    with MCPTestKit(env={}) as kit, kit.direct(SERVER) as client:
        result = client.call_tool(
            "create_note",
            {"notebook_id": "nb_launch", "title": "Press list", "body": "Three outlets."},
        )

    assert result.is_error is False
    assert result.structured_content["note"]["notebook_id"] == "nb_launch"


@WRITE
def test_positive_5_replace_text() -> None:
    """Positive case 5: the launch risks note gets the new text."""
    body = "Billing migration is on track; the flag stays as a fallback."
    with MCPTestKit(env={}) as kit, kit.direct(SERVER) as client:
        result = client.call_tool("replace_note_text", {"note_id": "note_002", "body": body})

    assert result.is_error is False
    assert result.structured_content["note"]["body"] == body
```

Expected values come from the test account's documented sample data, not
from a previous run of the tools.
