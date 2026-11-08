"""Hand-calculated objectives, numerical boundaries and solution validation."""

from itertools import permutations
import math

import numpy as np
import pytest

from tsp_hpc.problem.data import TSPInstance
from tsp_hpc.problem.objective import (
    COST_ATOL,
    COST_RTOL,
    MAX_EXACT_INTEGER,
    NumericalError,
    edge_cost,
    tour_cost,
    validate_solution,
)

KINDS = ("euclidean_2d", "tsplib_euc_2d")


def instance(points, kind="euclidean_2d"):
    return TSPInstance("fixture", np.array(points, dtype=np.float64), kind)


def tour(*cities):
    return np.array(cities, dtype=np.int64)


@pytest.mark.parametrize("kind", KINDS)
def test_triangle_hand_calculated_cost_and_implicit_closure(kind):
    problem = instance([(0, 0), (3, 0), (3, 4)], kind)
    expected = {(0, 1): 3.0, (1, 2): 4.0, (0, 2): 5.0}
    for (a, b), cost in expected.items():
        assert edge_cost(problem, a, b) == cost
        assert edge_cost(problem, b, a) == cost
    for order in permutations(range(3)):
        candidate = tour(*order)
        original = candidate.copy()
        assert tour_cost(problem, candidate) == 12.0
        assert validate_solution(problem, candidate, 12.0) == 12.0
        np.testing.assert_array_equal(candidate, original)
    assert type(tour_cost(problem, tour(0, 2, 1))) is float


@pytest.mark.parametrize("kind", KINDS)
def test_two_cities_counts_outward_and_return_edges(kind):
    problem = instance([(0, 0), (3, 4)], kind)
    assert tour_cost(problem, tour(0, 1)) == 10.0
    assert tour_cost(problem, tour(1, 0)) == 10.0


@pytest.mark.parametrize("kind", KINDS)
def test_square_ties_and_all_permutations(kind):
    problem = instance([(0, 0), (1, 0), (1, 1), (0, 1)], kind)
    assert edge_cost(problem, 0, 1) == edge_cost(problem, 0, 3) == 1.0
    diagonal = math.sqrt(2) if kind == "euclidean_2d" else 1.0
    for order in permutations(range(4)):
        # Opposite corners differ by 2 in this coordinate labelling.
        expected = sum(diagonal if abs(order[i] - order[(i + 1) % 4]) == 2 else 1.0 for i in range(4))
        assert tour_cost(problem, tour(*order)) == pytest.approx(expected, rel=1e-15)


@pytest.mark.parametrize("kind", KINDS)
def test_duplicates_are_distinct_city_visits_and_zero_cost_is_valid(kind):
    problem = instance([(0, 0), (0, 0), (3, 4)], kind)
    assert edge_cost(problem, 0, 1) == 0.0
    assert tour_cost(problem, tour(0, 1, 2)) == 10.0
    zeros = instance([(7, -2)] * 4, kind)
    assert validate_solution(zeros, tour(3, 1, 0, 2), 0.0) == 0.0
    with pytest.raises(ValueError):
        tour_cost(zeros, tour(0, 1, 1, 3))


@pytest.mark.parametrize("kind", KINDS)
def test_diagonal_zero_including_extreme_coordinates(kind):
    maximum = np.finfo(np.float64).max
    problem = instance([(maximum, maximum)] * 2, kind)
    assert edge_cost(problem, 0, 0) == 0.0
    assert edge_cost(problem, 0, 1) == 0.0
    assert tour_cost(problem, tour(0, 1)) == 0.0


@pytest.mark.parametrize("distance,rounded", [
    (0.0, 0), (0.49, 0), (np.nextafter(0.5, 0.0), 0), (0.5, 1),
    (np.nextafter(0.5, np.inf), 1), (1.5, 2), (2.5, 3),
    (np.nextafter(2.5, 0.0), 2), (np.nextafter(2.5, np.inf), 3),
    (float(2**52 + 1), 2**52 + 1), (float(MAX_EXACT_INTEGER), MAX_EXACT_INTEGER),
])
def test_rounding_is_half_up_without_intermediate_addition_error(distance, rounded):
    problem = instance([(0, 0), (distance, 0)], "tsplib_euc_2d")
    assert edge_cost(problem, 0, 1) == rounded
    assert edge_cost(instance([(0, 0), (distance, 0)]), 0, 1) == distance


def test_round_per_edge_not_after_summing():
    problem = instance([(0, 0), (0.5, 0), (1, 0)], "tsplib_euc_2d")
    assert tour_cost(problem, tour(0, 1, 2)) == 3.0
    assert tour_cost(instance([(0, 0), (0.5, 0), (1, 0)]), tour(0, 1, 2)) == 2.0


def test_hypot_avoids_overflow_of_intermediate_squares():
    problem = instance([(0, 0), (3e200, 4e200)])
    assert edge_cost(problem, 0, 1) == pytest.approx(5e200)
    assert tour_cost(problem, tour(0, 1)) == pytest.approx(1e201)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("points", [
    [(-np.finfo(np.float64).max, 0), (np.finfo(np.float64).max, 0)],
    [(0, 0), (np.finfo(np.float64).max, np.finfo(np.float64).max)],
])
def test_nonfinite_derived_distance_is_a_numerical_failure(kind, points):
    problem = instance(points, kind)
    with np.errstate(all="raise"), pytest.raises(NumericalError, match="edge distance"):
        edge_cost(problem, 0, 1)
    with pytest.raises(NumericalError):
        tour_cost(problem, tour(0, 1))


def test_finite_edges_can_overflow_the_complete_tour():
    maximum = np.finfo(np.float64).max
    problem = instance([(0, 0), (maximum, 0)])
    assert edge_cost(problem, 0, 1) == maximum
    with pytest.raises(NumericalError, match="tour total"):
        tour_cost(problem, tour(0, 1))


def test_rounded_edge_outside_exact_integer_range():
    distance = np.nextafter(float(MAX_EXACT_INTEGER), np.inf)
    with pytest.raises(NumericalError, match="rounded edge"):
        edge_cost(instance([(0, 0), (distance, 0)], "tsplib_euc_2d"), 0, 1)


def test_rounded_total_at_exact_limit_and_one_unit_overflow():
    problem = instance([(0, 0), (2**52, 0)], "tsplib_euc_2d")
    assert tour_cost(problem, tour(0, 1)) == MAX_EXACT_INTEGER
    # The first two rounded edges total 2**53. Adding the last unit must fail,
    # not silently round the sum back down to 2**53.
    overflow = instance([(0, 0), (2**52, 0), (0, 1)], "tsplib_euc_2d")
    with pytest.raises(NumericalError, match="rounded tour total"):
        tour_cost(overflow, tour(0, 1, 2))
    for reported in (MAX_EXACT_INTEGER + 1, np.int64(MAX_EXACT_INTEGER + 1)):
        with pytest.raises(ValueError, match="disagrees"):
            validate_solution(problem, tour(0, 1), reported)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("reported", [12, 12.0, np.int64(12), np.float32(12), np.float64(12)])
def test_reported_numeric_cost_types(kind, reported):
    assert validate_solution(instance([(0, 0), (3, 0), (3, 4)], kind), tour(0, 1, 2), reported) == 12.0


@pytest.mark.parametrize("reported,error", [
    (None, TypeError), ("12", TypeError), (True, TypeError),
    (np.bool_(False), TypeError), (12 + 0j, TypeError), (np.array(12.0), TypeError),
    (np.nan, ValueError), (np.inf, ValueError), (-np.inf, ValueError),
    (-1, ValueError), (10**1000, ValueError),
])
def test_reject_invalid_reported_cost(reported, error):
    with pytest.raises(error):
        validate_solution(instance([(0, 0), (3, 4)]), tour(0, 1), reported)


def test_validation_tolerances_and_exact_rounded_comparison():
    points = [(0, 0), (3, 0), (3, 4)]
    tolerance = COST_ATOL + COST_RTOL * 12.0
    problem = instance(points)
    for sign in (-1, 1):
        assert validate_solution(problem, tour(0, 1, 2), 12.0 + sign * tolerance / 2) == 12.0
        with pytest.raises(ValueError, match="disagrees"):
            validate_solution(problem, tour(0, 1, 2), 12.0 + sign * tolerance * 2)
    with pytest.raises(ValueError, match="disagrees"):
        validate_solution(instance(points, "tsplib_euc_2d"), tour(0, 1, 2), 12.0 + 1e-12)
    zeros = instance([(0, 0), (0, 0)])
    assert validate_solution(zeros, tour(0, 1), COST_ATOL) == 0.0
    with pytest.raises(ValueError, match="disagrees"):
        validate_solution(zeros, tour(0, 1), np.nextafter(COST_ATOL, np.inf))
    with pytest.raises(ValueError, match="disagrees"):
        validate_solution(instance([(0, 0), (0, 0)], "tsplib_euc_2d"), tour(0, 1), COST_ATOL)


def test_float32_report_does_not_reduce_reference_precision():
    problem = instance([(0, 0), (50_000_000.5, 0)])
    assert tour_cost(problem, tour(0, 1)) == 100_000_001.0
    with pytest.raises(ValueError, match="disagrees"):
        validate_solution(problem, tour(0, 1), np.float32(100_000_000))


def test_reported_zero_does_not_hide_an_invalid_permutation():
    with pytest.raises(ValueError, match="exactly once"):
        validate_solution(instance([(0, 0)] * 3), tour(0, 0, 2), 0)


@pytest.mark.parametrize("bad_instance", [None, {}, np.zeros((2, 2))])
def test_require_canonical_instance(bad_instance):
    for function, arguments in (
        (edge_cost, (0, 1)), (tour_cost, (tour(0, 1),)),
        (validate_solution, (tour(0, 1), 0)),
    ):
        with pytest.raises(TypeError, match="TSPInstance"):
            function(bad_instance, *arguments)


@pytest.mark.parametrize("index,error", [(True, TypeError), (0.0, TypeError), (-1, ValueError), (2, ValueError)])
def test_edge_checks_both_indices(index, error):
    problem = instance([(0, 0), (3, 4)])
    with pytest.raises(error):
        edge_cost(problem, index, 0)
    with pytest.raises(error):
        edge_cost(problem, 0, index)


def test_cost_checks_tour_instead_of_accepting_closed_or_float_arrays():
    problem = instance([(0, 0), (3, 4)])
    with pytest.raises(ValueError):
        tour_cost(problem, tour(0, 1, 0))
    with pytest.raises(TypeError):
        tour_cost(problem, np.array([0.0, 1.0]))
