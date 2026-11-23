"""Strict-math reversal moves for complete symmetric tours."""

from numba import njit

from tsp_hpc.problem.distances import distance_edge_kernel


@njit(cache=True, fastmath=False)
def reversal_delta(coordinates, matrix, use_dense, tour, i, j):
    n = len(tour)
    a, b = tour[i], tour[i + 1]
    c, d = tour[j], tour[(j + 1) % n]
    return (distance_edge_kernel(coordinates, matrix, use_dense, a, c)
            + distance_edge_kernel(coordinates, matrix, use_dense, b, d)
            - distance_edge_kernel(coordinates, matrix, use_dense, a, b)
            - distance_edge_kernel(coordinates, matrix, use_dense, c, d))


@njit(cache=True, fastmath=False)
def apply_reversal(tour, i, j):
    left, right = i + 1, j
    while left < right:
        tour[left], tour[right] = tour[right], tour[left]
        left += 1
        right -= 1


@njit(cache=True, fastmath=False)
def sample_edges(n, rng):
    while True:
        i = rng.integers(0, n)
        j = rng.integers(0, n - 1)
        if j >= i:
            j += 1
        if i > j:
            i, j = j, i
        if j != i + 1 and (i != 0 or j != n - 1):
            return i, j
