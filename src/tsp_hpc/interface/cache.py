"""Observed aggregate compiler-cache ceilings, not a filesystem quota/attestation.

Scan without following links; bound traversal itself. The serial controller checks
before launch, periodically while a verified child runs, and after exit. Individual
writes additionally have the worker's OS file-size limit. No automatic deletion of
cache or scientific evidence; owners can select a new root when capacity is full.
"""

import os
import stat

from tsp_hpc.interface.errors import InterfaceError


def cache_usage(directory, limits):
    entries = contexts = size = 0

    def visit(current, depth):
        nonlocal entries, contexts, size
        if depth > 8:
            raise InterfaceError("resource_limit", "Compiler-cache traversal exceeds its depth bound.", 413)
        with os.scandir(current.fd) as children:
            for child in children:
                entries += 1
                if entries > limits.max_cache_entries:
                    raise InterfaceError("resource_limit", "Compiler-cache entry capacity reached.", 413)
                try:
                    info = child.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue  # Compiler's atomic temporary-file rename/removal.
                if stat.S_ISDIR(info.st_mode):
                    if depth == 0:
                        contexts += 1
                        if contexts > limits.max_cache_contexts:
                            raise InterfaceError("resource_limit", "Compiler-cache context capacity reached.", 413)
                    with current.directory(child.name) as nested:
                        visit(nested, depth + 1)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    size += max(info.st_size, getattr(info, "st_blocks", 0) * 512)
                    if size > limits.max_cache_bytes:
                        raise InterfaceError("resource_limit", "Compiler-cache byte capacity reached.", 413)
                else:
                    raise InterfaceError("invalid_artifact", "Compiler cache contains an unsupported entry.", 422)
    visit(directory, 0)
    return {"entries": entries, "contexts": contexts, "bytes": size}
