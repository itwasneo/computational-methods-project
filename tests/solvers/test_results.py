"""Shared result structure and ownership, separate from solver correctness."""

from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from tsp_hpc.solvers.results import SolverResult, WorkCounters


def result(**changes):
    fields = dict(status="ok", best_tour=np.arange(3, dtype=np.int64), best_cost=12.,
                  work=WorkCounters(nn_starts_completed=1, full_tour_evaluations=1),
                  termination_reason="work_limit")
    fields.update(changes)
    return SolverResult(**fields)


def test_result_owns_readonly_tour_and_immutable_counters():
    tour = np.arange(3, dtype=np.int64)
    record = result(best_tour=tour)
    tour[:] = 0
    np.testing.assert_array_equal(record.best_tour, [0, 1, 2])
    assert not np.shares_memory(record.best_tour, tour)
    assert not record.best_tour.flags.writeable
    assert record.best_tour.dtype == np.int64 and record.best_tour.flags.c_contiguous
    with pytest.raises(ValueError):
        record.best_tour[0] = 1
    with pytest.raises(FrozenInstanceError):
        record.best_cost = 0.
    with pytest.raises(FrozenInstanceError):
        record.work.nn_starts_completed = 10
    record.work.to_dict()["nn_starts_completed"] = 99
    assert record.work.nn_starts_completed == 1


@pytest.mark.parametrize("name,value,error", [
    # Exercise shared type validation once, and range validation for every field.
    ("nn_starts_completed", True, TypeError),
    ("nn_starts_completed", 1., TypeError),
    ("nn_starts_completed", np.int64(1), TypeError),
    ("nn_starts_completed", -1, ValueError),
    ("ga_generations_completed", -1, ValueError),
    ("sa_proposals_evaluated", -1, ValueError),
    ("full_tour_evaluations", -1, ValueError),
])
def test_invalid_work_counters(name, value, error):
    with pytest.raises(error):
        WorkCounters(**{name: value})


@pytest.mark.parametrize("changes,error", [
    ({"status": "failed"}, ValueError), ({"termination_reason": "converged"}, ValueError),
    ({"status": None}, TypeError), ({"termination_reason": False}, TypeError),
    ({"work": {}}, TypeError), ({"history": []}, ValueError),
    ({"best_tour": None}, TypeError), ({"best_tour": [0, 1, 2]}, TypeError),
    ({"best_tour": np.array([0., 1., 2.])}, TypeError),
    ({"best_tour": np.array([0, 0, 2], dtype=np.int64)}, ValueError),
    ({"best_tour": np.array([0, 1, 3], dtype=np.int64)}, ValueError),
    ({"best_cost": None}, TypeError), ({"best_cost": True}, TypeError),
    ({"best_cost": -1.}, ValueError), ({"best_cost": float("nan")}, ValueError),
    ({"best_cost": float("inf")}, ValueError), ({"best_cost": 10**1000}, ValueError),
    ({"search_time_s": -1.}, ValueError), ({"validation_time_s": float("nan")}, ValueError),
    ({"search_time_s": True}, TypeError),
])
def test_invalid_result_fields(changes, error):
    with pytest.raises(error):
        result(**changes)


def test_no_solution_is_not_an_infinity_or_empty_tour_sentinel():
    record = SolverResult("no_solution", None, None, WorkCounters(), "work_limit")
    assert record.best_tour is record.best_cost is record.history is None
    assert record.search_time_s == record.validation_time_s == 0.
    for tour, cost in ((np.empty(0, dtype=np.int64), None), (None, float("inf")), (None, 0.)):
        with pytest.raises(ValueError):
            SolverResult("no_solution", tour, cost, WorkCounters(), "work_limit")
    with pytest.raises(ValueError):
        SolverResult("no_solution", None, None, WorkCounters(full_tour_evaluations=1), "work_limit")


def test_zero_cost_is_a_valid_complete_solution():
    assert result(best_cost=0).best_cost == 0.
