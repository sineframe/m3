# MCP Pal CLI runner

The project SDK and standalone CLI have different installation scopes:

| Component | Install scope | Provides |
|---|---|---|
| `mcp-pal[pytest]` | Project environment | Python SDK and pytest support |
| MCP Pal CLI | Machine-level isolated environment | `mcp-pal` command and bundled UI |

Adding the SDK with `uv add` or `pip install` does not install the CLI. The CLI
project is distributed as `mcp-pal-cli`, but install it from an exact release
with the release installer rather than adding it to the tested project.

## Install the CLI

The repository is private. Check GitHub CLI authentication first:

```bash
gh auth status
```

If authentication is missing, ask the user to complete `gh auth login`; do not
attempt to automate an interactive login. Determine the exact release tag from
the user, the project's required SDK version, or the repository's releases.
Do not guess a version, and do not silently replace an existing CLI with a
different release.

```bash
gh release list --repo rishhavv/mcp-pal --limit 10
```

For a project that already pins MCP Pal, select the matching release. For a new
setup without a pinned version, use the most recent intended release from this
list; releases are currently prereleases, so do not rely on GitHub's implicit
`latest` release selection.

On macOS or Linux, replace `X.Y.Z` with that exact version:

```bash
gh release download vX.Y.Z --repo rishhavv/mcp-pal \
  --pattern install.sh --output install.sh
sh install.sh
rm install.sh
```

On Windows PowerShell:

```powershell
gh release download vX.Y.Z --repo rishhavv/mcp-pal `
  --pattern install.ps1 --output install.ps1
.\install.ps1
Remove-Item install.ps1
```

The installer keeps the CLI and bundled UI outside the project environment. It
prefers `uv tool install` and otherwise creates a dedicated virtual
environment.

## Prepare a project

From the project root, run:

```bash
mcp-pal setup
mcp-pal doctor
```

`mcp-pal setup` installs the exact matching `mcp-pal[pytest,storage]` SDK into
the selected project environment. It does not install the CLI there and does
not edit the project's dependency manifest or lockfile. The CLI and project
SDK versions must match; do not work around a mismatch by bypassing `doctor`.

Once the project is ready, run a narrow test with normal pytest feedback:

```bash
mcp-pal test -- tests/test_shipping.py
```

Use `mcp-pal test --ui -- tests/test_shipping.py` only when the user wants the
local viewer. It stays open after pytest finishes until interrupted. Everything
after `--` is forwarded to pytest. Direct pytest remains valid when the
standalone CLI is not needed:

```bash
uv run pytest tests/test_shipping.py
```
