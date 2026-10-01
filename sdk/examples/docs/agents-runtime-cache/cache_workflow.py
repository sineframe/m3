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
