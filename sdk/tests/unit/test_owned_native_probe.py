from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from m3._bootstrap_operations import OwnedBootstrapOperations


@pytest.mark.process_lifecycle
def test_owned_native_help_includes_stderr(tmp_path: Path) -> None:
    script = tmp_path / "stderr_help.py"
    script.write_text(
        'import sys\nprint("serve --hostname --port", file=sys.stderr)\n',
        encoding="utf-8",
    )
    operations = OwnedBootstrapOperations()
    try:
        result = operations.probe_native(
            sys.executable, (str(script),), deadline=time.monotonic() + 5
        )
        assert "serve --hostname --port" in json.loads(result.consume())["output"]
    finally:
        operations.close(deadline=time.monotonic() + 5)
    assert operations.cleanup_complete
