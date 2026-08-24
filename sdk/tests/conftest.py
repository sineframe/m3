"""Keep legacy application defaults out of the repository during test runs."""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path


_test_state_dir = Path(tempfile.mkdtemp(prefix="mcp-pal-pytest-"))
_test_database = _test_state_dir / "mcp_pal.db"
os.environ["DATABASE_PATH"] = str(_test_database)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True


@atexit.register
def _remove_test_state() -> None:
    shutil.rmtree(_test_state_dir, ignore_errors=True)
