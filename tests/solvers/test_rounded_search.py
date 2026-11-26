"""Objective governs choices, not just final rescoring."""
import numpy as np
import pytest

from tsp_hpc.problem.data import TSPInstance
from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.problem.instances import uniform_instance
from tsp_hpc.problem.objective import tour_cost
from tsp_hpc.solvers.registry import default_registry
from tsp_hpc.solvers.resources import available_threads


@pytest.mark.parametrize('mode', ['dense', 'on_demand'])
def test_rounding_changes_nn_candidate_choice_and_retains_stable_reduction(mode):
    coords = np.array([[0., 0.], [1.4, 0.], [1.1, 0.], [0., 5.]])
    def run(kind, impl, starts, threads=None):
        instance = TSPInstance('tie', coords, kind)
        backend = 'serial' if threads is None else 'multicore'
        spec = default_registry().resolve('nn', backend=backend, distance_kind=kind, implementation=impl)
        prepared = prepare_distances(instance, mode=mode)
        adapter = spec.factory(prepared, **({} if threads is None else {'threads': threads}))
        value = adapter.solve(instance, seed=11, parameters={}, budget={'start_cities': starts})
        assert value.best_cost == tour_cost(instance, value.best_tour)
        return value.best_tour.tolist(), value.work
    assert run('euclidean_2d', 'visited-scan-v1', [0])[0][1] == 2
    for starts in ([0], [3, 1, 0, 2]):
        baseline = run('tsplib_euc_2d', 'visited-scan-v1', starts)
        if starts == [0]:
            assert baseline[0][1] == 1  # Rounded tie: lower city ID, not the shorter unrounded edge.
        assert run('tsplib_euc_2d', 'remaining-array-v1', starts) == baseline
        for t in {1, min(2, available_threads())}:
            assert run('tsplib_euc_2d', 'visited-scan-threaded-v1', starts, t) == baseline


def test_rounded_ga_replays_work_and_incumbents_across_modes():
    instance = uniform_instance(19, seed=41, side_length=100, distance_kind='tsplib_euc_2d').instance
    spec = default_registry().resolve('ga', distance_kind=instance.distance_kind)
    outcomes = []
    for mode in ('dense', 'on_demand'):
        solver = spec.factory(prepare_distances(instance, mode=mode))
        value = solver.solve(instance, seed=7, parameters={'population_size': 12}, budget={'max_generations': 8},
                             record_history=True, history_policy={'interval_s': 0})
        assert value.best_cost == tour_cost(instance, value.best_tour)
        assert value.work.full_tour_evaluations == 12 + 8*11
        trajectory = [(p.work, p.best_cost) for p in value.history]
        assert all(cost.is_integer() for _, cost in trajectory)
        outcomes.append((value.best_tour.tolist(), value.best_cost, value.work, trajectory))
    assert outcomes[0] == outcomes[1]
