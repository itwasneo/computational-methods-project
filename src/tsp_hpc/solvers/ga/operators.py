"""Compiled GA operators for validated permutations and caller-owned buffers.

Random operators advance the supplied logical population generator. Parents,
offspring, and the old and new populations must occupy separate buffers.
"""

from numba import njit


@njit(cache=True, fastmath=False)
def initialize_tour(tour, rng):
    """Fill one population slot with a uniform Fisher–Yates permutation."""
    for city in range(len(tour)):
        tour[city] = city
    for position in range(len(tour) - 1, 0, -1):
        other = rng.integers(0, position + 1)
        tour[position], tour[other] = tour[other], tour[position]


@njit(cache=True, fastmath=False)
def tournament_select(costs, tournament_size, rng):
    """Sample with replacement, breaking cost ties by population slot."""
    winner = rng.integers(0, len(costs))
    for _ in range(tournament_size - 1):
        slot = rng.integers(0, len(costs))
        if costs[slot] < costs[winner] or (costs[slot] == costs[winner] and slot < winner):
            winner = slot
    return winner


@njit(cache=True, fastmath=False)
def ordered_crossover(parent_a, parent_b, start, end, child, copied):
    """Copy [start, end) and fill cyclically from end using parent B's order."""
    size = len(parent_a)
    copied[:] = False
    for position in range(start, end):
        city = parent_a[position]
        child[position] = city
        copied[city] = True

    read = write = end % size
    for _ in range(size):
        city = parent_b[read]
        if not copied[city]:
            child[write] = city
            write = (write + 1) % size
        read = (read + 1) % size


@njit(cache=True, fastmath=False)
def _distinct_pair(size, rng):
    first = rng.integers(0, size)
    second = rng.integers(0, size - 1)
    if second >= first:
        second += 1
    return first, second


@njit(cache=True, fastmath=False)
def swap_mutation(child, rng):
    """Swap two uniformly sampled distinct positions in the child."""
    first, second = _distinct_pair(len(child), rng)
    child[first], child[second] = child[second], child[first]


@njit(cache=True, fastmath=False)
def make_offspring(population, costs, tournament_size, crossover_probability,
                   mutation_probability, child, copied, rng):
    """Construct one child using two independent tournaments and operator draws."""
    parent_a = population[tournament_select(costs, tournament_size, rng)]
    parent_b = population[tournament_select(costs, tournament_size, rng)]
    if rng.random() < crossover_probability:
        first, second = _distinct_pair(len(child) + 1, rng)
        ordered_crossover(parent_a, parent_b, min(first, second), max(first, second),
                          child, copied)
    else:
        child[:] = parent_a
    if rng.random() < mutation_probability:
        swap_mutation(child, rng)


@njit(cache=True, fastmath=False)
def copy_elites(population, costs, elite_count, next_population, next_costs):
    """Copy elites and their cached costs to the front of the new population."""
    if elite_count == 0:
        return
    # Stable cost sorting retains old slot order among ties.
    ranked_slots = costs.argsort(kind="mergesort")
    for new_slot in range(elite_count):
        old_slot = ranked_slots[new_slot]
        next_population[new_slot] = population[old_slot]
        next_costs[new_slot] = costs[old_slot]
