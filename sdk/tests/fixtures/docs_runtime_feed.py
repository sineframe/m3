"""Local native-runtime release metadata and archives for documentation tests."""

from __future__ import annotations

import hashlib
import io
import json
import shlex
import stat
import sys
import threading
import zipfile
from collections.abc import Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from m3.runtime.core import detect_target

FIXTURES = Path(__file__).resolve().parent
NATIVE_FIXTURES = {
    "codex": ("codex", "codex_app_server_fixture.py"),
    "pi": ("pi/pi", "pi_rpc_fixture.py"),
    "claude": ("claude", "claude_stream_fixture.py"),
    "opencode": ("opencode", "opencode_serve_fixture.py"),
}


class LocalRuntimeFeed:
    """Serve target-specific selector JSON and hashed executable ZIPs.

    Each archived native executable reports its own selected version for the
    runtime manager smoke check, then delegates to a local harness-protocol
    fixture. The fixture performs real MCP initialize/list/call exchanges with
    the configured server. These archives are test fixtures, not vendor builds.
    """

    def __init__(
        self,
        versions: Sequence[str] | None = None,
        marker_path: str | Path | None = None,
        *,
        kind_versions: Mapping[str, Sequence[str]] | None = None,
    ):
        if marker_path is None:
            raise ValueError("runtime feed needs an MCP wire marker path")
        if kind_versions is None:
            kind_versions = {"codex": tuple(versions or ())}
        elif versions:
            raise ValueError("use versions or kind_versions, not both")
        normalized: dict[str, tuple[str, ...]] = {}
        for kind, pins in kind_versions.items():
            selected = {"claude_code": "claude", "open-code": "opencode"}.get(
                kind.lower(), kind.lower()
            )
            if selected not in NATIVE_FIXTURES:
                raise ValueError(f"unsupported fixture runtime kind: {kind}")
            if not pins or len(set(pins)) != len(pins):
                raise ValueError(f"{kind} feed needs distinct version pins")
            normalized[selected] = tuple(pins)
        if not normalized:
            raise ValueError("runtime feed needs at least one runtime kind")
        self.kind_versions = normalized
        self.versions = normalized.get("codex", ())
        self.marker_path = Path(marker_path).resolve()
        self.targets = {kind: detect_target(kind) for kind in normalized}
        self.requests: list[str] = []
        self._assets = {
            (kind, version): self._archive(kind, version)
            for kind, pins in normalized.items()
            for version in pins
        }
        self._server = self._make_server()
        self._thread: threading.Thread | None = None

    def _archive(self, kind: str, version: str) -> bytes:
        executable, fixture_name = NATIVE_FIXTURES[kind]
        fixture = FIXTURES / fixture_name
        if kind == "codex":
            version_banner = f"codex-cli {version}"
        elif kind in {"claude", "opencode"}:
            version_banner = f"{kind} {version}"
        else:
            version_banner = version
        wrapper = "\n".join(
            (
                "#!/bin/sh",
                'if [ "${1-}" = "--version" ]; then',
                f"  printf '{version_banner}\\n'",
                "  exit 0",
                "fi",
                "export M3_DOCS_LOCAL_PROVIDER=1",
                f"export M3_DOCS_FIXTURE_VERSION={shlex.quote(version)}",
                f"export M3_DOCS_RUNTIME_KIND={shlex.quote(kind)}",
                f"export M3_DOCS_RUNTIME_VERSION={shlex.quote(version)}",
                f"export M3_DOCS_MCP_WIRE_MARKER={shlex.quote(str(self.marker_path))}",
                f"export PYTHONPATH={shlex.quote(str(FIXTURES))}",
                f'exec {shlex.quote(sys.executable)} {shlex.quote(str(fixture))} "$@"',
                "",
            )
        ).encode("utf-8")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            info = zipfile.ZipInfo(executable)
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            archive.writestr(info, wrapper)
        return buffer.getvalue()

    def _make_server(self) -> ThreadingHTTPServer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner.requests.append(self.path)
                parts = self.path.strip("/").split("/")
                if len(parts) == 3 and parts[0] == "manifest":
                    _route, kind, version = parts
                    body = owner._manifest(kind, version)
                    content_type = "application/json"
                elif len(parts) == 3 and parts[0] == "asset":
                    _route, kind, archive = parts
                    version = archive.removesuffix(".zip")
                    body = owner._assets.get((kind, version))
                    if body is None:
                        self.send_error(404)
                        return
                    content_type = "application/zip"
                else:
                    self.send_error(404)
                    return
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                return

        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)

    def _manifest(self, kind: str, version: str) -> bytes | None:
        asset = self._assets.get((kind, version))
        if asset is None:
            return None
        executable, _fixture_name = NATIVE_FIXTURES[kind]
        value: dict[str, Any] = {
            self.targets[kind]: {
                "version": version,
                "url": f"{self.base_url}/asset/{kind}/{version}.zip",
                "sha256": hashlib.sha256(asset).hexdigest(),
                "executable": executable,
            }
        }
        return json.dumps(value, sort_keys=True).encode("utf-8")

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    @property
    def manifest_feed_url(self) -> str:
        return f"{self.base_url}/manifest"

    def __enter__(self) -> LocalRuntimeFeed:
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server.server_close()
