"""Owner-run image verification; never connects to a cluster or submits jobs."""

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

import numba
import numpy as np
from numba import njit, prange


@njit(cache=True)
def _serial_sum_of_squares(values):
    total = 0.0
    for value in values:
        total += value * value
    return total


@njit(cache=True, parallel=True)
def _parallel_sum_of_squares(values):
    total = 0.0
    for index in prange(len(values)):
        total += values[index] * values[index]
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the packaged CPU environment")
    parser.add_argument("--output", required=True, type=Path, help="new JSON report path")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; choose a new report path")

    root = Path("/opt/tsp")
    if Path(sys.prefix) != root / ".venv":
        raise RuntimeError("Use the image's /opt/tsp/.venv/bin/python")
    if sys.flags.isolated != 1:
        raise RuntimeError("Run the verifier with python -I")
    build_info = json.loads((root / "build-info.json").read_text())

    cache = Path(os.environ["NUMBA_CACHE_DIR"])
    cache.mkdir(parents=True, exist_ok=True)
    # Check writability even if an earlier invocation populated this cache.
    with tempfile.TemporaryFile(dir=cache) as probe:
        probe.write(b"cache write check\n")
        probe.flush()

    cpus = sorted(os.sched_getaffinity(0))
    cpu_models = sorted({
        line.split(":", 1)[1].strip()
        for line in Path("/proc/cpuinfo").read_text().splitlines()
        if line.startswith("model name") and ":" in line
    })
    if numba.get_num_threads() > len(cpus):
        raise RuntimeError("Numba thread count exceeds the process's allowed CPU set")

    values = np.arange(16, dtype=np.float64)
    original = values.copy()
    for kernel in (_serial_sum_of_squares, _parallel_sum_of_squares):
        if kernel(values) != 1240.0 or not kernel.nopython_signatures:
            raise RuntimeError("Numerical compilation/execution check failed")
        if kernel(values) != 1240.0:
            raise RuntimeError("Repeated compiled execution failed")
    np.testing.assert_array_equal(values, original)
    if not any(cache.rglob("*.nbc")):
        raise RuntimeError("No native Numba cache artifact was written to the selected cache")

    subprocess.run(
        [sys.executable, "-I", "-B", "-m", "pytest", "-p", "no:cacheprovider", str(root / "tests")],
        cwd=root,
        check=True,
    )
    report = {
        "status": "passed",
        "scope": "container correctness smoke test, not a performance measurement",
        "build": build_info,
        "runtime": {
            "python_version": platform.python_version(),
            "architecture": platform.machine(),
            "cpu_models": cpu_models,
            "numpy": np.__version__,
            "numba": numba.__version__,
            "llvmlite": version("llvmlite"),
            "numba_threading_layer": numba.threading_layer(),
            "numba_threads": numba.get_num_threads(),
            "allowed_cpus": cpus,
            "numba_cache_dir": str(cache),
            "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        },
    }
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, allow_nan=False)
        output.write("\n")
    if json.loads(args.output.read_text())["status"] != "passed":
        raise RuntimeError("Output report could not be read back")
    print(json.dumps(report, indent=2))
    print(f"Verification report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
