"""Run managed Codex/Pi examples with real runtime code and a local release feed."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).parents[3]
_SDK = _ROOT / "sdk"
_FIXTURES = _SDK / "tests" / "fixtures"
_RUNTIME_FEED_PATH = _FIXTURES / "docs_runtime_feed.py"
_RUNTIME_FEED_SPEC = importlib.util.spec_from_file_location(
    "docs_runtime_feed", _RUNTIME_FEED_PATH
)
if _RUNTIME_FEED_SPEC is None or _RUNTIME_FEED_SPEC.loader is None:
    raise RuntimeError("could not load the local runtime feed fixture")
_RUNTIME_FEED_MODULE = importlib.util.module_from_spec(_RUNTIME_FEED_SPEC)
_RUNTIME_FEED_SPEC.loader.exec_module(_RUNTIME_FEED_MODULE)
LocalRuntimeFeed = _RUNTIME_FEED_MODULE.LocalRuntimeFeed

PIN_A = "0.156.1"
PIN_B = "0.155.1"
MODEL = "fixture-codex"

pytestmark = [
    pytest.mark.process_lifecycle,
    pytest.mark.skipif(
        os.name == "nt",
        reason="the fake managed-runtime archives use POSIX shell wrappers",
    ),
]


def _copy_project(project_id: str, destination: Path) -> Path:
    source = _SDK / "examples" / "docs" / project_id
    manifest = json.loads((source / "example.json").read_text(encoding="utf-8"))
    destination.mkdir()
    for relative_name in manifest["files"]:
        source_file = source / relative_name
        target_file = destination / relative_name
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)
    return destination


def _environment(
    tmp_path: Path,
    *,
    manifest_feed_url: str,
    marker_path: Path,
    versions: tuple[str, ...] = (PIN_A,),
) -> dict[str, str]:
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    home.mkdir(exist_ok=True)
    codex_home.mkdir(exist_ok=True)
    environment = {
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), os.defpath)),
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(_FIXTURES),
        "PYTEST_PLUGINS": "docs_runtime_feed_plugin",
        "M3_DOCS_RUNTIME_FEED_URL": manifest_feed_url,
        "M3_DOCS_MCP_WIRE_MARKER": str(marker_path),
        "M3_HARNESS_CACHE_DIR": str(tmp_path / "runtime-cache"),
        "M3_DOCS_CODEX_MODEL": MODEL,
        "M3_DOCS_CODEX_VERSION": versions[0],
        "M3_DOCS_CODEX_VERSION_A": versions[0],
        "M3_DOCS_CODEX_VERSION_B": versions[1] if len(versions) > 1 else PIN_B,
        "M3_DOCS_PYTHON": sys.executable,
    }
    return environment


def _wire_calls(marker_path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in marker_path.read_text(encoding="utf-8").splitlines()
    ]


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=(os.name == "posix"),
    )
    try:
        output, _ = process.communicate(timeout=240)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            output, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            output, _ = process.communicate(timeout=5)
        raise AssertionError(f"timed out: {command!r}\n{output[-12_000:]}") from None
    assert process.returncode == 0, output[-12_000:]
    return output


def _assert_tool_calls(marker_path: Path, expected_count: int) -> None:
    calls = _wire_calls(marker_path)
    assert len(calls) == expected_count
    assert all(call["method"] == "tools/call" for call in calls)
    assert all(call["name"] == "shipping_quote" for call in calls)
    assert all(call["arguments"] == {"weight_kg": 2, "zone": "local"} for call in calls)
    assert all(
        call["result"].get(
            "structuredContent", call["result"].get("structured_content")
        )
        == {"amount": 9.0, "currency": "USD"}
        for call in calls
    )


@pytest.fixture
def local_runtime_feed(tmp_path: Path):
    marker_path = tmp_path / "codex-mcp-wire.jsonl"
    with LocalRuntimeFeed((PIN_A, PIN_B), marker_path) as feed:
        yield feed, marker_path


def test_managed_runtime_example_uses_local_codex_protocol_and_real_mcp(
    tmp_path: Path, local_runtime_feed: tuple[Any, Path]
) -> None:
    feed, marker_path = local_runtime_feed
    project = _copy_project("agents-managed-runtimes", tmp_path / "managed-runtimes")
    environment = _environment(
        tmp_path,
        manifest_feed_url=feed.manifest_feed_url,
        marker_path=marker_path,
    )

    output = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "test_managed_runtime.py",
        ],
        cwd=project,
        env=environment,
    )
    assert "1 passed" in output
    _assert_tool_calls(marker_path, expected_count=1)
    assert feed.requests == [
        f"/manifest/codex/{PIN_A}",
        f"/asset/codex/{PIN_A}.zip",
    ]


def test_two_version_example_acquires_both_selected_pins_locally(
    tmp_path: Path, local_runtime_feed: tuple[Any, Path]
) -> None:
    feed, marker_path = local_runtime_feed
    project = _copy_project("agents-runtime-versions", tmp_path / "runtime-versions")
    environment = _environment(
        tmp_path,
        manifest_feed_url=feed.manifest_feed_url,
        marker_path=marker_path,
        versions=(PIN_A, PIN_B),
    )

    output = _run(
        [sys.executable, "-m", "pytest", "-q", "test_sdk_versions.py"],
        cwd=project,
        env=environment,
    )
    assert "1 passed" in output
    _assert_tool_calls(marker_path, expected_count=2)
    assert feed.requests == [
        f"/manifest/codex/{PIN_A}",
        f"/asset/codex/{PIN_A}.zip",
        f"/manifest/codex/{PIN_B}",
        f"/asset/codex/{PIN_B}.zip",
    ]


def test_cli_version_comparison_reports_each_pin_and_reuses_cache(
    tmp_path: Path, local_runtime_feed: tuple[Any, Path]
) -> None:
    feed, marker_path = local_runtime_feed
    project = _copy_project("agents-runtime-versions", tmp_path / "cli-versions")
    environment = _environment(
        tmp_path,
        manifest_feed_url=feed.manifest_feed_url,
        marker_path=marker_path,
        versions=(PIN_A, PIN_B),
    )
    cli = Path(sys.executable).with_name("m3")
    command = [
        str(cli),
        "test",
        "--python",
        sys.executable,
        "--runtime",
        "managed",
        "--harness",
        f"codex@{PIN_A}={MODEL}",
        "--harness",
        f"codex@{PIN_B}={MODEL}",
        "--",
        "-v",
        "-s",
        "test_versions.py",
    ]
    first = _run(command, cwd=project, env=environment)
    assert "2 passed" in first
    for version in (PIN_A, PIN_B):
        assert f"requested={version} resolved={version}" in first
        assert f"managed-{version}-trial-1" in first
    assert 'quote={"amount": 9.0, "currency": "USD"}' in first
    assert len(set(re.findall(r"execution=(\S+)", first))) == 2
    requests_after_first = tuple(feed.requests)
    second = _run(command, cwd=project, env=environment)
    assert "2 passed" in second
    for version in (PIN_A, PIN_B):
        assert f"Codex {version}: loaded from cache" in second
    assert tuple(feed.requests) == requests_after_first
    first_ids = set(re.findall(r"execution=(\S+)", first))
    second_ids = set(re.findall(r"execution=(\S+)", second))
    assert len(second_ids) == 2
    assert first_ids.isdisjoint(second_ids)
    _assert_tool_calls(marker_path, expected_count=4)


def test_cli_comparison_uses_two_codex_and_two_pi_versions(tmp_path: Path) -> None:
    pi_versions = ("0.85.0", "0.85.1")
    marker_path = tmp_path / "comparison-wire.jsonl"
    with LocalRuntimeFeed(
        marker_path=marker_path,
        kind_versions={"codex": (PIN_A, PIN_B), "pi": pi_versions},
    ) as feed:
        project = _copy_project("agents-runtime-versions", tmp_path / "cross-harness")
        environment = _environment(
            tmp_path,
            manifest_feed_url=feed.manifest_feed_url,
            marker_path=marker_path,
        )
        environment["M3_DOCS_PI_API_KEY"] = "dummy-local-provider-key"
        cli = Path(sys.executable).with_name("m3")
        command = [str(cli), "test", "--python", sys.executable, "--runtime", "managed"]
        for kind, versions, model in (
            ("codex", (PIN_A, PIN_B), MODEL),
            ("pi", pi_versions, "openai/fixture-pi"),
        ):
            for version in versions:
                command.extend(("--harness", f"{kind}@{version}={model}"))
        command.extend(
            (
                "--credential-env",
                "pi:OPENAI_API_KEY=M3_DOCS_PI_API_KEY",
                "--",
                "-v",
                "-s",
                "test_versions.py",
            )
        )
        output = _run(command, cwd=project, env=environment)
        assert "4 passed" in output
        assert len(set(re.findall(r"execution=(\S+)", output))) == 4
        for kind, versions in (("codex", (PIN_A, PIN_B)), ("pi", pi_versions)):
            for version in versions:
                assert f"{kind}: requested={version} resolved={version}" in output
        _assert_tool_calls(marker_path, expected_count=4)
        assert len(feed.requests) == 8


def test_cli_selected_pin_fixture_runs_from_manifest_copy(
    tmp_path: Path, local_runtime_feed: tuple[Any, Path]
) -> None:
    feed, marker_path = local_runtime_feed
    project = _copy_project("agents-managed-runtimes", tmp_path / "cli-fixture")
    environment = _environment(
        tmp_path,
        manifest_feed_url=feed.manifest_feed_url,
        marker_path=marker_path,
    )
    cli = Path(sys.executable).absolute().parent / "m3"
    assert cli.is_file(), "candidate m3 CLI must come from this branch environment"
    output = _run(
        [
            str(cli),
            "test",
            "--python",
            sys.executable,
            "--runtime",
            "managed",
            "--harness",
            f"codex@{PIN_A}={MODEL}",
            "--",
            "test_managed_runtime_fixture.py",
        ],
        cwd=project,
        env=environment,
    )
    assert "1 passed" in output
    _assert_tool_calls(marker_path, expected_count=1)
    assert feed.requests == [
        f"/manifest/codex/{PIN_A}",
        f"/asset/codex/{PIN_A}.zip",
    ]
