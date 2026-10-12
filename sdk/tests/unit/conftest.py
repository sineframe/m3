from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from m3 import _bootstrap_operations as operations
from m3._bootstrap_operations import OwnedBootstrapOperations, PrivateBytes
from m3._preparation_owner import PreparationWorker

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def blocked_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from m3 import _preparation_owner as owners

    arm, marker, fifo, late = (
        tmp_path / name for name in ("arm", "marker", "fifo", "late")
    )
    os.mkfifo(fifo, 0o600)
    original_popen = owners.subprocess.Popen
    workers: list[PreparationWorker] = []

    def create(mode: str = "write") -> tuple[PreparationWorker, Path, Path, Path, Path]:
        def launch(argv: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
            if argv[1:3] == ["-m", "m3._preparation_worker"]:
                argv = [
                    argv[0],
                    str(FIXTURES / "owned_bootstrap_helper.py"),
                    mode,
                    str(arm),
                    str(marker),
                    str(fifo),
                    str(late),
                    *argv[3:],
                ]
            return original_popen(argv, **kwargs)

        monkeypatch.setattr(owners.subprocess, "Popen", launch)
        worker = PreparationWorker(64)
        workers.append(worker)
        monkeypatch.setattr(operations, "worker_preparation", lambda: worker)
        warm = OwnedBootstrapOperations()
        warm.create_capsule(PrivateBytes(b"warm"), deadline=time.monotonic() + 5)
        warm.close(deadline=time.monotonic() + 2)
        assert warm.cleanup_complete
        arm.touch()
        return worker, arm, marker, fifo, late

    yield create
    arm.unlink(missing_ok=True)
    for worker in workers:
        worker.close(3)
