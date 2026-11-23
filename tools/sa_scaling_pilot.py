"""Fixed-work serial/parallel SA pilot; run only inside an appropriate allocation."""

import argparse
import json
import os
import platform
import statistics
import sys
from pathlib import Path

import llvmlite
import numba
import numpy as np

from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.problem.instances import uniform_instance
from tsp_hpc.reproducibility.identity import coordinate_checksum
from tsp_hpc.solvers.resources import available_threads
from tsp_hpc.solvers.sa.parallel import solve_chains
from tsp_hpc.solvers.sa.solver import SimulatedAnnealingSolver


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--proposals", type=int, default=3_000_000)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.proposals < 1 or args.repetitions < 2:
        parser.error("proposals must be positive and repetitions must be at least 2")
    if available_threads() < 8:
        raise RuntimeError("this pilot requires an allocation with at least eight allowed CPUs")
    if args.output.exists():
        raise FileExistsError(args.output)

    instance = uniform_instance(200, seed=42).instance
    seeds = [11, 22, 33, 44, 55, 66, 77, 88]
    budget = {"max_proposals": args.proposals}
    parameters = {"cost_check_interval": 1024}
    records = []
    preparations = {}
    for mode in ("dense", "on_demand"):
        prepared = prepare_distances(instance, mode=mode)
        solver = SimulatedAnnealingSolver(prepared)
        preparations[mode] = solver.metadata
        reference = None
        for repeat in range(args.repetitions):
            order = (1, 2, 4, 8) if repeat % 2 == 0 else (8, 4, 2, 1)
            for threads in order:
                batch = solve_chains(solver, seeds=seeds, budget=budget,
                                     parameters=parameters, threads=threads)
                outcome = [{"tour": r.best_tour.tolist(), "cost": r.best_cost,
                            "work": r.work.to_dict(), "termination_reason": r.termination_reason}
                           for r in batch.results]
                if reference is None:
                    reference = outcome
                if outcome != reference:
                    raise AssertionError(f"fixed-work results differ for {mode}, {threads} threads")
                records.append({"distance_mode": mode, "repeat": repeat, "threads": threads,
                                "wall_time_s": batch.wall_time_s, "best_index": batch.best_index,
                                "chains": outcome})
                print(f"{mode} repeat={repeat} threads={threads} wall_s={batch.wall_time_s:.6f}",
                      flush=True)
    medians = {mode: {str(threads): statistics.median(
        r["wall_time_s"] for r in records if r["distance_mode"] == mode and r["threads"] == threads)
        for threads in (1, 2, 4, 8)} for mode in preparations}
    cpu_model = None
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        cpu_model = next((line.split(":", 1)[1].strip() for line in cpuinfo.read_text().splitlines()
                          if line.startswith("model name")), None)
    report = {
        "schema_version": 1, "source_revision": args.revision,
        "measurement_scope": "batch wall: scheduling, search, and per-chain validation; excludes preparation/JIT",
        "instance": {"instance_id": instance.instance_id,
                     "coordinate_checksum": coordinate_checksum(instance.coordinates),
                     "n_cities": instance.n_cities, "generator": "uniform", "seed": 42},
        "seeds": seeds, "budget_per_chain": budget, "parameters": parameters,
        "runtime": {"python": sys.version, "numpy": np.__version__, "numba": numba.__version__,
                    "llvmlite": llvmlite.__version__, "platform": platform.platform(),
                    "hostname": platform.node(), "cpu_model": cpu_model,
                    "cpu_affinity": sorted(os.sched_getaffinity(0)),
                    "environment": {name: os.environ.get(name) for name in
                                    ("SLURM_JOB_ID", "SLURM_CPUS_PER_TASK", "OMP_NUM_THREADS",
                                     "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS")}},
        "preparation": preparations, "measurements": records, "median_wall_time_s": medians,
        "speedup": {mode: {p: times["1"] / t for p, t in times.items()}
                    for mode, times in medians.items()},
    }
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(f"Saved {args.output}", flush=True)


if __name__ == "__main__":
    main()
