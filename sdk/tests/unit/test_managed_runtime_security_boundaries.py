"""Security regression coverage for managed runtime cache acquisition."""

from __future__ import annotations

import io
import json
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from m3.runtime import (
    RuntimeValidationError,
    core,
    list_cache,
    prune_cache,
    resolve_cache_root,
)


def _zip(entries: list[tuple[str, bytes, int | None]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            if mode is not None:
                info.external_attr = mode << 16
            archive.writestr(info, data)
    return output.getvalue()


def _tar_link() -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../outside"
        archive.addfile(info)
    return output.getvalue()


@pytest.mark.parametrize(
    "payload",
    [
        _zip([("../escape", b"x", None)]),
        _zip([("same", b"a", None), ("same", b"b", None)]),
        _zip([("device", b"x", stat.S_IFCHR)]),
        _tar_link(),
    ],
    ids=["traversal", "duplicate", "device", "symlink"],
)
def test_archive_members_cannot_escape_or_use_unsafe_types(
    tmp_path: Path, payload: bytes
) -> None:
    source = tmp_path / "asset"
    source.write_bytes(payload)
    destination = tmp_path / "install"
    destination.mkdir()
    with pytest.raises(RuntimeValidationError):
        core.RuntimeManager._install(source, destination, "claude", "claude")
    assert not (tmp_path / "escape").exists()


def test_archive_expansion_ceiling_is_hard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = _zip([("claude", b"x" * 32, None)])
    source = tmp_path / "asset.zip"
    source.write_bytes(payload)
    destination = tmp_path / "install"
    destination.mkdir()
    original = core.CAPS["claude"]
    monkeypatch.setitem(core.CAPS, "claude", (1024, 16, 1))
    try:
        with pytest.raises(RuntimeValidationError, match="expands beyond"):
            core.RuntimeManager._install(source, destination, "claude", "claude")
    finally:
        monkeypatch.setitem(core.CAPS, "claude", original)


def test_corrupt_receipt_is_not_accepted_as_cache_hit(tmp_path: Path) -> None:
    entry = tmp_path / "cache" / "claude" / "1.2.3" / "target" / ("sha256-" + "a" * 64)
    entry.mkdir(parents=True)
    (entry / "receipt.json").write_text(json.dumps({"broken": True}), encoding="utf-8")
    assert not core.RuntimeManager._receipt_valid(entry)


@pytest.mark.parametrize("payload", ([], None, "invalid"))
def test_non_object_receipt_is_reported_as_corrupt(
    tmp_path: Path, payload: object
) -> None:
    entry = tmp_path / "cache" / "claude" / "1.2.3" / "target" / ("sha256-" + "a" * 64)
    entry.mkdir(parents=True)
    (entry / "receipt.json").write_text(json.dumps(payload), encoding="utf-8")

    assert not core.RuntimeManager._receipt_valid(entry)
    assert list_cache(tmp_path / "cache") == [{"path": str(entry), "status": "corrupt"}]


@pytest.mark.asyncio
async def test_digest_mismatch_removes_staged_asset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    staged = tmp_path / "staged"
    staged.write_bytes(b"asset")
    monkeypatch.setattr(
        core.RuntimeManager,
        "_download_staged",
        staticmethod(lambda *_args, **_kwargs: (staged, 5, "b" * 64)),
    )
    manager = core.RuntimeManager(tmp_path / "cache", tmp_path / "project")
    with pytest.raises(RuntimeValidationError, match="sha256 mismatch"):
        await manager.acquire(
            "claude",
            {
                "version": "1.2.3",
                "url": "https://example.invalid/asset",
                "sha256": "a" * 64,
            },
        )
    assert not staged.exists()


def test_cache_root_never_resolves_inside_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(RuntimeValidationError):
        resolve_cache_root(project, project / "cache")


def test_github_token_only_reaches_metadata_host_and_not_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str | None]] = []

    class Opener:
        def __init__(self, redirect: object) -> None:
            self.redirect = redirect

        def open(self, request: object, timeout: int) -> object:
            assert timeout == 30
            assert isinstance(request, core.urllib.request.Request)
            seen.append((request.full_url, request.get_header("Authorization")))
            redirected = self.redirect.redirect_request(
                request, None, 302, "Found", {}, "https://example.com/asset"
            )
            assert redirected is not None
            assert redirected.get_header("Authorization") is None
            return object()

    monkeypatch.setenv("M3_GITHUB_TOKEN", "secret-token")
    monkeypatch.setattr(
        core.urllib.request, "build_opener", lambda handler: Opener(handler)
    )
    core.RuntimeManager._open_download(
        "https://api.github.com/repos/org/repo/releases/latest"
    )
    core.RuntimeManager._open_download("https://api.github.com.evil.test/asset")
    core.RuntimeManager._open_download("https://example.com/asset")
    assert seen == [
        (
            "https://api.github.com/repos/org/repo/releases/latest",
            "Bearer secret-token",
        ),
        ("https://api.github.com.evil.test/asset", None),
        ("https://example.com/asset", None),
    ]


def test_prune_keeps_active_leases_and_removes_free_entries(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    leased = root / "claude" / "1.0.0" / "target" / "sha256-leased"
    free = root / "pi" / "1.0.0" / "target" / "sha256-free"
    for entry in (leased, free):
        entry.mkdir(parents=True)
        (entry / "receipt.json").write_text("{}", encoding="utf-8")
    (leased / "lease-active").write_text("1", encoding="ascii")
    removed = prune_cache(root, max_age=-1)
    assert free in removed
    assert leased.exists()
