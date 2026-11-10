"""Small binary-source boundary shared by CLI paths and authorized interface handles.

Sources provide an opaque identity, a stable (device, inode, size, mtime, ctime)
stamp, and a context-managed seekable binary stream. Interface implementations
must enforce their own authority on every open/stat; the reader never converts
opaque source identities into paths.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, ContextManager, Protocol


class ShardSource(Protocol):
    @property
    def identity(self) -> str: ...

    def stamp(self) -> tuple[int, ...]: ...

    def open(self) -> ContextManager[BinaryIO]: ...


@dataclass(frozen=True)
class PathSource:
    path: Path

    @property
    def identity(self):
        return str(self.path)

    def stamp(self):
        info = self.path.stat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    def open(self):
        return self.path.open("rb")
