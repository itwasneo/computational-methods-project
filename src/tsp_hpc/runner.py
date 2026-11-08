"""Sequential fixed-work campaigns with exclusive, single-writer JSONL shards.

Manifest/input binding errors precede execution. Trial failures are records;
serialization/write failures and process interrupts abort rather than masquerade
as completed campaigns. No resume, implicit generation, or resource allocation.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import subprocess
from time import perf_counter
from uuid import uuid4

import llvmlite
import numba
import numpy as np

from tsp_hpc import __version__
from tsp_hpc.distances import DEFAULT_MAX_MATRIX_BYTES, prepare_distances
from tsp_hpc.identity import canonical_json, stable_id
from tsp_hpc.instances import load_instance
from tsp_hpc.manifests import Manifest
from tsp_hpc import nearest_neighbour as nn
from tsp_hpc.objective import validate_solution
from tsp_hpc.results import SolverResult


def _source_state() -> dict:
    """Identify this source checkout, never an unrelated caller's working tree."""
    root = Path(__file__).resolve().parents[2]
    unknown = {"revision": None, "dirty": None}
    if not (root / ".git").exists():
        return unknown
    try:
        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(root), *args], text=True,
                stderr=subprocess.DEVNULL, timeout=5,
            ).strip()
        git("ls-files", "--error-unmatch", "src/tsp_hpc/runner.py")
        return {"revision": git("rev-parse", "HEAD"),
                "dirty": bool(git("status", "--porcelain"))}
    except (OSError, subprocess.SubprocessError):
        return unknown


def _memory_resources() -> dict:
    """Best-effort host/RLIMIT observations, not a cgroup or Slurm allocation claim."""
    physical, address_space = None, None
    try:
        size = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        physical = size if size > 0 else None
    except (AttributeError, OSError, ValueError):
        pass
    try:
        import resource
        soft, _ = resource.getrlimit(resource.RLIMIT_AS)
        address_space = None if soft == resource.RLIM_INFINITY else soft
    except (ImportError, AttributeError, OSError, ValueError):
        pass
    return {"host_physical_bytes": physical, "address_space_soft_limit_bytes": address_space}


def runtime_provenance() -> dict:
    """Observe the local runtime; environment collection is explicitly allowlisted."""
    try:
        affinity = sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = None
    return {
        "versions": {"python": platform.python_version(), "tsp_hpc": __version__,
                     "numpy": np.__version__, "numba": numba.__version__,
                     "llvmlite": llvmlite.__version__},
        "source": _source_state(),
        "platform": platform.platform(), "machine": platform.machine(),
        "processor": platform.processor(), "cpu_count": os.cpu_count(),
        "memory": _memory_resources(), "runner_workers": 1, "solver_threads": 1,
        "cpu_affinity": affinity,
        "environment": {key: os.environ[key] for key in (
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
            "NUMBA_NUM_THREADS", "NUMBA_THREADING_LAYER", "NUMBA_CPU_NAME",
            "NUMBA_CPU_FEATURES", "SLURM_JOB_ID", "SLURM_CPUS_PER_TASK",
            "SLURM_MEM_PER_NODE", "SLURM_MEM_PER_CPU",
        ) if key in os.environ},
    }


def _execution(plan) -> dict:
    search, execution = plan.search, plan.execution
    if search["algorithm"] != "nn":
        raise ValueError(f"runner does not support algorithm {search['algorithm']!r}")
    if execution["backend"] != "serial":
        raise ValueError("runner supports only serial execution")
    if search["instance"]["distance_kind"] != "euclidean_2d":
        raise ValueError("runner supports only unrounded euclidean_2d execution")
    execution.setdefault("implementation", nn.DEFAULT_IMPLEMENTATION)
    if execution["implementation"] not in nn.implementation_names():
        raise ValueError(f"unavailable NN implementation: {execution['implementation']!r}")
    return execution


def _write_record(stream, record) -> None:
    # Serialize fully before writing: NaN/type failures cannot write half a JSON
    # object. I/O failure or interruption can still leave a partial last line.
    line = canonical_json(record) + "\n"
    stream.write(line)
    stream.flush()
    os.fsync(stream.fileno())


def _result_record(result: SolverResult, reference_cost) -> dict:
    return {
        "status": result.status,
        "best_tour": None if result.best_tour is None else result.best_tour.tolist(),
        "best_cost": reference_cost,
        "work": result.work.to_dict(), "termination_reason": result.termination_reason,
        "history": result.history,
    }


def _trial(plan, execution, capability_error, bindings, provenance, campaign_id, matrix_limit):
    start = perf_counter()
    search = plan.search
    identifier = search["instance"]["instance_id"]
    record = {
        "schema_version": 1, "record_type": "trial", "campaign_id": campaign_id,
        "attempt_id": uuid4().hex, "started_at": datetime.now(timezone.utc).isoformat(),
        "requested_plan": plan.to_dict(), "effective_execution": execution,
        "execution_id": None if execution is None else stable_id(
            "execution", {"search_id": plan.search_id, "execution": execution}),
        "runtime": provenance, "input": {"path": str(bindings[identifier]) if identifier in bindings else None},
        "setup_policy": "fresh-per-trial", "max_matrix_bytes": matrix_limit,
        "adapter": None, "outcome": "failed", "result": None, "error": None,
        "timings": {},
    }
    result = None
    phase = "capability"
    phase_start = perf_counter()
    try:
        if capability_error is not None:
            raise capability_error
        phase = "input"
        phase_start = perf_counter()
        if identifier not in bindings:
            raise ValueError(f"no input binding for {identifier!r}")
        loaded = load_instance(bindings[identifier])
        descriptor = loaded.descriptor
        record["input"].update(descriptor=descriptor, provenance=loaded.provenance)
        if descriptor != search["instance"]:
            raise ValueError("artifact descriptor does not match the manifest (including generation seed)")
        record["timings"]["input_time_s"] = perf_counter() - phase_start

        phase = "distance_preparation"
        phase_start = perf_counter()
        prepared = prepare_distances(loaded.instance, mode=execution["distance_mode"],
                                     max_matrix_bytes=matrix_limit)
        record["timings"]["distance_preparation_wall_time_s"] = perf_counter() - phase_start
        record["timings"].update(distance_warmup_time_s=prepared.jit_warmup_time_s,
                                  preprocessing_time_s=prepared.preprocessing_time_s)
        record["adapter"] = prepared.metadata

        phase = "solver_warmup"
        phase_start = perf_counter()
        solver = nn.NearestNeighbourSolver(prepared, implementation=execution["implementation"])
        record["adapter"] = solver.metadata
        record["timings"]["solver_warmup_time_s"] = solver.solver_warmup_time_s

        phase = "solve"
        phase_start = perf_counter()
        # NN consumes no randomness. Future stochastic adapters must receive
        # fresh logical chain/population seeds, never reuse a warm-up generator.
        result = solver.solve(loaded.instance, seed=plan.trial_seed, budget=search["budget"],
                              parameters=search["parameters"], record_history=search["record_history"])
        record["timings"]["solve_wall_time_s"] = perf_counter() - phase_start
        if not isinstance(result, SolverResult):
            raise TypeError("solver must return SolverResult")
        record["timings"].update(search_time_s=result.search_time_s,
                                  solver_validation_time_s=result.validation_time_s)

        phase = "validation"
        phase_start = perf_counter()
        reference = None
        if result.status == "ok":
            reference = validate_solution(loaded.instance, result.best_tour, result.best_cost)
        record["timings"]["runner_validation_time_s"] = perf_counter() - phase_start
        record["result"] = _result_record(result, reference)
        record["outcome"] = "completed"
    except Exception as error:
        # Do not catch KeyboardInterrupt/SystemExit or errors from shard writes.
        record["error"] = {
            "phase": phase, "type": type(error).__name__, "message": str(error),
            "reported_work": result.work.to_dict() if isinstance(result, SolverResult) else None,
        }
        record["timings"]["failed_phase_time_s"] = perf_counter() - phase_start
    record["timings"]["pre_persistence_time_s"] = perf_counter() - start
    return record


def run_manifest(manifest: Manifest, *, instance_paths: Mapping[str, str | Path],
                 output_path: str | Path, max_matrix_bytes: int = DEFAULT_MAX_MATRIX_BYTES) -> dict:
    """Execute plans in order and return the persisted campaign summary.

    Bind artifact paths explicitly by manifest instance ID. Each trial reloads,
    prepares, and warms independently; repeated trials may hit native caches but
    never share mutable search state. Unsupported plans become failure records.
    Effective-ID collisions and malformed bindings fail before opening output.

    Output must not exist; its parent must exist. Each line is flushed/fsynced.
    The final summary measures API entry through all trial writes, excluding its
    own serialization/write, CLI parsing and process startup/imports. An absent
    summary signals an interrupted/failed campaign, not resumable completion.
    """
    campaign_start = perf_counter()
    if not isinstance(manifest, Manifest):
        raise TypeError("manifest must be a Manifest")
    if not isinstance(instance_paths, Mapping):
        raise TypeError("instance_paths must map instance IDs to artifact paths")
    if type(max_matrix_bytes) is not int:
        raise TypeError("max_matrix_bytes must be a Python integer")
    if max_matrix_bytes < 0:
        raise ValueError("max_matrix_bytes must be non-negative")
    known = {item["instance_id"] for item in manifest.to_dict()["instances"]}
    if any(type(key) is not str or key not in known for key in instance_paths):
        raise ValueError("input bindings contain unknown instance IDs")
    bindings = {key: Path(value) for key, value in instance_paths.items()}
    output = Path(output_path)
    if any(output.resolve() == path.resolve() for path in bindings.values()):
        raise ValueError("output must not be an input artifact")
    resolved = []
    seen = set()
    for plan in manifest.expand():
        execution, error = None, None
        try:
            execution = _execution(plan)
        except ValueError as failure:
            error = failure
        if execution is not None:
            identity = stable_id("execution", {"search_id": plan.search_id, "execution": execution})
            if identity in seen:
                raise ValueError("duplicate effective execution after implementation resolution")
            seen.add(identity)
        resolved.append((plan, execution, error))
    provenance = runtime_provenance()
    campaign_id = uuid4().hex
    counts = {"completed": 0, "failed": 0}
    persistence_time = 0.0
    trial_timings = []
    # Exclusive creation forbids accidental overwrite, append, and concurrent
    # writers. Do not unlink incomplete output on failure: preserve evidence.
    with output.open("x", encoding="utf-8") as stream:
        setup_time = perf_counter() - campaign_start
        for plan, execution, error in resolved:
            trial_start = perf_counter()
            record = _trial(plan, execution, error, bindings, provenance, campaign_id, max_matrix_bytes)
            write_start = perf_counter()
            _write_record(stream, record)
            write_end = perf_counter()
            persistence_time += write_end - write_start
            trial_timings.append({"attempt_id": record["attempt_id"],
                                  "persistence_time_s": write_end - write_start,
                                  "end_to_end_time_s": write_end - trial_start})
            counts[record["outcome"]] += 1
        summary = {
            "schema_version": 1, "record_type": "campaign_summary", "campaign_id": campaign_id,
            "counts": counts, "planned_trials": len(resolved), "trial_timings": trial_timings,
            "timings": {"setup_time_s": setup_time, "trial_persistence_time_s": persistence_time,
                        "end_to_end_time_s": perf_counter() - campaign_start},
        }
        _write_record(stream, summary)
    return summary
