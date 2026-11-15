"""Non-executing worker-code/configuration binding, not scientific attestation."""

import hashlib
from importlib import metadata, resources
import os
import platform
import signal
import sys

from tsp_hpc.interface.storage import encode


def code_identity():
    digest = hashlib.sha256(b"tsp-local-worker-code-v1\0")
    total = 0
    def visit(directory, prefix=""):
        nonlocal total
        for child in sorted(directory.iterdir(), key=lambda path: path.name):
            name = prefix + child.name
            if child.is_dir() and not child.name.startswith("__"):
                visit(child, name + "/")
            elif child.name.endswith(".py"):
                raw = child.read_bytes()
                total += len(raw)
                if total > 16 * 1024 * 1024:
                    raise ValueError("worker source inventory exceeds its bound")
                digest.update(encode(name)); digest.update(raw)
    visit(resources.files("tsp_hpc"))
    return digest.hexdigest()


def runtime_binding():
    from tsp_hpc.reproducibility.native import compiler_observation
    observation = compiler_observation()
    config = observation["config"]
    env = {"NUMBA_OPT": config["optimization"], "NUMBA_CPU_NAME": config["cpu_name"],
           "NUMBA_CPU_FEATURES": config["cpu_features"],
           "NUMBA_BOUNDSCHECK": None if config["boundscheck"] is None else str(int(config["boundscheck"]))}
    for key, field in (("NUMBA_DISABLE_JIT", "disable_jit"), ("NUMBA_LOOP_VECTORIZE", "loop_vectorize"),
                       ("NUMBA_SLP_VECTORIZE", "slp_vectorize"), ("NUMBA_ENABLE_AVX", "enable_avx"),
                       ("NUMBA_DEBUGINFO", "debug_info")):
        env[key] = str(int(config[field]))
    # Resolve known compiler flags once: a child must not reinterpret environment
    # edits made after Numba was imported by the backend.
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS"):
        env[key] = "1"
    affinity = sorted(os.sched_getaffinity(os.getpid())) if hasattr(os, "sched_getaffinity") else None
    identity = {"code": code_identity(), "python": list(sys.version_info[:3]),
                "executable": os.path.realpath(sys.executable), "compiler": observation,
                "versions": {key: metadata.version(key) for key in ("numpy", "numba", "llvmlite")},
                "affinity": affinity, "worker_environment": env}
    return {"runtime_key": hashlib.sha256(encode(identity)).hexdigest(), "code": identity["code"],
            "compiler": observation, "worker_environment": env, "affinity": affinity,
            "platform_available": platform.system() == "Linux" and hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal")}
