"""Real NN kernels under explicit deadlines and safe-checkpoint histories."""

import numpy as np
import pytest

from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.problem.instances import fixture
from tsp_hpc.solvers.nn.solver import NearestNeighbourSolver, implementation_names, solve
from tsp_hpc.solvers import progress


@pytest.mark.parametrize("implementation", implementation_names())
@pytest.mark.parametrize("mode", ["dense", "on_demand"])
def test_history_does_not_change_fixed_work_result(implementation, mode):
    instance = fixture("square").instance
    adapter = NearestNeighbourSolver(prepare_distances(instance, mode=mode), implementation=implementation)
    arguments = dict(seed=7, budget={"start_cities": [2, 0, 3, 1]}, parameters={})
    plain = adapter.solve(instance, **arguments)
    observed = adapter.solve(instance, **arguments, record_history=True, history_policy={"interval_s": 0})
    np.testing.assert_array_equal(observed.best_tour, plain.best_tour)
    assert observed.best_cost == plain.best_cost
    assert observed.work == plain.work
    assert [point.work.nn_starts_completed for point in observed.history] == [1, 2, 3, 4]
    assert observed.history[-1].elapsed_s <= observed.search_time_s
    assert all(point.best_cost == 4 for point in observed.history)


@pytest.mark.parametrize("starts,expected_reason,completed", [([0, 1], "time_limit", 1),
                                                             ([0], "work_limit", 1)])
def test_safe_checkpoint_overshoot_and_precedence(monkeypatch, starts, expected_reason, completed):
    instance = fixture("square").instance
    ticks = iter([0., 0., 2., 2., 2.])
    monkeypatch.setattr(progress, "perf_counter", lambda: next(ticks))
    result = solve(instance, seed=1, parameters={}, budget={"start_cities": starts, "time_limit_s": 1},
                   record_history=True)
    assert result.termination_reason == expected_reason
    assert result.work.nn_starts_completed == completed
    assert result.search_time_s == 2
    assert result.best_cost == result.history[-1].best_cost == 4


def test_deadline_before_first_complete_tour_is_no_solution(monkeypatch):
    ticks = iter([0., 2., 2.])
    monkeypatch.setattr(progress, "perf_counter", lambda: next(ticks))
    result = solve(fixture("square").instance, seed=1, parameters={},
                   budget={"start_cities": [0, 1], "time_limit_s": 1}, record_history=True)
    assert result.status == "no_solution"
    assert result.termination_reason == "time_limit"
    assert result.work.full_tour_evaluations == 0
    assert result.history == ()


def test_empty_work_and_history_do_not_invent_an_incumbent():
    result = solve(fixture("square").instance, seed=1, parameters={},
                   budget={"start_cities": [], "time_limit_s": 1}, record_history=True)
    assert result.history == ()
    assert result.search_time_s == 0
    assert result.termination_reason == "work_limit"
