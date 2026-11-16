"""Independent exhaustive references, not heuristic optimality requirements."""
import math
from itertools import permutations

import numpy as np
import pytest

from tsp_hpc.problem.exact import exact_reference, MAX_EXACT_CITIES
from tsp_hpc.problem.instances import fixture, uniform_instance
from tsp_hpc.problem.objective import tour_cost, NumericalError
from tsp_hpc.problem.data import TSPInstance


@pytest.mark.parametrize("name,cost", [("two-city", 10), ("triangle", 12), ("square", 4),
                                      ("collinear", 12), ("duplicates", 10), ("coincident", 0)])
@pytest.mark.parametrize("kind", ["euclidean_2d", "tsplib_euc_2d"])
def test_hand_checked_optima(name, cost, kind):
    instance = fixture(name, distance_kind=kind).instance
    reference = exact_reference(instance)
    assert reference.cost == cost
    assert reference.tour[0] == 0 and sorted(reference.tour) == list(range(instance.n_cities))
    assert reference.tours_evaluated == math.factorial(instance.n_cities - 1)
    assert reference.reference()["kind"] == "exact"
    assert reference.reference()["coordinate_checksum"]
    assert tour_cost(instance, np.array(reference.tour, dtype=np.int64)) == cost
    assert exact_reference(instance) == reference


def test_random_small_reference_matches_all_orientations_and_rotations():
    instance = uniform_instance(6, seed=413).instance
    reference = exact_reference(instance)
    costs = [tour_cost(instance, np.array(p, dtype=np.int64)) for p in permutations(range(6))]
    assert reference.cost == pytest.approx(min(costs), rel=1e-10, abs=1e-12)


def test_guard_precedes_factorial_enumeration_and_numerical_failure_is_not_skipped():
    with pytest.raises(ValueError, match="limited"):
        exact_reference(uniform_instance(MAX_EXACT_CITIES + 1, seed=1).instance)
    with pytest.raises(TypeError):
        exact_reference(None)
    instance = TSPInstance("overflow", np.array([[-1e308, 0.], [1e308, 0.]]))
    with pytest.raises(NumericalError):
        exact_reference(instance)


def test_coincident_tie_is_lexical_and_reference_is_immutable():
    ref = exact_reference(fixture("coincident").instance)
    assert ref.tour == (0, 1, 2, 3)
    with pytest.raises(AttributeError):
        ref.cost = 1
