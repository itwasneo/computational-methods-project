import math
import numpy as np
import pytest

from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.problem.instances import fixture, uniform_instance
from tsp_hpc.problem.objective import NumericalError, tour_cost
from tsp_hpc.solvers.conformance import check_adapter
from tsp_hpc.solvers.sa.kernels import advance_chain
from tsp_hpc.solvers.sa.operators import sample_edges
from tsp_hpc.solvers.sa.solver import SimulatedAnnealingSolver, solver_spec


@pytest.mark.parametrize("mode", ["dense", "on_demand"])
def test_fixed_work_and_history_replay(mode):
    instance = uniform_instance(12, seed=3).instance
    result = check_adapter(solver_spec(), instance, seed=17, budget={"max_proposals": 1000},
                           parameters={"cost_check_interval": 20}, distance_mode=mode)
    assert result.work.sa_proposals_evaluated == 1000
    assert result.work.full_tour_evaluations >= 51
    assert result.best_cost == tour_cost(instance, result.best_tour)


@pytest.mark.parametrize("name", ["two-city", "triangle"])
@pytest.mark.parametrize("limit,reason", [(0, "work_limit"), (5, "algorithm_stop")])
def test_empty_neighborhood_and_zero_budget(name, limit, reason):
    instance = fixture(name).instance
    solver = SimulatedAnnealingSolver(prepare_distances(instance, mode="on_demand"))
    result = solver.solve(instance, seed=7, budget={"max_proposals": limit}, parameters={})
    assert result.termination_reason == reason
    assert result.work.sa_proposals_evaluated == 0
    assert result.work.full_tour_evaluations == 1
    assert result.best_cost == tour_cost(instance, result.best_tour)


def test_chunking_preserves_fixed_work_trajectory():
    instance = uniform_instance(20, seed=5).instance
    solver = SimulatedAnnealingSolver(prepare_distances(instance, mode="dense"))
    args = dict(seed=42, budget={"max_proposals": 2000},
                parameters={"cost_check_interval": 17})
    single = solver.solve(instance, **args)
    observed = solver.solve(instance, **args, record_history=True)
    np.testing.assert_array_equal(single.best_tour, observed.best_tour)
    assert single.best_cost == observed.best_cost
    assert single.work == observed.work


def test_time_only_stops_and_respects_checkpoint():
    instance = uniform_instance(12, seed=5).instance
    solver = SimulatedAnnealingSolver(prepare_distances(instance, mode="dense"))
    result = solver.solve(instance, seed=1,
                          budget={"time_limit_s": .01, "time_check_interval_proposals": 32},
                          parameters={}, record_history=True)
    assert result.termination_reason == "time_limit"
    assert result.search_time_s >= .01
    assert result.work.sa_proposals_evaluated % 32 == 0


def test_cooling_floor_and_periodic_checks():
    coordinates = np.zeros((4, 2), dtype=np.float64)
    matrix = np.zeros((4, 4), dtype=np.float64)
    current = np.arange(4, dtype=np.int64)
    best = current.copy()
    state = advance_chain(coordinates, matrix, True, current, best, 0., 0.,
                          1., .5, .2, 2, 0, 1, 5, np.random.default_rng(7))
    assert state == (0., 0., .2, 5, 3)
    np.testing.assert_array_equal(best, np.arange(4))


def test_numerical_drift_is_reported_not_hidden():
    coordinates = np.zeros((4, 2), dtype=np.float64)
    matrix = np.zeros((4, 4), dtype=np.float64)
    current = np.arange(4, dtype=np.int64)
    with pytest.raises(NumericalError, match="drift"):
        advance_chain(coordinates, matrix, True, current, current.copy(), 1., 0.,
                      1., .9, .1, 1, 0, 1, 1, np.random.default_rng(3))


def test_uphill_rejections_do_not_modify_current_or_best():
    instance = fixture("square").instance
    prepared = prepare_distances(instance, mode="dense")
    current = np.array([0, 1, 2, 3], dtype=np.int64)
    best = current.copy()
    state = advance_chain(prepared.coordinates, prepared.matrix, True, current, best,
                          4., 4., 1e-300, .9, 1e-300, 100, 0, 1, 7,
                          np.random.default_rng(1))
    np.testing.assert_array_equal(current, [0, 1, 2, 3])
    np.testing.assert_array_equal(best, current)
    assert state[3:] == (7, 1)


def test_equal_cost_move_is_accepted_without_acceptance_draw():
    coordinates = np.zeros((4, 2), dtype=np.float64)
    matrix = np.zeros((4, 4), dtype=np.float64)
    current = np.arange(4, dtype=np.int64)
    best = current.copy()
    rng, reference_rng = np.random.default_rng(11), np.random.default_rng(11)
    sample_edges(4, reference_rng)
    advance_chain(coordinates, matrix, True, current, best, 0., 0.,
                  1., 1., .1, 100, 0, 1, 1, rng)
    assert not np.array_equal(current, best)
    assert rng.random() == reference_rng.random()


def test_seeded_trajectory_matches_independent_python_reference():
    instance = uniform_instance(9, seed=123).instance
    prepared = prepare_distances(instance, mode="dense")
    solver = SimulatedAnnealingSolver(prepared)
    seed, count, interval = 81, 180, 7
    params = {"initial_temperature": .3, "cooling_factor": .97,
              "minimum_temperature": .05, "cost_check_interval": interval}
    result = solver.solve(instance, seed=seed, budget={"max_proposals": count}, parameters=params)

    rng = np.random.default_rng(seed)
    tour = np.arange(instance.n_cities, dtype=np.int64)
    for pos in range(len(tour) - 1, 0, -1):
        other = rng.integers(0, pos + 1)
        tour[pos], tour[other] = tour[other], tour[pos]
    cost = tour_cost(instance, tour)
    best, best_cost, evaluations, temperature = tour.copy(), cost, 1, params["initial_temperature"]
    matrix = prepared.matrix
    for proposal in range(1, count + 1):
        while True:
            i = rng.integers(0, len(tour))
            j = rng.integers(0, len(tour) - 1)
            if j >= i:
                j += 1
            i, j = sorted((i, j))
            if j != i + 1 and (i, j) != (0, len(tour) - 1):
                break
        a, b, c, d = tour[i], tour[i + 1], tour[j], tour[(j + 1) % len(tour)]
        delta = matrix[a, c] + matrix[b, d] - matrix[a, b] - matrix[c, d]
        accept = delta <= 0
        if not accept:
            u = rng.random()
            while u == 0:
                u = rng.random()
            accept = math.log(u) < -delta / temperature
        if accept:
            tour[i + 1:j + 1] = tour[i + 1:j + 1][::-1]
            cost += delta
        if cost < best_cost or proposal % interval == 0:
            cost = tour_cost(instance, tour)
            evaluations += 1
            if cost < best_cost:
                best, best_cost = tour.copy(), cost
        temperature = max(params["minimum_temperature"], temperature * params["cooling_factor"])

    np.testing.assert_array_equal(result.best_tour, best)
    assert result.best_cost == best_cost
    assert result.work.sa_proposals_evaluated == count
    assert result.work.full_tour_evaluations == evaluations
