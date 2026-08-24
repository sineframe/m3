#!/usr/bin/env python3
"""Small local HTTP server matching the adapter's serve/session contract."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
import uuid


if "--help" in sys.argv:
    print("serve --hostname HOST --port PORT")
    raise SystemExit(0)


session_id = uuid.uuid4().hex


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

    def do_POST(self) -> None:
        if self.path == "/session":
            self._json(200, {"id": session_id})
            return
        if self.path == f"/session/{session_id}/message":
            size = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(size))
            text = value["parts"][0]["text"]
            if text == "huge":
                self._json(200, {"text": "x" * (1024 * 1024 + 1)})
                return
            self._json(200, {"text": text, "usage": {"input_tokens": 1}})
            return
        self._json(404, {})

    def do_DELETE(self) -> None:
        self._json(204, {})


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(f"http://127.0.0.1:{server.server_address[1]}", flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
