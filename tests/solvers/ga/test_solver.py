from functools import partial

import numpy as np
import pytest

from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.problem.instances import fixture
from tsp_hpc.solvers.ga import solver as ga


@pytest.mark.parametrize("elite_count", [0, 2])
def test_equal_costs_keep_first_ever_tour_and_evaluate_only_offspring(elite_count):
    instance = fixture("coincident").instance
    solver = ga.GeneticAlgorithmSolver(prepare_distances(instance, mode="on_demand"))
    seed = 2**63 + 7
    rng = np.random.Generator(np.random.PCG64(seed))
    expected = np.arange(instance.n_cities)
    for position in range(len(expected) - 1, 0, -1):
        other = rng.integers(0, position + 1)
        expected[position], expected[other] = expected[other], expected[position]

    population_size = 5
    result = solver.solve(instance, seed=seed, budget={"max_generations": 3},
                          parameters={"population_size": population_size, "elite_count": elite_count,
                                      "crossover_probability": 1.0, "mutation_probability": 1.0},
                          record_history=True, history_policy={"interval_s": 0})
    assert result.termination_reason == "work_limit"
    assert result.best_cost == 0.0
    assert result.work.ga_generations_completed == 3
    offspring = population_size - elite_count
    assert [point.work.full_tour_evaluations for point in result.history] == [
        1, 2, 3, 4, 5, 5 + offspring, 5 + 2 * offspring, 5 + 3 * offspring,
    ]
    np.testing.assert_array_equal(result.best_tour, expected)
    assert not result.best_tour.flags.writeable


@pytest.mark.parametrize("limit,generation_limit,generations,evaluations,reason", [
    (0.5, 0, 0, 0, "time_limit"),
    (2.0, 0, 0, 1, "time_limit"),
    (4.0, 0, 0, 3, "work_limit"),
    (5.0, None, 1, 5, "time_limit"),
    (5.0, 1, 1, 5, "work_limit"),
])
def test_deadlines_during_initialization_and_after_atomic_generations(
    monkeypatch, limit, generation_limit, generations, evaluations, reason,
):
    instance = fixture("square").instance
    solver = ga.GeneticAlgorithmSolver(prepare_distances(instance, mode="dense"))
    clock_values = iter((0.0, 1.0))
    now = 1.0

    def clock():
        return next(clock_values, now)

    monkeypatch.setattr(ga, "SearchControl", partial(ga.SearchControl, clock=clock))

    def timed_step(step, seconds):
        def run(*args):
            nonlocal now
            result = step(*args)
            now += seconds
            return result
        return run

    monkeypatch.setattr(ga, "initialize_candidate", timed_step(ga.initialize_candidate, 1.0))
    monkeypatch.setattr(ga, "advance_generation", timed_step(ga.advance_generation, 6.0))
    budget = {"time_limit_s": limit}
    if generation_limit is not None:
        budget["max_generations"] = generation_limit
    result = solver.solve(instance, seed=7, budget=budget, parameters={"population_size": 3},
                          record_history=True, history_policy={"interval_s": 0})
    assert result.termination_reason == reason
    assert result.work.ga_generations_completed == generations
    assert result.work.full_tour_evaluations == evaluations
    assert result.status == ("ok" if evaluations else "no_solution")
    if evaluations:
        assert result.history[-1].work == result.work
    else:
        assert result.history == ()
    if generations:
        assert result.search_time_s == 10.0
