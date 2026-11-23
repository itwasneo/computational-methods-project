import numpy as np
import pytest

from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.problem.instances import uniform_instance
from tsp_hpc.problem.objective import tour_cost
from tsp_hpc.solvers.sa.operators import apply_reversal, reversal_delta, sample_edges


@pytest.mark.parametrize("mode", ["dense", "on_demand"])
@pytest.mark.parametrize("n", [4, 5, 12])
def test_every_eligible_move_matches_independent_cost(mode, n):
    instance = uniform_instance(n, seed=42).instance
    prepared = prepare_distances(instance, mode=mode)
    rng = np.random.default_rng(19)
    for _ in range(5):
        tour = rng.permutation(n)
        before = tour_cost(instance, tour)
        for i in range(n):
            for j in range(i + 1, n):
                if j == i + 1 or (i == 0 and j == n - 1):
                    continue
                original = tour.copy()
                delta = reversal_delta(prepared.coordinates, prepared.matrix,
                                       prepared.use_dense, tour, i, j)
                np.testing.assert_array_equal(tour, original)
                candidate = tour.copy()
                apply_reversal(candidate, i, j)
                assert sorted(candidate.tolist()) == list(range(n))
                assert candidate[0] == tour[0]
                assert tour_cost(instance, candidate) == pytest.approx(before + delta, abs=1e-10)
                apply_reversal(candidate, i, j)
                np.testing.assert_array_equal(candidate, tour)


@pytest.mark.parametrize("n", [4, 5, 12])
def test_sampling_never_returns_excluded_edges_and_is_reproducible(n):
    a, b = np.random.default_rng(123), np.random.default_rng(123)
    for _ in range(2000):
        i, j = sample_edges(n, a)
        assert (i, j) == sample_edges(n, b)
        assert 0 <= i < j < n
        assert j != i + 1
        assert (i, j) != (0, n - 1)
