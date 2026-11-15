"""Owner-selected local execution authority. No browser request can enable it.

The read workspace and writable output root are disjoint. Same-user processes
and the owner are trusted, as for WorkspaceCatalog; observed replacement fails
closed. Root selection is not authority to follow client-supplied paths.
"""

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import secrets
import stat

from tsp_hpc.interface.errors import InterfaceError


@dataclass(frozen=True)
class ExecutionLimits:
    max_cities: int = 4096
    max_logical_searches: int = 128
    max_repetitions: int = 32
    max_trials: int = 128
    max_pending: int = 8
    max_jobs: int = 256
    max_snapshots: int = 64
    max_preflights: int = 64
    max_instance_bytes: int = 2 * 1024 * 1024
    max_matrix_bytes: int = 128 * 1024 * 1024
    max_history_points: int = 100_000
    max_analysis_rows: int = 10_000
    max_analysis_bytes: int = 128 * 1024 * 1024
    max_cache_bytes: int = 256 * 1024 * 1024
    max_cache_entries: int = 4096
    max_cache_contexts: int = 16
    max_result_bytes: int = 64 * 1024 * 1024
    max_record_bytes: int = 4 * 1024 * 1024
    max_plan_bytes: int = 4 * 1024 * 1024
    max_response_bytes: int = 4 * 1024 * 1024
    max_worker_memory_bytes: int = 4 * 1024**3
    max_job_seconds: int = 300
    preflight_seconds: int = 900

    def __post_init__(self):
        if any(type(value) is not int or not 1 <= value <= 2**53 - 1 for value in asdict(self).values()):
            raise ValueError("Execution bounds must be positive safe integers.")


class ExecutionAuthority:
    def __init__(self, workspace, *, enabled=False, output_root=None, limits=None):
        if type(enabled) is not bool or enabled != (output_root is not None):
            raise ValueError("Execution requires both --enable-execution and --execution-root.")
        self.limits = limits or ExecutionLimits()
        if not isinstance(self.limits, ExecutionLimits):
            raise TypeError("limits must be ExecutionLimits")
        self.enabled, self.fd, self.root = enabled, -1, None
        self.root_id = secrets.token_urlsafe(24)
        if not enabled:
            return
        try:
            source = Path(workspace).resolve(strict=True)
            root = Path(output_root).resolve(strict=True)
            if source == root or source in root.parents or root in source.parents:
                raise ValueError("Execution and read-workspace roots must be disjoint.")
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            self.fd, self.root = fd, root
            info = os.fstat(fd)
            self.identity = (info.st_dev, info.st_ino)
        except OSError:
            self.close()
            raise InterfaceError("execution_unavailable", "Execution root must be an accessible directory.", 503) from None

    def require(self):
        if not self.enabled:
            raise InterfaceError("execution_disabled", "The owner has not enabled local execution.", 403)
        try:
            info = os.stat(self.root, follow_symlinks=False)
            if self.fd < 0 or not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self.identity:
                raise OSError
        except OSError:
            raise InterfaceError("execution_unavailable", "Execution root changed or closed; restart the backend.", 503) from None
        return self

    def describe(self):
        if self.enabled:
            self.require()
        return {"local_runs_enabled": self.enabled, "limits": asdict(self.limits),
                "output_root": None if not self.enabled else {"root_id": self.root_id, "display_name": self.root.name}}

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
