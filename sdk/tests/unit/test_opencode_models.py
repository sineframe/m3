from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from m3.runtime.core import RuntimeManager, RuntimeValidationError, prune_cache
from m3.runtime.opencode_models import models_directory, prepare_models

CATALOG = (Path(__file__).parents[1] / "fixtures" / "opencode_models.json").read_bytes()


def test_snapshot_is_frozen_and_independent_of_runtime_pruning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloads: list[str] = []

    def download(url: str, kind: str) -> bytes:
        downloads.append(url)
        return CATALOG

    monkeypatch.setattr(RuntimeManager, "_download", download)
    directory = models_directory(tmp_path / "results.sqlite", "run-one")
    snapshot, digest = prepare_models(directory)
    assert snapshot.read_bytes() == CATALOG
    assert digest == hashlib.sha256(CATALOG).hexdigest()
    monkeypatch.setattr(
        RuntimeManager, "_download", lambda *_: pytest.fail("refetched")
    )
    assert prepare_models(directory) == (snapshot, digest)
    cache = tmp_path / "runtime-cache"
    cache.mkdir()
    prune_cache(cache)
    assert snapshot.read_bytes() == CATALOG
    assert models_directory(tmp_path / "results.sqlite", "run-two") != directory


def test_explicit_catalog_is_copied_without_network_and_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.json"
    source.write_bytes(CATALOG)
    monkeypatch.setattr(RuntimeManager, "_download", lambda *_: pytest.fail("network"))
    snapshot, digest = prepare_models(tmp_path / "run", str(source))
    source.write_bytes(b'{"changed":true}')
    assert snapshot.read_bytes() == CATALOG
    assert prepare_models(tmp_path / "run", str(source)) == (snapshot, digest)


@pytest.mark.parametrize("raw", (b"not json", b"[]", b"{}", b'{"error":"unavailable"}'))
def test_invalid_download_is_not_published_and_retry_can_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    monkeypatch.setattr(RuntimeManager, "_download", lambda *_: raw)
    with pytest.raises(RuntimeValidationError, match="OpenCode model catalog"):
        prepare_models(tmp_path)
    assert not (tmp_path / "opencode-models.json").exists()
    assert not tuple(tmp_path.glob("*.lock"))
    monkeypatch.setattr(RuntimeManager, "_download", lambda *_: CATALOG)
    assert prepare_models(tmp_path)[0].read_bytes() == CATALOG


@pytest.mark.parametrize("field", ("env", "models", "model", "limit"))
def test_invalid_provider_or_model_is_rejected_and_corrected_source_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    catalog = json.loads(CATALOG)
    provider = catalog["opencode"]
    if field == "env":
        del provider["env"]
    elif field == "models":
        provider["models"] = []
    elif field == "model":
        provider["models"]["fixture"] = "unavailable"
    else:
        del provider["models"]["fixture"]["limit"]
    source = tmp_path / "source.json"
    source.write_text(json.dumps(catalog))
    monkeypatch.setattr(RuntimeManager, "_download", lambda *_: pytest.fail("network"))
    directory = tmp_path / "run"
    with pytest.raises(RuntimeValidationError, match="OpenCode model catalog"):
        prepare_models(directory, str(source))
    assert not (directory / "opencode-models.json").exists()
    source.write_bytes(CATALOG)
    assert prepare_models(directory, str(source))[0].read_bytes() == CATALOG


def test_failed_download_does_not_publish_a_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: object) -> bytes:
        raise RuntimeValidationError("runtime download failed")

    monkeypatch.setattr(RuntimeManager, "_download", fail)
    with pytest.raises(RuntimeValidationError, match="catalog download failed"):
        prepare_models(tmp_path)
    assert not (tmp_path / "opencode-models.json").exists()


def test_valid_catalog_can_omit_temperature_and_preserves_extra_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = json.loads(CATALOG)
    model = catalog["opencode"]["models"]["fixture"]
    del model["temperature"]
    model["cost"] = {"input": 0.1, "output": 0.2}
    raw = json.dumps(catalog).encode()
    monkeypatch.setattr(RuntimeManager, "_download", lambda *_: raw)
    assert prepare_models(tmp_path)[0].read_bytes() == raw


def test_invalid_saved_catalog_can_be_replaced_by_a_corrected_explicit_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "opencode-models.json"
    snapshot.write_bytes(b'{"error":"unavailable"}')
    monkeypatch.setattr(RuntimeManager, "_download", lambda *_: pytest.fail("network"))
    with pytest.raises(RuntimeValidationError, match="invalid provider or model"):
        prepare_models(tmp_path)
    source = tmp_path / "corrected.json"
    source.write_bytes(CATALOG)
    assert prepare_models(tmp_path, str(source)) == (
        snapshot.resolve(),
        hashlib.sha256(CATALOG).hexdigest(),
    )
    assert snapshot.read_bytes() == CATALOG


@pytest.mark.process_lifecycle
def test_concurrent_processes_download_once_and_read_complete_identical_snapshot(
    tmp_path: Path,
) -> None:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            time.sleep(0.2)
            self.send_response(200)
            self.send_header("Content-Length", str(len(CATALOG)))
            self.end_headers()
            self.wfile.write(CATALOG)

        def log_message(self, *_: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script = (
        "import json,sys; from pathlib import Path; "
        "from m3.runtime import opencode_models as m; "
        "m.MODELS_URL=sys.argv[2]; "
        "p,h=m.prepare_models(Path(sys.argv[1])); "
        "print(json.dumps([str(p),h,p.read_text()]))"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    children: list[subprocess.Popen[str]] = []
    try:
        for _ in range(4):
            children.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        script,
                        str(tmp_path),
                        f"http://127.0.0.1:{server.server_port}/models",
                    ],
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, stderr
            results.append(json.loads(stdout))
        assert requests == ["/models"]
        assert all(result == results[0] for result in results)
        assert results[0][2] == CATALOG.decode()
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
