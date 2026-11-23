"""Serial reversal simulated annealing through the shared solver contract."""

from dataclasses import dataclass, field
from time import perf_counter

import numpy as np

from tsp_hpc.problem.data import TSPInstance
from tsp_hpc.problem.distances import PreparedDistances
from tsp_hpc.problem.objective import validate_solution
from tsp_hpc.reproducibility.native import require_native_runtime, verify_dispatchers
from tsp_hpc.reproducibility.random_streams import validate_seed
from tsp_hpc.solvers.configuration import VARIANTS, normalize_budget, normalize_history, normalize_parameters
from tsp_hpc.solvers.progress import SearchControl
from tsp_hpc.solvers.registry import SolverSpec
from tsp_hpc.solvers.results import SolverResult, WorkCounters
from tsp_hpc.solvers.sa.kernels import advance_chain, initialize_chain

DEFAULT_IMPLEMENTATION = "serial-reversal-v1"
_DISPATCHERS = (initialize_chain, advance_chain)


def solver_spec():
    return SolverSpec("sa", DEFAULT_IMPLEMENTATION, SimulatedAnnealingSolver, "independent",
                      supports_time=True, supports_history=True)


def _warmup():
    require_native_runtime()
    coordinates = np.array([[0., 0.], [1., 0.], [1., 1.], [0., 1.]])
    matrix = np.linalg.norm(coordinates[:, None] - coordinates[None, :], axis=2)
    coordinates.flags.writeable = False
    matrix.flags.writeable = False
    current = np.empty(4, dtype=np.int64)
    best = np.empty_like(current)
    rng = np.random.Generator(np.random.PCG64(0))
    for use_dense in (False, True):
        cost = initialize_chain(coordinates, matrix, use_dense, current, best, rng)
        advance_chain(coordinates, matrix, use_dense, current, best, cost, cost,
                      1., .999, 1e-12, 2, 0, 1, 2, rng)
    verify_dispatchers(_DISPATCHERS)


def _search(prepared, seed, budget, parameters, control):
    work = WorkCounters()
    reason = control.stop_reason(initialized=False)
    if reason is not None:
        return None, None, work, reason

    current = np.empty(prepared.instance.n_cities, dtype=np.int64)
    best = np.empty_like(current)
    rng = np.random.Generator(np.random.PCG64(seed))
    distances = prepared.coordinates, prepared.matrix, prepared.use_dense
    cost = initialize_chain(*distances, current, best, rng)
    best_cost = cost
    temperature = parameters["initial_temperature"]
    proposals, evaluations = 0, 1
    limit = budget.get("max_proposals")
    work = WorkCounters(full_tour_evaluations=evaluations)
    reason = control.stop_reason(work_limit_reached=limit == 0, algorithm_stop=len(current) < 4)
    if reason is not None:
        return best, best_cost, work, reason
    control.observe(work, best_cost)

    chunk = budget.get("time_check_interval_proposals", 256 if control.policy is not None else limit)
    while True:
        count = chunk if limit is None else min(chunk, limit - proposals)
        cost, best_cost, temperature, proposals, evaluations = advance_chain(
            *distances, current, best, cost, best_cost, temperature,
            parameters["cooling_factor"], parameters["minimum_temperature"],
            parameters["cost_check_interval"], proposals, evaluations, count, rng)
        work = WorkCounters(sa_proposals_evaluated=proposals, full_tour_evaluations=evaluations)
        reason = control.stop_reason(work_limit_reached=proposals == limit)
        if reason is not None:
            return best, best_cost, work, reason
        control.observe(work, best_cost)


@dataclass(frozen=True, eq=False)
class SimulatedAnnealingSolver:
    prepared: PreparedDistances
    solver_warmup_time_s: float = field(init=False)

    def __post_init__(self):
        if not isinstance(self.prepared, PreparedDistances):
            raise TypeError("prepared must come from prepare_distances()")
        start = perf_counter()
        _warmup()
        object.__setattr__(self, "solver_warmup_time_s", perf_counter() - start)

    @property
    def native_dispatchers(self):
        return _DISPATCHERS

    @property
    def metadata(self):
        return {**self.prepared.metadata, "algorithm": "sa", "variant": VARIANTS["sa"],
                "backend": "serial", "implementation": DEFAULT_IMPLEMENTATION,
                "solver_warmup_time_s": self.solver_warmup_time_s}

    def solve(self, instance, *, seed, budget, parameters, record_history=False,
              history_policy=None):
        if not isinstance(instance, TSPInstance):
            raise TypeError("instance must be a TSPInstance")
        if instance is not self.prepared.instance:
            raise ValueError("solver is bound to a different instance; prepare a new adapter")
        validate_seed(seed)
        parameters = normalize_parameters("sa", parameters)
        budget = normalize_budget("sa", budget, instance.n_cities)
        policy = normalize_history(record_history, history_policy)
        require_native_runtime()
        control = SearchControl(time_limit_s=budget.get("time_limit_s"),
                                record_history=record_history, history_policy=policy)
        tour, cost, work, reason = _search(self.prepared, seed, budget, parameters, control)
        search_time, history = control.finish(work, cost)
        if tour is None:
            return SolverResult("no_solution", None, None, work, reason, history=history,
                                search_time_s=search_time)
        start = perf_counter()
        reference_cost = validate_solution(instance, tour, cost)
        return SolverResult("ok", tour, reference_cost, work, reason, history=history,
                            search_time_s=search_time, validation_time_s=perf_counter() - start)
