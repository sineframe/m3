#!/usr/bin/env python3
"""Process-backed OpenCode ``serve`` fixture used by native adapter tests.

The fixture deliberately behaves like a tiny HTTP OpenCode server while also
keeping enough on-disk telemetry to test ownership and cleanup.  The child
process is an MCP-like long-lived worker: it has no protocol duties here, but
it gives the adapter a real descendant that must be reaped with the server.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import subprocess
import sys
import threading
import time
from typing import cast
import uuid
from pathlib import Path


mode = os.environ.get("MCP_PAL_OPENCODE_MODE", "normal")
if "--version" in sys.argv:
    version_marker = os.environ.get("MCP_PAL_VERSION_MARKER")
    if version_marker:
        marker_path = Path(version_marker)
        try:
            version_count = int(marker_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            version_count = 0
        marker_path.write_text(str(version_count + 1), encoding="utf-8")
        print("1.18.15" if version_count == 0 else "2.0.0")
    else:
        print("2.0.0" if mode == "v2" else "1.18.15")
    raise SystemExit(0)
if "--help" in sys.argv:
    print("serve --hostname HOST --port PORT")
    raise SystemExit(0)


marker_name = os.environ.get("MCP_PAL_MARKER")
marker = Path(marker_name) if marker_name else None
state_lock = threading.Lock()
session_id = uuid.uuid4().hex
state: dict[str, object] = {
    "mode": mode,
    "serve_pid": os.getpid(),
    "child_pids": [],
    "session_post_count": 0,
    "session_id": None,
    "message_urls": [],
    "message_bodies": [],
    "message_entered": False,
    "cwd": os.getcwd(),
    "directory": None,
    "home": os.environ.get("HOME"),
    "xdg_config_home": os.environ.get("XDG_CONFIG_HOME"),
    "xdg_data_home": os.environ.get("XDG_DATA_HOME"),
    "xdg_state_home": os.environ.get("XDG_STATE_HOME"),
    "opencode_config": os.environ.get("OPENCODE_CONFIG"),
    "config_exists": False,
    "config_content": None,
    "api_hits": [],
    "child_env_marker": str(marker.with_name(marker.name + ".child.json")) if marker else None,
}


def record(**updates: object) -> None:
    if marker is None:
        return
    with state_lock:
        state.update(updates)
        temporary = marker.with_name(marker.name + ".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.replace(temporary, marker)


def child_source() -> str:
    # Keep the worker alive until the process group is terminated.  The
    # private marker is deliberately the only place a resolved credential is
    # recorded, allowing tests to prove explicit delivery without putting a
    # value in the public fixture state or an OpenCode descriptor.
    marker_path = str(marker.with_name(marker.name + ".child.json")) if marker else ""
    return (
        "import json, os, time\n"
        f"marker={marker_path!r}\n"
        "if marker:\n"
        "  payload={'OPENCODE_API_KEY': os.environ.get('OPENCODE_API_KEY'), 'OPENROUTER_API_KEY': os.environ.get('OPENROUTER_API_KEY'), 'ANTHROPIC_API_KEY': os.environ.get('ANTHROPIC_API_KEY'), 'MCP_API_KEY': os.environ.get('MCP_API_KEY')}\n"
        "  with open(marker, 'w', encoding='utf-8') as output: json.dump(payload, output, sort_keys=True)\n"
        "while True: time.sleep(60)\n"
    )


worker = subprocess.Popen(
    [sys.executable, "-c", child_source()],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
record(
    child_pid=worker.pid,
    child_pids=[worker.pid],
    config_exists=bool(state["opencode_config"] and Path(str(state["opencode_config"])).is_file()),
    config_content=(
        json.loads(Path(str(state["opencode_config"])).read_text(encoding="utf-8"))
        if state["opencode_config"] and Path(str(state["opencode_config"])).is_file()
        else None
    ),
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, status: int, value: object) -> None:
        encoded = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _body(self) -> dict[str, object]:
        size = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError("request body is not an object")
        return value

    def do_POST(self) -> None:
        if self.path == "/session":
            record(api_hits=list(cast(list[str], state["api_hits"])) + ["POST /session"])
            with state_lock:
                state["session_post_count"] = cast(int, state["session_post_count"]) + 1
            record(directory=self.headers.get("x-opencode-directory"), session_id=session_id)
            self._json(200, {"id": session_id})
            return
        if self.path == f"/session/{session_id}/message":
            record(api_hits=list(cast(list[str], state["api_hits"])) + ["POST /session/message"])
            value = self._body()
            with state_lock:
                urls = list(cast(list[str], state["message_urls"]))
                bodies = list(cast(list[dict[str, object]], state["message_bodies"]))
                urls.append(self.path)
                bodies.append(value)
            record(message_urls=urls, message_bodies=bodies, message_entered=True)
            if mode == "blocking-message":
                while True:
                    time.sleep(60)
            if mode == "socket-close":
                try:
                    # Advertise a larger body, write only a prefix, then
                    # close the connection to model a truncated HTTP turn.
                    partial = json.dumps({"parts": [{"type": "text", "text": "partial"}]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(partial) + 32))
                    self.end_headers()
                    self.wfile.write(partial[:1])
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                return
            model = value.get("model")
            parts = value.get("parts")
            text = parts[0].get("text", "") if isinstance(parts, list) and parts and isinstance(parts[0], dict) else ""
            if text == "huge":
                self._json(200, {"text": "x" * (1024 * 1024 + 1)})
                return
            if mode == "redaction":
                canary = os.environ.get("OPENCODE_API_KEY", "")
                print(canary, file=sys.stderr, flush=True)
                if text == "provider-error":
                    self._json(200, {"info": {"error": {"message": canary}, "finish": "error"}, "parts": []})
                    return
                if text == "finish-error":
                    self._json(200, {"info": {"error": canary, "finish": "failed"}, "parts": []})
                    return
                self._json(200, {"info": {"finish": "stop", "diagnostic": canary}, "parts": [{"type": "text", "text": f"success {canary}"}]})
                return
            self._json(
                200,
                {
                    "info": {
                        "providerID": model.get("providerID") if isinstance(model, dict) else "opencode",
                        "modelID": model.get("modelID") if isinstance(model, dict) else "fixture",
                        "finish": "stop",
                        "tokens": {"input": 1, "output": 1},
                        "cost": 0,
                    },
                    "parts": [{"type": "text", "text": text}],
                },
            )
            return
        self._json(404, {})

    def do_GET(self) -> None:
        record(api_hits=list(cast(list[str], state["api_hits"])) + [f"GET {self.path}"])
        self._json(404, {})

    def do_DELETE(self) -> None:
        self._json(204, {})


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
record(port=server.server_address[1])
print(f"http://127.0.0.1:{server.server_address[1]}", flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
