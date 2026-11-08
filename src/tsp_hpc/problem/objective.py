"""Independent coordinate-based objective reference, not an optimized backend.

All costs use float64 precision. No distance matrix, JIT compilation, relaxed
floating-point transformations, or solver-maintained fitness/deltas are used.
"""

import math
from numbers import Real

import numpy as np
from numpy.typing import NDArray

from tsp_hpc.problem.data import TSPInstance, validate_city_index, validate_tour

COST_RTOL = 1e-10
COST_ATOL = 1e-12
# Every integer in this contiguous range is exactly representable in float64.
MAX_EXACT_INTEGER = 2**53


class NumericalError(ArithmeticError):
    """A derived distance or total is outside the supported numerical range."""


def _require_instance(instance: TSPInstance) -> None:
    if not isinstance(instance, TSPInstance):
        raise TypeError("instance must be a TSPInstance")


def _edge_cost(instance: TSPInstance, city_a: int, city_b: int) -> float:
    coordinates = instance.coordinates
    # Python floats avoid NumPy's warning-only overflow behavior. hypot avoids
    # overflowing squared components when the distance itself is representable.
    dx = float(coordinates[city_a, 0]) - float(coordinates[city_b, 0])
    dy = float(coordinates[city_a, 1]) - float(coordinates[city_b, 1])
    distance = math.hypot(dx, dy)
    if not math.isfinite(distance):
        raise NumericalError("derived edge distance is not finite")
    if instance.distance_kind == "tsplib_euc_2d":
        # Mathematically floor(r + 0.5), without premature rounding of r + 0.5
        # near half-integers or large exactly representable integer distances.
        whole = math.floor(distance)
        rounded = whole + int(distance - whole >= 0.5)
        if rounded > MAX_EXACT_INTEGER:
            raise NumericalError("rounded edge exceeds the exact float64 integer range")
        return float(rounded)
    return distance


def edge_cost(instance: TSPInstance, city_a: int, city_b: int) -> float:
    """Evaluate a validated edge, including zero-cost diagonal/duplicate points."""
    _require_instance(instance)
    city_a = validate_city_index(city_a, instance.n_cities)
    city_b = validate_city_index(city_b, instance.n_cities)
    return _edge_cost(instance, city_a, city_b)


def tour_cost(instance: TSPInstance, tour: NDArray[np.int64]) -> float:
    """Validate and independently sum the complete tour, including its closure.

    Edges are accumulated in stored-tour order with float64 precision. Rounded
    totals are checked *before* addition to prevent losing an integer unit.
    """
    _require_instance(instance)
    validate_tour(tour, instance.n_cities)
    rounded = instance.distance_kind == "tsplib_euc_2d"
    total = 0.0
    for position in range(instance.n_cities):
        city_a = int(tour[position])
        city_b = int(tour[(position + 1) % instance.n_cities])
        edge = _edge_cost(instance, city_a, city_b)
        if rounded and edge > MAX_EXACT_INTEGER - total:
            raise NumericalError("rounded tour total exceeds the exact float64 integer range")
        total += edge
        if not math.isfinite(total):
            raise NumericalError("derived tour total is not finite")
    return total


def validate_solution(
    instance: TSPInstance, tour: NDArray[np.int64], reported_cost: float
) -> float:
    """Check a reported solution and return its independently recomputed cost.

    Rounded costs must agree exactly. Unrounded costs use
    abs(reported - reference) <= COST_ATOL + COST_RTOL * abs(reference).
    These tolerances are for validation only, not search decisions.
    """
    _require_instance(instance)
    if isinstance(reported_cost, (bool, np.bool_)) or not isinstance(reported_cost, Real):
        raise TypeError("reported_cost must be a real number, not a boolean")
    try:
        finite = math.isfinite(reported_cost)
    except OverflowError:
        finite = False
    if not finite or reported_cost < 0:
        raise ValueError("reported_cost must be finite and non-negative")
    reference = tour_cost(instance, tour)
    if instance.distance_kind == "tsplib_euc_2d":
        # Compare integer values without NumPy coercing a large integer to float.
        matches = reported_cost == int(reported_cost) and int(reported_cost) == int(reference)
    else:
        # Avoid NumPy's weak-scalar rules reducing the reference to float32.
        matches = abs(float(reported_cost) - reference) <= COST_ATOL + COST_RTOL * abs(reference)
    if not matches:
        raise ValueError("reported_cost disagrees with the independently recomputed tour cost")
    return reference
