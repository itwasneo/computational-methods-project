"""Bounded local NN phase/profile/scaling pilot; not a production benchmark claim.

Run from a clean checkout with uv run python tools/multicore_pilot.py OUTPUT.
Writes fresh canonical inputs, manifests, native records, serial cProfile output
and a descriptive summary. Never overwrites an existing output directory.
"""
import argparse
import cProfile
import json
import os
from pathlib import Path
import pstats
import statistics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--cities", type=int, nargs="+", default=[256, 1024])
    parser.add_argument("--threads", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--starts", type=int, default=64)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if (not all(2 <= n <= 4096 for n in args.cities) or not 1 <= args.starts <= 128
            or not 3 <= args.repetitions <= 10 or len(set(args.threads)) != len(args.threads)
            or len(set(args.cities)) != len(args.cities)):
        parser.error("unsupported pilot bounds")
    # Deliberate child-process policy before importing numerical packages.
    for key in ("NUMBA_NUM_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[key] = "1"
    from tsp_hpc.solvers.resources import validate_threads
    for threads in args.threads:
        validate_threads(threads)
    if hasattr(os, "sched_getaffinity"):
        allowed = sorted(os.sched_getaffinity(0))
        os.sched_setaffinity(0, allowed[:max(args.threads)])
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ["NUMBA_CACHE_DIR"] = str(args.output.resolve() / "cache")
    from tsp_hpc.problem.instances import uniform_instance, save_instance
    from tsp_hpc.problem.distances import prepare_distances
    from tsp_hpc.solvers.nn.solver import NearestNeighbourSolver
    from tsp_hpc.experiments.manifests import Manifest
    from tsp_hpc.experiments.runner import run_manifest
    from tsp_hpc.analysis import read_shards

    summary = []
    for n in args.cities:
        record = uniform_instance(n, seed=2026)
        path = args.output / f"instance-{n}.json"
        save_instance(record, path)
        starts = list(range(min(n, args.starts)))
        # Warm before cProfile; this profile is separate from measured campaigns.
        adapter = NearestNeighbourSolver(prepare_distances(record.instance, mode="on_demand"))
        request = {"seed": 42, "parameters": {}, "budget": {"start_cities": starts}}
        adapter.solve(record.instance, **request)
        profiler = cProfile.Profile()
        profiler.runcall(adapter.solve, record.instance, **request)
        with (args.output / f"serial-profile-{n}.txt").open("x") as stream:
            pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(25)
        executions = []
        for mode in ("on_demand", "dense"):
            executions.append({"backend": "serial", "implementation": "visited-scan-v1", "distance_mode": mode,
                "resources": {"profile": "local-pilot", "workers": 1, "threads_per_worker": 1}})
            executions.extend({"backend": "multicore", "implementation": "visited-scan-threaded-v1", "distance_mode": mode,
                "resources": {"profile": "local-pilot", "workers": 1, "threads_per_worker": t}} for t in args.threads)
        experiments = [{"instance_id": record.instance.instance_id, "algorithm": "nn", "parameters": {},
            "budget": {"start_cities": starts}, "solver_seed": 42, "repetitions": [r], "record_history": False,
            "executions": executions[r % len(executions):] + executions[:r % len(executions)]}
            for r in range(args.repetitions)]
        manifest = Manifest({"schema_version": 1, "instances": [record.descriptor], "experiments": experiments})
        (args.output / f"manifest-{n}.json").write_text(json.dumps(manifest.to_dict(), indent=2) + "\n")
        shard = args.output / f"results-{n}.jsonl"
        result = run_manifest(manifest, instance_paths={record.instance.instance_id: path}, output_path=shard)
        if result["counts"]["failed"]:
            raise RuntimeError("pilot contains failed trials; inspect preserved records")
        analysis = read_shards([shard], expected_manifest=manifest)
        if analysis.diagnostics or not analysis.trials["usable"].all():
            raise RuntimeError("pilot records failed shared validation")
        by_attempt = analysis.trials.set_index("attempt_id")
        rows = [json.loads(line) for line in shard.read_text().splitlines()][:-1]
        baseline = rows[0]["result"]
        if any((row["result"]["best_tour"], row["result"]["best_cost"], row["result"]["work"]) !=
               (baseline["best_tour"], baseline["best_cost"], baseline["work"]) for row in rows):
            raise RuntimeError("fixed-work equivalence failed")
        for mode in ("on_demand", "dense"):
            groups = {}
            for row in rows:
                config = row["effective_execution"]
                if config["distance_mode"] != mode:
                    continue
                key = (config["backend"], config["resources"]["threads_per_worker"])
                groups.setdefault(key, []).append(row)
            serial_time = statistics.median(r["timings"]["search_time_s"] for r in groups["serial", 1])
            for (backend, threads), trials in groups.items():
                phases = {key: statistics.median(float(by_attempt.at[r["attempt_id"], key]) for r in trials)
                          for key in ("search_time_s", "end_to_end_time_s", "preprocessing_time_s", "solver_warmup_time_s")}
                summary.append({"n_cities": n, "starts": len(starts), "distance_mode": mode,
                    "backend": backend, "threads": threads, "trials": len(trials), **phases,
                    "descriptive_search_ratio": serial_time / phases["search_time_s"]})
    document = {"scope": "local exploratory pilot; explicit resource changes, not implementation-only paired ratios",
                "rows": summary, "provenance": rows[0]["runtime"]}
    (args.output / "summary.json").write_text(json.dumps(document, indent=2) + "\n")
    print(json.dumps(document, indent=2))


if __name__ == "__main__":
    main()
