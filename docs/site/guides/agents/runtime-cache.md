---
title: "Inspect and maintain the runtime cache"
description: "Acquire one pinned Codex runtime into a temporary cache, inspect its receipt, verify reuse and lease protection, then prune that cache."
---

# Inspect and maintain the runtime cache

Acquire a pinned native runtime into a dedicated cache, read its receipt,
acquire the same pin again, and prune the entry after releasing its leases.
The example uses a temporary cache beside an empty temporary project. It does
not touch your normal M3 cache.

## Requirements

Use Python 3.10 or newer and install `sf-m3`. The example uses the pinned
Codex runtime `0.156.1`. M3 fetches release metadata and the runtime asset
from GitHub. No Codex login, model, provider
credential, CLI installation, or test harness session is needed.

Install the SDK in the Python environment used to run the script:

```sh
python -m pip install sf-m3
```

The target platform must have a matching Codex `0.156.1` release asset. See the
[managed runtime reference](../../reference/managed-runtimes.md) for the
supported target lookup and the [pinned runtime guide](managed-runtimes.md)
for running an agent with a managed binary.

## Complete cache workflow

Create a directory named `runtime-cache` outside the M3 checkout and save the
complete file below as `cache_workflow.py`. The script uses the directory
containing that file as `project_root`. It creates a separate temporary cache,
checks that the cache root is outside the project, then uses the public
`m3.runtime` API to acquire and inspect the pin.

```python
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from m3.runtime import RuntimeManager, list_cache, prune_cache, resolve_cache_root

PIN = "0.156.1"


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def run_workflow() -> None:
    with tempfile.TemporaryDirectory(prefix="m3-runtime-cache-") as temporary:
        workspace = Path(temporary)
        project = Path(__file__).resolve().parent
        cache = workspace / "cache"
        events_path = workspace / "progress.jsonl"

        resolved_cache = resolve_cache_root(project, cache)
        assert resolved_cache == cache.resolve()

        manager = RuntimeManager(
            cache_root=cache,
            project_root=project,
            progress_file=events_path,
        )
        leases = []
        try:
            cold = await manager.acquire("codex", PIN)
            leases.append(cold)

            entries = list_cache(cache)
            assert len(entries) == 1
            receipt = entries[0]["provenance"]
            assert receipt["kind"] == "codex"
            assert receipt["version"] == PIN
            assert receipt["target"]
            assert len(receipt["sha256"]) == 64
            assert entries[0]["status"] == "ready"

            event_count_before_reuse = len(read_events(events_path))
            warm = await manager.acquire("codex", PIN)
            leases.append(warm)
            new_events = read_events(events_path)[event_count_before_reuse:]
            assert [event["event"] for event in new_events] == ["cache_hit"]

            removed_while_leased = prune_cache(cache)
            assert removed_while_leased == []
            assert cold.executable.is_file()
            assert warm.executable.is_file()
            assert len(list_cache(cache)) == 1

            for lease in leases:
                await manager.release(lease)
            leases.clear()

            removed_after_release = prune_cache(cache)
            assert len(removed_after_release) == 1
            assert list_cache(cache) == []

            print(
                f"Pinned receipt: {receipt['kind']} {receipt['version']} "
                f"target={receipt['target']} sha256={receipt['sha256']}"
            )
            print("Repeat acquisition: cache_hit=true; extra_download_events=0")
            print("Active lease: pruned=0; cache_entry_preserved=true")
            print("After release: pruned=1; entries_remaining=0")
        finally:
            for lease in leases:
                await manager.release(lease)
            await manager.close()


if __name__ == "__main__":
    asyncio.run(run_workflow())
```

From the parent of `runtime-cache`, run:

```sh
cd runtime-cache
python cache_workflow.py
```

Captured output on macOS arm64:

```text
Pinned receipt: codex 0.156.1 target=darwin-arm64-64 sha256=2bd64af14dedd47795f2f6bfd5d125cf79199acc2c7ba222144e08127111a5ca
Repeat acquisition: cache_hit=true; extra_download_events=0
Active lease: pruned=0; cache_entry_preserved=true
After release: pruned=1; entries_remaining=0
```

The first acquisition produces a ready receipt for the pin, with its target
and 64-character archive digest. Acquiring it again adds one `cache_hit` event
without downloading or verifying the archive again.

While either lease is active, pruning keeps the executable. Once both leases
are released, pruning removes the entry and the cache list is empty. Cleanup
removes the script's temporary cache and progress file; your project and
existing M3 cache remain in place.

Complete source project: [`sdk/examples/docs/agents-runtime-cache`](../../../../sdk/examples/docs/agents-runtime-cache).

## Configure another cache root

This example passes an explicit `cache_root` to `RuntimeManager`. The SDK
resolves that path ahead of `M3_HARNESS_CACHE_DIR`, then the operating-system
default. Native test kits accept `harness_cache_dir`; an explicit session path
overrides the kit path. The `m3 runtime cache list` and
`m3 runtime cache prune` commands accept `--cache-dir` or
`--harness-cache-dir` ahead of the environment variable. The `m3 test` command
accepts `--harness-cache-dir`. Every selected cache root must be outside the
project, `PATH`, and blocked system directories. Do not run prune against a
shared cache when another process may be using it. See
[cache location, leases, and pruning](../../reference/managed-runtimes.md#cache-location-and-precedence)
for all precedence and CLI behavior.

## Limits

If GitHub metadata or the pinned asset cannot be reached, acquisition fails
before a valid entry is listed. The SDK verifies the independent release
digest and executable version before it writes a ready receipt. Provider
authentication and agent execution require a separate harness session, and a
runtime cache does not sandbox filesystem or network access. Continue to
[run a pinned agent harness](managed-runtimes.md).
