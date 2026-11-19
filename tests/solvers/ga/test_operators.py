from itertools import combinations

import numpy as np
import pytest

from tsp_hpc.reproducibility.random_streams import derive_stream
from tsp_hpc.solvers.ga.operators import (
    copy_elites,
    initialize_tour,
    make_offspring,
    ordered_crossover,
    swap_mutation,
    tournament_select,
)


def population_stream():
    return derive_stream(2**63 + 7, "population", "operator-tests")


@pytest.mark.parametrize("size", [2, 5])
def test_initialization_and_offspring_replay(size):
    stream = population_stream()
    costs = np.array([3.0, 1.0, 1.0, 2.0])

    def run():
        rng = stream.generator()
        population = np.empty((len(costs), size), dtype=np.int64)
        for tour in population:
            initialize_tour(tour, rng)
        original = population.copy()
        children = np.empty((12, size), dtype=np.int64)
        copied = np.empty(size, dtype=np.bool_)
        for child in children:
            make_offspring(population, costs, 6, 0.9, 0.2, child, copied, rng)
        np.testing.assert_array_equal(population, original)
        for tour in (*population, *children):
            np.testing.assert_array_equal(np.sort(tour), np.arange(size))
        return population, children

    for first, replayed in zip(run(), run(), strict=True):
        np.testing.assert_array_equal(first, replayed)


@pytest.mark.parametrize("costs", [[0.0, 0.0, 0.0], [4.0, 1.0, 2.0]])
def test_tournament_uses_replacement_and_lowest_slot_tie_break(costs):
    costs = np.array(costs)
    stream = population_stream()
    rng = stream.generator()
    reference = stream.generator()
    for size in (1, 7):
        for _ in range(12):
            sampled = [reference.integers(len(costs)) for _ in range(size)]
            expected = min(sampled, key=lambda slot: (costs[slot], slot))
            assert tournament_select(costs, size, rng) == expected


def test_ordered_crossover_all_boundaries_and_parent_immutability():
    parent_a = np.array([0, 1, 2, 3, 4])
    parent_b = np.array([4, 3, 0, 2, 1])
    child = np.empty_like(parent_a)
    copied = np.empty(len(child), dtype=np.bool_)
    for start, end in combinations(range(len(child) + 1), 2):
        segment = parent_a[start:end]
        rotated = np.roll(parent_b, -end)
        remaining = rotated[~np.isin(rotated, segment)]
        split = len(child) - end
        expected = np.concatenate((remaining[split:], segment, remaining[:split]))
        ordered_crossover(parent_a, parent_b, start, end, child, copied)
        np.testing.assert_array_equal(child, expected)
    np.testing.assert_array_equal(parent_a, [0, 1, 2, 3, 4])
    np.testing.assert_array_equal(parent_b, [4, 3, 0, 2, 1])


@pytest.mark.parametrize("size", [2, 7])
def test_mutation_swaps_exactly_two_positions(size):
    original = np.arange(size)
    child = original.copy()
    swap_mutation(child, population_stream().generator())
    assert np.count_nonzero(child != original) == 2
    np.testing.assert_array_equal(np.sort(child), original)


@pytest.mark.parametrize("crossover", [0.0, 1.0])
@pytest.mark.parametrize("mutation", [0.0, 1.0])
def test_offspring_probability_boundaries(crossover, mutation):
    population = np.array([[0, 1, 2, 3, 4], [4, 3, 0, 2, 1]])
    costs = np.array([1.0, 1.0])
    stream = population_stream()
    rng = stream.generator()
    reference = stream.generator()
    child = np.empty_like(population[0])
    copied = np.empty(len(child), dtype=np.bool_)
    for _ in range(12):
        first = population[reference.integers(len(population))]
        second = population[reference.integers(len(population))]
        expected = first.copy()
        if reference.random() < crossover:
            a = reference.integers(len(child) + 1)
            b = reference.integers(len(child))
            start, end = sorted((a, b + (b >= a)))
            ordered_crossover(first, second, start, end, expected, copied)
        if reference.random() < mutation:
            swap_mutation(expected, reference)
        make_offspring(population, costs, 1, crossover, mutation, child, copied, rng)
        np.testing.assert_array_equal(child, expected)
        assert rng.bit_generator.state == reference.bit_generator.state


@pytest.mark.parametrize("elite_count", [0, 1, 3])
def test_elites_keep_cached_costs_and_stable_slot_order(elite_count):
    population = np.array([[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0]])
    costs = np.array([3.0, 1.0, 2.0, 1.0])
    original = population.copy()
    next_population = np.full_like(population, -1)
    next_costs = np.full_like(costs, -1)
    copy_elites(population, costs, elite_count, next_population, next_costs)
    slots = [1, 3, 2][:elite_count]
    np.testing.assert_array_equal(next_population[:elite_count], original[slots])
    np.testing.assert_array_equal(next_costs[:elite_count], costs[slots])
    assert np.all(next_population[elite_count:] == -1)
    assert np.all(next_costs[elite_count:] == -1)
    np.testing.assert_array_equal(population, original)
    next_population[:] = -2
    np.testing.assert_array_equal(population, original)
