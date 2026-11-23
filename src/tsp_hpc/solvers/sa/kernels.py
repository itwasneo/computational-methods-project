"""Compiled independent-chain initialization and bounded proposal chunks."""

import math

from numba import njit

from tsp_hpc.problem.distances import distance_tour_kernel
from tsp_hpc.problem.objective import COST_ATOL, COST_RTOL, NumericalError
from tsp_hpc.solvers.ga.operators import initialize_tour
from tsp_hpc.solvers.sa.operators import apply_reversal, reversal_delta, sample_edges


@njit(cache=True, fastmath=False, nogil=True)
def initialize_chain(coordinates, matrix, use_dense, current, best, rng):
    initialize_tour(current, rng)
    best[:] = current
    return distance_tour_kernel(coordinates, matrix, use_dense, current)


@njit(cache=True, fastmath=False, nogil=True)
def advance_chain(coordinates, matrix, use_dense, current, best, current_cost, best_cost,
                  temperature, cooling, floor, check_interval, proposals, evaluations,
                  count, rng):
    for _ in range(count):
        i, j = sample_edges(len(current), rng)
        delta = reversal_delta(coordinates, matrix, use_dense, current, i, j)
        if not math.isfinite(delta):
            raise NumericalError("reversal delta is not finite")
        accept = delta <= 0
        if not accept:
            u = rng.random()
            while u == 0.0:
                u = rng.random()
            accept = math.log(u) < -delta / temperature
        if accept:
            apply_reversal(current, i, j)
            current_cost += delta
            if not math.isfinite(current_cost):
                raise NumericalError("incremental tour cost is not finite")
        proposals += 1
        if current_cost < best_cost or proposals % check_interval == 0:
            checked = distance_tour_kernel(coordinates, matrix, use_dense, current)
            evaluations += 1
            if abs(current_cost - checked) > COST_ATOL + COST_RTOL * abs(checked):
                raise NumericalError("incremental tour cost drift exceeds tolerance")
            current_cost = checked
            if current_cost < best_cost:
                best[:] = current
                best_cost = current_cost
        temperature = max(floor, temperature * cooling)
    return current_cost, best_cost, temperature, proposals, evaluations
