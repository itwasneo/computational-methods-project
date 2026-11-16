"""Private installed worker entry point. Never selected/configured by HTTP clients.

Only three inherited descriptors grant the job directory, compiler-cache directory
and worker lease. This is process isolation/resource limiting, not a same-user
sandbox. Scientific execution is exclusively run_manifest.
"""

import hashlib
import os
import re
import resource
import sys
from time import monotonic

from tsp_hpc.interface.execution import ExecutionLimits
from tsp_hpc.interface.processes import process_identity
from tsp_hpc.interface.storage import Directory, decode, encode


def _limit(kind, soft, hard=None):
    _, inherited = resource.getrlimit(kind)
    hard = soft if hard is None else hard
    if inherited != resource.RLIM_INFINITY:
        hard = min(hard, inherited)
    resource.setrlimit(kind, (min(soft, hard), hard))


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 5 or not re.fullmatch(r"[0-9a-f]{32}", args[0]) or not re.fullmatch(r"[0-9a-f]{64}", args[4]):
        return 2
    os.umask(0o077)  # Compiler-created cache files/directories remain owner-only too.
    job_id, job_fd, lease_fd, cache_fd, expected_digest = args
    directory = Directory(int(job_fd))
    # Keep lease_fd open until actual process exit, including interpreter teardown.
    # A free lease must not falsely certify exit while Python is still running.
    os.fstat(int(lease_fd)); os.fstat(int(cache_fd))
    os.fchdir(directory.fd)
    start = monotonic()
    attempts, counts = [], {"completed": 0, "failed": 0}
    receipt = {"schema_version": 1, "job_id": job_id, "state": "failed", "failure_code": "worker_failed"}
    phase = "request"
    try:
        identity = process_identity(os.getpid(), job_id)
        if identity is None:
            raise ValueError("worker identity unavailable")
        directory.write_new("worker.json", encode(identity))
        raw = directory.read("request.json", 4 * 1024 * 1024)
        if hashlib.sha256(raw).hexdigest() != expected_digest:
            raise ValueError("submission changed")
        request = decode(raw)
        if (type(request["schema_version"]) is not int or request["schema_version"] != 1 or request["job_id"] != job_id
                or not re.fullmatch(r"[0-9a-f]{32}", request["campaign_id"])):
            raise ValueError("invalid submission")
        plan = request["plan"]
        limits = ExecutionLimits(**plan["limits"])
        _limit(resource.RLIMIT_AS, limits.max_worker_memory_bytes)
        _limit(resource.RLIMIT_FSIZE, limits.max_result_bytes)
        threads = plan["public"]["configuration"].get("threads_per_worker", 1)
        if type(threads) is not int or not 1 <= threads <= limits.max_solver_threads:
            raise ValueError("invalid worker thread bound")
        # RLIMIT_CPU counts aggregate thread CPU time; the owner watchdog separately
        # enforces the unchanged wall-clock ceiling for the whole process.
        cpu_seconds = limits.max_job_seconds * threads
        _limit(resource.RLIMIT_CPU, cpu_seconds, cpu_seconds + 1)
        _limit(resource.RLIMIT_CORE, 0)
        affinity = plan["runtime"]["affinity"]
        if affinity is not None:
            os.sched_setaffinity(0, affinity)
            if sorted(os.sched_getaffinity(0)) != affinity:
                raise ValueError("CPU affinity changed")
        phase = "runtime"
        from tsp_hpc.interface.runtime import runtime_binding
        observed_runtime = runtime_binding()
        if observed_runtime["runtime_key"] != plan["runtime"]["runtime_key"]:
            receipt["observed_runtime"] = observed_runtime
            raise ValueError("reviewed runtime changed")
        phase = "input"
        artifact = directory.read("instance.json", limits.max_instance_bytes)
        if hashlib.sha256(artifact).hexdigest() != plan["public"]["snapshot_sha256"]:
            raise ValueError("snapshot changed")
        from tsp_hpc.experiments.manifests import Manifest
        from tsp_hpc.experiments.runner import run_manifest
        manifest = Manifest(plan["manifest"])
        instance_id = manifest.to_dict()["instances"][0]["instance_id"]

        def checkpoint():
            try:
                control = decode(directory.read("control.json", 4096))
            except FileNotFoundError:
                return
            if type(control) is dict and set(control) == {"stop_after_trial"} and control["stop_after_trial"] is True:
                raise InterruptedError("owner requested trial-boundary stop")
            raise ValueError("invalid worker control")

        def on_trial(attempt, observed):
            attempts.append(attempt)
            counts.update(observed)
            directory.replace("progress.json", encode({"attempt_ids": attempts, "counts": counts}))

        phase = "campaign"
        summary = run_manifest(manifest, instance_paths={instance_id: "instance.json"}, output_path="results.jsonl",
                               campaign_id=request["campaign_id"],
                               max_matrix_bytes=plan["public"]["configuration"]["max_matrix_bytes"],
                               checkpoint=checkpoint, on_trial=on_trial)
        if summary["counts"] != counts:
            raise ValueError("progress accounting mismatch")
        receipt.update(state="completed", failure_code=None)
    except InterruptedError:
        receipt.update(state="cancelled", failure_code=None)
    except Exception as error:
        receipt["failure_code"] = ("resource_limit" if isinstance(error, MemoryError) else
                                   "preflight_stale" if phase == "runtime" else
                                   "snapshot_stale" if phase == "input" else "worker_failed")
        receipt["error_type"] = type(error).__name__  # No private exception text/traceback.
    receipt.update(phase=phase, attempt_ids=attempts, counts=counts, elapsed_time_s=monotonic() - start)
    try:
        directory.write_new("receipt.json", encode(receipt))
    except Exception:
        return 1  # Missing receipt is never completed evidence.
    finally:
        directory.close()
    return 0 if receipt["state"] in ("completed", "cancelled") else 1


if __name__ == "__main__":
    raise SystemExit(main())
