from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SECRET = "m3-cli-secret-sentinel-7f2c"


def _cli_case(
    tmp_path: Path,
    source: str,
    env_text: str,
    *options: str,
    ambient: dict[str, str] | None = None,
):
    test_file = tmp_path / "test_cli_case.py"
    test_file.write_text(source, encoding="utf-8")
    env_file = tmp_path / "provider.env"
    env_file.write_text(env_text, encoding="utf-8")
    database = tmp_path / "results.sqlite"
    environment = os.environ.copy()
    for key in (
        "PAL_FILE_ONLY",
        "PAL_AMBIENT",
        "PAL_OTHER",
        "PAL_INTERPOLATED",
        "PAL_MARKED",
        "PAL_GLOBAL",
        "PAL_SCOPED",
        "M3_CLAUDE_MODEL",
    ):
        environment.pop(key, None)
    if ambient:
        environment.update(ambient)
    workspace = Path(__file__).parents[2]
    command = [
        sys.executable,
        "-m",
        "m3_cli",
        "test",
        "--python",
        sys.executable,
        "--results-db",
        str(database),
        "--env-file",
        str(env_file),
        *options,
        "--",
        str(test_file),
    ]
    source_path = os.pathsep.join(
        (
            str(workspace / "cli" / "src"),
            str(workspace / "sdk" / "src"),
        )
    )
    environment["PYTHONPATH"] = (
        source_path + os.pathsep + environment.get("PYTHONPATH", "")
    )
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    return result, database


def _assert_no_secret(result, database: Path, root: Path) -> None:
    argv = result.args if isinstance(result.args, list) else [str(result.args)]
    assert SECRET not in "\n".join(map(str, argv))
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr
    if database.exists():
        assert SECRET not in database.read_bytes().decode("utf-8", errors="ignore")
    reports = root / ".m3" / "reports"
    if reports.exists():
        for path in reports.rglob("*"):
            if path.is_file():
                assert SECRET not in path.read_text(encoding="utf-8", errors="ignore")


def test_env_file_reaches_child_without_interpolation_or_ambient_leak(
    tmp_path: Path,
) -> None:
    source = """
import os
import pytest
from m3 import StdioServer, UserMessage
pytestmark = pytest.mark.m3(agents=[{"harness": "opencode", "models": ["mystery/model"]}])
def test_child(agent):
    assert os.environ["PAL_FILE_ONLY"] == "file-value"
    assert os.environ["PAL_AMBIENT"] == "ambient-value"
    assert os.environ["PAL_OTHER"] == "other-value"
    assert os.environ["PAL_INTERPOLATED"] == "literal-${PAL_OTHER}"
    assert "PAL_NO_IMPLICIT" not in os.environ
    assert agent._spec(UserMessage(content="probe"), server=StdioServer(name="s", command="echo")).harness.credential_references == {}
"""
    (tmp_path / ".env").write_text("PAL_NO_IMPLICIT=ambient-dotenv\n", encoding="utf-8")
    result, database = _cli_case(
        tmp_path,
        source,
        f"PAL_FILE_ONLY=file-value\nPAL_AMBIENT=file-value\nPAL_OTHER=other-value\nPAL_INTERPOLATED=literal-${{PAL_OTHER}}\nPAL_SECRET={SECRET}\n",
        ambient={"PAL_AMBIENT": "ambient-value"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert os.environ.get("PAL_FILE_ONLY") is None
    _assert_no_secret(result, database, tmp_path)


def test_env_file_passes_unknown_prefixed_variable_through_fixture_setup(
    tmp_path: Path,
) -> None:
    source = """
import os
def test_child(m3_kit):
    assert os.environ["M3_CLAUDE_MODEL"] == "claude-sonnet-5"
    assert m3_kit.config.telemetry_enabled is False
"""
    result, database = _cli_case(
        tmp_path,
        source,
        "M3_CLAUDE_MODEL=claude-sonnet-5\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert os.environ.get("M3_CLAUDE_MODEL") is None
    _assert_no_secret(result, database, tmp_path)


def test_cli_and_marker_credential_precedence_is_scoped(tmp_path: Path) -> None:
    source = """
import pytest
from m3 import StdioServer, UserMessage
pytestmark = pytest.mark.m3(agents=[{"harness": "opencode", "models": ["vendor/model"], "credential_env": {"VENDOR_API_KEY": "PAL_MARKED"}}])
def test_child(agent):
    spec = agent._spec(UserMessage(content="probe"), server=StdioServer(name="s", command="echo"))
    assert spec.harness.credential_references["VENDOR_API_KEY"].name == "PAL_SCOPED"
"""
    result, database = _cli_case(
        tmp_path,
        source,
        f"PAL_MARKED=marked\nPAL_GLOBAL=global\nPAL_SCOPED=scoped\nPAL_SECRET={SECRET}\n",
        "--credential-env",
        "VENDOR_API_KEY=PAL_GLOBAL",
        "--credential-env",
        "opencode:VENDOR_API_KEY=PAL_SCOPED",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_no_secret(result, database, tmp_path)


def test_known_mapping_and_unknown_provider_do_not_guess_credentials(
    tmp_path: Path,
) -> None:
    source = """
import pytest
from m3 import StdioServer, UserMessage
pytestmark = pytest.mark.m3(agents=[{"harness": "opencode", "models": ["openai/gpt"]}])
def test_known(agent):
    spec = agent._spec(UserMessage(content="probe"), server=StdioServer(name="s", command="echo"))
    assert spec.harness.credential_references["OPENAI_API_KEY"].name == "PAL_FILE_ONLY"
"""
    result, database = _cli_case(
        tmp_path,
        source,
        f"PAL_FILE_ONLY=known-source\nPAL_SECRET={SECRET}\n",
        "--credential-env",
        "OPENAI_API_KEY=PAL_FILE_ONLY",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_no_secret(result, database, tmp_path)

    unknown = source.replace("openai/gpt", "unknown/gpt").replace(
        'spec.harness.credential_references["OPENAI_API_KEY"].name == "PAL_FILE_ONLY"',
        "spec.harness.credential_references == {}",
    )
    unknown_root = tmp_path / "unknown"
    unknown_root.mkdir()
    result, database = _cli_case(
        unknown_root, unknown, f"PAL_FILE_ONLY=known-source\nPAL_SECRET={SECRET}\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_no_secret(result, database, unknown_root)


def test_missing_explicit_credential_names_source_without_value(tmp_path: Path) -> None:
    source = """
import pytest
from m3 import StdioServer, UserMessage
pytestmark = pytest.mark.m3(agents=[{"harness": "opencode", "models": ["vendor/model"]}])
def test_missing(agent):
    agent._spec(UserMessage(content="probe"), server=StdioServer(name="s", command="echo"))
"""
    result, database = _cli_case(
        tmp_path,
        source,
        "",
        "--credential-env",
        "VENDOR_API_KEY=PAL_MISSING_SOURCE",
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "PAL_MISSING_SOURCE" in output
    _assert_no_secret(result, database, tmp_path)


def test_selected_native_credential_is_used_and_redacted_from_persisted_run(
    tmp_path: Path,
) -> None:
    source = """
import json
import pytest
from m3 import StdioServer, UserMessage
from m3.agent_session import HarnessAdapter
from m3.harness import HarnessAdapterRegistry
from m3.types import TextContent, TurnResponse

class EchoAdapter(HarnessAdapter):
    async def start(self, _spec):
        return None
    async def send(self, message, *, timeout=None, metadata=None):
        return TurnResponse(content=(TextContent(text="deterministic"),))
    async def close(self):
        return None

pytestmark = pytest.mark.m3(agents=[{"harness": "opencode", "models": ["vendor/model"]}])
def test_native(agent, m3_kit):
    m3_kit._adapter_registry = HarnessAdapterRegistry({"opencode": lambda _harness: EchoAdapter()})
    spec = agent._spec(UserMessage(content="probe"), server=StdioServer(name="s", command="echo"), tools=[])
    assert spec.harness.credential_references["VENDOR_API_KEY"].name == "PAL_SECRET"
    result = m3_kit.run(spec)
    assert result.snapshot.outcome.value == "completed"
"""
    result, database = _cli_case(
        tmp_path,
        source,
        f"PAL_SECRET={SECRET}\n",
        "--credential-env",
        "VENDOR_API_KEY=PAL_SECRET",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_no_secret(result, database, tmp_path)
    from m3.storage import SQLiteExecutionStore

    store = SQLiteExecutionStore(database)
    try:
        run = store.list_test_runs()[0]
        attempts = store.list_test_results(str(run["run_id"]))
        execution_ids = [
            eid for attempt in attempts for eid in attempt["execution_ids"]
        ]
        assert execution_ids
        for execution_id in execution_ids:
            spec = store.get_execution_spec(execution_id)
            assert spec is not None
            assert (
                spec.harness.credential_references["VENDOR_API_KEY"].name
                == "PAL_SECRET"
            )
            assert SECRET not in json.dumps(
                spec.model_dump(mode="json"), sort_keys=True
            )
            report = store.get_report(execution_id)
            assert SECRET not in json.dumps(
                report.model_dump(mode="json"), sort_keys=True
            )
        from fastapi.testclient import TestClient

        from m3_app.api.app import create_app
        from m3_app.settings import Settings

        with TestClient(
            create_app(Settings(database_path=str(database))),
            base_url="http://127.0.0.1",
            client=("127.0.0.1", 50000),
        ) as client:
            for execution_id in execution_ids:
                response = client.get(f"/api/v2/executions/{execution_id}/report")
                assert response.status_code == 200
                assert SECRET not in response.text
    finally:
        store.close()
