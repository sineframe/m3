---
title: "Publish or retry a CI run"
description: "Publishing is explicit. Run locally without upload first, then add --upload with M3_ACCESS_TOKEN in the environment:"
---

# Publish or retry a CI run

Publishing is explicit. Run locally without upload first, then add `--upload`
with `M3_ACCESS_TOKEN` in the environment:

```sh
m3 ci test --upload -- tests/
```

The command prints the run ID created by that invocation. If upload fails, the
local run remains in the selected database. Copy that run ID and retry without
rerunning tests:

```sh
printf 'Run ID to retry: '
IFS= read -r RUN_ID
m3 upload "$RUN_ID"
```

PowerShell:

```powershell
$RUN_ID = Read-Host "Run ID to retry"
m3 upload $RUN_ID
```

Do not copy an ID from documentation; it must identify a run in your database.
If credentials came from a file, pass `--env-file` again. M3 refuses unsafe or
changed payloads according to its upload inspection rules.
