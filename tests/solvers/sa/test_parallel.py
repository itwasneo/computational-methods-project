import numpy as np
import pytest

from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.problem.instances import fixture, uniform_instance
from tsp_hpc.solvers.resources import available_threads
from tsp_hpc.solvers.sa.parallel import solve_chains
from tsp_hpc.solvers.sa.solver import SimulatedAnnealingSolver


@pytest.mark.parametrize("mode", ["dense", "on_demand"])
@pytest.mark.parametrize("n", [2, 3, 4, 20])
def test_parallel_matches_serial_in_logical_order(mode, n):
    instance = uniform_instance(n, seed=42).instance
    solver = SimulatedAnnealingSolver(prepare_distances(instance, mode=mode))
    args = dict(seeds=[7, 2**63 + 1, 9, 7], budget={"max_proposals": 1000},
                parameters={"cost_check_interval": 11})
    serial = solve_chains(solver, threads=1, **args)
    parallel = solve_chains(solver, threads=min(4, available_threads()), **args)
    assert parallel.seeds == tuple(args["seeds"])
    assert serial.best_index == parallel.best_index
    for left, right in zip(serial.results, parallel.results):
        np.testing.assert_array_equal(left.best_tour, right.best_tour)
        assert left.best_cost == right.best_cost
        assert left.work == right.work
        assert left.termination_reason == right.termination_reason
    assert parallel.wall_time_s >= 0


def test_ties_keep_first_chain_and_kernels_release_gil():
    instance = fixture("coincident").instance
    solver = SimulatedAnnealingSolver(prepare_distances(instance, mode="dense"))
    result = solve_chains(solver, seeds=[3, 2, 1], budget={"max_proposals": 30},
                          parameters={}, threads=min(2, available_threads()))
    assert result.best_index == 0
    assert result.best is result.results[0]
    assert all(item.targetoptions.get("nogil") is True for item in solver.native_dispatchers)


@pytest.mark.parametrize("seeds,threads,budget", [
    ([], 1, {"max_proposals": 1}),
    ([True], 1, {"max_proposals": 1}),
    ([1], True, {"max_proposals": 1}),
    ([1], 0, {"max_proposals": 1}),
    ([1], 1000000, {"max_proposals": 1}),
    ([1], 1, {"time_limit_s": 1}),
    ([1], 1, {"max_proposals": 1, "time_limit_s": 1}),
])
def test_rejects_invalid_inputs_before_execution(seeds, threads, budget):
    instance = fixture("square").instance
    solver = SimulatedAnnealingSolver(prepare_distances(instance, mode="dense"))
    with pytest.raises((TypeError, ValueError)):
        solve_chains(solver, seeds=seeds, budget=budget, parameters={}, threads=threads)


def test_parallel_does_not_consume_global_rng():
    instance = fixture("square").instance
    solver = SimulatedAnnealingSolver(prepare_distances(instance, mode="on_demand"))
    state = np.random.get_state()
    solve_chains(solver, seeds=[1, 2], budget={"max_proposals": 25}, parameters={},
                 threads=min(2, available_threads()))
    after = np.random.get_state()
    assert state[0] == after[0]
    np.testing.assert_array_equal(state[1], after[1])
    assert state[2:] == after[2:]


def test_zero_work_still_initializes_each_chain():
    instance = fixture("square").instance
    solver = SimulatedAnnealingSolver(prepare_distances(instance, mode="dense"))
    batch = solve_chains(solver, seeds=[1, 2, 3], budget={"max_proposals": 0},
                         parameters={}, threads=min(2, available_threads()))
    assert all(r.termination_reason == "work_limit" for r in batch.results)
    assert all(r.work.sa_proposals_evaluated == 0 and r.work.full_tour_evaluations == 1
               for r in batch.results)


def test_worker_error_propagates_without_partial_result(monkeypatch):
    instance = fixture("square").instance
    solver = SimulatedAnnealingSolver(prepare_distances(instance, mode="dense"))
    original = SimulatedAnnealingSolver.solve

    def fail_one(self, instance, *, seed, **kwargs):
        if seed == 2:
            raise ArithmeticError("chain failure")
        return original(self, instance, seed=seed, **kwargs)

    monkeypatch.setattr(SimulatedAnnealingSolver, "solve", fail_one)
    with pytest.raises(ArithmeticError, match="chain failure"):
        solve_chains(solver, seeds=[1, 2, 3], budget={"max_proposals": 5}, parameters={},
                     threads=min(2, available_threads()))
