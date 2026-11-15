"""Reusable tiny-input adapter checks; neither a solver nor a benchmark harness.

No pytest dependency. Algorithm tests supply a SolverSpec and a bounded fixture.
Failures raise the ordinary validation error or AssertionError. Operators and
scientific trajectories still require algorithm-specific tests.
"""

from copy import deepcopy
import random
from time import perf_counter

import numpy as np

from tsp_hpc.problem.data import TSPInstance
from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.problem.objective import validate_solution
from tsp_hpc.reproducibility.random_streams import validate_seed
from tsp_hpc.solvers.configuration import normalize_budget, normalize_history, normalize_parameters
from tsp_hpc.solvers.registry import SolverSpec, validate_adapter_metadata
from tsp_hpc.solvers.validation import validate_nested_duration, validate_result


def check_adapter(spec, instance, *, seed, budget, parameters=None, distance_mode="on_demand",
                  record_history=False, history_policy=None):
    """Check ownership, validation, replay and instrumentation on at most 64 cities.

    Fixed work is replayed after an intervening different-seed call and compared
    with recording toggled. Timed runs are validated, never required to replay
    wall-clock-dependent work. Initialization and safe stopping follow the shared
    result rules. Return the original validated SolverResult for further tests.
    """
    if not isinstance(spec, SolverSpec) or not isinstance(instance, TSPInstance):
        raise TypeError("conformance requires a SolverSpec and TSPInstance")
    if instance.n_cities > 64:
        raise ValueError("conformance uses tiny fixtures (at most 64 cities), not benchmark inputs")
    validate_seed(seed)
    budget = normalize_budget(spec.algorithm, budget, instance.n_cities)
    parameters = normalize_parameters(spec.algorithm, {} if parameters is None else parameters)
    policy = normalize_history(record_history, history_policy)
    search = {"algorithm": spec.algorithm, "parameters": parameters, "budget": budget,
              "instance": {"n_cities": instance.n_cities}, "record_history": record_history,
              "history_policy": policy}
    coordinates = instance.coordinates.copy()
    python_state, numpy_state = random.getstate(), np.random.get_state()
    prepared = prepare_distances(instance, mode=distance_mode)
    matrix = prepared.matrix.copy()
    flags = instance.coordinates.flags.writeable, prepared.matrix.flags.writeable
    adapter = spec.factory(prepared)
    validate_adapter_metadata(adapter, prepared, spec)

    def invoke(request_seed, history, sampling):
        request = {"seed": request_seed, "budget": deepcopy(budget), "parameters": deepcopy(parameters),
                   "record_history": history, "history_policy": deepcopy(sampling)}
        snapshot = deepcopy(request)
        start = perf_counter()
        result = adapter.solve(instance, **request)
        wall = perf_counter() - start
        validate_result(result, {**search, "record_history": history, "history_policy": sampling})
        validate_nested_duration(result.search_time_s + result.validation_time_s, wall)
        assert request == snapshot, "adapter mutated caller configuration"
        np.testing.assert_array_equal(instance.coordinates, coordinates)
        np.testing.assert_array_equal(prepared.matrix, matrix)
        assert (instance.coordinates.flags.writeable, prepared.matrix.flags.writeable) == flags
        assert random.getstate() == python_state, "adapter consumed Python global RNG"
        for before, after in zip(numpy_state, np.random.get_state()):
            np.testing.assert_array_equal(before, after, err_msg="adapter consumed NumPy global RNG")
        if result.status == "ok":
            validate_solution(instance, result.best_tour, result.best_cost)
            assert not result.best_tour.flags.writeable, "result must be a read-only snapshot"
            assert not np.shares_memory(result.best_tour, instance.coordinates)
            assert not np.shares_memory(result.best_tour, prepared.matrix)
        return result

    first = invoke(seed, record_history, policy)
    if "time_limit_s" not in budget:
        snapshot = None if first.best_tour is None else first.best_tour.copy()
        invoke(seed ^ 1, record_history, policy)
        repeated = invoke(seed, record_history, policy)
        candidates = [repeated]
        if spec.supports_history:
            alternate = normalize_history(not record_history)
            candidates.append(invoke(seed, not record_history, alternate))
        for result in candidates:
            assert (result.status, result.work, result.termination_reason, result.best_cost) == (
                first.status, first.work, first.termination_reason, first.best_cost), "fixed-work replay/history changed search"
            if snapshot is not None:
                np.testing.assert_array_equal(first.best_tour, snapshot, err_msg="later call changed an earlier result")
                np.testing.assert_array_equal(result.best_tour, snapshot)
                assert not np.shares_memory(result.best_tour, first.best_tour)
    return first
