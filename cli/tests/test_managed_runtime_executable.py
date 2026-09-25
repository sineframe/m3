from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
from pathlib import Path

import pytest

from m3.runtime.core import detect_target


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def fake_runtime_server(tmp_path: Path):
    target = detect_target("opencode")
    system, arch = target.split("-", 2)[:2]
    suffix = f"{system}-{arch}"
    if system == "linux":
        _, _, libc, variant = target.split("-")
        if arch == "x64" and variant == "baseline":
            suffix += "-baseline"
        if libc == "musl":
            suffix += "-musl"
    asset = tmp_path / f"opencode-{suffix}.tar.gz"
    executable = b"#!/bin/sh\nprintf 'opencode 1.2.3\\n'\n"
    with tarfile.open(asset, "w:gz") as archive:
        info = tarfile.TarInfo("opencode")
        info.mode = 0o755
        info.size = len(executable)
        archive.addfile(info, io.BytesIO(executable))
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "tag_name": "v1.2.3",
                "assets": [
                    {
                        "name": asset.name,
                        "browser_download_url": "",
                        "digest": "sha256:" + digest,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    requests = {"manifest": 0, "asset": 0}
    request_lock = threading.Lock()

    class CountingHandler(_QuietHandler):
        def do_GET(self) -> None:
            with request_lock:
                if self.path == "/manifest.json":
                    requests["manifest"] += 1
                elif self.path == "/" + asset.name:
                    requests["asset"] += 1
            super().do_GET()

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        lambda *args, **kwargs: CountingHandler(
            *args, directory=str(tmp_path), **kwargs
        ),
    )
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    manifest["assets"][0]["browser_download_url"] = (
        f"http://127.0.0.1:{server.server_port}/{asset.name}"
    )
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/manifest.json", requests
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _m3(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "--project", "cli", "m3", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _receipt(root: Path, kind: str, version: str, target: str = "darwin-arm64") -> Path:
    path = root / kind / version / target / f"sha256-{kind}-{version}"
    path.mkdir(parents=True)
    (path / "receipt.json").write_text(
        json.dumps(
            {
                "provenance": {
                    "kind": kind,
                    "version": version,
                    "target": target,
                    "sha256": f"{kind}-{version}",
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def test_public_runtime_help_and_unknown_commands() -> None:
    result = _m3("runtime", "--help")
    assert result.returncode == 0
    assert "cache" in result.stdout
    result = _m3("runtime", "cache", "--help")
    assert result.returncode == 0
    assert "list" in result.stdout and "prune" in result.stdout
    result = _m3("runtime", "cache", "list", "--unknown")
    assert result.returncode == 2
    result = _m3("runtime", "unknown")
    assert result.returncode == 2


def test_public_cache_list_is_sorted_and_prune_removes_all_harnesses(
    tmp_path: Path,
) -> None:
    for kind in ("pi", "codex", "opencode", "claude"):
        _receipt(tmp_path, kind, "1.0.0")
    result = _m3("runtime", "cache", "list", "--harness-cache-dir", str(tmp_path))
    assert result.returncode == 0, result.stderr
    entries = json.loads(result.stdout)
    assert [entry["kind"] for entry in entries] == [
        "claude",
        "codex",
        "opencode",
        "pi",
    ]
    expected_bytes = sum(
        file.stat().st_size for file in tmp_path.rglob("*") if file.is_file()
    )
    result = _m3("runtime", "cache", "prune", "--harness-cache-dir", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert f"4 entries, {expected_bytes} bytes removed" in result.stdout
    result = _m3("runtime", "cache", "list", "--harness-cache-dir", str(tmp_path))
    assert result.returncode == 0
    assert json.loads(result.stdout) == []


def test_public_prune_recovers_crashed_worker_lease(tmp_path: Path) -> None:
    entry = _receipt(tmp_path, "claude", "1.0.0")
    (entry / "lease-999999999-1").touch()  # Legacy empty marker.
    result = _m3("runtime", "cache", "prune", "--cache-dir", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "1 entries" in result.stdout
    assert not entry.exists()


def test_public_test_forwards_pytest_args_and_exit_code(tmp_path: Path) -> None:
    (tmp_path / "test_exit.py").write_text(
        "import pytest\n"
        "pytestmark = pytest.mark.m3(suite_name='cli-executable')\n"
        "def test_forwarded_marker():\n    assert False\n",
        encoding="utf-8",
    )
    result = _m3(
        "test",
        "--runtime=system",
        "--python",
        sys.executable,
        "--project-root",
        str(tmp_path),
        "--",
        "-q",
        "test_exit.py",
    )
    assert result.returncode == 1
    assert "1 failed" in result.stdout


def test_public_selection_errors_happen_before_pytest(tmp_path: Path) -> None:
    result = _m3(
        "test",
        "--runtime=system",
        "--harness",
        "opencode@1.18.30=model",
        "--python",
        sys.executable,
        "--project-root",
        str(tmp_path),
    )
    assert result.returncode == 2
    assert "managed" in result.stderr
    result = _m3(
        "test",
        "--runtime=managed",
        "--harness",
        "acp=model",
        "--python",
        sys.executable,
        "--project-root",
        str(tmp_path),
    )
    assert result.returncode == 2
    assert "ACP" in result.stderr


def test_public_managed_multiple_versions_collect_without_download(
    tmp_path: Path,
) -> None:
    (tmp_path / "test_matrix.py").write_text(
        "import pytest\n"
        "@pytest.mark.m3(suite_name='cli-executable')\n"
        "def test_selected(agent):\n    pass\n",
        encoding="utf-8",
    )
    result = _m3(
        "test",
        "--runtime=managed",
        "--harness",
        "opencode@1.18.30=model-a",
        "--harness",
        "opencode@1.18.31=model-b",
        "--python",
        sys.executable,
        "--project-root",
        str(tmp_path),
        "--",
        "--collect-only",
        "-q",
    )
    assert result.returncode == 0, result.stderr
    assert "2" in result.stdout
    assert "required evaluations blocked" not in result.stdout


def test_public_managed_pytest_xdist_shares_local_latest_pin(
    tmp_path: Path, fake_runtime_server: tuple[str, dict[str, int]]
) -> None:
    """Exercise the public CLI, xdist workers, local download, and pin file."""
    (tmp_path / "test_runtime.py").write_text(
        """
import asyncio
import os
from pathlib import Path

import pytest

from m3.runtime import RuntimeManager

pytestmark = pytest.mark.m3(suite_name="cli-executable")


def _acquire(worker):
    async def acquire():
        manager = RuntimeManager(
            os.environ["M3_HARNESS_CACHE_DIR"],
            Path.cwd(),
            os.environ["M3_INVOCATION_PIN_DIR"],
        )
        lease = await manager.acquire(
            "opencode",
            {
                "version": "latest",
                "manifest_url": os.environ["M3_TEST_MANIFEST_URL"],
            },
        )
        assert lease.executable.name == "opencode"
        await manager.close()

    asyncio.run(acquire())
    Path(os.environ["M3_TEST_WORKERS"]).joinpath(worker).touch()


def test_worker_a():
    _acquire(os.environ["PYTEST_XDIST_WORKER"])


def test_worker_b():
    _acquire(os.environ["PYTEST_XDIST_WORKER"])
""",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    manifest_url, requests = fake_runtime_server
    environment["M3_TEST_MANIFEST_URL"] = manifest_url
    workers = tmp_path / "workers"
    workers.mkdir()
    environment["M3_TEST_WORKERS"] = str(workers)
    cache = tmp_path.parent / (tmp_path.name + "-cache")
    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "cli",
            "m3",
            "test",
            "--runtime=managed",
            "--harness-cache-dir",
            str(cache),
            "--python",
            sys.executable,
            "--project-root",
            str(tmp_path),
            "--",
            "-n",
            "2",
            "-q",
            "test_runtime.py",
        ],
        cwd=Path(__file__).parents[2],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "2 passed" in result.stdout
    assert {path.name for path in workers.iterdir()} == {"gw0", "gw1"}
    assert requests["manifest"] == 1
    assert requests["asset"] == 1
    assert len(tuple(cache.glob("opencode/*/*/sha256-*/receipt.json"))) == 1


def test_public_cache_environment_precedence(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["M3_HARNESS_CACHE_DIR"] = str(tmp_path)
    result = subprocess.run(
        ["uv", "run", "--project", "cli", "m3", "runtime", "cache", "list"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == []
