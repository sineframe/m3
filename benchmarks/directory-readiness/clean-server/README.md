# Fieldnotes MCP

Search, read and edit the notes in your Fieldnotes workspace from an AI
assistant.

## Run locally

```sh
python -m fieldnotes_mcp.server
```

The server speaks MCP over stdio. It needs no sign-in: it serves the local
workspace in `fieldnotes_mcp/seed.json`.

## Launch status

Not launched yet. The hosted server (`https://mcp.fieldnotes.example/mcp`),
these docs (`https://fieldnotes.example/docs/mcp`) and the privacy policy
(`https://fieldnotes.example/privacy`, from [PRIVACY.md](PRIVACY.md)) go live
together on launch day, before the directory submissions. Until then, review
the files in this repository.

## Tools

| Tool | Class | Inputs | Output | Side effects |
|---|---|---|---|---|
| `list_notebooks` | Read | none | notebooks: `id`, `name` | none |
| `search_notes` | Read | `query` (required), `notebook_id`, `limit` (1-50, default 10) | notes: `id`, `title`, `notebook_id` | none |
| `read_note` | Read | `note_id` | note: `id`, `title`, `notebook_id`, `body` | none |
| `create_note` | Write | `notebook_id`, `title`, `body` | the new note | adds one note |
| `replace_note_text` | Sensitive write | `note_id`, `body` | the updated note | overwrites the note's text; not recoverable |
| `delete_note` | Sensitive write | `note_id` | `deleted`: the note id | deletes the note; not recoverable |

No tool sends, shares or publishes anything, and no tool contacts another
service.

## Permissions

The server reads and changes only the notes in the connected workspace. It
asks for no account permissions: the local server has no sign-in, and the
hosted server will offer a read-only option that allows only
`list_notebooks`, `search_notes` and `read_note`.

## Status handling

Every tool finishes its change before it returns, so a result always reports
a completed action: `create_note` returns the stored note with its id,
`replace_note_text` the note's new text, and `delete_note` the deleted id.
Nothing is queued or pending. A tool error means nothing changed; retrying
`create_note` after an error doesn't create a duplicate.

## Errors

Tools return an MCP tool error with a message that says what to fix:

- `query must contain at least one word`
- `limit must be between 1 and 50`
- `no note with id '…'; find ids with search_notes`
- `no notebook with id '…'; see list_notebooks`

## Rate limits

The local server has no rate limit. The hosted Fieldnotes API allows 60
requests per minute per account and returns a tool error naming the limit when
it is reached.

## Example prompts

- "What did we decide in the launch kickoff?"
- "Find my notes about field teams."
- "Which notebooks do I have?"
- "Add a note to Launch called 'Press list' with the three outlets we picked."
- "Replace the text of my launch risks note with the updated mitigation."

## Privacy

See [PRIVACY.md](PRIVACY.md).
