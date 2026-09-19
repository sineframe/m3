"""Content-addressed filesystem blobs used by persistent stores.

The SQLite metadata store owns searchable metadata and the lifetime of a
reference.  This module owns only bytes on disk.  A blob is published only
after it has been completely written, flushed, fsynced, verified, and moved
into its digest-derived location.  In particular, callers can safely commit
SQLite metadata *after* :meth:`FilesystemBlobStore.put` returns.

No retention policy is applied implicitly.  ``garbage_collect`` is explicit
and receives the current reference counts (normally from one SQLite
transaction), so a shared blob is retained while any reference remains.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .ephemeral import ArtifactNotFound, BlobIntegrityError, StorageError

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class BlobRecord:
    """Verified metadata for one compressed content-addressed blob."""

    sha256: str
    size_bytes: int
    compressed_size_bytes: int
    path: Path


def _validate_digest(value: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(
            "blob digest must be a 64-character lowercase SHA-256 hex value"
        )
    return value


def _verify_compressed(
    compressed: bytes,
    digest: str,
    size_bytes: int,
    *,
    max_size_bytes: int | None = None,
) -> bytes:
    """Decompress with a hard output bound and verify digest/length."""
    if size_bytes < 0 or (max_size_bytes is not None and size_bytes > max_size_bytes):
        raise BlobIntegrityError("blob length exceeds configured read limit")
    output = bytearray()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
            while True:
                chunk = stream.read(min(1024 * 1024, size_bytes - len(output) + 1))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > size_bytes:
                    raise BlobIntegrityError("blob expands beyond recorded length")
    except (EOFError, OSError) as exc:
        raise BlobIntegrityError("compressed blob cannot be decompressed") from exc
    content = bytes(output)
    if len(content) != size_bytes or hashlib.sha256(content).hexdigest() != digest:
        raise BlobIntegrityError("blob hash or length does not match metadata")
    return content


class FilesystemBlobStore:
    """Atomic, compressed, content-addressed filesystem storage.

    The store never deletes blobs during normal writes or object cleanup.  A
    process restart is therefore safe: unreferenced files remain available to
    an explicit GC pass, while SQLite remains the source of truth for refs.
    """

    suffix = ".gz"

    def __init__(
        self, root: str | os.PathLike[str], *, max_read_bytes: int = 512 * 1024 * 1024
    ) -> None:
        if max_read_bytes < 0:
            raise ValueError("max_read_bytes must be non-negative")
        supplied_root = Path(root).expanduser()
        if supplied_root.is_symlink():
            raise StorageError("blob root must not be a symlink")
        # ``abspath`` normalizes ``..`` without following symlinks.  Keep the
        # configured root itself intact so an attacker cannot redirect writes
        # by replacing it with a symlink between startup and first use.
        self._root = Path(os.path.abspath(os.fspath(supplied_root)))
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            if self._root.is_symlink() or not self._root.is_dir():
                raise StorageError("blob root must be a directory")
            os.chmod(self._root, 0o700)
        except StorageError:
            raise
        except OSError:
            raise StorageError("blob root is unavailable") from None
        # Blob bytes can include redacted evidence and must not be readable by
        # other local users.  Tighten an existing application-owned directory
        # as well as newly-created directories.
        self._max_read_bytes = max_read_bytes

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, sha256: str) -> Path:
        digest = _validate_digest(sha256)
        first = self._root / digest[:2]
        second = first / digest[2:4]
        path = second / f"{digest}{self.suffix}"
        for directory in (first, second):
            try:
                if directory.is_symlink():
                    raise StorageError("blob directory must not be a symlink")
            except OSError as exc:
                raise StorageError("blob directory could not be inspected") from exc
        if path.is_symlink():
            raise StorageError("blob path must not be a symlink")
        resolved = path.resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:  # defensive even though digest is validated
            raise StorageError("blob path escaped store root") from exc
        return path

    def put(
        self,
        content: bytes,
        *,
        sha256: str | None = None,
        size_bytes: int | None = None,
    ) -> BlobRecord:
        """Atomically persist ``content`` and return verified metadata.

        ``sha256`` and ``size_bytes`` are optional expected values supplied by
        a metadata layer.  A mismatch fails before publication.  Existing
        digest paths are verified rather than silently accepted, which makes
        corruption observable to the caller.
        """

        if not isinstance(content, bytes):
            raise TypeError("blob content must be bytes")
        digest = hashlib.sha256(content).hexdigest()
        if sha256 is not None and _validate_digest(sha256) != digest:
            raise BlobIntegrityError("blob hash does not match content")
        if size_bytes is not None and (size_bytes < 0 or size_bytes != len(content)):
            raise BlobIntegrityError("blob length does not match content")

        path = self.path_for(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        if path.exists():
            compressed = self._read_compressed(path)
            _verify_compressed(
                compressed, digest, len(content), max_size_bytes=self._max_read_bytes
            )
            return BlobRecord(digest, len(content), len(compressed), path)

        compressed = gzip.compress(content, mtime=0)
        temporary: Path | None = None
        fd, temporary_name = tempfile.mkstemp(
            prefix=".m3-blob-", suffix=".tmp", dir=str(path.parent)
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(compressed)
                handle.flush()
                os.fsync(handle.fileno())
            # Verify the exact bytes that are about to become visible.  This
            # catches an interrupted/short write before os.replace.
            _verify_compressed(
                temporary.read_bytes(),
                digest,
                len(content),
                max_size_bytes=self._max_read_bytes,
            )
            try:
                # A hard-link publish is atomic and O_EXCL-like: unlike
                # os.replace it cannot overwrite a valid blob another worker
                # published between our existence check and this point.
                os.link(temporary, path)
            except FileExistsError:
                # Another process won the race.  Never replace a valid blob
                # and never hide a corrupt winner.
                _verify_compressed(
                    self._read_compressed(path),
                    digest,
                    len(content),
                    max_size_bytes=self._max_read_bytes,
                )
            else:
                temporary.unlink(missing_ok=True)
            self._fsync_directory(path.parent)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        # Read back the published file.  Metadata must not point at a partial
        # file even on unusual filesystems or after an inter-process race.
        published = self._read_compressed(path)
        _verify_compressed(
            published, digest, len(content), max_size_bytes=self._max_read_bytes
        )
        return BlobRecord(digest, len(content), len(published), path)

    put_blob = put

    def read(self, sha256: str, *, size_bytes: int | None = None) -> bytes:
        digest = _validate_digest(sha256)
        path = self.path_for(digest)
        try:
            compressed = self._read_compressed(path)
        except FileNotFoundError as exc:
            raise ArtifactNotFound("blob is missing") from exc
        if size_bytes is None:
            # Do not use gzip.decompress here: a corrupt or hostile gzip file
            # could otherwise allocate unbounded memory.  Determine its size
            # with the same bounded streaming reader used for verification.
            size_bytes = self._bounded_uncompressed_length(compressed)
        return _verify_compressed(
            compressed, digest, size_bytes, max_size_bytes=self._max_read_bytes
        )

    get = read
    read_blob = read

    def verify(self, sha256: str, size_bytes: int) -> BlobRecord:
        digest = _validate_digest(sha256)
        path = self.path_for(digest)
        try:
            compressed = self._read_compressed(path)
        except FileNotFoundError as exc:
            raise ArtifactNotFound("blob is missing") from exc
        _verify_compressed(
            compressed, digest, size_bytes, max_size_bytes=self._max_read_bytes
        )
        return BlobRecord(digest, size_bytes, len(compressed), path)

    def iter_records(self) -> tuple[BlobRecord, ...]:
        records: list[BlobRecord] = []
        for path in self._root.glob("[0-9a-f][0-9a-f]/[0-9a-f][0-9a-f]/*.gz"):
            name = path.name.removesuffix(self.suffix)
            if _DIGEST.fullmatch(name) is None:
                continue
            try:
                compressed = self._read_compressed(path)
                content = self._bounded_decompress(compressed)
                digest = hashlib.sha256(content).hexdigest()
                if digest != name:
                    continue
                records.append(BlobRecord(name, len(content), len(compressed), path))
            except (EOFError, OSError):
                continue
        return tuple(sorted(records, key=lambda item: item.sha256))

    def garbage_collect(
        self, references: Mapping[str, int] | Iterable[str]
    ) -> tuple[str, ...]:
        """Delete only explicitly unreferenced, valid blobs.

        Invalid files are retained and reported by the next verification pass;
        deleting them automatically could destroy forensic evidence and would
        make a metadata/data mismatch harder to diagnose.  ``references`` may
        be a digest→count mapping or an iterable of digests.  No automatic
        retention or age policy is applied.
        """

        if isinstance(references, Mapping):
            live = {
                _validate_digest(key) for key, count in references.items() if count > 0
            }
        else:
            live = {_validate_digest(key) for key in references}
        deleted: list[str] = []
        for path in self._root.glob("[0-9a-f][0-9a-f]/[0-9a-f][0-9a-f]/*.gz"):
            digest = path.name.removesuffix(self.suffix)
            if _DIGEST.fullmatch(digest) is None or digest in live:
                continue
            # Only collect a file whose path and bytes agree.  Keep corrupt or
            # missing metadata candidates for explicit integrity diagnostics.
            try:
                compressed = self._read_compressed(path)
                content = self._bounded_decompress(compressed)
            except (BlobIntegrityError, EOFError, OSError):
                continue
            if hashlib.sha256(content).hexdigest() != digest:
                continue
            path.unlink(missing_ok=True)
            deleted.append(digest)
        return tuple(sorted(deleted))

    collect_garbage = garbage_collect

    def _read_compressed(self, path: Path) -> bytes:
        if path.is_symlink():
            raise StorageError("blob path must not be a symlink")
        return path.read_bytes()

    def _bounded_decompress(self, compressed: bytes) -> bytes:
        output = bytearray()
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
                while True:
                    chunk = stream.read(
                        min(1024 * 1024, self._max_read_bytes - len(output) + 1)
                    )
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > self._max_read_bytes:
                        raise BlobIntegrityError(
                            "blob expands beyond configured read limit"
                        )
        except (EOFError, OSError) as exc:
            raise BlobIntegrityError("compressed blob cannot be decompressed") from exc
        return bytes(output)

    def _bounded_uncompressed_length(self, compressed: bytes) -> int:
        return len(self._bounded_decompress(compressed))

    def cleanup_temporary_files(self) -> tuple[Path, ...]:
        """Remove incomplete temp files left by interrupted writers."""
        removed: list[Path] = []
        for path in self._root.glob("**/.m3-blob-*.tmp"):
            if path.is_symlink() or not path.is_file():
                continue
            path.unlink(missing_ok=True)
            removed.append(path)
        return tuple(removed)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


__all__ = ["BlobRecord", "FilesystemBlobStore"]
