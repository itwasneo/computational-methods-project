"""Scientific counter/stopping rules, independent of installed implementations."""

import numpy as np
import pytest

from tsp_hpc.solvers.configuration import normalize_budget, normalize_parameters
from tsp_hpc.solvers.results import SolverResult, WorkCounters
from tsp_hpc.solvers.validation import decode_result, encode_result, validate_result


def check(algorithm, work, budget, reason, *, n=4, valid=True):
    parameters = normalize_parameters(algorithm, {"population_size": 3} if algorithm == "ga" else
                                      {"cost_check_interval": 2} if algorithm == "sa" else {})
    search = {"algorithm": algorithm, "parameters": parameters, "budget": normalize_budget(algorithm, budget, n),
              "instance": {"n_cities": n}, "record_history": False}
    has_solution = work.full_tour_evaluations > 0
    result = SolverResult("ok" if has_solution else "no_solution",
                          np.arange(n, dtype=np.int64) if has_solution else None,
                          4. if has_solution else None, work, reason, search_time_s=1)
    if valid:
        assert validate_result(result, search) is result
        restored = decode_result(encode_result(result), search, search_time_s=1)
        assert restored.work == result.work
    else:
        with pytest.raises(ValueError):
            validate_result(result, search)


@pytest.mark.parametrize("generations,full,limit,reason,valid", [
    (0, 3, 0, "work_limit", True), (0, 2, 0, "time_limit", True),
    (0, 0, 0, "time_limit", True), (0, 2, 0, "work_limit", False),
    (1, 5, 1, "work_limit", True), (1, 4, 1, "work_limit", False),
    (1, 5, 2, "work_limit", False), (2, 7, 1, "work_limit", False),
    (1, 5, 1, "time_limit", False), (0, 3, 1, "algorithm_stop", False),
])
def test_ga_initialization_generation_and_precedence(generations, full, limit, reason, valid):
    check("ga", WorkCounters(ga_generations_completed=generations, full_tour_evaluations=full),
          {"max_generations": limit, "time_limit_s": 1}, reason, valid=valid)


@pytest.mark.parametrize("proposals,full,limit,reason,n,valid", [
    (0, 1, 0, "work_limit", 4, True), (0, 0, 0, "time_limit", 4, True),
    (4, 3, 4, "work_limit", 4, True), (4, 4, 4, "work_limit", 4, True),
    (4, 5, 4, "work_limit", 4, True), (4, 2, 4, "work_limit", 4, False),
    (4, 6, 4, "work_limit", 4, False), (0, 1, 1, "algorithm_stop", 3, True),
    (1, 1, 1, "work_limit", 3, False), (0, 1, 0, "algorithm_stop", 3, False),
    (0, 1, 1, "algorithm_stop", 4, False), (4, 3, 4, "time_limit", 4, False),
])
def test_sa_checks_empty_neighborhood_and_precedence(proposals, full, limit, reason, n, valid):
    check("sa", WorkCounters(sa_proposals_evaluated=proposals, full_tour_evaluations=full),
          {"max_proposals": limit, "time_limit_s": 1}, reason, n=n, valid=valid)


@pytest.mark.parametrize('wrong', [1.5, 2**53 + 1, True])
def test_decode_rounded_incumbent_rejects_inexact_raw_json(wrong):
    search = {'algorithm': 'nn', 'parameters': {}, 'budget': {'start_cities': [0]},
              'instance': {'n_cities': 2, 'distance_kind': 'tsplib_euc_2d'}, 'record_history': False}
    work = WorkCounters(full_tour_evaluations=1, nn_starts_completed=1)
    value = encode_result(SolverResult('ok', np.arange(2, dtype=np.int64), 1., work, 'work_limit'))
    with pytest.raises((TypeError, ValueError)):
        decode_result({**value, 'best_cost': wrong}, search, search_time_s=1)


def test_nn_actual_work_and_algorithm_counter_separation():
    check("nn", WorkCounters(nn_starts_completed=1, full_tour_evaluations=1),
          {"start_cities": [0, 1], "time_limit_s": 1}, "time_limit")
    check("nn", WorkCounters(nn_starts_completed=1, full_tour_evaluations=1),
          {"start_cities": [0, 1]}, "work_limit", valid=False)
    check("sa", WorkCounters(ga_generations_completed=1, full_tour_evaluations=1),
          {"max_proposals": 0}, "work_limit", valid=False)
