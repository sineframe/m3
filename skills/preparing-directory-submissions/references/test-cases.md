# Review test cases

Each directory asks for example prompts or test cases:

- **OpenAI**: exactly five positive and three negative cases
  (`openai-review-information`, `openai-test-case-count`). Each positive case
  has a scenario, a prompt, the expected tools and the expected result. Each
  negative case has a prompt, why the plugin shouldn't act, and the expected
  refusal, clarification or safe fallback.
- **Claude**: the primary use cases, what users need before they connect, and
  whether the connector reads, writes or both (`claude-listing-fields`), plus
  at least three working example prompts (`claude-policy-3E`).
- **Muse**: the intended users, supported tasks, how the connector improves
  on the browser, and restrictions (`muse-5.1-overview`). The page doesn't ask
  for prompts by name; give the supported tasks with an example request each.

## Draft the cases

1. Read the tool descriptions, the README or docs, and any existing
   `review.test_cases` in a plugin manifest. Draft from what the server is
   documented to do, not from its current output.
2. Write prompts the way a user would ask. Don't name tools, parameters or
   ids the user wouldn't know ("What did we decide in the launch kickoff?",
   not "Call search_notes with query=kickoff").
3. For OpenAI, make the five positive cases cover five different tools where
   the server has that many; with fewer tools, cover each tool once and vary
   the inputs. Make the three negative cases near-misses: requests that sound
   like the server's domain but that it shouldn't handle (an operation it
   doesn't offer, data outside the connected account, an unsupported use
   case).
4. Leave out tools that failed a use-case check (E). Don't write a positive
   case for a transfer tool you just reported as unsupported.
5. Each expected result says what the user sees, using values from the docs
   or the test account's sample data ("Lists INV-1002 as overdue").

## Write an M3 direct test for each positive case

For each positive case, write one M3 direct test that calls the expected tool
with the expected arguments, then asserts that the call succeeded and
returned real data: `is_error` is false, and the structured result contains
the documented values from the test account or sample data. Never copy the
tool's current output into the assertion. If the project has the
testing-with-m3 skill, follow it; its references document the M3 API for the
installed release. Otherwise read https://m3.sineframe.com/docs.

Put the tests in one file, for example
`tests/directory_readiness/test_review_cases.py`. If the project has no
`m3.toml`, run `m3 init --project-name NAME --suite directory-readiness`, then
`m3 setup` and `m3 doctor`, and tell the user which files that added.

A stdio server:

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


def test_positive_1_kickoff_decisions() -> None:
    """Positive case 1: the kickoff note is found by topic."""
    with MCPTestKit(env={}) as kit, kit.direct(SERVER) as client:
        result = client.call_tool("search_notes", {"query": "kickoff"})

    assert result.is_error is False
    titles = [note["title"] for note in result.structured_content["notes"]]
    assert "Launch kickoff" in titles
```

A running server at a URL uses `HTTPServer` from `m3.types` in place of
`StdioServer`, with `url=` and, for a loopback URL,
`trust=TrustLevel.TRUSTED_PRIVATE`. For a server that needs sign-in, the test
reads the test account's token from an environment variable the user sets;
never write a credential into the test.

## Which tests may run

Run a test only when the tool it calls is a **read** in your own
classification (check B), not because its readOnlyHint says so. A tool that
claims to be read-only but sends email is a sensitive write.

- Read tools: run the tests.
- Write and sensitive-write tools: write the test, mark it
  `@pytest.mark.skip(reason="calls a write tool; needs a confirmed test account")`,
  and run it only after the user confirms, in this conversation, that the
  server points at a dedicated test account. Ask once, listing the tools
  the run would call and what each would change.
- A tool you reported under E (money movement, trades, media generation):
  never run it, even with a test account.

Run with `m3 test -- tests/directory_readiness/test_review_cases.py`, then
confirm each test passed and recorded an execution, as testing-with-m3
describes. Report every outcome in the report as source `test`: `pass` for a
passing test with an execution, `fail` with the assertion message, and
`not checked` with the skip reason for a skipped one.

## Form layouts

Give the cases in the report as text the user can paste.

### OpenAI: review information

Also accepted in the plugin manifest under
`extensions.com.openai.review.test_cases` (`positive` and `negative` arrays
with `description`, `prompt`, `tools_triggered` and `expected_behavior`).

```
Positive test case 1
  Scenario: Find notes by topic
  User prompt: What did we decide in the launch kickoff?
  Expected tools: search_notes, read_note
  Expected result: Finds the "Launch kickoff" note and lists its decisions (ship the beta on November 12).

Negative test case 1
  Scenario: Request outside the plugin's scope
  User prompt: Email the kickoff notes to the whole team.
  Why it should not act: Fieldnotes can't send email.
  Expected response: Says it can't send email and offers to show the note.
```

### Claude: use cases step

```
Primary use cases:
  1. Find notes by topic. Example prompt: "What did we decide in the launch kickoff?"
  2. …
What users need before connecting: a Fieldnotes account.
Reads data, writes data, or both: both (search and read; create, replace and delete notes).
```

### Muse: connector overview

```
Intended users: …
Supported tasks:
  - Read: find notes by topic ("Find my notes about field teams.")
  - Write: create a note (…)
  - Sensitive write: delete a note (…)
How it improves on the browser: …
Account, plan or regional restrictions: …
```
