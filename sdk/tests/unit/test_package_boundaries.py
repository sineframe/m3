import os
import subprocess
import sys
from pathlib import Path


def test_sdk_does_not_expose_application_modules():
    import importlib.util

    for name in (
        "m3.api",
        "m3.ui",
        "m3.main",
        "m3.persistence",
        "m3.config",
    ):
        assert importlib.util.find_spec(name) is None


def test_sqlite_storage_does_not_import_application_modules(tmp_path):
    script = """
import sys
from m3.storage import SQLiteExecutionStore

database = sys.argv[1]
store = SQLiteExecutionStore(database)
store.close()
reopened = SQLiteExecutionStore(database)
reopened.close()
assert not any(name.startswith('m3_app') for name in sys.modules)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "independent.sqlite")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
