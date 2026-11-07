"""Serial fixed-set nearest-neighbour baseline, nn-fixed-starts-v1.

Only unrounded Euclidean instances are supported. No random starts, deadlines,
progress history, solver persistence, or parallel execution are added implicitly.
"""

from dataclasses import dataclass, field
import math
from time import perf_counter

from numba import njit
import numpy as np

from tsp_hpc.data import TSPInstance, validate_city_index, validate_coordinates
from tsp_hpc.distances import (
    PreparedDistances, distance_edge_kernel, distance_tour_kernel, prepare_distances,
)
from tsp_hpc.objective import NumericalError, validate_solution
from tsp_hpc.random_streams import validate_seed
from tsp_hpc.results import SolverResult, WorkCounters

VARIANT = "nn-fixed-starts-v1"


@njit(cache=True, fastmath=False)
def _search_kernel(coordinates, matrix, use_dense, starts):
    """Trusted canonical arrays and distinct in-range starts; no Python callbacks.

    All workspace initialization is inside search. Scratch storage is O(n),
    reused across starts; the returned best buffer never aliases the candidate.
    """
    if len(starts) == 0:
        return np.empty(0, dtype=np.int64), 0.0, 0
    n = len(coordinates)
    candidate = np.empty(n, dtype=np.int64)
    visited = np.empty(n, dtype=np.bool_)
    best = np.empty(n, dtype=np.int64)
    best_cost = math.inf  # Internal sentinel only; never exposed as a result.
    completed = 0
    for start in starts:
        for city in range(n):
            visited[city] = False
        candidate[0] = start
        visited[start] = True
        current = start
        for position in range(1, n):
            nearest = -1
            nearest_cost = math.inf
            # Ascending city IDs plus strict improvement implement the tie rule.
            for city in range(n):
                if not visited[city]:
                    cost = distance_edge_kernel(coordinates, matrix, use_dense, current, city)
                    if cost < nearest_cost:
                        nearest = city
                        nearest_cost = cost
            if nearest < 0:
                raise NumericalError("NN could not select a finite unvisited city")
            candidate[position] = nearest
            visited[nearest] = True
            current = nearest
        cost = distance_tour_kernel(coordinates, matrix, use_dense, candidate)
        if completed == 0 or cost < best_cost:
            best_cost = cost
            for city in range(n):
                best[city] = candidate[city]
        completed += 1
    return best, best_cost, completed


def warmup_nearest_neighbour() -> None:
    """Warm both NN distance paths on fixed tiny data, outside measured search.

    Actual solver invocations use the same readonly coordinate/matrix and
    writable int64 start-array signatures. No caller data or RNG is consumed.
    """
    coordinates = np.array([[0., 0.], [1., 0.]], dtype=np.float64)
    matrix = np.array([[0., 1.], [1., 0.]], dtype=np.float64)
    coordinates.flags.writeable = False
    matrix.flags.writeable = False
    starts = np.array([0, 1], dtype=np.int64)
    for use_dense in (False, True):
        _search_kernel(coordinates, matrix, use_dense, starts)


def _request(instance, seed, budget, parameters, record_history) -> np.ndarray:
    if not isinstance(instance, TSPInstance):
        raise TypeError("instance must be a TSPInstance")
    if instance.distance_kind != "euclidean_2d":
        raise ValueError("NN supports only unrounded euclidean_2d instances")
    validate_coordinates(instance.coordinates)
    validate_seed(seed)
    if type(record_history) is not bool:
        raise TypeError("record_history must be boolean")
    if record_history:
        raise ValueError("NN history recording is not implemented")
    if type(parameters) is not dict:
        raise TypeError("parameters must be a dictionary")
    if parameters:
        raise ValueError("nn-fixed-starts-v1 accepts no algorithm parameters")
    if type(budget) is not dict:
        raise TypeError("budget must be a dictionary")
    if budget.keys() != {"start_cities"}:
        raise ValueError("NN requires only budget.start_cities; time limits are not supported")
    starts = budget["start_cities"]
    if type(starts) is not list:
        raise TypeError("start_cities must be an ordered list")
    starts = starts.copy()
    seen = set()
    for city in starts:
        if type(city) is not int:
            raise TypeError("start cities must be Python integers, not booleans")
        validate_city_index(city, instance.n_cities)
        if city in seen:
            raise ValueError("start_cities must contain distinct city IDs")
        seen.add(city)
    return np.array(starts, dtype=np.int64)


def _empty_result() -> SolverResult:
    return SolverResult("no_solution", None, None, WorkCounters(), "work_limit")


def _run(prepared: PreparedDistances, starts: np.ndarray) -> SolverResult:
    if starts.size == 0:
        return _empty_result()
    start = perf_counter()
    tour, cost, completed = _search_kernel(
        prepared.coordinates, prepared.matrix, prepared.use_dense, starts,
    )
    search_time = perf_counter() - start
    start = perf_counter()
    reference_cost = validate_solution(prepared.instance, tour, cost)
    validation_time = perf_counter() - start
    return SolverResult(
        "ok", tour, reference_cost,
        WorkCounters(nn_starts_completed=completed, full_tour_evaluations=completed),
        "work_limit", search_time_s=search_time, validation_time_s=validation_time,
    )


@dataclass(frozen=True, eq=False)
class NearestNeighbourSolver:
    """Serial adapter bound to one explicitly prepared instance/representation.

    Construct before measuring search. Reuse is safe under the input immutability
    contract; every solve owns its workspace and returned tour. No last-run state
    or RNG is stored. Use solve() below for a default on-demand convenience call.
    """

    prepared: PreparedDistances
    solver_warmup_time_s: float = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.prepared, PreparedDistances):
            raise TypeError("prepared must come from prepare_distances()")
        start = perf_counter()
        warmup_nearest_neighbour()
        object.__setattr__(self, "solver_warmup_time_s", perf_counter() - start)

    @property
    def metadata(self) -> dict:
        return {
            **self.prepared.metadata, "algorithm": "nn", "variant": VARIANT,
            "backend": "serial",
            "solver_warmup_time_s": self.solver_warmup_time_s,
        }

    def solve(self, instance, *, seed, budget, parameters, record_history=False) -> SolverResult:
        if not isinstance(instance, TSPInstance):
            raise TypeError("instance must be a TSPInstance")
        if instance is not self.prepared.instance:
            raise ValueError("solver is bound to a different instance; prepare a new adapter")
        starts = _request(instance, seed, budget, parameters, record_history)
        return _run(self.prepared, starts)


def solve(instance, *, seed, budget, parameters, record_history=False) -> SolverResult:
    """Shared solver interface, with default on-demand execution.

    This convenience call includes setup in its outer call duration, but not in
    result.search_time_s. Use NearestNeighbourSolver for explicit preparation,
    setup metadata, and reuse. Empty work returns without preparation or warm-up.
    """
    starts = _request(instance, seed, budget, parameters, record_history)
    if starts.size == 0:
        return _empty_result()
    solver = NearestNeighbourSolver(prepare_distances(instance, mode="on_demand"))
    return _run(solver.prepared, starts)
