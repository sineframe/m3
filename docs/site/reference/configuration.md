---
title: "Configuration"
description: "Project identity and environment-file loading behavior."
---

# Configuration

## Project identity

`m3.toml` stores project identity. Keep its generated project ID stable; renaming a project changes its name, not that identity.

## Environment files

The CLI reads a dotenv file only when a command receives `--env-file PATH`. Ambient environment values take precedence, including empty values. File values are not interpolated, and M3 does not search for `.env` automatically. The SDK reads from the process environment or a resolver supplied by the application; it does not load dotenv files.

See the [credential reference](credentials.md) for credential destinations and their resolution behavior.
