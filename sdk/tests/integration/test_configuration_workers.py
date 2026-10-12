"""Real pytest workers inherit one invocation configuration."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="local POSIX bootstrap")


@contextmanager
def _pytest_process(
    arguments: list[str], directory: Path
) -> Iterator[subprocess.Popen[str]]:
    process = subprocess.Popen(
        [sys.executable, "-m", "pytest", "-p", "m3.pytest_plugin", *arguments],
        cwd=directory,
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        yield process
    finally:
        # Release fixture waits even when an assertion fails. Escalate against
        # the owned process group if pytest or its workers cannot finish.
        (directory / "release").touch()
        for action in (None, signal.SIGTERM, signal.SIGKILL):
            if action is not None:
                try:
                    os.killpg(process.pid, action)
                except ProcessLookupError:
                    pass
            try:
                process.communicate(timeout=5)
                break
            except subprocess.TimeoutExpired:
                if action == signal.SIGKILL:
                    raise


_WORKER_SOURCE = """
import os
import time
from pathlib import Path

import pytest

from m3.configuration import ConfigSource
from m3.sync_api import MCPTestKit
from m3.types import HarnessTimeouts

@pytest.fixture(scope="session", autouse=True)
def wait_for_source_deletion(request):
    root = Path(__file__).parent
    worker = request.config.workerinput["workerid"]
    (root / (worker + ".ready")).touch()
    deadline = time.monotonic() + 20
    while not (root / "release").exists():
        if time.monotonic() >= deadline:
            pytest.fail("controller did not release workers")
        time.sleep(0.01)

@pytest.mark.parametrize("trial", range(4))
def test_frozen_settings(trial, request):
    assert "--m3-config" not in request.config.invocation_params.args
    assert "--m3-config" not in request.config.workerinput["mainargv"]
    os.environ["M3_ARTIFACT_POLICY"] = "never"
    with MCPTestKit({"harness_timeouts": HarnessTimeouts(initialize_seconds=15)}) as kit:
        assert kit.config.artifact_policy == "always"
        assert kit.config.harness_timeouts.startup_seconds == 90
        assert kit.config.harness_timeouts.initialize_seconds == 15
        assert kit.config.harnesses.codex.permissions.profile == "PRIVATE-CANARY"
        assert kit.config.source_for("artifact_policy").source is ConfigSource.PROJECT
    assert not (Path(__file__).parent / "settings.toml").exists()
"""


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("M3_ARTIFACT_POLICY", None)
    environment.pop("M3_PROTOCOL_REVISION", None)
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    return environment


def test_default_workers_ignore_later_configuration_environment_changes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "test_default.py"
    source.write_text("""
import os
import pytest
from m3.sync_api import MCPTestKit

@pytest.mark.parametrize("trial", range(4))
def test_default(trial, request):
    os.environ["M3_ARTIFACT_POLICY"] = "never"
    with MCPTestKit() as kit:
        assert kit.config.artifact_policy == "failed"
        assert kit._literal_registry is None
    plugin = request.config.pluginmanager.get_plugin("m3-configuration")
    assert plugin._invocation._broker is None
""")
    with _pytest_process(["-n", "2", "-q", str(source)], tmp_path) as process:
        output, error = process.communicate(timeout=30)
        assert process.returncode == 0, output + error
        assert "4 passed" in output


def test_workers_keep_config_after_file_deletion(tmp_path: Path) -> None:
    configuration = tmp_path / "settings.toml"
    configuration.write_text(
        '[tool.m3]\nartifact_policy="always"\n'
        "[tool.m3.harness_timeouts]\nstartup_seconds=90\n"
        '[tool.m3.harnesses.codex.permissions]\nprofile="PRIVATE-CANARY"\n'
    )
    (tmp_path / "test_workers.py").write_text(_WORKER_SOURCE)
    with _pytest_process(
        [
            "--m3-config",
            str(configuration),
            "-n",
            "2",
            "-q",
            str(tmp_path / "test_workers.py"),
        ],
        tmp_path,
    ) as process:
        try:
            deadline = time.monotonic() + 25
            while len(list(tmp_path.glob("gw*.ready"))) < 2:
                if process.poll() is not None:
                    output, error = process.communicate()
                    pytest.fail(output + error)
                if time.monotonic() >= deadline:
                    pytest.fail("workers did not finish configuration bootstrap")
                time.sleep(0.01)
            configuration.unlink()
            (tmp_path / "release").touch()
            output, error = process.communicate(timeout=25)
            assert process.returncode == 0, output + error
            assert "4 passed" in output
            assert "PRIVATE-CANARY" not in output + error
        finally:
            (tmp_path / "release").touch()


def test_server_literals_use_private_worker_transfer(tmp_path: Path) -> None:
    source = tmp_path / "test_server_inputs.py"
    source.write_text("""
import json
import pytest
from m3._configuration_context import current_configuration
from m3.sync_api import MCPTestKit

@pytest.mark.parametrize("trial", range(4))
def test_inputs(trial, request):
    context = current_configuration()
    assert context.server_selections[0].args == ("SERVER-LITERAL-CANARY",)
    assert "SERVER-LITERAL-CANARY" not in json.dumps(request.config.workerinput)
    assert "SERVER-LITERAL-CANARY" not in str(request.config.invocation_params.args)
    assert request.config.getoption("--m3-server-selections") is None
    for _ in range(2):
        with MCPTestKit() as kit:
            assert kit._literal_registry is context.literal_registry
    assert context.literal_registry is not None
    assert context.server_selections[0].args == ("SERVER-LITERAL-CANARY",)
""")
    servers = json.dumps(
        [{"type": "stdio", "command": "python", "args": ["SERVER-LITERAL-CANARY"]}]
    )
    with _pytest_process(
        ["--m3-server-selections", servers, "-n", "2", "-q", str(source)], tmp_path
    ) as process:
        output, error = process.communicate(timeout=30)
        assert process.returncode == 0, output + error
        assert "4 passed" in output
        assert "SERVER-LITERAL-CANARY" not in output + error


def test_replacement_worker_receives_original_config(tmp_path: Path) -> None:
    configuration = tmp_path / "settings.toml"
    configuration.write_text(
        '[tool.m3]\nartifact_policy="always"\n'
        "[tool.m3.harness_timeouts]\nstartup_seconds=90\n"
        '[tool.m3.harnesses.codex.permissions]\nprofile="PRIVATE-CANARY"\n'
    )
    source = _WORKER_SOURCE.replace("range(4)", "range(12)").replace(
        '    os.environ["M3_ARTIFACT_POLICY"] = "never"',
        "    if trial == 0:\n"
        "        os._exit(19)\n"
        '    if request.config.workerinput["workerid"] in ("gw0", "gw1"):\n'
        "        deadline = time.monotonic() + 20\n"
        '        while not (Path(__file__).parent / "gw2.ready").exists():\n'
        '            assert time.monotonic() < deadline, "replacement did not start"\n'
        "            time.sleep(0.01)\n"
        '    os.environ["M3_ARTIFACT_POLICY"] = "never"',
    )
    source += '\n    (Path(__file__).parent / (request.config.workerinput["workerid"] + ".verified")).touch()\n'
    test_file = tmp_path / "test_replacement.py"
    test_file.write_text(source)
    with _pytest_process(
        ["--m3-config", str(configuration), "-n", "2", "-q", str(test_file)],
        tmp_path,
    ) as process:
        deadline = time.monotonic() + 25
        while len(list(tmp_path.glob("gw*.ready"))) < 2:
            if process.poll() is not None:
                output, error = process.communicate()
                pytest.fail(output + error)
            if time.monotonic() >= deadline:
                pytest.fail("workers did not finish configuration bootstrap")
            time.sleep(0.01)
        configuration.unlink()
        (tmp_path / "release").touch()
        output, error = process.communicate(timeout=30)
        assert process.returncode == 1, output + error
        assert "1 failed, 11 passed" in output
        assert (tmp_path / "gw2.verified").exists(), output + error
        assert "PRIVATE-CANARY" not in output + error
