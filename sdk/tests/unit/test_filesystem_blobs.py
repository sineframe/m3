from __future__ import annotations

import gzip
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_pal.storage import (
    ArtifactNotFound,
    BlobIntegrityError,
    FilesystemBlobStore,
    StorageError,
)


def test_blob_write_is_content_addressed_and_verified(tmp_path: Path) -> None:
    store = FilesystemBlobStore(tmp_path / "blobs")
    content = b"large stable evidence\x00" * 4096
    digest = hashlib.sha256(content).hexdigest()
    record = store.put(content, sha256=digest, size_bytes=len(content))
    assert record.sha256 == digest
    assert record.size_bytes == len(content)
    assert record.path == store.path_for(digest)
    assert store.read(digest, size_bytes=len(content)) == content
    assert store.verify(digest, len(content)) == record


def test_existing_shared_blob_is_reused_and_gc_preserves_live_reference(
    tmp_path: Path,
) -> None:
    store = FilesystemBlobStore(tmp_path / "blobs")
    content = b"shared"
    first = store.put(content)
    second = store.put(content)
    assert first.path == second.path
    assert store.garbage_collect({first.sha256: 2}) == ()
    assert store.read(first.sha256, size_bytes=first.size_bytes) == content
    assert store.garbage_collect({first.sha256: 1}) == ()
    assert store.garbage_collect({first.sha256: 0}) == (first.sha256,)
    with pytest.raises(ArtifactNotFound):
        store.read(first.sha256, size_bytes=first.size_bytes)


def test_corrupt_and_missing_blobs_are_detected_and_not_gc_swept(
    tmp_path: Path,
) -> None:
    store = FilesystemBlobStore(tmp_path / "blobs")
    record = store.put(b"integrity")
    record.path.write_bytes(b"not gzip")
    with pytest.raises(BlobIntegrityError):
        store.read(record.sha256, size_bytes=record.size_bytes)
    # GC is fail-closed for corrupt data: it must not erase evidence of a
    # metadata/filesystem mismatch.
    assert store.garbage_collect({}) == ()
    record.path.unlink()
    with pytest.raises(ArtifactNotFound):
        store.read(record.sha256, size_bytes=record.size_bytes)


def test_expected_metadata_mismatch_does_not_publish_a_file(tmp_path: Path) -> None:
    store = FilesystemBlobStore(tmp_path / "blobs")
    content = b"content"
    with pytest.raises(BlobIntegrityError):
        store.put(content, sha256="0" * 64)
    with pytest.raises(BlobIntegrityError):
        store.put(content, size_bytes=len(content) + 1)
    assert store.iter_records() == ()


def test_gc_has_no_implicit_retention_or_cleanup(tmp_path: Path) -> None:
    store = FilesystemBlobStore(tmp_path / "blobs")
    record = store.put(b"retained until explicit gc")
    # Re-instantiating the store does not silently delete an unreferenced file.
    reopened = FilesystemBlobStore(tmp_path / "blobs")
    assert (
        reopened.read(record.sha256, size_bytes=record.size_bytes)
        == b"retained until explicit gc"
    )
    assert reopened.garbage_collect({}) == (record.sha256,)


def test_blob_store_uses_private_modes_and_explicit_temp_cleanup(
    tmp_path: Path,
) -> None:
    store = FilesystemBlobStore(tmp_path / "blobs")
    record = store.put(b"private")
    assert store.root.stat().st_mode & 0o777 == 0o700
    assert record.path.stat().st_mode & 0o777 == 0o600
    temporary = record.path.parent / ".mcp-pal-blob-crashed.tmp"
    temporary.write_bytes(b"partial")
    assert temporary.exists()
    assert store.cleanup_temporary_files() == (temporary,)
    assert not temporary.exists()


def test_symlinked_digest_directory_and_file_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "blobs"
    store = FilesystemBlobStore(root)
    content = b"symlink attack"
    digest = hashlib.sha256(content).hexdigest()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / digest[:2]).symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageError):
        store.put(content)

    (root / digest[:2]).unlink()
    (root / digest[:2] / digest[2:4]).mkdir(parents=True)
    (root / digest[:2] / digest[2:4] / f"{digest}.gz").symlink_to(outside / "target")
    with pytest.raises(StorageError):
        store.put(content)


def test_symlinked_blob_root_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "real-blobs"
    target.mkdir()
    link = tmp_path / "blobs"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(StorageError, match="symlink"):
        FilesystemBlobStore(link)


def test_read_bound_rejects_gzip_bomb_without_allocating_unbounded_output(
    tmp_path: Path,
) -> None:
    store = FilesystemBlobStore(tmp_path / "blobs", max_read_bytes=1024)
    content = b"A" * 100_000
    digest = hashlib.sha256(content).hexdigest()
    path = store.path_for(digest)
    path.parent.mkdir(parents=True)
    path.write_bytes(gzip.compress(content, mtime=0))
    with pytest.raises(BlobIntegrityError):
        store.read(digest, size_bytes=len(content))
    with pytest.raises(BlobIntegrityError):
        store.read(digest)


@pytest.mark.process_lifecycle
def test_concurrent_process_writes_publish_one_valid_shared_blob(
    tmp_path: Path,
) -> None:
    root = str(tmp_path / "blobs")
    script = (
        "import sys; "
        "from mcp_pal.storage import FilesystemBlobStore; "
        "print(FilesystemBlobStore(sys.argv[1]).put(b'multiprocess-shared').sha256)"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, root],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
        )
        for _ in range(8)
    ]
    digests: list[str] = []
    try:
        for process in processes:
            try:
                stdout, stderr = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
                pytest.fail("multiprocess blob writer exceeded its bounded lifetime")
            assert process.returncode == 0, stderr
            digests.append(stdout.strip())
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    assert len(set(digests)) == 1
    store = FilesystemBlobStore(root)
    digest = digests[0]
    assert (
        store.read(digest, size_bytes=len(b"multiprocess-shared"))
        == b"multiprocess-shared"
    )
