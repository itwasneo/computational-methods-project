"""remaining-array-v1: compact unvisited candidates with constant-time removal.

Implements nn-fixed-starts-v1 without changing its scientific comparisons.
Inputs are trusted canonical arrays supplied by the checked NN adapter.
Workspace allocation/reset is part of measured search, not preprocessing.
"""

import math

from numba import njit
import numpy as np

from tsp_hpc.problem.distances import distance_edge_kernel, distance_tour_kernel
from tsp_hpc.problem.objective import NumericalError


@njit(cache=True, fastmath=False)
def search_kernel(coordinates, matrix, use_dense, starts):
    """Scan only active candidates; break ties by city ID, never array position."""
    if len(starts) == 0:
        return np.empty(0, dtype=np.int64), 0.0, 0
    n = len(coordinates)
    candidate = np.empty(n, dtype=np.int64)
    remaining = np.empty(n - 1, dtype=np.int64)
    best = np.empty(n, dtype=np.int64)
    best_cost = math.inf
    completed = 0
    for start in starts:
        active = 0
        for city in range(n):
            if city != start:
                remaining[active] = city
                active += 1
        candidate[0] = start
        current = start
        for position in range(1, n):
            nearest = -1
            nearest_slot = -1
            nearest_cost = math.inf
            for slot in range(active):
                city = remaining[slot]
                cost = distance_edge_kernel(coordinates, matrix, use_dense, current, city)
                # Swap-removal destroys sorted order. Explicit ID comparison
                # preserves the baseline's exact (cost, city ID) selection.
                if cost < nearest_cost or (cost == nearest_cost and city < nearest):
                    nearest = city
                    nearest_slot = slot
                    nearest_cost = cost
            if nearest_slot < 0:
                raise NumericalError("NN could not select a finite unvisited city")
            candidate[position] = nearest
            active -= 1
            remaining[nearest_slot] = remaining[active]
            current = nearest
        cost = distance_tour_kernel(coordinates, matrix, use_dense, candidate)
        if completed == 0 or cost < best_cost:
            best_cost = cost
            for city in range(n):
                best[city] = candidate[city]
        completed += 1
    return best, best_cost, completed
