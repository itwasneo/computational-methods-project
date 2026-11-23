"""Fixed logical chains run concurrently with isolated state and RNG streams."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from time import perf_counter

from tsp_hpc.reproducibility.random_streams import validate_seed
from tsp_hpc.solvers.configuration import normalize_budget, normalize_parameters
from tsp_hpc.solvers.resources import validate_threads
from tsp_hpc.solvers.sa.solver import SimulatedAnnealingSolver


@dataclass(frozen=True)
class ChainBatch:
    seeds: tuple
    results: tuple
    best_index: int
    wall_time_s: float
    threads: int

    @property
    def best(self):
        return self.results[self.best_index]


def solve_chains(solver, *, seeds, budget, parameters, threads=1):
    """Run ordered fixed-work chains; wall time includes scheduling and validation.

    Seeds are already-derived logical chain seeds. Each chain owns its generator
    and state; completion order does not affect the returned order or tie-breaking.
    Time limits and progress histories are intentionally not supported here.
    """
    if not isinstance(solver, SimulatedAnnealingSolver):
        raise TypeError("solver must be a prepared SimulatedAnnealingSolver")
    validate_threads(threads)
    if type(seeds) not in (list, tuple) or not seeds:
        raise ValueError("seeds must be a nonempty ordered list or tuple")
    seeds = tuple(seeds)
    for seed in seeds:
        validate_seed(seed)
    budget = normalize_budget("sa", budget, solver.prepared.instance.n_cities)
    parameters = normalize_parameters("sa", parameters)
    if "time_limit_s" in budget:
        raise ValueError("parallel chains require fixed work without time limits")

    start = perf_counter()
    instance = solver.prepared.instance

    def run(seed):
        return solver.solve(instance, seed=seed, budget=budget, parameters=parameters)

    if threads == 1:
        results = tuple(map(run, seeds))
    else:
        with ThreadPoolExecutor(max_workers=min(threads, len(seeds)), thread_name_prefix="tsp-sa") as pool:
            results = tuple(pool.map(run, seeds))
    elapsed = perf_counter() - start
    winner = min(range(len(results)), key=lambda index: results[index].best_cost)
    return ChainBatch(seeds, results, winner, elapsed, threads)
