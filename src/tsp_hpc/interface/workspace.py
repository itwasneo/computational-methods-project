"""Bounded, read-only POSIX workspace catalog, anchored to an open root directory.

This catalogs candidate files, not scientific records. No endpoint exposes read_candidate.
The owner and same-user processes are trusted; hostile filesystem owners/mount changes
are outside this boundary. Symlinks/special files are never followed and observed
replacement/modification races fail closed. Metadata revisions are not content hashes.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import base64
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import threading

from tsp_hpc.interface.contracts import SHARDS_MAX
from tsp_hpc.interface.errors import InterfaceError

MAX_ENTRIES = 10_000
MAX_DEPTH = 16
MAX_READ_BYTES = 64 * 1024
MAX_DATASET_BYTES = 64 * 1024 * 1024


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _stale():
    return InterfaceError("stale_revision", "Workspace files changed; refresh the catalog.", 409, retryable=True)


@dataclass(frozen=True)
class Candidate:
    parts: tuple[str, ...]
    stamp: tuple[int, ...]
    kind: str


@dataclass(frozen=True)
class AuthorizedSource:
    """Opaque analysis source; no path leaves the workspace authority boundary."""

    catalog: "WorkspaceCatalog"
    identity: str
    revision: str
    size_bytes: int

    def stamp(self):
        return self.catalog.source_stamp(self.identity, self.revision)

    def open(self):
        return self.catalog.open_source(self.identity, self.revision)


class WorkspaceCatalog:
    def __init__(self, root: str | Path, *, max_entries: int = MAX_ENTRIES, max_depth: int = MAX_DEPTH):
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise InterfaceError("workspace_unavailable", "This backend requires POSIX no-follow directory access.", 503)
        if max_entries < 1 or max_depth < 1:
            raise ValueError("catalog bounds must be positive")
        self._fd = -1
        self._lock = threading.Lock()
        self._max_entries, self._max_depth = max_entries, max_depth
        self.workspace_id = secrets.token_urlsafe(24)
        self._cursor_key = secrets.token_bytes(32)
        self._revision = ""
        self._files: dict[str, Candidate] = {}
        self._directories: dict[tuple[str, ...], tuple[int, ...]] = {}
        self._valid = False
        try:
            # Explicit startup authorization resolves the root once; descendants never follow symlinks.
            self._root = Path(root).resolve(strict=True)
            self._fd = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            self._root_identity = _stamp(os.fstat(self._fd))[:2]
            self.display_name = self._root.name or "Workspace"
        except (OSError, ValueError, RuntimeError):
            self.close()
            raise InterfaceError("workspace_unavailable", "Workspace must be an accessible directory.", 503) from None

    def close(self):
        with self._lock:
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1
            self._valid = False
            self._files.clear()
            self._directories.clear()

    @contextmanager
    def _operation(self, *, wait=False):
        if not self._lock.acquire(blocking=wait):
            raise InterfaceError("catalog_busy", "A catalog operation is already running.", 409, retryable=True)
        try:
            if self._fd < 0:
                raise InterfaceError("workspace_unavailable", "Workspace session is closed.", 503)
            info = os.stat(self._root, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or _stamp(info)[:2] != self._root_identity:
                raise _stale()
            yield
        except OSError:
            self._valid = False
            raise _stale() from None
        finally:
            self._lock.release()

    @contextmanager
    def _directory(self, parts):
        fd = os.dup(self._fd)
        try:
            for part in parts:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            yield fd
        finally:
            os.close(fd)

    def workspace(self):
        with self._operation():
            return {"workspace_id": self.workspace_id, "display_name": self.display_name, "access": "read_only"}

    def _scan(self):
        files = {}
        directories = {}
        seen = 0

        def visit(fd, parts):
            nonlocal seen
            before = _stamp(os.fstat(fd))
            with os.scandir(fd) as entries:
                for entry in entries:
                    seen += 1
                    if seen > self._max_entries:
                        raise InterfaceError("catalog_limit_exceeded", "Workspace scan exceeds the entry limit; use a smaller workspace.", 413)
                    if entry.name.startswith(".") or entry.is_symlink():
                        continue
                    info = entry.stat(follow_symlinks=False)
                    child = (*parts, entry.name)
                    if stat.S_ISDIR(info.st_mode):
                        if len(child) > self._max_depth:
                            raise InterfaceError("catalog_limit_exceeded", "Workspace scan exceeds the directory depth limit.", 413)
                        child_fd = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                        try:
                            if _stamp(os.fstat(child_fd)) != _stamp(info):
                                raise _stale()
                            visit(child_fd, child)
                        finally:
                            os.close(child_fd)
                    elif stat.S_ISREG(info.st_mode) and Path(entry.name).suffix.lower() in (".json", ".jsonl"):
                        kind = "result_shard" if Path(entry.name).suffix.lower() == ".jsonl" else "json"
                        files[secrets.token_urlsafe(24)] = Candidate(child, _stamp(info), kind)
            if _stamp(os.fstat(fd)) != before:
                raise _stale()
            directories[parts] = before

        visit(self._fd, ())
        return dict(sorted(files.items(), key=lambda item: item[1].parts)), directories

    def refresh(self):
        with self._operation():
            self._valid = False
            # Publish only a complete catalog; old handles/cursors become invalid even on a no-change refresh.
            files, directories = self._scan()
            self._files, self._directories = files, directories
            self._revision = secrets.token_urlsafe(24)
            self._valid = True
            return self._revision

    def _check_current(self):
        if not self._valid:
            raise _stale()
        for parts, expected in self._directories.items():
            with self._directory(parts) as fd:
                if _stamp(os.fstat(fd)) != expected:
                    self._valid = False
                    raise _stale()
        for candidate in self._files.values():
            with self._directory(candidate.parts[:-1]) as fd:
                actual = os.stat(candidate.parts[-1], dir_fd=fd, follow_symlinks=False)
                if _stamp(actual) != candidate.stamp:
                    self._valid = False
                    raise _stale()

    def _cursor(self, offset, limit):
        payload = json.dumps([self._revision, offset, limit], separators=(",", ":")).encode()
        signature = hmac.digest(self._cursor_key, payload, "sha256")
        return base64.urlsafe_b64encode(payload + signature).decode()

    def _offset(self, cursor, limit):
        if cursor is None:
            return 0
        try:
            if not isinstance(cursor, str) or len(cursor) > 512:
                raise ValueError
            raw = base64.b64decode(cursor, altchars=b"-_", validate=True)
            payload, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(signature, hmac.digest(self._cursor_key, payload, "sha256")):
                raise ValueError
            revision, offset, size = json.loads(payload)
            if revision != self._revision or type(offset) is not int or offset < 0 or size != limit:
                raise ValueError
            return offset
        except (ValueError, TypeError, UnicodeError):
            raise InterfaceError("cursor_invalid", "Cursor is invalid for this catalog or page size.", 409) from None

    def page(self, *, limit=100, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise InterfaceError("invalid_request", "Page size must be an integer from 1 to 500.")
        with self._operation():
            self._check_current()
            offset = self._offset(cursor, limit)
            files = list(self._files.items())
            items = [
                {"artifact_handle": handle, "display_name": "/".join(candidate.parts),
                 "kind": candidate.kind, "revision": self._revision, "validation": "unvalidated"}
                for handle, candidate in files[offset:offset + limit]
            ]
            next_cursor = self._cursor(offset + limit, limit) if offset + limit < len(files) else None
            return {"revision": self._revision, "items": items, "next_cursor": next_cursor}

    def assert_revision(self, revision, *, wait=False):
        with self._operation(wait=wait):
            self._check_current()
            if revision != self._revision:
                raise _stale()

    def select_sources(self, handles, manifest_handle, revision):
        """Capture bounded source handles, not paths or unbounded file contents."""
        if not handles or len(handles) > SHARDS_MAX or len(set(handles)) != len(handles):
            raise InterfaceError("invalid_request", f"Select between 1 and {SHARDS_MAX} distinct result shards.")
        with self._operation():
            self._check_current()
            if revision != self._revision:
                raise _stale()
            total = 0

            def source(handle, kind, limit):
                nonlocal total
                candidate = self._files.get(handle)
                if candidate is None:
                    raise InterfaceError("artifact_not_found", "Selected artifact is unavailable.", 404)
                if candidate.kind != kind:
                    raise InterfaceError("invalid_artifact", "Selected artifact has an unsupported file kind.", 422)
                size = candidate.stamp[3]
                total += size
                if size > limit or total > MAX_DATASET_BYTES:
                    raise InterfaceError("dataset_limit_exceeded", "Selected sources exceed ingestion byte limits.", 413)
                return AuthorizedSource(self, handle, revision, size)

            shards = [source(handle, "result_shard", MAX_DATASET_BYTES) for handle in handles]
            manifest = source(manifest_handle, "json", MAX_READ_BYTES) if manifest_handle is not None else None
            return shards, manifest

    def source_stamp(self, handle, revision):
        with self._operation(wait=True):
            self._check_current()
            if revision != self._revision:
                raise _stale()
            candidate = self._files.get(handle)
            if candidate is None:
                raise _stale()
            dev, ino, _mode, size, modified, changed = candidate.stamp
            return dev, ino, size, modified, changed

    @contextmanager
    def open_source(self, handle, revision):
        """Open a captured source briefly under the catalog lock; parse off-lock."""
        with self._operation(wait=True):
            self._check_current()
            if revision != self._revision or handle not in self._files:
                raise _stale()
            candidate = self._files[handle]
            if candidate.stamp[3] > MAX_DATASET_BYTES:
                raise InterfaceError("dataset_limit_exceeded", "Source exceeds ingestion byte limit.", 413)
            with self._directory(candidate.parts[:-1]) as parent:
                fd = os.open(candidate.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
            try:
                info = os.fstat(fd)
                if _stamp(info) != candidate.stamp or not stat.S_ISREG(info.st_mode):
                    raise _stale()
            except BaseException:
                os.close(fd)
                raise
        try:
            with os.fdopen(fd, "rb") as stream:
                try:
                    yield stream
                finally:
                    if _stamp(os.fstat(stream.fileno())) != candidate.stamp:
                        raise _stale()
                    self.assert_revision(revision, wait=True)
        except OSError:
            raise _stale() from None

    def read_candidate(self, handle: str, revision: str, *, max_bytes=MAX_READ_BYTES) -> bytes:
        """Internal bounded reader for future adapters, NOT an HTTP file-read endpoint."""
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_READ_BYTES:
            raise ValueError("invalid read bound")
        with self._operation():
            self._check_current()
            if revision != self._revision:
                raise _stale()
            candidate = self._files.get(handle)
            if candidate is None:
                raise InterfaceError("artifact_not_found", "Artifact handle is not available in this session.", 404)
            with self._directory(candidate.parts[:-1]) as parent:
                fd = os.open(candidate.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or _stamp(info) != candidate.stamp:
                        raise _stale()
                    if info.st_size > max_bytes:
                        raise InterfaceError("artifact_too_large", "Candidate exceeds the bounded read limit.", 413)
                    chunks, size = [], 0
                    while chunk := os.read(fd, min(8192, max_bytes + 1 - size)):
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > max_bytes:
                            raise InterfaceError("artifact_too_large", "Candidate exceeds the bounded read limit.", 413)
                    if _stamp(os.fstat(fd)) != candidate.stamp:
                        raise _stale()
                    self._check_current()
                    return b"".join(chunks)
                finally:
                    os.close(fd)
