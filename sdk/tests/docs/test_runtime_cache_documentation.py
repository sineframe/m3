"""Exercise the runtime-cache guide against the real runtime manager offline."""

from __future__ import annotations

import hashlib
import io
import json
import os
import runpy
import stat
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from m3.runtime.core import detect_target

_ROOT = Path(__file__).parents[3]
_PROJECT = _ROOT / "sdk" / "examples" / "docs" / "agents-runtime-cache"


def _copy_manifest_project(destination: Path) -> Path:
    manifest = json.loads((_PROJECT / "example.json").read_text(encoding="utf-8"))
    destination.mkdir()
    for relative_name in manifest["files"]:
        source = _PROJECT / relative_name
        target = destination / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    return destination


def _codex_archive() -> bytes:
    """Make an installable, inert executable archive for the selected target."""

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo("codex")
        info.external_attr = (stat.S_IFREG | 0o755) << 16
        archive.writestr(info, b"#!/bin/sh\nprintf 'codex-cli 0.156.1\\n'\n")
    return output.getvalue()


@pytest.mark.skipif(os.name == "nt", reason="fixture executable uses a POSIX shell")
def test_runtime_cache_workflow_uses_local_release_feed_and_real_manager(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    from m3.runtime import core

    runner_copy = os.environ.get("M3_DOCS_RUNTIME_CACHE_PROJECT")
    project = (
        Path(runner_copy)
        if runner_copy
        else _copy_manifest_project(tmp_path / "agents-runtime-cache")
    )
    asset = _codex_archive()
    digest = hashlib.sha256(asset).hexdigest()
    target = detect_target("codex")
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            if self.path == f"/manifest/{target}":
                body = json.dumps(
                    {
                        target: {
                            "version": "0.156.1",
                            "url": f"http://127.0.0.1:{self.server.server_port}/asset.zip",
                            "sha256": digest,
                            "executable": "codex",
                        }
                    }
                ).encode("utf-8")
                content_type = "application/json"
            elif self.path == "/asset.zip":
                body = asset
                content_type = "application/zip"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manifest_url = f"http://127.0.0.1:{server.server_port}/manifest/{target}"
    monkeypatch.setattr(
        core,
        "default_manifest_url",
        lambda kind, version: (
            manifest_url if kind == "codex" and version == "0.156.1" else None
        ),
    )
    try:
        runpy.run_path(str(project / "cache_workflow.py"), run_name="__main__")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    captured = capsys.readouterr()
    assert captured.err == ""
    assert (
        f"Pinned receipt: codex 0.156.1 target={target} sha256={digest}" in captured.out
    )
    assert "Repeat acquisition: cache_hit=true; extra_download_events=0" in captured.out
    assert "Active lease: pruned=0; cache_entry_preserved=true" in captured.out
    assert "After release: pruned=1; entries_remaining=0" in captured.out
    assert requests == [f"/manifest/{target}", "/asset.zip"]
