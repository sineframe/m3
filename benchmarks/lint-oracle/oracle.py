#!/usr/bin/env python3
"""Harness oracle for `m3 lint`.

Runs the real Claude Code and Codex binaries against every rule case in the lint
corpus, through a capture proxy, and compares what reached the model request with
what `m3 lint` predicted for the same binary version. Manual, not part of CI.
Stdlib only (Python >= 3.10).

    python3 benchmarks/lint-oracle/oracle.py [--real-api] [--claude BIN] [--codex BIN]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fnmatch
import gzip
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "cli" / "tests" / "fixtures" / "lint_server.py"
DEFAULT_CASES = REPO / "cli" / "tests" / "fixtures" / "lint_cases"

RANK = {"none": 0, "degraded": 1, "tool": 2, "server": 3, "session": 4}

# (rule_id, harness) pairs whose effect never shows up in the first model request.
UNOBSERVABLE: set[tuple[str, str]] = {
    ("root-type-not-object", "codex"),
    ("root-combinator", "codex"),
    ("codex-ts-too-large", "codex"),
    ("codex-unresolvable-ref", "codex"),
    ("integer-id-precision", "*"),
    ("discover-probe-unanswered", "claude_code"),
    ("instructions-too-long", "*"),
    ("fractional-priority", "codex"),
    ("resource-entry-invalid", "*"),
    ("prompts-ignored-by-codex", "codex"),
    ("output-schema-structured-only", "*"),
}


def unobservable(rule: str, harness: str) -> bool:
    return (rule, harness) in UNOBSERVABLE or (rule, "*") in UNOBSERVABLE


CONTROL_SPEC = {
    "tools": [
        {
            "name": "echo",
            "description": "Echo text.",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        }
    ]
}

FAKE_ANTHROPIC = [
    (
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_fake",
                "type": "message",
                "role": "assistant",
                "model": "fake",
                "content": [],
                "stop_reason": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        },
    ),
    (
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "done"},
        },
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 1},
        },
    ),
    ("message_stop", {"type": "message_stop"}),
]
FAKE_RESPONSES = [
    (
        "response.created",
        {"type": "response.created", "response": {"id": "resp_oracle"}},
    ),
    (
        "response.output_item.done",
        {
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "role": "assistant",
                "id": "msg_oracle",
                "content": [{"type": "output_text", "text": "done"}],
            },
        },
    ),
    (
        "response.completed",
        {
            "type": "response.completed",
            "response": {
                "id": "resp_oracle",
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        },
    ),
]


def sse(events: list[tuple[str, dict[str, Any]]]) -> bytes:
    return "".join(f"event: {t}\ndata: {json.dumps(d)}\n\n" for t, d in events).encode()


# --------------------------------------------------------------------------- proxy


def decode_zstd(data: bytes) -> bytes | None:
    try:
        import zstandard  # type: ignore[import-not-found]

        return zstandard.ZstdDecompressor().stream_reader(data).read()  # type: ignore[no-any-return]
    except ImportError:
        pass
    except Exception:
        return None
    try:
        from compression import zstd  # type: ignore[import-not-found]  # Python 3.14+

        return zstd.decompress(data)  # type: ignore[no-any-return]
    except ImportError:
        pass
    except Exception:
        return None
    return None


class RunState:
    def __init__(self, directory: Path, forward: bool) -> None:
        self.dir = directory
        self.forward = forward
        self.count = 0
        self.forwarded = False
        self.undecodable = False
        self.lock = threading.Lock()


class Proxy:
    def __init__(self) -> None:
        self.runs: dict[str, RunState] = {}
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:
                return

            def _body(self) -> bytes:
                if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                    out = b""
                    while True:
                        size = int(self.rfile.readline().strip() or b"0", 16)
                        if size == 0:
                            self.rfile.readline()
                            return out
                        out += self.rfile.read(size)
                        self.rfile.readline()
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _send(self, status: int, ctype: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            def _stream(self, body: bytes) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
                except OSError:
                    pass
                self.close_connection = True

            def _handle(self) -> None:
                raw = self._body() if self.command in ("POST", "PUT", "PATCH") else b""
                full = self.path
                path, _, query = full.partition("?")
                parts = path.split("/", 2)
                run_id = parts[1] if len(parts) > 1 else ""
                real_path = "/" + (parts[2] if len(parts) > 2 else "")
                state = proxy.runs.get(run_id)
                if state is None:
                    self._send(404, "application/json", b"{}")
                    return
                body: Any = None
                if raw:
                    data: bytes | None = raw
                    enc = (self.headers.get("Content-Encoding") or "").lower()
                    if enc == "gzip":
                        try:
                            data = gzip.decompress(raw)
                        except Exception:
                            data = None
                    elif enc == "zstd":
                        data = decode_zstd(raw)
                        if data is None:
                            body = {"_undecodable": "zstd"}
                            state.undecodable = True
                    if data is not None:
                        try:
                            body = json.loads(data)
                        except ValueError:
                            body = {"_undecodable": "json"}
                with state.lock:
                    state.count += 1
                    n = state.count
                (state.dir / f"req-{n:03d}.json").write_text(
                    json.dumps(
                        {
                            "method": self.command,
                            "path": real_path + (f"?{query}" if query else ""),
                            "body": body,
                        }
                    )
                )
                if self.command == "POST" and real_path.endswith("/v1/messages"):
                    if state.forward and self._is_main(body):
                        with state.lock:
                            first = not state.forwarded
                            state.forwarded = True
                        if first:
                            self._forward(state, body, real_path, query)
                            return
                    self._stream(sse(FAKE_ANTHROPIC))
                elif self.command == "POST" and real_path.endswith("/responses"):
                    self._stream(sse(FAKE_RESPONSES))
                elif self.command == "GET" and "/models" in real_path:
                    self._send(200, "application/json", b'{"models":[],"data":[]}')
                else:
                    self._send(200, "application/json", b"{}")

            @staticmethod
            def _is_main(body: Any) -> bool:
                tools = body.get("tools") if isinstance(body, dict) else None
                return isinstance(tools, list) and any(
                    isinstance(t, dict) and t.get("name") == "mcp__control__echo"
                    for t in tools
                )

            def _forward(
                self, state: RunState, body: dict[str, Any], real_path: str, query: str
            ) -> None:
                out = dict(body)
                out["max_tokens"] = 1
                out.pop("thinking", None)
                # Its clear_thinking strategy needs `thinking`; left in, the API
                # rejects every forwarded request for that reason alone.
                out.pop("context_management", None)
                headers = {
                    k: v
                    for k, v in self.headers.items()
                    if k.lower()
                    not in {
                        "host",
                        "content-length",
                        "accept-encoding",
                        "content-encoding",
                    }
                }
                headers["Content-Type"] = "application/json"
                url = (
                    "https://api.anthropic.com"
                    + real_path
                    + (f"?{query}" if query else "")
                )
                req = urllib.request.Request(
                    url, data=json.dumps(out).encode(), headers=headers, method="POST"
                )
                try:
                    resp = urllib.request.urlopen(req, timeout=120)
                    status, data, ctype = (
                        resp.status,
                        resp.read(),
                        resp.headers.get("Content-Type", "application/json"),
                    )
                except urllib.error.HTTPError as exc:
                    status, data, ctype = (
                        exc.code,
                        exc.read(),
                        exc.headers.get("Content-Type", "application/json"),
                    )
                except Exception as exc:  # network failure
                    status, data, ctype = 599, b"{}", "application/json"
                    (state.dir / "upstream.json").write_text(
                        json.dumps(
                            {
                                "status": status,
                                "error_type": "network",
                                "message": type(exc).__name__,
                            }
                        )
                    )
                    self._send(502, "application/json", b"{}")
                    return
                info: dict[str, Any] = {
                    "status": status,
                    "error_type": None,
                    "message": None,
                }
                try:
                    err = json.loads(data).get("error")
                    if isinstance(err, dict):
                        info["error_type"] = err.get("type")
                        info["message"] = str(err.get("message"))[:300]
                except (ValueError, AttributeError):
                    pass
                (state.dir / "upstream.json").write_text(json.dumps(info))
                self._send(status, ctype, data)

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            request_queue_size = 64

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add_run(self, run_id: str, state: RunState) -> None:
        self.runs[run_id] = state


# --------------------------------------------------------------------------- spec helpers


def load_expand() -> Any:
    spec = importlib.util.spec_from_file_location("lint_server_fixture", FIXTURE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._expand


def cc_name(s: str) -> str:
    # JavaScript's regex without the u flag works on UTF-16 units, so a
    # character outside the BMP becomes "__".
    return "".join(
        c if re.fullmatch(r"[a-zA-Z0-9_-]", c) else "_" * (2 if ord(c) > 0xFFFF else 1)
        for c in s
    )


def cx_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", s)


def drop_added_properties(observed: Any, spec: Any) -> Any:
    """`observed` without empty `properties: {}` the harness added where the
    spec's object had no `properties` key."""
    if isinstance(observed, dict):
        spec_dict = spec if isinstance(spec, dict) else {}
        return {
            k: drop_added_properties(v, spec_dict.get(k))
            for k, v in observed.items()
            if not (k == "properties" and v == {} and "properties" not in spec_dict)
        }
    if isinstance(observed, list):
        spec_list = spec if isinstance(spec, list) else []
        return [
            drop_added_properties(v, spec_list[i] if i < len(spec_list) else None)
            for i, v in enumerate(observed)
        ]
    return observed


def canon(schema: Any) -> str:
    return json.dumps(schema, sort_keys=True, ensure_ascii=False)


# --------------------------------------------------------------------------- observation


def tool_maps(harness: str, body: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """{server: {tool_name: {"description", "schema"}}} from one request body."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    tools = body.get("tools") if isinstance(body, dict) else None
    if not isinstance(tools, list):
        return out

    def add(server: str, name: str, desc: Any, schema: Any) -> None:
        bucket = out.setdefault(server, {})
        key = name
        i = 1
        while key in bucket:  # duplicate names stay distinct
            i += 1
            key = f"{name}\0{i}"
        bucket[key] = {"name": name, "description": desc, "schema": schema}

    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not isinstance(name, str):
            continue
        if harness == "claude_code":
            if name.startswith("mcp__"):
                server, sep, rest = name[5:].partition("__")
                if sep:
                    add(server, rest, tool.get("description"), tool.get("input_schema"))
        elif tool.get("type") == "namespace" and name.startswith("mcp__"):
            prefix = name
            server = name[5:].removesuffix("__")
            for inner in tool.get("tools") or []:
                if isinstance(inner, dict) and isinstance(inner.get("name"), str):
                    inner_name = inner["name"]
                    if inner_name.startswith(prefix):
                        inner_name = inner_name[len(prefix) :]
                        if not prefix.endswith("__") and inner_name.startswith("__"):
                            inner_name = inner_name[2:]
                    add(
                        server,
                        inner_name,
                        inner.get("description"),
                        inner.get("parameters"),
                    )
        elif tool.get("type") == "function" and name.startswith("mcp__"):
            server, sep, rest = name[5:].partition("__")
            if sep:
                add(server, rest, tool.get("description"), tool.get("parameters"))
    return out


def observe(
    harness: str, run_dir: Path, spec_tools: list[Any], real_api: bool
) -> tuple[str, str]:
    """Returns (level, note). level is none|degraded|tool|server|session|invalid."""
    reqs = sorted(run_dir.glob("req-*.json"))
    main: dict[str, dict[str, dict[str, Any]]] | None = None
    for path in reqs:
        try:
            body = json.loads(path.read_text()).get("body")
        except ValueError:
            continue
        maps = tool_maps(harness, body)
        if any(t["name"] == "echo" for t in maps.get("control", {}).values()):
            main = maps
            break
    if main is None:
        return "invalid", "no model request carried control/echo"
    upstream = run_dir / "upstream.json"
    if real_api and harness == "claude_code" and upstream.exists():
        try:
            answer = json.loads(upstream.read_text())
        except ValueError:
            return "invalid", "unreadable upstream.json"
        status = answer.get("status")
        if status == 400:
            return "session", "upstream 400"
        if not isinstance(status, int) or not 200 <= status < 300:
            # Auth, rate-limit or network failures say nothing about the rule.
            return "invalid", f"upstream {status} {answer.get('error_type')}"
    quirk = main.get("quirk")
    named = [
        t for t in spec_tools if isinstance(t, dict) and isinstance(t.get("name"), str)
    ]
    if not quirk:
        # A valid listing with no tools leaves nothing to observe.
        empty = isinstance(spec_tools, list) and not named
        return ("none", "") if empty else ("server", "")
    if len(quirk) < len(named):
        return "tool", f"{len(quirk)} of {len(named)} tools"
    # The harness's documented character replacement (rule tool-name-sanitized,
    # rated advisory) is not a degradation; any other rename (hash suffixes,
    # "_" for an empty name) is.
    sanitize = cc_name if harness == "claude_code" else cx_name
    pools: dict[str, list[dict[str, Any]]] = {}
    for t in named:
        pools.setdefault(sanitize(t["name"]), []).append(t)
    for obs in quirk.values():
        pool = pools.get(sanitize(obs["name"]))
        if not pool:
            return "degraded", "tool name rewritten"
        spec = pool.pop(0)
        # A missing, null or empty description all reach the model as "".
        if (obs["description"] or "") != (spec.get("description") or ""):
            return "degraded", "description differs"
        spec_schema = spec.get("inputSchema")
        if canon(drop_added_properties(obs["schema"], spec_schema)) != canon(
            spec_schema
        ):
            return "degraded", "schema differs"
    return "none", ""


# --------------------------------------------------------------------------- running


def version_of(binary: str) -> str | None:
    try:
        out = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=60
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


def run_process(
    cmd: list[str], env: dict[str, str], cwd: Path, timeout: float, log: Path
) -> None:
    with log.open("wb") as fh:
        proc = subprocess.Popen(
            cmd,
            env=env,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
        finally:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass


def predict(
    case: dict[str, Any], harness: str, version: str, run_dir: Path
) -> tuple[str, list[list[Any]], str]:
    args: list[str] = []
    it = iter(case.get("args", []))
    for a in it:
        if a == "--harness":
            next(it, None)
        elif a.startswith("--harness="):
            continue
        else:
            args.append(a)
    env = dict(os.environ, LINT_SPEC=str(run_dir / "spec.json"))
    cmd = [
        "uv",
        "run",
        "--project",
        "cli",
        "m3",
        "lint",
        "--json",
        "--server-name",
        "quirk",
        "--harness",
        f"{harness}@{version}",
        *args,
        "--",
        sys.executable,
        str(FIXTURE),
    ]
    proc = subprocess.run(cmd, env=env, cwd=REPO, capture_output=True, text=True)
    try:
        report = json.loads(proc.stdout)
    except ValueError:
        return "invalid", [], "lint produced no JSON"
    if proc.returncode not in (0, 1) or "findings" not in report:
        return "invalid", [], f"lint failed: {report.get('error', proc.returncode)}"
    level = "none"
    effects: list[list[Any]] = []
    for f in report["findings"]:
        for e in f["effects"]:
            if e["harness"] != harness:
                continue
            effects.append(
                [
                    f["rule"],
                    harness,
                    e["range"]["introduced"],
                    e["range"]["fixed"],
                    e["impact"],
                    e["summary"],
                ]
            )
            if e["impact"] == "advisory" or unobservable(f["rule"], harness):
                continue
            if RANK[e["impact"]] > RANK[level]:
                level = e["impact"]
    if any(e[4] == "server" and not unobservable(e[0], harness) for e in effects):
        # A server whose tools are all lost never sends them to the model, so
        # no session failure can come from them.
        level = "server"
    return level, effects, ""


def run_one(
    ctx: dict[str, Any],
    rule: str,
    case: dict[str, Any],
    harness: str,
    binary: str,
    version: str,
    idx: int,
) -> dict[str, Any]:
    out_dir: Path = (
        ctx["out"]
        / re.sub(r"[^\w.-]", "_", rule)
        / re.sub(r"[^\w.-]", "_", case["name"])
        / f"{harness}-{version}"
    )
    # A rerun into the same --out must not observe an earlier run's captures.
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    rec: dict[str, Any] = {
        "case": f"{rule}/{case['name']}",
        "harness": harness,
        "version": version,
        "predicted": None,
        "observed": None,
        "verdict": None,
        "run_dir": str(out_dir),
        "expect": case.get("expect"),
        "effects": [],
        "note": "",
    }
    expand = ctx["expand"]
    spec = case["spec"]
    (out_dir / "spec.json").write_text(json.dumps(spec))
    (out_dir / "control.json").write_text(json.dumps(CONTROL_SPEC))
    predicted, effects, why = predict(case, harness, version, out_dir)
    rec["predicted"], rec["effects"], rec["note"] = predicted, effects, why
    if predicted == "invalid":
        rec["verdict"] = "invalid"
        return rec
    oracle = case.get("oracle") or {}
    also_ok = (
        (oracle.get("also_ok") or {}).get(harness, [])
        if isinstance(oracle, dict)
        else []
    )
    real_api = ctx["real_api"]
    if harness == "claude_code" and predicted == "session" and not real_api:
        rec["verdict"] = "skipped"
        rec["note"] = "needs --real-api"
        return rec
    # Every Claude Code run of a rule that can break the session is sent to
    # the API, so the absent side of a threshold is tested as well.
    forward = (
        real_api
        and harness == "claude_code"
        and (
            predicted == "session"
            or "session" in also_ok
            or rule in ctx["session_rules"]
        )
    )
    state = RunState(out_dir, forward)
    run_id = f"r{idx}"
    ctx["proxy"].add_run(run_id, state)
    port = ctx["proxy"].port
    env = dict(os.environ)
    python, fixture = sys.executable, str(FIXTURE)
    log = str(out_dir / "server.log")
    if harness == "claude_code":
        servers = {
            "control": {
                "command": python,
                "args": [fixture],
                "env": {"LINT_SPEC": str(out_dir / "control.json")},
            },
            "quirk": {
                "command": python,
                "args": [fixture],
                "env": {"LINT_SPEC": str(out_dir / "spec.json"), "LINT_LOG": log},
            },
        }
        (out_dir / "mcp.json").write_text(json.dumps({"mcpServers": servers}))
        cmd = [
            binary,
            "-p",
            "Reply with just: done",
            "--mcp-config",
            str(out_dir / "mcp.json"),
            "--strict-mcp-config",
            "--output-format",
            "json",
            "--model",
            "haiku",
        ]
        env.update(
            {
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}/{run_id}",
                "ENABLE_TOOL_SEARCH": "false",
                "DISABLE_AUTOUPDATER": "1",
                "MCP_TIMEOUT": "30000",
            }
        )
        if not real_api:
            env["ANTHROPIC_API_KEY"] = "oracle-fake"
            env["CLAUDE_CONFIG_DIR"] = str(out_dir / "cc-config")
            env.pop("ANTHROPIC_AUTH_TOKEN", None)
            env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        elif env.get("ANTHROPIC_API_KEY"):
            env["CLAUDE_CONFIG_DIR"] = str(out_dir / "cc-config")
        else:
            # Use the user's logged-in default configuration.
            env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        home = out_dir / "codex-home"
        home.mkdir(exist_ok=True)
        env["CODEX_HOME"] = str(home)
        provider = (
            'model_providers.oracle={name="oracle",'
            f'base_url="http://127.0.0.1:{port}/{run_id}/v1",wire_api="responses",'
            "requires_openai_auth=false,request_max_retries=0,stream_max_retries=0}"
        )
        cmd = [
            binary,
            "exec",
            "--skip-git-repo-check",
            "-s",
            "read-only",
            "--ephemeral",
            "-m",
            "oracle-direct",
            "-c",
            'model_provider="oracle"',
            "-c",
            provider,
        ]
        for name, spec_file, extra in (
            ("control", out_dir / "control.json", ""),
            ("quirk", out_dir / "spec.json", f",LINT_LOG={json.dumps(log)}"),
        ):
            cmd += [
                "-c",
                f"mcp_servers.{name}.command={json.dumps(python)}",
                "-c",
                f"mcp_servers.{name}.args=[{json.dumps(fixture)}]",
                "-c",
                f"mcp_servers.{name}.env={{LINT_SPEC={json.dumps(str(spec_file))}{extra}}}",
            ]
        cmd.append("Reply with just: done")
    run_process(cmd, env, out_dir, ctx["timeout"], out_dir / "harness.log")
    if state.undecodable:
        rec["observed"], rec["verdict"], rec["note"] = (
            "invalid",
            "invalid",
            "undecodable zstd request body",
        )
        return rec
    spec_tools = expand(spec.get("tools", []))
    observed, note = observe(harness, out_dir, spec_tools, real_api)
    rec["observed"] = observed
    rec["note"] = note
    if observed == "invalid":
        rec["verdict"] = "invalid"
    elif predicted == observed:
        rec["verdict"] = "match"
    elif observed in also_ok:
        rec["verdict"] = "allowed"
    else:
        rec["verdict"] = "mismatch"
    return rec


# --------------------------------------------------------------------------- coverage / main


def vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def in_range(version: str, rng: dict[str, Any]) -> bool:
    v = vtuple(version)
    return (rng["introduced"] is None or v >= vtuple(rng["introduced"])) and (
        rng["fixed"] is None or v < vtuple(rng["fixed"])
    )


def label(rng: dict[str, Any]) -> str:
    a, b = rng["introduced"], rng["fixed"]
    if a and b:
        return f"{a}-<{b}"
    return f">={a}" if a else (f"<{b}" if b else "all versions")


def load_catalogue() -> list[dict[str, Any]] | None:
    listing = subprocess.run(
        ["uv", "run", "--project", "cli", "m3", "lint", "--list-rules", "--json"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    try:
        rules: list[dict[str, Any]] = json.loads(listing.stdout)["rules"]
    except (ValueError, KeyError):
        return None
    return rules


def coverage(
    records: list[dict[str, Any]],
    tested: dict[str, list[str]],
    rules: set[str],
    real_api: bool,
    catalogue: list[dict[str, Any]],
) -> list[str]:
    good = [
        r
        for r in records
        if r["verdict"] in ("match", "allowed") and r.get("expect") == "present"
    ]
    covered: set[tuple[Any, ...]] = set()
    for r in good:
        for rule, harness, intro, fixed, _impact, summary in r["effects"]:
            if r["case"].split("/", 1)[0] == rule:
                covered.add((rule, harness, intro, fixed, summary))
    lines = []
    for rule in catalogue:
        if rule["id"] not in rules:
            continue
        for e in rule["effects"]:
            h = e["harness"]
            if e["impact"] == "advisory" or unobservable(rule["id"], h):
                continue
            rng = e["range"]
            if not any(in_range(v, rng) for v in tested.get(h, [])):
                continue
            key = (rule["id"], h, rng["introduced"], rng["fixed"], e["summary"])
            if key not in covered:
                # Without --real-api a Claude Code session failure cannot be
                # observed (the run is skipped), so it is left to that run.
                status = (
                    "needs-real-api"
                    if h == "claude_code" and e["impact"] == "session" and not real_api
                    else "uncovered"
                )
                lines.append(
                    f"{status} {rule['id']} {h} {label(rng)} [{e['impact']}] evidence={e['evidence']}"
                )
    return lines


# --------------------------------------------------------------------------- directory policies

DIRECTORIES_MODULE = REPO / "cli" / "src" / "m3_cli" / "lint" / "directories.py"
HEADING = re.compile(r"\x00(\d)\x00([^\x00]*)\x00")


class _PolicyHTML(HTMLParser):
    """Rendered text of a page, with each heading marked by level and id."""

    SKIP = ("script", "style", "svg", "nav", "template")

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self.skip += 1
        elif self.skip:
            return
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.parts.append(f"\n\x00{tag[1]}\x00{dict(attrs).get('id') or ''}\x00")
        elif tag in ("p", "li", "br", "div", "tr", "dt", "dd"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def _md_slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", heading.lower()).strip().replace(" ", "-")


def _md_text(markdown: str) -> str:
    lines = []
    for line in markdown.splitlines():
        m = re.match(r"(#{1,6}) (.*)", line)
        if m:
            lines.append(
                f"\x00{len(m.group(1))}\x00{_md_slug(m.group(2))}\x00{m.group(2)}"
            )
            continue
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)  # links keep their text
        line = re.sub(r"^\s*[*-] ", "", line)
        lines.append(line.replace("`", "").replace("**", ""))
    return "\n".join(lines)


def policy_text(url: str, body: str) -> str:
    if urlsplit(url).hostname == "claude.com":
        return _md_text(body)
    parser = _PolicyHTML()
    parser.feed(body)
    return "".join(parser.parts)


def policy_section(text: str, anchor: str) -> str | None:
    """The anchored heading's text up to the next heading at its level or above."""

    marks = list(HEADING.finditer(text))
    for i, mark in enumerate(marks):
        if mark.group(2) != anchor:
            continue
        level = int(mark.group(1))
        end = len(text)
        for later in marks[i + 1 :]:
            if int(later.group(1)) <= level:
                end = later.start()
                break
        return HEADING.sub("\n", text[mark.start() : end])
    return None


def _squash(text: str) -> str:
    return " ".join(text.split())


def quote_found(section: str, quote: str, item: str | None) -> bool:
    if item is not None:
        # Lettered items ("G. Instructional Software ...") each sit on a line.
        lines = [ln.strip() for ln in section.splitlines()]
        found = [ln for ln in lines if ln.startswith(f"{item}. ")]
        if len(found) != 1:
            return False
        section = found[0]
    haystack = _squash(section)
    position = 0
    for piece in quote.split(" … "):
        at = haystack.find(_squash(piece), position)
        if at < 0:
            return False
        position = at + len(_squash(piece))
    return True


def fetch_page(url: str, timeout: float) -> tuple[str | None, str]:
    """(body, reason); claude.com pages are read in their Markdown form."""

    page = url.split("#", 1)[0]
    if urlsplit(page).hostname == "claude.com":
        page += ".md"
    request = urllib.request.Request(page, headers={"User-Agent": "m3-lint-oracle"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace"), ""
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, type(exc).__name__


def load_sources() -> tuple[str, dict[str, Any]]:
    spec = importlib.util.spec_from_file_location(
        "lint_directories", DIRECTORIES_MODULE
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations through it
    spec.loader.exec_module(mod)
    return mod.POLICY_DATE, mod.SOURCES


def run_policies(out: Path, timeout: float) -> int:
    """Check every directory source quote against its live page."""

    policy_date, sources = load_sources()
    pages: dict[str, tuple[str | None, str]] = {}
    for source in sources.values():
        page = source.url.split("#", 1)[0]
        if page not in pages:
            pages[page] = fetch_page(source.url, timeout)
    slices = out / "policy-sections"
    slices.mkdir(parents=True, exist_ok=True)
    verdicts: dict[str, list[str]] = {
        "page-unavailable": [],
        "anchor-missing": [],
        "quote-missing": [],
        "ok": [],
    }
    for key, source in sources.items():
        page, _, anchor = source.url.partition("#")
        body, reason = pages[page]
        if body is None:
            verdicts["page-unavailable"].append(f"{key} {page} ({reason})")
            continue
        section = policy_section(policy_text(source.url, body), anchor)
        if section is None:
            verdicts["anchor-missing"].append(f"{key} {source.url}")
            continue
        (slices / f"{key}.txt").write_text(
            f"{source.url}\n\n{section.strip()}\n", encoding="utf-8"
        )
        if quote_found(section, source.quote, source.item):
            verdicts["ok"].append(key)
        else:
            where = f" item {source.item}" if source.item else ""
            verdicts["quote-missing"].append(f"{key} {source.url}{where}")
    actions = {
        "page-unavailable": "could not fetch; nothing on this page was checked",
        "anchor-missing": "the heading moved or was renamed; re-check the page and update the source URL",
        "quote-missing": "the policy text changed; re-check the section and update the quote and POLICY_DATE",
    }
    print(f"policy date in rule data: {policy_date}")
    for page, (body, _) in sorted(pages.items()):
        modified = re.search(
            r'"(?:dateModified|lastUpdatedDate)"\s*:\s*"([^"]+)"', body or ""
        )
        print(
            f"  {page}: {'modified ' + modified.group(1) if modified else 'no modified date'}"
        )
    for kind, action in actions.items():
        if verdicts[kind]:
            print(f"\n{kind} ({len(verdicts[kind])}): {action}")
            for line in verdicts[kind]:
                print(f"  {line}")
    print(
        f"\n{len(verdicts['ok'])} of {len(sources)} quotes found verbatim in their sections"
    )
    print(f"sections: {slices}")
    (out / "policies.json").write_text(json.dumps(verdicts, indent=2))
    if verdicts["anchor-missing"] or verdicts["quote-missing"]:
        return 1
    # Unchecked pages are not a pass.
    return 2 if verdicts["page-unavailable"] else 0


DIRECTORY_SERVERS = Path(__file__).parent / "directory_servers.json"


def _finding_key(finding: dict[str, Any]) -> tuple[str, str]:
    return finding["rule"], finding["location"]


def _severities(finding: dict[str, Any]) -> dict[str, str | None]:
    worst: dict[str, str | None] = {d: None for d in ("claude", "openai", "muse")}
    for effect in finding.get("directories", []):
        if worst[effect["directory"]] != "error":
            worst[effect["directory"]] = effect["severity"]
    return worst


def lint_server(url: str, timeout: float) -> tuple[dict[str, Any] | None, str]:
    proc = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "cli",
            "m3",
            "lint",
            url,
            "--directory",
            "all",
            "--json",
            "--fail-on",
            "never",
            "--timeout",
            str(timeout),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    try:
        report = json.loads(proc.stdout)
    except ValueError:
        return None, f"lint printed no JSON (exit {proc.returncode})"
    if "findings" not in report:
        return None, str(report.get("error", f"exit {proc.returncode}"))
    if not report["collection"]["complete"]:
        return report, "listing incomplete; findings cover only what was read"
    return report, ""


def run_directories(out: Path, timeout: float, jobs: int) -> int:
    """Lint the listed servers and diff their directory findings against the
    reviewed expectations in directory_servers.json."""

    servers = json.loads(DIRECTORY_SERVERS.read_text())["servers"]
    catalogue = load_catalogue() or []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        results = list(pool.map(lambda s: lint_server(s["url"], timeout), servers))
    sections: dict[str, list[str]] = {
        "unexpected": [],
        "missing": [],
        "unavailable": [],
        "uncovered": [],
    }
    seen: set[tuple[str, str, str]] = set()
    reports = {}
    for server, (report, note) in zip(servers, results, strict=True):
        name = server["name"]
        if report is None:
            sections["unavailable"].append(f"{name} {server['url']} ({note})")
            continue
        if note:
            sections["unavailable"].append(f"{name} {server['url']} (partial: {note})")
        reports[name] = report
        actual: dict[tuple[str, str], tuple[dict[str, str | None], str]] = {}
        for finding in report["findings"]:
            key = _finding_key(finding)
            levels = _severities(finding)
            if key in actual:
                # Several findings at one location: keep the worst per directory.
                old, detail = actual[key]
                levels = {
                    d: "error"
                    if "error" in (old[d], levels[d])
                    else old[d] or levels[d]
                    for d in levels
                }
                actual[key] = (levels, f"{detail}; {finding['detail']}")
            else:
                actual[key] = (levels, finding["detail"])
            seen.update(
                (finding["rule"], e["directory"], e["summary"])
                for e in finding.get("directories", [])
            )
        expected = {
            (e["rule"], e["location"]): e["directories"] for e in server["expected"]
        }
        for key, (levels, detail) in sorted(actual.items()):
            if expected.get(key) != levels:
                was = f" (expected {expected[key]})" if key in expected else ""
                sections["unexpected"].append(
                    f"{name}: {key[0]} {key[1]} {levels}{was} detail={detail!r}"
                )
        if note:
            # A partial listing can't show that an expected finding is gone.
            continue
        for key in sorted(expected.keys() - actual.keys()):
            sections["missing"].append(f"{name}: {key[0]} {key[1]} {expected[key]}")
    for rule in catalogue:
        for effect in rule.get("directories", []):
            if (rule["id"], effect["directory"], effect["summary"]) not in seen:
                sections["uncovered"].append(
                    f"{rule['id']} {effect['directory']} [{effect['severity']}] "
                    f"{effect['summary']}"
                )
    actions = {
        "unexpected": "decide: a true finding goes into the server's expected list; "
        "a false positive means fixing the rule and adding a regression case",
        "missing": "an expected finding was not produced: the rule regressed or the "
        "server changed; re-run m3 lint on it and compare",
        "unavailable": "not checked; this is not a pass",
        "uncovered": "no listed server exercises these effects (case files still "
        "must); informational",
    }
    for kind, action in actions.items():
        if sections[kind]:
            print(f"{kind} ({len(sections[kind])}): {action}")
            for line in sections[kind]:
                print(f"  {line}")
            print()
    checked = len(servers) - sum(1 for r, _ in results if r is None)
    print(f"{checked} of {len(servers)} servers checked")
    (out / "directories.json").write_text(
        json.dumps({"sections": sections, "reports": reports}, indent=2)
    )
    print(f"reports: {out / 'directories.json'}")
    if sections["unexpected"] or sections["missing"]:
        return 1
    # Unchecked servers are not a pass.
    return 2 if sections["unavailable"] else 0


def _run_dir(given: str | None) -> Path:
    out = (
        Path(given)
        if given
        else Path(__file__).parent
        / "runs"
        / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    ).resolve()  # harnesses run inside run directories; paths must be absolute
    out.mkdir(parents=True, exist_ok=True)
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--claude", action="append", default=[], metavar="BIN")
    p.add_argument("--codex", action="append", default=[], metavar="BIN")
    p.add_argument("--real-api", action="store_true")
    p.add_argument("--case", action="append", default=[], metavar="GLOB")
    p.add_argument("--out", default=None)
    p.add_argument("--timeout", type=float, default=180.0)
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--cases-dir", default=str(DEFAULT_CASES))
    p.add_argument(
        "--policies",
        action="store_true",
        help="check every directory policy quote against its live page",
    )
    p.add_argument(
        "--directories",
        action="store_true",
        help="lint the listed servers in directory_servers.json against their expectations",
    )
    a = p.parse_args()

    if a.policies:
        return run_policies(_run_dir(a.out), a.timeout)
    if a.directories:
        return run_directories(_run_dir(a.out), min(a.timeout, 60.0), a.jobs)

    binaries: list[tuple[str, str, str]] = []
    claude = a.claude or ([shutil.which("claude")] if shutil.which("claude") else [])
    codex = a.codex or ([shutil.which("codex")] if shutil.which("codex") else [])
    for kind, bins in (("claude_code", claude), ("codex", codex)):
        for given in bins:
            # Harnesses run inside run directories, so relative paths break.
            b = os.path.abspath(given) if os.sep in given else given
            version = version_of(b)
            if version is None:
                print(f"oracle: cannot run {b}", file=sys.stderr)
                return 2
            if any(k == kind and v == version for k, _, v in binaries):
                # Run directories are keyed by harness and version.
                print(f"oracle: two binaries report {kind} {version}", file=sys.stderr)
                return 2
            binaries.append((kind, b, version))
    if not binaries:
        print("oracle: no claude or codex binary found", file=sys.stderr)
        return 2

    out = _run_dir(a.out)

    cases: list[tuple[str, dict[str, Any]]] = []
    for f in sorted(Path(a.cases_dir).glob("*.json")):
        data = json.loads(f.read_text())
        for c in data["cases"]:
            if c.get("oracle") is False:
                continue
            ident = f"{data['rule']}/{c['name']}"
            if a.case and not any(fnmatch.fnmatch(ident, g) for g in a.case):
                continue
            cases.append((data["rule"], c))
    catalogue = load_catalogue()
    if catalogue is None:
        print("oracle: could not read m3 lint --list-rules --json", file=sys.stderr)
        return 2
    # Directory rules have no harness effect to observe.
    harness_rules = {rule["id"] for rule in catalogue if rule["effects"]}
    cases = [(rule, c) for rule, c in cases if rule in harness_rules]
    if not cases:
        print("oracle: no cases selected", file=sys.stderr)
        return 2
    session_rules = {
        rule["id"]
        for rule in catalogue
        if any(
            e["harness"] == "claude_code" and e["impact"] == "session"
            for e in rule["effects"]
        )
    }
    ctx = {
        "out": out,
        "proxy": Proxy(),
        "real_api": a.real_api,
        "timeout": a.timeout,
        "expand": load_expand(),
        "session_rules": session_rules,
    }
    jobs = []
    idx = 0
    for rule, c in cases:
        for kind, b, v in binaries:
            idx += 1
            jobs.append((rule, c, kind, b, v, idx))
    records: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        futs = {pool.submit(run_one, ctx, *j[:5], j[5]): j for j in jobs}
        for fut in concurrent.futures.as_completed(futs):
            j = futs[fut]
            try:
                records.append(fut.result())
            except Exception as exc:
                records.append(
                    {
                        "case": f"{j[0]}/{j[1]['name']}",
                        "harness": j[2],
                        "version": j[4],
                        "predicted": None,
                        "observed": None,
                        "verdict": "invalid",
                        "run_dir": "",
                        "expect": j[1].get("expect"),
                        "effects": [],
                        "note": f"oracle error: {type(exc).__name__}: {exc}",
                    }
                )
    records.sort(key=lambda r: (r["case"], r["harness"], r["version"]))

    report = [
        {
            k: r[k]
            for k in (
                "case",
                "harness",
                "version",
                "predicted",
                "observed",
                "verdict",
                "run_dir",
            )
        }
        for r in records
    ]
    (out / "report.json").write_text(json.dumps(report, indent=2))

    width = max(len(r["case"]) for r in records)
    print(
        f"{'case'.ljust(width)}  {'harness':<11} {'version':<8} {'predicted':<9} {'observed':<9} verdict"
    )
    for r in records:
        extra = (
            f"  ({r['note']})"
            if r["note"] and r["verdict"] in ("invalid", "skipped", "mismatch")
            else ""
        )
        print(
            f"{r['case'].ljust(width)}  {r['harness']:<11} {r['version']:<8} {r['predicted']!s:<9} {r['observed']!s:<9} {r['verdict']}{extra}"
        )
    counts: dict[str, int] = {}
    for r in records:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print()
    print(", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    tested: dict[str, list[str]] = {}
    for kind, _b, v in binaries:
        tested.setdefault(kind, []).append(v)
    rules = {r for r, _ in cases}
    for line in coverage(records, tested, rules, a.real_api, catalogue):
        print(line)
    print(f"report: {out / 'report.json'}")
    return 1 if counts.get("mismatch") or counts.get("invalid") else 0


if __name__ == "__main__":
    sys.exit(main())
