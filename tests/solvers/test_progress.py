"""Deterministic clock tests; these helpers do not implement GA or SA."""

from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from tsp_hpc.solvers.configuration import normalize_budget, normalize_history
from tsp_hpc.solvers.progress import HistoryLimitError, SearchControl
from tsp_hpc.solvers.results import ProgressPoint, SolverResult, WorkCounters


class Clock:
    value = 0.

    def __call__(self):
        return self.value


def test_initialization_and_stopping_precedence():
    clock = Clock()
    control = SearchControl(time_limit_s=1, clock=clock)
    assert control.stop_reason(work_limit_reached=True, initialized=False) is None
    clock.value = 2
    assert control.stop_reason(work_limit_reached=True, initialized=False) == "time_limit"
    assert control.stop_reason(work_limit_reached=True, algorithm_stop=True) == "work_limit"
    assert control.stop_reason(algorithm_stop=True) == "time_limit"
    assert SearchControl(clock=clock).stop_reason(algorithm_stop=True) == "algorithm_stop"


def test_sampling_terminal_observation_and_detachment():
    clock = Clock()
    policy = {"interval_s": 1, "max_points": 3}
    control = SearchControl(record_history=True, history_policy=policy, clock=clock)
    policy["max_points"] = 0
    first = WorkCounters(full_tour_evaluations=1)
    second = WorkCounters(full_tour_evaluations=2)
    control.observe(first, 5)
    clock.value = .5
    control.observe(second, 4)  # Not a sample time; do not invent the point later.
    clock.value = .75
    elapsed, history = control.finish(second, 4)
    assert elapsed == .75
    assert [(point.elapsed_s, point.best_cost) for point in history] == [(0, 5), (.75, 4)]
    assert history[-1].work == second
    with pytest.raises(FrozenInstanceError):
        history[0].best_cost = 1
    assert control.policy["max_points"] == 3


def test_history_bound_is_explicit_failure_not_truncation():
    control = SearchControl(record_history=True, history_policy={"interval_s": 0, "max_points": 2})
    control.observe(WorkCounters(full_tour_evaluations=1), 5)
    with pytest.raises(HistoryLimitError):
        control.observe(WorkCounters(full_tour_evaluations=2), 4)


@pytest.mark.parametrize("policy", [{"interval_s": -1}, {"interval_s": float("nan")},
                                   {"max_points": 1}, {"max_points": True}, {"extra": 1}])
def test_invalid_history_policies(policy):
    with pytest.raises((ValueError, TypeError)):
        normalize_history(True, policy)


@pytest.mark.parametrize("algorithm", ["nn", "ga", "sa"])
def test_budget_modes_and_invalid_limits(algorithm):
    name = {"nn": "start_cities", "ga": "max_generations", "sa": "max_proposals"}[algorithm]
    work = {name: [2, 0] if algorithm == "nn" else 0}
    assert normalize_budget(algorithm, work, 4) == work
    normalized = normalize_budget(algorithm, {**work, "time_limit_s": 1}, 4)
    assert normalized["time_limit_s"] == 1.
    if algorithm != "nn":
        assert "time_limit_s" in normalize_budget(algorithm, {"time_limit_s": 1}, 4)
    if algorithm == "sa":
        assert normalized["time_check_interval_proposals"] == 256
    for value in (0, -1, float("inf"), float("nan"), True, "1", 10**1000):
        with pytest.raises((ValueError, TypeError)):
            normalize_budget(algorithm, {**work, "time_limit_s": value}, 4)


@pytest.mark.parametrize("points", [
    (ProgressPoint(2, WorkCounters(full_tour_evaluations=1), 4),),
    (ProgressPoint(0, WorkCounters(full_tour_evaluations=1), 5),),
    (ProgressPoint(.5, WorkCounters(full_tour_evaluations=1), 3),
     ProgressPoint(.75, WorkCounters(full_tour_evaluations=1), 4)),
    (ProgressPoint(.75, WorkCounters(full_tour_evaluations=1), 4),
     ProgressPoint(.5, WorkCounters(full_tour_evaluations=1), 4)),
])
def test_inconsistent_histories_are_rejected(points):
    with pytest.raises(ValueError):
        SolverResult("ok", np.arange(3, dtype=np.int64), 4, WorkCounters(full_tour_evaluations=1),
                     "work_limit", search_time_s=1, history=points)
