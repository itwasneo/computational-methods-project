"""Serial fixed-set nearest-neighbour baseline, nn-fixed-starts-v1.

Only unrounded Euclidean instances are supported. Time/history are explicit;
random starts, persistence and parallel execution are never added implicitly.
"""

from dataclasses import dataclass, field
from functools import partial
from time import perf_counter
from types import MappingProxyType

import numpy as np

from tsp_hpc.problem.data import TSPInstance, validate_coordinates
from tsp_hpc.solvers.configuration import normalize_budget, normalize_parameters, normalize_history
from tsp_hpc.solvers.progress import SearchControl
from tsp_hpc.problem.distances import PreparedDistances, prepare_distances
from tsp_hpc.solvers.nn.visited_scan import search_kernel as _search_kernel
from tsp_hpc.solvers.nn.remaining_array import search_kernel as _remaining_array_kernel
from tsp_hpc.problem.objective import validate_solution
from tsp_hpc.reproducibility.random_streams import validate_seed
from tsp_hpc.solvers.results import SolverResult, WorkCounters

VARIANT = "nn-fixed-starts-v1"
DEFAULT_IMPLEMENTATION = "visited-scan-v1"
_IMPLEMENTATIONS = MappingProxyType({
    "visited-scan-v1": _search_kernel,
    "remaining-array-v1": _remaining_array_kernel,
})


def solver_specs():
    """Explicit adapter registrations; constructing them does not warm kernels."""
    from tsp_hpc.solvers.registry import SolverSpec
    return tuple(SolverSpec("nn", name, partial(NearestNeighbourSolver, implementation=name), "batch",
                            supports_time=True, supports_history=True)
                 for name in implementation_names())


def implementation_names() -> tuple[str, ...]:
    """Available serial implementations of the same scientific NN variant."""
    return tuple(_IMPLEMENTATIONS)


def _resolve_implementation(implementation: str):
    if type(implementation) is not str:
        raise TypeError("implementation must be a string")
    try:
        return _IMPLEMENTATIONS[implementation]
    except KeyError:
        raise ValueError(f"unsupported serial NN implementation: {implementation!r}") from None


def warmup_nearest_neighbour(*, implementation: str = DEFAULT_IMPLEMENTATION) -> None:
    """Warm both NN distance paths on fixed tiny data, outside measured search.

    Actual solver invocations use the same readonly coordinate/matrix and
    writable int64 start-array signatures. No caller data or RNG is consumed.
    """
    kernel = _resolve_implementation(implementation)
    coordinates = np.array([[0., 0.], [1., 0.]], dtype=np.float64)
    matrix = np.array([[0., 1.], [1., 0.]], dtype=np.float64)
    coordinates.flags.writeable = False
    matrix.flags.writeable = False
    starts = np.array([0, 1], dtype=np.int64)
    for use_dense in (False, True):
        kernel(coordinates, matrix, use_dense, starts)


def _request(instance, seed, budget, parameters, record_history, history_policy):
    if not isinstance(instance, TSPInstance):
        raise TypeError("instance must be a TSPInstance")
    if instance.distance_kind != "euclidean_2d":
        raise ValueError("NN supports only unrounded euclidean_2d instances")
    validate_coordinates(instance.coordinates)
    validate_seed(seed)
    policy = normalize_history(record_history, history_policy)
    normalize_parameters("nn", parameters)
    normalized = normalize_budget("nn", budget, instance.n_cities)
    return np.array(normalized["start_cities"], dtype=np.int64), normalized, policy


def _empty_result(record_history=False) -> SolverResult:
    return SolverResult("no_solution", None, None, WorkCounters(), "work_limit",
                        history=() if record_history else None)


def _run(prepared, starts, kernel, budget, record_history, policy) -> SolverResult:
    if starts.size == 0:
        return _empty_result(record_history)
    history = None
    reason = "work_limit"
    if "time_limit_s" not in budget and not record_history:
        # Preserve the original fixed-work kernel call and workspace allocation.
        start = perf_counter()
        tour, cost, completed = kernel(prepared.coordinates, prepared.matrix, prepared.use_dense, starts)
        search_time = perf_counter() - start
        work = WorkCounters(nn_starts_completed=completed, full_tour_evaluations=completed)
    else:
        control = SearchControl(time_limit_s=budget.get("time_limit_s"),
                                record_history=record_history, history_policy=policy)
        tour = cost = None
        work = WorkCounters()
        reason = control.stop_reason()
        for index in range(len(starts)):
            if reason is not None:
                break
            candidate, candidate_cost, _ = kernel(
                prepared.coordinates, prepared.matrix, prepared.use_dense, starts[index:index + 1])
            if cost is None or candidate_cost < cost:
                tour, cost = candidate, candidate_cost
            work = WorkCounters(nn_starts_completed=index + 1, full_tour_evaluations=index + 1)
            reason = control.stop_reason(work_limit_reached=index + 1 == len(starts))
            if reason is None:
                control.observe(work, cost)
        search_time, history = control.finish(work, cost)
        if tour is None:
            return SolverResult("no_solution", None, None, work, reason, history=history,
                                search_time_s=search_time)
    start = perf_counter()
    reference_cost = validate_solution(prepared.instance, tour, cost)
    validation_time = perf_counter() - start
    return SolverResult("ok", tour, reference_cost, work, reason, history=history,
                        search_time_s=search_time, validation_time_s=validation_time)


@dataclass(frozen=True, eq=False)
class NearestNeighbourSolver:
    """Serial adapter bound to one explicitly prepared instance/representation.

    Construct before measuring search. Reuse is safe under the input immutability
    contract; every solve owns its workspace and returned tour. No last-run state
    or RNG is stored. Use solve() below for a default on-demand convenience call.
    """

    prepared: PreparedDistances
    implementation: str = field(default=DEFAULT_IMPLEMENTATION, kw_only=True)
    solver_warmup_time_s: float = field(init=False)
    _kernel: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.prepared, PreparedDistances):
            raise TypeError("prepared must come from prepare_distances()")
        kernel = _resolve_implementation(self.implementation)
        start = perf_counter()
        warmup_nearest_neighbour(implementation=self.implementation)
        object.__setattr__(self, "solver_warmup_time_s", perf_counter() - start)
        object.__setattr__(self, "_kernel", kernel)

    @property
    def metadata(self) -> dict:
        return {
            **self.prepared.metadata, "algorithm": "nn", "variant": VARIANT,
            "backend": "serial", "implementation": self.implementation,
            "solver_warmup_time_s": self.solver_warmup_time_s,
        }

    def solve(self, instance, *, seed, budget, parameters, record_history=False,
              history_policy=None) -> SolverResult:
        if not isinstance(instance, TSPInstance):
            raise TypeError("instance must be a TSPInstance")
        if instance is not self.prepared.instance:
            raise ValueError("solver is bound to a different instance; prepare a new adapter")
        starts, budget, policy = _request(instance, seed, budget, parameters, record_history, history_policy)
        return _run(self.prepared, starts, self._kernel, budget, record_history, policy)


def solve(instance, *, seed, budget, parameters, record_history=False, history_policy=None) -> SolverResult:
    """Shared solver interface, with default on-demand execution.

    This convenience call includes setup in its outer call duration, but not in
    result.search_time_s. Use NearestNeighbourSolver for explicit preparation,
    setup metadata, and reuse. Empty work returns without preparation or warm-up.
    """
    starts, budget, policy = _request(instance, seed, budget, parameters, record_history, history_policy)
    if starts.size == 0:
        return _empty_result(record_history)
    solver = NearestNeighbourSolver(prepare_distances(instance, mode="on_demand"))
    return _run(solver.prepared, starts, solver._kernel, budget, record_history, policy)
