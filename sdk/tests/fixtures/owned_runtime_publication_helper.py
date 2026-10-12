"""Hold final acceptance IO in the actual fixed runtime/catalog guardian."""

from __future__ import annotations

import json
import os
import runpy
from pathlib import Path

fault = Path(os.environ["M3_TEST_RUNTIME_PUBLICATION"])
original_write = Path.write_text


def wait_gate(name: str) -> None:
    descriptor = os.open(fault / name, os.O_RDONLY)
    try:
        os.read(descriptor, 1)
    finally:
        os.close(descriptor)


def controlled_write(path: Path, text: str, *args: object, **kwargs: object) -> int:
    if path.name in {".m3-owned-generation.json", "opencode-models.json.generation"}:
        value = json.loads(text)
        if value.get("accepted") is True:
            original_write(
                fault / "entered", json.dumps({"pid": os.getpid(), "marker": str(path)})
            )
            wait_gate("write")
            count = original_write(path, text, *args, **kwargs)
            (fault / "late").touch()
            wait_gate("return")
            return count
    return original_write(path, text, *args, **kwargs)


Path.write_text = controlled_write
runpy.run_module("m3._preparation_worker", run_name="__main__")
