"""Serial NN contracts, independent greedy construction, and setup isolation."""

import numpy as np
import pytest

from tsp_hpc.problem.data import TSPInstance, validate_tour
from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.problem.instances import fixture, fixture_names, uniform_instance, clustered_instance
from tsp_hpc.experiments.manifests import Manifest
import tsp_hpc.solvers.nn.solver as nn
from tsp_hpc.problem.objective import NumericalError, edge_cost, tour_cost, validate_solution

MODES = ("dense", "on_demand")


def python_greedy(instance, start):
    """Independent Python construction using reference edges and explicit tie keys."""
    remaining = set(range(instance.n_cities)) - {start}
    route = [start]
    while remaining:
        chosen = min(remaining, key=lambda city: (edge_cost(instance, route[-1], city), city))
        route.append(chosen)
        remaining.remove(chosen)
    return np.array(route, dtype=np.int64)


def adapter(instance, mode, implementation="visited-scan-v1"):
    return nn.NearestNeighbourSolver(prepare_distances(instance, mode=mode),
                                     implementation=implementation)


def run(solver, instance, starts, seed=42):
    return solver.solve(instance, seed=seed, budget={"start_cities": starts}, parameters={})


@pytest.mark.parametrize("implementation", nn.implementation_names())
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", fixture_names())
def test_all_fixture_starts_and_multistart_reference(mode, name, implementation):
    instance = fixture(name).instance
    solver = adapter(instance, mode, implementation)
    starts = list(reversed(range(instance.n_cities)))
    expected_tours = []
    for start in starts:
        result = run(solver, instance, [start])
        expected = python_greedy(instance, start)
        np.testing.assert_array_equal(result.best_tour, expected)
        assert result.best_cost == tour_cost(instance, expected)
        assert result.work.nn_starts_completed == result.work.full_tour_evaluations == 1
        expected_tours.append(expected)
    # Selection uses native scores, not reference tolerances or a reranking of
    # near-ties after post-search recomputation.
    winner = min(range(len(starts)), key=lambda i: solver.prepared.tour_cost(expected_tours[i]))
    result = run(solver, instance, starts)
    np.testing.assert_array_equal(result.best_tour, expected_tours[winner])
    assert result.status == "ok" and result.termination_reason == "work_limit"
    assert result.history is None
    assert result.work.to_dict() == {
        "nn_starts_completed": len(starts), "full_tour_evaluations": len(starts),
        "ga_generations_completed": 0, "sa_proposals_evaluated": 0,
    }
    assert validate_solution(instance, result.best_tour, result.best_cost) == result.best_cost


@pytest.mark.parametrize("builder", [uniform_instance, clustered_instance])
def test_generated_instances_backend_equivalence_and_replay(builder):
    instance = builder(24, seed=17, side_length=100.).instance
    before = instance.coordinates.copy()
    starts = [7, 2, 0, 23, 11]
    original_starts = starts.copy()
    results = []
    for implementation in nn.implementation_names():
        for mode in MODES:
            solver = adapter(instance, mode, implementation)
            for seed in (0, 42, 2**64 - 1):
                result = run(solver, instance, starts, seed)
                validate_tour(result.best_tour, instance.n_cities)
                assert result.best_cost == tour_cost(instance, result.best_tour)
                results.append(result)
            for start in starts:
                np.testing.assert_array_equal(run(solver, instance, [start]).best_tour,
                                              python_greedy(instance, start))
    for result in results[1:]:
        np.testing.assert_array_equal(result.best_tour, results[0].best_tour)
        assert result.best_cost == results[0].best_cost
        assert result.work == results[0].work
        assert not np.shares_memory(result.best_tour, results[0].best_tour)
    np.testing.assert_array_equal(instance.coordinates, before)
    assert starts == original_starts


@pytest.mark.parametrize("implementation", nn.implementation_names())
@pytest.mark.parametrize("mode", MODES)
def test_close_but_unequal_edges_are_not_ties(mode, implementation):
    instance = TSPInstance("near-tie", np.array([[0., 0.], [1. + 2e-12, 0.], [1., 0.]]))
    result = run(adapter(instance, mode, implementation), instance, [0])
    np.testing.assert_array_equal(result.best_tour, [0, 2, 1])


@pytest.mark.parametrize("implementation", nn.implementation_names())
@pytest.mark.parametrize("mode", MODES)
def test_equal_best_keeps_earliest_logical_start_without_zero_cost_stop(mode, implementation):
    instance = fixture("coincident").instance
    solver = adapter(instance, mode, implementation)
    # Removing city 0 swaps city 2 ahead of city 1 in the compact array.
    # Equal edges must still choose city 1, not the first remaining slot.
    result = run(solver, instance, [3, 1, 0])
    np.testing.assert_array_equal(result.best_tour, [3, 0, 1, 2])
    assert result.best_cost == 0. and result.work.nn_starts_completed == 3
    reverse = run(solver, instance, [0, 1, 3])
    np.testing.assert_array_equal(reverse.best_tour, [0, 1, 2, 3])
    assert reverse.work.full_tour_evaluations == 3


@pytest.mark.parametrize("implementation", nn.implementation_names())
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("scale", [1., 1e-13])
def test_improvement_and_later_worse_candidate_do_not_overwrite_best(mode, scale, implementation):
    # The tiny scale also checks strict best-cost comparison: the improvement
    # is smaller than the objective validator's absolute tolerance.
    points = np.array([[0., 0.], [1., 0.], [3., 0.], [0., 4.], [3., 4.]])
    instance = TSPInstance("multi", points * scale)
    solver = adapter(instance, mode, implementation)
    improved = run(solver, instance, [1, 0, 2])
    np.testing.assert_array_equal(improved.best_tour, [0, 1, 2, 4, 3])
    assert improved.best_cost == pytest.approx(14. * scale, rel=1e-15, abs=0.)
    best_then_worse = run(solver, instance, [2, 1])
    np.testing.assert_array_equal(best_then_worse.best_tour, [2, 1, 0, 3, 4])
    assert best_then_worse.best_cost == pytest.approx(14. * scale, rel=1e-15, abs=0.)
    assert not best_then_worse.best_tour.flags.writeable
    best_then_worse.best_tour.flags.writeable = True  # Deliberate caller misuse.
    best_then_worse.best_tour[:] = 0
    replay = run(solver, instance, [2, 1])
    np.testing.assert_array_equal(replay.best_tour, [2, 1, 0, 3, 4])


@pytest.mark.parametrize("implementation,error", [(None, TypeError), (True, TypeError),
                                                  ("", ValueError), ("__unregistered_test_implementation__", ValueError)])
def test_unavailable_implementations_fail_before_warmup(implementation, error, monkeypatch):
    prepared = prepare_distances(fixture("square").instance, mode="dense")
    monkeypatch.setattr(nn, "warmup_nearest_neighbour", lambda **kw: pytest.fail("unexpected warm-up"))
    with pytest.raises(error):
        nn.NearestNeighbourSolver(prepared, implementation=implementation)


def test_selected_kernel_is_warmed_and_executed(monkeypatch):
    instance = fixture("square").instance
    prepared = prepare_distances(instance, mode="dense")
    calls = []
    phase = "warmup"
    original = nn._resolve_implementation
    def observed_kernel(coordinates, matrix, use_dense, starts):
        calls.append(phase)
        return nn._search_kernel(coordinates, matrix, use_dense, starts)
    def resolve(name):
        return observed_kernel if name == "test-only" else original(name)
    monkeypatch.setattr(nn, "_resolve_implementation", resolve)
    solver = nn.NearestNeighbourSolver(prepared, implementation="test-only")
    assert calls and all(call == "warmup" for call in calls)
    calls.clear()
    phase = "search"
    assert run(solver, instance, [2, 0]).best_cost == 4.
    assert calls and all(call == "search" for call in calls)
    assert solver.metadata["implementation"] == "test-only"
    assert "visited-scan-v1" in nn.implementation_names()
    calls.clear()
    default = nn.NearestNeighbourSolver(prepared)
    assert default.metadata["implementation"] == nn.DEFAULT_IMPLEMENTATION
    assert run(default, instance, [2, 0]).best_cost == 4.
    assert not calls


def test_empty_convenience_request_skips_preparation_warmup_search_and_validation(monkeypatch):
    instance = fixture("square").instance
    def fail(*args, **kwargs):
        raise AssertionError("empty work must not execute these phases")
    for name in ("prepare_distances", "warmup_nearest_neighbour", "_resolve_implementation", "validate_solution"):
        monkeypatch.setattr(nn, name, fail)
    result = nn.solve(instance, seed=42, budget={"start_cities": []}, parameters={})
    assert result.status == "no_solution" and result.termination_reason == "work_limit"
    assert result.best_tour is result.best_cost is result.history is None
    assert not any(result.work.to_dict().values())
    assert result.search_time_s == result.validation_time_s == 0.


def test_empty_bound_request_skips_selected_kernel_and_validation(monkeypatch):
    instance = fixture("square").instance
    searching = False
    kernel = nn._resolve_implementation(nn.DEFAULT_IMPLEMENTATION)
    def guarded_kernel(*args):
        if searching:
            pytest.fail("empty work reached selected kernel")
        return kernel(*args)
    # Bind the guard before construction: changing a module alias afterwards
    # cannot intercept the callable already retained by the adapter.
    monkeypatch.setattr(nn, "_resolve_implementation", lambda name: guarded_kernel)
    solver = adapter(instance, "on_demand")
    searching = True
    monkeypatch.setattr(nn, "validate_solution", lambda *args: pytest.fail("unexpected validation"))
    result = run(solver, instance, [])
    assert result.status == "no_solution" and not any(result.work.to_dict().values())
    assert result.best_tour is result.best_cost is None
    assert result.search_time_s == result.validation_time_s == 0.


@pytest.mark.parametrize("changes,error", [
    ({"seed": True}, TypeError), ({"seed": 2**64}, ValueError),
    ({"parameters": None}, TypeError), ({"parameters": {"start_city": 0}}, ValueError),
    ({"budget": None}, TypeError), ({"budget": {}}, ValueError),
    ({"budget": {"start_cities": [0], "time_limit_s": 1.}}, ValueError),
    ({"budget": {"time_limit_s": 1.}}, ValueError),
    ({"budget": {"start_cities": None}}, TypeError),
    ({"budget": {"start_cities": (0, 1)}}, TypeError),
    ({"budget": {"start_cities": [True]}}, TypeError),
    ({"budget": {"start_cities": [1.]}}, TypeError),
    ({"budget": {"start_cities": [np.int64(1)]}}, TypeError),
    ({"budget": {"start_cities": [-1]}}, ValueError),
    ({"budget": {"start_cities": [4]}}, ValueError),
    ({"budget": {"start_cities": [0, 0]}}, ValueError),
    ({"record_history": True}, ValueError), ({"record_history": 0}, TypeError),
])
def test_invalid_requests_fail_before_preparation(changes, error, monkeypatch):
    arguments = dict(seed=42, budget={"start_cities": [0]}, parameters={})
    arguments.update(changes)
    monkeypatch.setattr(nn, "prepare_distances", lambda *a, **kw: pytest.fail("unexpected preparation"))
    with pytest.raises(error):
        nn.solve(fixture("square").instance, **arguments)


def test_invalid_instance_and_rounded_convention_even_for_empty_work():
    for instance, error in ((None, TypeError),
                            (fixture("square", distance_kind="tsplib_euc_2d").instance, ValueError)):
        with pytest.raises(error):
            nn.solve(instance, seed=0, budget={"start_cities": []}, parameters={})
    with pytest.raises(TypeError):
        nn.NearestNeighbourSolver(None)


def test_adapter_rejects_other_instance_even_with_same_label():
    instance = fixture("square").instance
    other = TSPInstance(instance.instance_id, instance.coordinates * 2)
    solver = adapter(instance, "dense")
    with pytest.raises(ValueError, match="different instance"):
        run(solver, other, [0])
    with pytest.raises(TypeError, match="TSPInstance"):
        run(solver, None, [0])


@pytest.mark.parametrize("implementation", nn.implementation_names())
@pytest.mark.parametrize("mode,points", [
    ("on_demand", [(-np.finfo(np.float64).max, 0.), (np.finfo(np.float64).max, 0.)]),
    ("dense", [(0., 0.), (np.finfo(np.float64).max, 0.)]),
])
def test_numerical_failures_propagate_not_no_solution(points, mode, implementation):
    # Exercise edge and total failures during search, not dense preparation.
    instance = TSPInstance("overflow", np.array(points))
    with pytest.raises(NumericalError):
        run(adapter(instance, mode, implementation), instance, [0])


def test_final_reference_validation_failure_propagates(monkeypatch):
    instance = fixture("triangle").instance
    solver = adapter(instance, "on_demand")
    def fail(*args):
        raise ValueError("independent validation failed")
    monkeypatch.setattr(nn, "validate_solution", fail)
    with pytest.raises(ValueError, match="independent validation failed"):
        run(solver, instance, [0])


def test_warmup_and_phase_timing_are_separate_and_final_evaluation_not_counted(monkeypatch):
    instance = fixture("square").instance
    prepared = prepare_distances(instance, mode="on_demand")
    times = iter([10., 17., 20., 23., 30., 32.])
    monkeypatch.setattr(nn, "perf_counter", lambda: next(times))
    solver = nn.NearestNeighbourSolver(prepared)
    calls = []
    reference = nn.validate_solution
    def observe(*args):
        calls.append(args)
        return reference(*args)
    monkeypatch.setattr(nn, "validate_solution", observe)
    result = run(solver, instance, [3, 1])
    assert solver.metadata["solver_warmup_time_s"] == 7.
    assert result.search_time_s == 3. and result.validation_time_s == 2.
    assert len(calls) == 1
    assert result.work.full_tour_evaluations == result.work.nn_starts_completed == 2


def test_reused_adapter_never_prepares_or_warms_during_search(monkeypatch):
    instance = fixture("triangle").instance
    solver = adapter(instance, "dense")
    kernel = solver._kernel
    signatures = tuple(kernel.signatures)
    def fail(*args, **kwargs):
        raise AssertionError("setup must not be repeated during solve")
    monkeypatch.setattr(nn, "prepare_distances", fail)
    monkeypatch.setattr(nn, "warmup_nearest_neighbour", fail)
    for _ in range(2):
        assert run(solver, instance, [0, 1]).best_cost == 12.
    assert tuple(kernel.signatures) == signatures
    assert kernel.nopython_signatures
    metadata = solver.metadata
    metadata["variant"] = "changed"
    assert solver.metadata["variant"] == "nn-fixed-starts-v1"
    assert solver.metadata["backend"] == "serial"


def test_manifest_scientific_fields_connect_to_serial_solver():
    record = fixture("square")
    manifest = Manifest({
        "schema_version": 1, "instances": [record.descriptor], "experiments": [{
            "instance_id": record.instance.instance_id, "algorithm": "nn",
            "budget": {"start_cities": [2, 0]}, "solver_seed": 42, "repetitions": [0],
            "executions": [{"backend": "serial", "distance_mode": "dense",
                            "implementation": implementation,
                            "resources": {"profile": "local-cpu"}}
                           for implementation in nn.implementation_names()],
        }],
    })
    plans = manifest.expand()
    assert len({plan.search_id for plan in plans}) == 1
    assert len({plan.execution_id for plan in plans}) == len(plans)
    for plan in plans:
        solver = nn.NearestNeighbourSolver(
            prepare_distances(record.instance, mode=plan.execution["distance_mode"]),
            implementation=plan.execution["implementation"],
        )
        assert solver.metadata["implementation"] == plan.execution["implementation"]
        result = solver.solve(record.instance, seed=plan.trial_seed, budget=plan.search["budget"],
                              parameters=plan.search["parameters"], record_history=plan.search["record_history"])
        np.testing.assert_array_equal(result.best_tour, [2, 1, 0, 3])
        assert result.work.nn_starts_completed == 2
