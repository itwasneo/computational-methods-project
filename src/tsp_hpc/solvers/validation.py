"""Implementation-independent result rules and JSON codec for agreed variants.

The runner and historical ingestion use the same work/stopping/history rules.
This module never imports a registry or an algorithm implementation. Objective
validation against original coordinates remains a separate runner obligation.
"""

import math

import numpy as np

from tsp_hpc.solvers.results import ProgressPoint, SolverResult, WorkCounters

RESULT_FIELDS = {"status", "best_tour", "best_cost", "work", "termination_reason", "history"}


def validate_nested_duration(inner, outer):
    if not math.isfinite(inner) or not math.isfinite(outer) or (
        inner > outer and not math.isclose(inner, outer, rel_tol=1e-10, abs_tol=1e-9)
    ):
        raise ValueError("inconsistent nested timing durations")


def _fields(value, names):
    if type(value) is not dict or value.keys() != set(names):
        raise ValueError(f"expected object fields {sorted(names)}")


def _state(work, search):
    """Validate a checkpoint; return (initialized, has_solution, work_reached)."""
    algorithm, budget, parameters = search["algorithm"], search["budget"], search["parameters"]
    nn, ga, sa, full = (work.nn_starts_completed, work.ga_generations_completed,
                        work.sa_proposals_evaluated, work.full_tour_evaluations)
    if algorithm == "nn":
        count = len(budget["start_cities"])
        if ga or sa or full != nn or nn > count:
            raise ValueError("NN counters disagree with completed starts")
        return True, nn > 0, nn == count
    if nn or (sa if algorithm == "ga" else ga):
        raise ValueError("counters belong to a different algorithm")
    if algorithm == "ga":
        population, elite = parameters["population_size"], parameters["elite_count"]
        initialized = full >= population
        if (not initialized and ga) or (initialized and full != population + ga * (population - elite)):
            raise ValueError("GA counters disagree with initialization/complete generations")
        limit = budget.get("max_generations")
        if limit is not None and ga > limit:
            raise ValueError("GA exceeded its work limit")
        return initialized, full > 0, initialized and limit is not None and ga == limit
    if algorithm != "sa":
        raise ValueError("unknown algorithm result")
    if (full == 0 and sa) or (full > 0 and not 1 + sa // parameters["cost_check_interval"] <= full <= 1 + sa):
        raise ValueError("SA counters disagree with initialization/recomputation bounds")
    limit = budget.get("max_proposals")
    if (limit is not None and sa > limit) or (search["instance"]["n_cities"] < 4 and sa):
        raise ValueError("SA proposals exceed the budget/neighborhood")
    return full > 0, full > 0, full > 0 and limit is not None and sa == limit


def validate_result(result, search):
    if not isinstance(result, SolverResult):
        raise TypeError("solver must return SolverResult")
    initialized, solution, work_reached = _state(result.work, search)
    if solution != (result.status == "ok"):
        raise ValueError("solution status disagrees with completed evaluations")
    if solution and len(result.best_tour) != search["instance"]["n_cities"]:
        raise ValueError("result tour size disagrees with instance")
    reason = result.termination_reason
    limit = search["budget"].get("time_limit_s")
    if reason == "work_limit":
        if not work_reached:
            raise ValueError("work_limit reported before completing requested work")
    elif reason == "time_limit":
        if work_reached or limit is None or result.search_time_s < limit:
            raise ValueError("time_limit disagrees with budget/precedence/elapsed time")
    elif not (search["algorithm"] == "sa" and initialized and not work_reached
              and search["instance"]["n_cities"] < 4):
        raise ValueError("algorithm_stop is not justified by this variant")
    if not initialized and reason != "time_limit":
        raise ValueError("unfinished initialization requires a time stop")
    if search["record_history"]:
        policy = search["history_policy"]
        if result.history is None or len(result.history) > policy["max_points"]:
            raise ValueError("missing or oversized enabled history")
        for index, point in enumerate(result.history):
            if not _state(point.work, search)[1]:
                raise ValueError("history lacks a complete evaluated incumbent")
            # The terminal sample is unconditional; all other samples respect cadence.
            if 0 < index < len(result.history) - 1 and (
                point.elapsed_s - result.history[index - 1].elapsed_s + 1e-9 < policy["interval_s"]
            ):
                raise ValueError("history violates its sampling interval")
    elif result.history is not None:
        raise ValueError("disabled history must be null")
    return result


def encode_result(result):
    return {"status": result.status,
            "best_tour": None if result.best_tour is None else result.best_tour.tolist(),
            "best_cost": result.best_cost, "work": result.work.to_dict(),
            "termination_reason": result.termination_reason,
            "history": None if result.history is None else [point.to_dict() for point in result.history]}


def decode_result(value, search, *, search_time_s, validation_time_s=0):
    _fields(value, RESULT_FIELDS)
    _fields(value["work"], WorkCounters().to_dict())
    work = WorkCounters(**value["work"])
    tour = value["best_tour"]
    if tour is not None:
        n = search["instance"]["n_cities"]
        if type(tour) is not list or len(tour) != n or any(type(city) is not int or not 0 <= city < n for city in tour):
            raise ValueError("invalid recorded tour")
        tour = np.array(tour, dtype=np.int64)
    history = value["history"]
    if history is not None:
        if type(history) is not list:
            raise ValueError("history must be a JSON array")
        if not search["record_history"] or len(history) > search["history_policy"]["max_points"]:
            raise ValueError("unexpected or oversized history")
        points = []
        for point in history:
            _fields(point, {"elapsed_s", "work", "best_cost"})
            _fields(point["work"], WorkCounters().to_dict())
            points.append(ProgressPoint(point["elapsed_s"], WorkCounters(**point["work"]), point["best_cost"]))
        history = tuple(points)
    result = SolverResult(value["status"], tour, value["best_cost"], work, value["termination_reason"],
                          history=history, search_time_s=search_time_s, validation_time_s=validation_time_s)
    return validate_result(result, search)


def aggregate_results(results):
    """Serial logical-order reduction; histories remain in their original clocks.

    The aggregate is deliberately not a SolverResult (it may have mixed stopping
    reasons). Keeping histories per invocation avoids invented timestamps and a
    second serialized copy. Analysis may explicitly concatenate the search clocks.
    """
    if not results:
        raise ValueError("a completed trial needs at least one logical result")
    best_index = None
    for index, result in enumerate(results):
        if result.status == "ok" and (best_index is None or result.best_cost < results[best_index].best_cost):
            best_index = index
    best = None if best_index is None else results[best_index]
    reasons = {result.termination_reason for result in results}
    return {"status": "no_solution" if best is None else "ok",
            "best_tour": None if best is None else best.best_tour.tolist(),
            "best_cost": None if best is None else best.best_cost,
            "work": {name: sum(result.work.to_dict()[name] for result in results) for name in WorkCounters().to_dict()},
            "termination_reason": next(iter(reasons)) if len(reasons) == 1 else "mixed",
            "history": None, "best_logical_index": best_index}
