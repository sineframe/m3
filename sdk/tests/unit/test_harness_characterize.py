from __future__ import annotations

import stat
from pathlib import Path

from m3.harness.characterize import MAX_PROBE_OUTPUT, _run_probe


def _executable(path: Path, body: str) -> str:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_probe_retains_capabilities_after_legacy_short_prefix(tmp_path: Path) -> None:
    executable = _executable(
        tmp_path / "help-fixture.py",
        "print('x' * 700 + ' --input-format stream-json --output-format stream-json')\n",
    )

    output = _run_probe(executable, ("--help",), {"PATH": "/usr/bin"})

    assert len(output) <= MAX_PROBE_OUTPUT
    assert "stream-json" in output
