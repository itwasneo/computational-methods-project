"""Preserved visited-scan-v1 implementation of nn-fixed-starts-v1.

Add optimizations under new implementation IDs rather than replacing this
baseline. The kernel trusts validated canonical inputs; use the NN adapter at
Python boundaries. All workspace initialization belongs to measured search.
"""

import math

from numba import njit
import numpy as np

from tsp_hpc.problem.distances import distance_edge_kernel, distance_tour_kernel
from tsp_hpc.problem.objective import NumericalError


@njit(cache=True, fastmath=False)
def search_kernel(coordinates, matrix, use_dense, starts):
    """Scan all city IDs at every step, skipping visited cities before scoring."""
    if len(starts) == 0:
        return np.empty(0, dtype=np.int64), 0.0, 0
    n = len(coordinates)
    candidate = np.empty(n, dtype=np.int64)
    visited = np.empty(n, dtype=np.bool_)
    best = np.empty(n, dtype=np.int64)
    best_cost = math.inf  # Internal sentinel only; never exposed as a result.
    completed = 0
    for start in starts:
        for city in range(n):
            visited[city] = False
        candidate[0] = start
        visited[start] = True
        current = start
        for position in range(1, n):
            nearest = -1
            nearest_cost = math.inf
            # Ascending city IDs plus strict improvement implement the tie rule.
            for city in range(n):
                if not visited[city]:
                    cost = distance_edge_kernel(coordinates, matrix, use_dense, current, city)
                    if cost < nearest_cost:
                        nearest = city
                        nearest_cost = cost
            if nearest < 0:
                raise NumericalError("NN could not select a finite unvisited city")
            candidate[position] = nearest
            visited[nearest] = True
            current = nearest
        cost = distance_tour_kernel(coordinates, matrix, use_dense, candidate)
        if completed == 0 or cost < best_cost:
            best_cost = cost
            for city in range(n):
                best[city] = candidate[city]
        completed += 1
    return best, best_cost, completed
