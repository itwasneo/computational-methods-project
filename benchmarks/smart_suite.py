import argparse
import hashlib
import importlib
import importlib.metadata
import json
import math
import numbers
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from smart_tsp_benchmark.tsp_benchmark import AlgorithmConfig, TSPBenchmark


SCENARIOS = (
    ("random", 50, 123),
    ("cluster", 50, 124),
    ("circle", 50, 125),
    ("grid", 100, 126),
)
SOLVER_SEEDS = (101, 102, 103)


def checked_solver(solver):
    def run(points, **params):
        n = len(points)
        route = solver(points.copy(), **params)
        if not isinstance(route, (list, tuple, np.ndarray)) or len(route) != n + 1:
            raise ValueError("solver must return a closed route with n+1 indices")
        if any(isinstance(x, (bool, np.bool_)) or not isinstance(x, numbers.Integral) for x in route):
            raise ValueError("route indices must be integers")
        route = [int(x) for x in route]
        if route[0] != route[-1] or sorted(route[:-1]) != list(range(n)):
            raise ValueError("route must visit each city once and return to its start")
        return route
    return run


def run_suite(solver, solver_name, params, scenarios=SCENARIOS, seeds=SOLVER_SEEDS):
    if "seed" in params:
        raise ValueError("seed is assigned by the suite, not solver parameters")
    warmup_points = np.array([[0., 0.], [1., 0.], [2., 0.], [2., 1.], [1., 1.], [0., 1.]])
    random_state = np.random.get_state()
    started = time.perf_counter()
    try:
        checked_solver(solver)(warmup_points, **params, seed=0)
    finally:
        np.random.set_state(random_state)
    warmup_seconds = time.perf_counter() - started
    records = []
    for method, n, instance_seed in scenarios:
        for solver_seed in seeds:
            config = {
                "n_points": n, "seed": instance_seed, "point_generation": method,
                "use_post_optimization": False, "plot_results": False, "verbose": False,
            }
            benchmark = TSPBenchmark(config=config)
            benchmark.add_algorithm(solver_name, AlgorithmConfig(
                function=checked_solver(solver), params={**params, "seed": solver_seed},
                post_optimize=False, is_class=False,
            ))
            random_state = np.random.get_state()
            try:
                result = benchmark.run_benchmark()[solver_name]
            finally:
                np.random.set_state(random_state)
            points = np.asarray(benchmark.points, dtype="<f8", order="C")
            route = result["route"]
            independent_length = sum(
                math.hypot(*(points[route[i]] - points[route[i + 1]])) for i in range(n)
            )
            if not math.isfinite(independent_length) or not math.isclose(
                independent_length, result["length"], rel_tol=1e-10, abs_tol=1e-12
            ):
                raise ValueError("benchmark length failed independent verification")
            records.append({
                "generator": method, "cities": n, "instance_seed": instance_seed,
                "instance_sha256": hashlib.sha256(points.tobytes()).hexdigest(),
                "solver_seed": solver_seed, "route": route,
                "length": float(independent_length), "seconds": float(result["time"]),
            })
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "solver": solver_name, "parameters": params,
        "distance": "unrounded Euclidean; closed tour",
        "warmup_seconds": warmup_seconds,
        "timer": "solver call including input copy, adapter, preprocessing and route validation",
        "environment": {
            "host": platform.node(), "python": platform.python_version(),
            "numpy": np.__version__,
            "smart_tsp_benchmark": importlib.metadata.version("smart-tsp-benchmark"),
            "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
            "slurm_cpus_per_task": os.environ.get("SLURM_CPUS_PER_TASK"),
        },
        "records": records,
    }


def main():
    parser = argparse.ArgumentParser(description="Run team solvers with Smart TSP Benchmark")
    parser.add_argument("--solver", required=True, help="importable module:function")
    parser.add_argument("--params", default="{}", help="JSON solver keyword parameters")
    parser.add_argument("--output", required=True, type=Path, help="new JSON file")
    args = parser.parse_args()
    module, separator, name = args.solver.partition(":")
    if not separator or not module or not name:
        parser.error("--solver must be module:function")
    params = json.loads(args.params)
    if not isinstance(params, dict):
        parser.error("--params must be a JSON object")
    solver = getattr(importlib.import_module(module), name)
    results = run_suite(solver, args.solver, params)
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(results, file, indent=2, allow_nan=False)
        file.write("\n")
    print(f"Completed {len(results['records'])} runs: {args.output}")


if __name__ == "__main__":
    main()
