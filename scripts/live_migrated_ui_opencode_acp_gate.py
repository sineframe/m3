"""Manual, one-turn live gate for legacy profiles, bundled UI, ACP, and SQLite.

Run only with an explicit provider credential and opt-in::

    M3_RUN_LIVE_MIGRATED_OPENCODE=1 uv run --env-file .env --project cli \
      python scripts/live_migrated_ui_opencode_acp_gate.py

The browser submits one OpenCode ACP turn using two migrated saved profiles.
The gate reopens the database and checks the actual v2 profile, execution,
binding, turn, and tool-call records. It creates no new UI functionality.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from m3.storage import SQLiteExecutionStore

try:
    from scripts import live_ui_gate as support
except ModuleNotFoundError:
    import live_ui_gate as support

ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT.parent / "mcppal-ui"
SERVER_NAME = "example-mcp"
SERVER_PROFILE_ID = "legacy-live-server"
SERVER_REVISION_ID = "legacy-live-server-revision"
HARNESS_PROFILE_ID = "legacy-live-harness"
HARNESS_REVISION_ID = "legacy-live-harness-revision"
PROFILE_NAME = "Migrated shipping server"
HARNESS_NAME = "Migrated OpenCode ACP"
PROMPT = (
    "Use only the example-mcp shipping_quote MCP tool with weight_kg 2 and "
    "zone local. Return its USD quote; do not use shell, web, or other tools."
)
EXECUTION_MARKER = re.compile(r"MCP_PAL_LIVE_MIGRATED_EXECUTION_ID=([^\s]+)")
READY_TIMEOUT = 45.0
BROWSER_TIMEOUT = 120.0


class GateError(RuntimeError):
    """A bounded, value-free failure from the live gate."""


def _require_inputs() -> tuple[str, str]:
    if os.environ.get("M3_RUN_LIVE_MIGRATED_OPENCODE") != "1":
        raise GateError("set M3_RUN_LIVE_MIGRATED_OPENCODE=1 to opt in")
    credential = os.environ.get("OPENCODE_API_KEY")
    if not credential:
        raise GateError("OPENCODE_API_KEY is required for the live gate")
    configured_executable = os.environ.get("OPENCODE_EXECUTABLE") or "opencode"
    executable = shutil.which(configured_executable) or configured_executable
    if not executable or not Path(executable).is_file():
        raise GateError("OpenCode executable is unavailable")
    model = os.environ.get("M3_LIVE_OPENCODE_MODEL", "opencode/big-pickle")
    if not model or "/" not in model:
        raise GateError("M3_LIVE_OPENCODE_MODEL must be provider/model")
    return str(Path(executable).resolve()), model


def _seed_legacy_database(
    database: Path, *, executable: str, config_path: Path
) -> None:
    if not config_path.is_file():
        raise GateError("OpenCode configuration fixture is unavailable")
    fixture = ROOT / "sdk" / "examples" / "servers" / "example_mcp_server.py"
    server = {
        "mcpServers": {
            SERVER_NAME: {
                "command": sys.executable,
                "args": [str(fixture)],
                "cwd": str(fixture.parent),
                "trust": "sdk_loopback",
            }
        }
    }
    manifest = {
        "command": executable,
        "args": ["acp", "--pure"],
        "protocol": "acp",
        "protocol_version": 1,
        "env": {
            "OPENCODE_API_KEY": "${OPENCODE_API_KEY}",
            "OPENCODE_CONFIG": "${OPENCODE_GATE_CONFIG}",
        },
    }
    stamp = "2024-01-01T00:00:00+00:00"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE mcp_profiles (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
              archived BOOLEAN, current_revision_id TEXT,
              created_at TEXT, updated_at TEXT
            );
            CREATE TABLE mcp_profile_revisions (
              id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
              revision_number INTEGER NOT NULL, mcp_json TEXT NOT NULL,
              created_at TEXT
            );
            CREATE TABLE harness_profiles (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
              archived BOOLEAN, current_revision_id TEXT,
              created_at TEXT, updated_at TEXT
            );
            CREATE TABLE harness_profile_revisions (
              id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
              revision_number INTEGER NOT NULL, manifest TEXT NOT NULL,
              trusted_unsandboxed BOOLEAN, created_at TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO mcp_profiles VALUES (?,?,?,?,?,?,?)",
            (
                SERVER_PROFILE_ID,
                PROFILE_NAME,
                "legacy",
                0,
                SERVER_REVISION_ID,
                stamp,
                stamp,
            ),
        )
        connection.execute(
            "INSERT INTO mcp_profile_revisions VALUES (?,?,?,?,?)",
            (SERVER_REVISION_ID, SERVER_PROFILE_ID, 1, json.dumps(server), stamp),
        )
        connection.execute(
            "INSERT INTO harness_profiles VALUES (?,?,?,?,?,?,?)",
            (
                HARNESS_PROFILE_ID,
                HARNESS_NAME,
                "legacy",
                0,
                HARNESS_REVISION_ID,
                stamp,
                stamp,
            ),
        )
        connection.execute(
            "INSERT INTO harness_profile_revisions VALUES (?,?,?,?,?,?)",
            (
                HARNESS_REVISION_ID,
                HARNESS_PROFILE_ID,
                1,
                json.dumps(manifest),
                1,
                stamp,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _json_get(origin: str, path: str) -> Any:
    try:
        with urlopen(origin + path, timeout=5) as response:
            return json.load(response)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise GateError(f"could not read {path}") from exc


def _wait_for_migration(process: subprocess.Popen[bytes], origin: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT
    next_report = 0.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise GateError("the UI application exited during migration")
        elapsed = READY_TIMEOUT - (deadline - time.monotonic())
        if time.monotonic() >= next_report:
            print(
                f"gate: waiting for migrated app readiness ({elapsed:.0f}s)", flush=True
            )
            next_report = time.monotonic() + 5
        try:
            profiles = _json_get(origin, "/api/v2/profiles")
            harnesses = _json_get(origin, "/api/v2/harness-profiles")
            capabilities = _json_get(origin, "/api/v2/capabilities")
        except GateError:
            time.sleep(0.2)
            continue
        if not any(item.get("id") == SERVER_PROFILE_ID for item in profiles):
            raise GateError("legacy server profile was not exposed by v2")
        if not any(item.get("id") == HARNESS_PROFILE_ID for item in harnesses):
            raise GateError("legacy harness profile was not exposed by v2")
        selected = next(
            (
                item
                for item in capabilities.get("harnesses", [])
                if item.get("selection_id") == f"profile:{HARNESS_PROFILE_ID}"
            ),
            None,
        )
        if not selected or not selected.get("ready"):
            raise GateError("migrated OpenCode ACP profile is not ready")
        return
    raise GateError("the UI application did not become ready after migration")


def _assert_sqlite(
    database: Path,
    execution_id: str,
    *,
    expected_tool: str = "shipping_quote",
    forbidden_secret: str | None = None,
) -> None:
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        for table, profile_id, revision_id in (
            ("server", SERVER_PROFILE_ID, SERVER_REVISION_ID),
            ("harness", HARNESS_PROFILE_ID, HARNESS_REVISION_ID),
        ):
            profile = connection.execute(
                f"SELECT current_revision_id FROM v2_{table}_profiles WHERE id=?",
                (profile_id,),
            ).fetchone()
            revision = connection.execute(
                f"SELECT revision_number FROM v2_{table}_profile_revisions WHERE id=? AND profile_id=?",
                (revision_id, profile_id),
            ).fetchone()
            if profile != (revision_id,) or revision != (1,):
                raise GateError(f"migrated {table} profile/revision is missing")
        execution = connection.execute(
            "SELECT snapshot_json FROM v2_executions WHERE id=?", (execution_id,)
        ).fetchone()
        if not execution:
            raise GateError("live execution is missing from v2_executions")
        snapshot = json.loads(execution[0])
        if (
            snapshot.get("lifecycle") != "finished"
            or snapshot.get("outcome") != "completed"
        ):
            raise GateError("stored live execution did not complete")
        server = connection.execute(
            "SELECT profile_id,revision_id FROM v2_execution_server_bindings WHERE execution_id=?",
            (execution_id,),
        ).fetchone()
        harness = connection.execute(
            "SELECT profile_id,revision_id FROM v2_execution_harness_bindings WHERE execution_id=?",
            (execution_id,),
        ).fetchone()
        if server != (SERVER_PROFILE_ID, SERVER_REVISION_ID):
            raise GateError("stored server binding lost migrated revision provenance")
        if harness != (HARNESS_PROFILE_ID, HARNESS_REVISION_ID):
            raise GateError("stored harness binding lost migrated revision provenance")
        turns = connection.execute(
            "SELECT COUNT(*) FROM v2_turns JOIN v2_sessions ON v2_turns.session_id=v2_sessions.id "
            "WHERE v2_sessions.execution_id=?",
            (execution_id,),
        ).fetchone()
        if not turns or turns[0] != 1:
            raise GateError("live execution did not persist exactly one turn")
        if forbidden_secret:
            durable_values = [
                row[0]
                for row in connection.execute(
                    "SELECT value_json FROM v2_harness_profile_revisions WHERE id=?",
                    (HARNESS_REVISION_ID,),
                )
            ]
            durable_values.extend(
                row[0]
                for row in connection.execute(
                    "SELECT specification_json FROM v2_executions WHERE id=?",
                    (execution_id,),
                )
            )
            durable_values.extend(
                row[0]
                for row in connection.execute(
                    "SELECT event_json FROM v2_events WHERE execution_id=?",
                    (execution_id,),
                )
            )
            if any(forbidden_secret in value for value in durable_values if value):
                raise GateError(
                    "provider credential crossed the durable SQLite boundary"
                )
    finally:
        connection.close()

    store = SQLiteExecutionStore(database)
    try:
        resolved = store.resolved_bindings(execution_id)
        if resolved["servers"][0]["revision_id"] != SERVER_REVISION_ID:
            raise GateError("SDK store did not reopen the migrated server binding")
        view = store.get_trace_view(execution_id)
        if view is None or not any(
            call.tool.value == expected_tool and call.server.value == SERVER_NAME
            for call in view.tool_calls
        ):
            raise GateError("persisted ACP trace has no expected MCP call")
    finally:
        store.close()


def _stop(process: subprocess.Popen[Any] | None) -> None:
    """Stop the app process safely, including its process group."""

    support.terminate_process(process)


def check() -> str:
    executable, model = _require_inputs()
    pinned = (ROOT / "cli" / "UI_REF").read_text(encoding="utf-8").strip()
    head = support._run(
        ["git", "rev-parse", "HEAD"],
        cwd=UI_ROOT,
        env=support.clean_environment(),
        timeout=15,
    ).stdout.strip()
    if head != pinned or not (UI_ROOT / "dist" / "index.html").is_file():
        raise GateError("the reviewed pinned UI build is unavailable")
    browser_env = support.clean_environment()
    playwright = support._check_browser_prerequisites(UI_ROOT, browser_env)

    root = Path(tempfile.mkdtemp(prefix="m3-live-migrated-")).resolve()
    process: subprocess.Popen[str] | None = None
    app_child: support.Child | None = None
    failure: BaseException | None = None
    try:
        database = root / "migrated.sqlite"
        config_path = root / "opencode.json"
        config_path.write_text(
            json.dumps(
                {
                    "model": model,
                    "autoupdate": False,
                    "tools": {"bash": False, "write": False},
                }
            ),
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        _seed_legacy_database(database, executable=executable, config_path=config_path)
        port = support._free_port()
        origin = f"http://127.0.0.1:{port}"
        # The SDK rejects unknown M3_* configuration keys. Gate-only
        # opt-in/browser variables must not enter the app process.
        server_env = {
            key: value
            for key, value in browser_env.items()
            if not key.startswith("M3_")
        }
        server_env["OPENCODE_API_KEY"] = os.environ["OPENCODE_API_KEY"]
        server_env["OPENCODE_GATE_CONFIG"] = str(config_path)
        server_code = (
            "import sys,uvicorn; from m3_cli.web import create_web_app; "
            "uvicorn.run(create_web_app(sys.argv[1],ui_dir=sys.argv[2]),"
            "host='127.0.0.1',port=int(sys.argv[3]),log_level='warning')"
        )
        app_log_path = root / "app.log"
        print(f"gate: artifacts will be retained on failure at {root}", flush=True)
        app_child = support.Child(
            [
                sys.executable,
                "-c",
                server_code,
                str(database),
                str(UI_ROOT / "dist"),
                str(port),
            ],
            cwd=ROOT,
            env=server_env,
            label="app",
            output_path=app_log_path,
        )
        process = app_child.process
        _wait_for_migration(process, origin)
        print("gate: migrated profiles and ACP capability are ready", flush=True)
        browser_env.update(
            {
                "MCP_PAL_LIVE_UI_BASE_URL": origin,
                "MCP_PAL_LIVE_MIGRATED_PROFILE_NAME": PROFILE_NAME,
                "MCP_PAL_LIVE_MIGRATED_SERVER_NAME": SERVER_NAME,
                "MCP_PAL_LIVE_MIGRATED_SERVER_PROFILE_ID": SERVER_PROFILE_ID,
                "MCP_PAL_LIVE_MIGRATED_HARNESS_PROFILE_ID": HARNESS_PROFILE_ID,
                "MCP_PAL_LIVE_MIGRATED_PROMPT": PROMPT,
            }
        )
        browser_log_path = root / "playwright.log"
        result = support._run_streaming(
            [str(playwright), "test", "e2e/live-migrated-opencode-acp.spec.ts"],
            cwd=UI_ROOT,
            env=browser_env,
            timeout=BROWSER_TIMEOUT,
            label="playwright",
            output_path=browser_log_path,
        )
        match = EXECUTION_MARKER.search(result.stdout)
        if match is None:
            raise GateError("browser test did not report its live execution ID")
        execution_id = match.group(1)
        print(f"gate: browser execution completed: {execution_id}", flush=True)
        _assert_sqlite(
            database,
            execution_id,
            forbidden_secret=os.environ["OPENCODE_API_KEY"],
        )
        print("gate: reopened SQLite graph and redaction checks passed", flush=True)
        return execution_id
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _stop(process)
        if app_child is not None:
            app_child.join()
        if failure is None:
            shutil.rmtree(root)
        else:
            diagnostics = [f"reason: {failure}"]
            app_log = root / "app.log"
            if app_log.is_file():
                diagnostics.append("[app tail]")
                try:
                    diagnostics.append(app_log.read_text(encoding="utf-8")[-6000:])
                except OSError:
                    diagnostics.append("<app log unavailable>")
            (root / "gate-diagnostics.txt").write_text(
                support.safe_diagnostics("\n".join(diagnostics) + "\n", os.environ),
                encoding="utf-8",
            )
            print(
                f"gate: failure artifacts retained at {root}",
                file=sys.stderr,
                flush=True,
            )


def main() -> int:
    try:
        execution_id = check()
    except (GateError, support.GateFailure) as exc:
        print(f"live migrated-profile gate failed: {exc}", file=sys.stderr)
        return 1
    print(f"live migrated-profile gate passed: execution_id={execution_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
