"""Serial adapter for the tournament/OX/swap/elitist GA baseline."""

from dataclasses import dataclass, field
from time import perf_counter

import numpy as np

from tsp_hpc.problem.data import TSPInstance
from tsp_hpc.problem.distances import PreparedDistances
from tsp_hpc.problem.objective import validate_solution
from tsp_hpc.reproducibility.native import require_native_runtime, verify_dispatchers
from tsp_hpc.reproducibility.random_streams import validate_seed
from tsp_hpc.solvers.configuration import VARIANTS, normalize_budget, normalize_history, normalize_parameters
from tsp_hpc.solvers.ga.kernels import advance_generation, initialize_candidate
from tsp_hpc.solvers.progress import SearchControl
from tsp_hpc.solvers.registry import SolverSpec
from tsp_hpc.solvers.results import SolverResult, WorkCounters

DEFAULT_IMPLEMENTATION = "serial-generational-v1"
_DISPATCHERS = (initialize_candidate, advance_generation)


def solver_spec():
    return SolverSpec("ga", DEFAULT_IMPLEMENTATION, GeneticAlgorithmSolver, "independent",
                      supports_time=True, supports_history=True)


def _warmup():
    """Compile both steps with the same array types as actual invocations."""
    require_native_runtime()
    coordinates = np.array([[0., 0.], [1., 0.]])
    matrix = np.array([[0., 1.], [1., 0.]])
    coordinates.flags.writeable = False
    matrix.flags.writeable = False
    population = np.empty((2, 2), dtype=np.int64)
    costs = np.empty(2, dtype=np.float64)
    next_population = np.empty_like(population)
    next_costs = np.empty_like(costs)
    copied = np.empty(2, dtype=np.bool_)
    best = np.empty(2, dtype=np.int64)
    rng = np.random.Generator(np.random.PCG64(0))
    for use_dense in (False, True):
        best_cost = float("inf")
        for slot, tour in enumerate(population):
            costs[slot], best_cost = initialize_candidate(
                coordinates, matrix, use_dense, tour, best, best_cost, rng)
        advance_generation(coordinates, matrix, use_dense, population, costs, next_population,
                           next_costs, 2, 1, 1.0, 1.0, copied, best, best_cost, rng)
    verify_dispatchers(_DISPATCHERS)


def _search(prepared, seed, budget, parameters, control):
    work = WorkCounters()
    reason = control.stop_reason(initialized=False)
    if reason is not None:
        return None, None, work, reason

    n = prepared.instance.n_cities
    size = parameters["population_size"]
    elites = parameters["elite_count"]
    generation_limit = budget.get("max_generations")
    distances = prepared.coordinates, prepared.matrix, prepared.use_dense
    rng = np.random.Generator(np.random.PCG64(seed))
    population = np.empty((size, n), dtype=np.int64)
    costs = np.empty(size, dtype=np.float64)
    best = np.empty(n, dtype=np.int64)
    best_cost = float("inf")
    for slot, tour in enumerate(population):
        costs[slot], best_cost = initialize_candidate(*distances, tour, best, best_cost, rng)
        work = WorkCounters(full_tour_evaluations=slot + 1)
        reason = control.stop_reason(initialized=slot + 1 == size,
                                     work_limit_reached=generation_limit == 0)
        if reason is not None:
            return best, best_cost, work, reason
        control.observe(work, best_cost)

    next_population = np.empty_like(population)
    next_costs = np.empty_like(costs)
    copied = np.empty(n, dtype=np.bool_)
    generations = 0
    while True:
        best_cost = advance_generation(
            *distances, population, costs, next_population, next_costs,
            parameters["tournament_size"], elites, parameters["crossover_probability"],
            parameters["mutation_probability"], copied, best, best_cost, rng)
        population, next_population = next_population, population
        costs, next_costs = next_costs, costs
        generations += 1
        work = WorkCounters(ga_generations_completed=generations,
                            full_tour_evaluations=size + generations * (size - elites))
        reason = control.stop_reason(work_limit_reached=generations == generation_limit)
        if reason is not None:
            return best, best_cost, work, reason
        control.observe(work, best_cost)


@dataclass(frozen=True, eq=False)
class GeneticAlgorithmSolver:
    """Bind prepared distances and reuse warm kernels across fresh populations."""

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
        return {
            **self.prepared.metadata, "algorithm": "ga", "variant": VARIANTS["ga"],
            "backend": "serial", "implementation": DEFAULT_IMPLEMENTATION,
            "solver_warmup_time_s": self.solver_warmup_time_s,
        }

    def solve(self, instance, *, seed, budget, parameters, record_history=False,
              history_policy=None) -> SolverResult:
        if not isinstance(instance, TSPInstance):
            raise TypeError("instance must be a TSPInstance")
        if instance is not self.prepared.instance:
            raise ValueError("solver is bound to a different instance; prepare a new adapter")
        validate_seed(seed)
        parameters = normalize_parameters("ga", parameters)
        budget = normalize_budget("ga", budget, instance.n_cities)
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
