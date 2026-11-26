"""Native GA steps operating on validated, invocation-owned search buffers."""

from numba import njit

from tsp_hpc.problem.distances import distance_tour_kernel
from tsp_hpc.solvers.ga.operators import copy_elites, initialize_tour, make_offspring


@njit(cache=True, fastmath=False)
def initialize_candidate(coordinates, matrix, use_dense, rounded, tour, best, best_cost, rng):
    """Initialize and evaluate one slot, a safe deadline checkpoint."""
    initialize_tour(tour, rng)
    cost = distance_tour_kernel(coordinates, matrix, use_dense, tour, rounded)
    if cost < best_cost:
        best[:] = tour
        best_cost = cost
    return cost, best_cost


@njit(cache=True, fastmath=False)
def advance_generation(coordinates, matrix, use_dense, rounded, population, costs, next_population,
                       next_costs, tournament_size, elite_count, crossover_probability,
                       mutation_probability, copied, best, best_cost, rng):
    """Fill the next population atomically, retaining the earliest equal-cost best."""
    copy_elites(population, costs, elite_count, next_population, next_costs)
    for slot in range(elite_count, len(population)):
        child = next_population[slot]
        make_offspring(population, costs, tournament_size, crossover_probability,
                       mutation_probability, child, copied, rng)
        cost = distance_tour_kernel(coordinates, matrix, use_dense, child, rounded)
        next_costs[slot] = cost
        if cost < best_cost:
            best[:] = child
            best_cost = cost
    return best_cost
