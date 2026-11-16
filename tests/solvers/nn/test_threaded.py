"""Native fixed-work equivalence across execution resources and failure modes."""
import numpy as np
import pytest

from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.problem.exact import exact_reference
from tsp_hpc.problem.instances import fixture, uniform_instance
from tsp_hpc.problem.data import TSPInstance
from tsp_hpc.problem.objective import NumericalError
from tsp_hpc.solvers.nn.solver import NearestNeighbourSolver
from tsp_hpc.solvers.nn.threaded import ThreadedNearestNeighbourSolver
from tsp_hpc.solvers.resources import available_threads, nn_scratch_bytes


@pytest.mark.parametrize("mode", ["dense", "on_demand"])
@pytest.mark.parametrize("threads", [1, 2, 4])
@pytest.mark.parametrize("name", ["square", "duplicates", "coincident", "collinear", "random"])
def test_exact_serial_equivalence_and_reference_bound(mode, threads, name):
    if threads > available_threads():
        pytest.skip("requires more affinity CPUs")
    instance = (uniform_instance(7, seed=123) if name == "random" else fixture(name)).instance
    prepared = prepare_distances(instance, mode=mode)
    serial = NearestNeighbourSolver(prepared)
    parallel = ThreadedNearestNeighbourSolver(prepared, threads)
    starts = list(reversed(range(instance.n_cities)))
    request = dict(seed=2**64-1, parameters={}, budget={"start_cities": starts})
    expected = serial.solve(instance, **request)
    for _ in range(3):
        observed = parallel.solve(instance, **request)
        assert observed.best_cost == expected.best_cost
        np.testing.assert_array_equal(observed.best_tour, expected.best_tour)
        assert observed.work == expected.work
        assert observed.best_cost + 1e-10 * max(1, observed.best_cost) >= exact_reference(instance).cost
        assert not observed.best_tour.flags.writeable
    assert starts == list(reversed(range(instance.n_cities)))
    assert not prepared.coordinates.flags.writeable
    assert nn_scratch_bytes(instance.n_cities, len(starts), threads) > 0


def test_zero_work_bad_resources_and_unsupported_modes():
    prepared = prepare_distances(fixture("square").instance, mode="on_demand")
    for bad in [0, True, -1, available_threads() + 1]:
        with pytest.raises((TypeError, ValueError)):
            ThreadedNearestNeighbourSolver(prepared, bad)
    adapter = ThreadedNearestNeighbourSolver(prepared)
    empty = adapter.solve(prepared.instance, seed=0, parameters={}, budget={"start_cities": []})
    assert empty.status == "no_solution" and empty.work.nn_starts_completed == 0
    for extra in [{"record_history": True}, {"budget": {"start_cities": [0], "time_limit_s": 1}}]:
        with pytest.raises(ValueError, match="fixed work"):
            adapter.solve(prepared.instance, **{ "seed": 0, "parameters": {}, "budget": {"start_cities": [0]}, **extra})


def test_native_thread_exception_propagates_without_a_partial_success():
    instance = TSPInstance("overflow", np.array([[-1e308, 0.], [1e308, 0.]]))
    adapter = ThreadedNearestNeighbourSolver(prepare_distances(instance, mode="on_demand"), min(2, available_threads()))
    with pytest.raises(NumericalError):
        adapter.solve(instance, seed=0, parameters={}, budget={"start_cities": [0, 1]})


def test_completion_order_is_not_the_tie_breaker(monkeypatch):
    import time
    import tsp_hpc.solvers.nn.threaded as module
    instance = fixture("coincident").instance
    adapter = ThreadedNearestNeighbourSolver(prepare_distances(instance, mode="on_demand"), min(2, available_threads()))
    native = module.chunk_kernel
    def delayed(coordinates, matrix, dense, starts):
        if starts[0] == 3:
            time.sleep(.03)
        return native(coordinates, matrix, dense, starts)
    monkeypatch.setattr(module, "chunk_kernel", delayed)
    result = adapter.solve(instance, seed=0, parameters={}, budget={"start_cities": [3, 2, 1, 0]})
    assert result.best_tour.tolist() == [3, 0, 1, 2]
