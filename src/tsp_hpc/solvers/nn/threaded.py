"""Fixed-work NN over bounded threads running GIL-free native serial kernels.

Contiguous logical chunks and ordered reduction preserve serial tie semantics.
There is one process, no nested native parallel team, and no per-start Python
scheduling. Exceptions propagate through futures, not through a prange loop.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from time import perf_counter

from numba import njit
import numpy as np

from tsp_hpc.problem.distances import PreparedDistances
from tsp_hpc.problem.objective import validate_solution
from tsp_hpc.reproducibility.native import require_native_runtime, verify_dispatchers
from tsp_hpc.solvers.nn.solver import _request, _empty_result, VARIANT
from tsp_hpc.solvers.nn.visited_scan import search_kernel
from tsp_hpc.solvers.resources import validate_threads
from tsp_hpc.solvers.results import SolverResult, WorkCounters

IMPLEMENTATION = "visited-scan-threaded-v1"


@njit(cache=True, fastmath=False, nogil=True)
def chunk_kernel(coordinates, matrix, use_dense, starts):
    return search_kernel(coordinates, matrix, use_dense, starts)


@dataclass(frozen=True, eq=False)
class ThreadedNearestNeighbourSolver:
    prepared: PreparedDistances
    threads: int = 1
    solver_warmup_time_s: float = field(init=False)

    def __post_init__(self):
        if not isinstance(self.prepared, PreparedDistances):
            raise TypeError("prepared must come from prepare_distances()")
        validate_threads(self.threads)
        require_native_runtime()
        start = perf_counter()
        coordinates = np.array([[0., 0.], [1., 0.]], dtype=np.float64)
        matrix = np.array([[0., 1.], [1., 0.]], dtype=np.float64)
        coordinates.flags.writeable = matrix.flags.writeable = False
        for dense in (False, True):
            chunk_kernel(coordinates, matrix, dense, np.array([0, 1], dtype=np.int64))
        verify_dispatchers(self.native_dispatchers)
        object.__setattr__(self, "solver_warmup_time_s", perf_counter() - start)

    @property
    def native_dispatchers(self):
        return (chunk_kernel,)

    @property
    def metadata(self):
        return {**self.prepared.metadata, "algorithm": "nn", "variant": VARIANT,
                "backend": "multicore", "implementation": IMPLEMENTATION,
                "solver_warmup_time_s": self.solver_warmup_time_s}

    def solve(self, instance, *, seed, budget, parameters, record_history=False, history_policy=None):
        if instance is not self.prepared.instance:
            raise ValueError("solver is bound to a different instance")
        starts, budget, _ = _request(instance, seed, budget, parameters, record_history, history_policy)
        if "time_limit_s" in budget or record_history:
            raise ValueError("multicore NN supports fixed work without progress history only")
        validate_threads(self.threads)  # Recheck if affinity changed after preparation.
        if not len(starts):
            return _empty_result()
        require_native_runtime()
        start = perf_counter()
        count = min(self.threads, len(starts))
        chunks = [starts[i * len(starts) // count:(i + 1) * len(starts) // count].copy()
                  for i in range(count)]
        prepared = self.prepared
        def run(chunk):
            return chunk_kernel(prepared.coordinates, prepared.matrix, prepared.use_dense, chunk)
        with ThreadPoolExecutor(max_workers=self.threads, thread_name_prefix="tsp-nn") as pool:
            # map returns in logical chunk order, not completion order.
            results = list(pool.map(run, chunks))
        winner = min(range(count), key=lambda i: results[i][1])
        tour, cost, _ = results[winner]
        completed = sum(item[2] for item in results)
        search_time = perf_counter() - start
        start = perf_counter()
        cost = validate_solution(instance, tour, cost)
        return SolverResult("ok", tour, cost,
                            WorkCounters(nn_starts_completed=completed, full_tour_evaluations=completed),
                            "work_limit", search_time_s=search_time, validation_time_s=perf_counter() - start)
