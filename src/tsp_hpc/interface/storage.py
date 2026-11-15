"""Private, bounded, descriptor-anchored local state; one controller per root.

No client paths, symlinks or special files. Atomic replace is used for mutable
receipts, exclusive creation for scientific inputs/outputs. Filesystem owners and
same-user processes remain trusted; this is not a sandbox or tamper attestation.
"""

from contextlib import contextmanager
import fcntl
import json
import os
import re
import secrets
import stat

from tsp_hpc.interface.errors import InterfaceError


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate state field")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("nonfinite state")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def component(name):
    if type(name) is not str or name in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name):
        raise InterfaceError("invalid_request", "Invalid storage identity.")
    return name


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class Directory:
    def __init__(self, fd):
        self.fd = fd

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    @contextmanager
    def directory(self, name, *, create=False):
        name = component(name)
        if create:
            os.mkdir(name, 0o700, dir_fd=self.fd)
            os.fsync(self.fd)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)
        child = Directory(fd)
        try:
            yield child
        finally:
            child.close()

    def names(self, limit):
        names = []
        with os.scandir(self.fd) as entries:
            for item in entries:
                names.append(item.name)
                if len(names) > limit:
                    raise InterfaceError("resource_limit", "Stored artifact count exceeds its bound.", 413)
        return sorted(names)

    def read(self, name, limit):
        fd = os.open(component(name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self.fd)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise InterfaceError("invalid_artifact", "Stored artifact is not a private regular file.", 422)
            if before.st_size > limit:
                raise InterfaceError("resource_limit", "Stored artifact exceeds its byte bound.", 413)
            data = stream.read(limit + 1)
            after = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            if len(data) > limit or stamp(before) != stamp(after) or stamp(before) != stamp(os.fstat(stream.fileno())):
                raise InterfaceError("snapshot_stale", "Stored artifact changed during reading.", 409)
            return data

    @contextmanager
    def open_regular(self, name, limit):
        fd = os.open(component(name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self.fd)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise InterfaceError("invalid_artifact", "Result is not a bounded private regular file.", 422)
            yield stream
            if stamp(before) != stamp(os.fstat(stream.fileno())) or stamp(before) != stamp(os.stat(name, dir_fd=self.fd, follow_symlinks=False)):
                raise InterfaceError("stale_revision", "Result changed during reading.", 409)

    def write_new(self, name, data):
        fd = os.open(component(name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=self.fd)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(self.fd)

    def replace(self, name, data):
        component(name)
        try:
            info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise InterfaceError("invalid_artifact", "Mutable state is not a private regular file.", 422)
        except FileNotFoundError:
            pass
        temporary = ".tmp-" + secrets.token_hex(16)
        self.write_new(temporary, data)
        os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        os.fsync(self.fd)


class LocalStorage(Directory):
    def __init__(self, authority, workspace):
        authority.require()
        super().__init__(-1)
        self.authority, self._lock_fd = authority, -1
        try:
            try:
                os.mkdir(".tsp-local", 0o700, dir_fd=authority.fd)
                os.fsync(authority.fd)
            except FileExistsError:
                pass
            self.fd = os.open(".tsp-local", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=authority.fd)
            info = os.fstat(self.fd)
            self.identity = (info.st_dev, info.st_ino)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("private directory permissions")
            self._lock_fd = os.open("controller.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                    0o600, dir_fd=self.fd)
            lock_info = os.fstat(self._lock_fd)
            if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1:
                raise ValueError("invalid controller lock")
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise InterfaceError("execution_busy", "Another backend owns this execution root.", 409) from None
            ws = os.stat(workspace)
            expected = [ws.st_dev, ws.st_ino]
            try:
                owner = decode(self.read("owner.json", 4096))
            except FileNotFoundError:
                owner = {"schema_version": 1, "workspace": expected, "storage_id": secrets.token_hex(16)}
                self.write_new("owner.json", encode(owner))
            if (type(owner) is not dict or set(owner) != {"schema_version", "workspace", "storage_id"}
                    or owner["schema_version"] != 1 or owner["workspace"] != expected
                    or not re.fullmatch(r"[0-9a-f]{32}", owner["storage_id"])):
                raise ValueError("state belongs to another workspace or schema")
            authority.root_id = owner["storage_id"]
            for name in ("snapshots", "jobs", "cache"):
                try:
                    os.mkdir(name, 0o700, dir_fd=self.fd)
                    os.fsync(self.fd)
                except FileExistsError:
                    with self.directory(name):
                        pass
        except BaseException as error:
            self.close()
            if isinstance(error, (OSError, ValueError, TypeError)):
                raise InterfaceError("execution_unavailable", "Local state could not be opened safely.", 503) from None
            raise

    def check(self):
        self.authority.require()
        info = os.stat(".tsp-local", dir_fd=self.authority.fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self.identity:
            raise InterfaceError("execution_unavailable", "Private execution directory changed.", 503)

    def close(self):
        if self._lock_fd >= 0:
            os.close(self._lock_fd)
            self._lock_fd = -1
        super().close()
