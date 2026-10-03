---
title: "Configuration"
description: "Project identity and environment-file loading behavior."
---

# Configuration

## Project identity

`m3.toml` stores project identity. Keep its generated project ID stable; renaming a project changes its name, not that identity.

## Environment files

The CLI automatically loads `.env` from the project root (`--project-root`, or the nearest `m3.toml` at or above the current directory, stopping at the Git root, otherwise the current directory) when that file exists. Pass `--env-file PATH` to load a custom file instead; a missing explicit file is an error. Ambient environment values take precedence, including empty values. File values are not interpolated. The SDK reads from the process environment or a resolver supplied by the application; it does not load dotenv files.

See the [credential reference](credentials.md) for credential destinations and their resolution behavior.
