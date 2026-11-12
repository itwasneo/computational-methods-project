"""Canonical array and permutation contracts, independent of any solver."""

from dataclasses import FrozenInstanceError
from itertools import permutations

import numpy as np
import pytest

from tsp_hpc.problem.data import TSPInstance, validate_city_index, validate_coordinates, validate_tour


@pytest.mark.parametrize("kind", ["euclidean_2d", "tsplib_euc_2d"])
def test_instance_owns_read_only_coordinates(kind):
    coordinates = np.array([[0.0, 0.0], [3.0, 4.0]])
    original = coordinates.copy()
    instance = TSPInstance("triangle-input", coordinates, kind)
    assert instance.instance_id == "triangle-input"
    assert instance.distance_kind == kind
    assert instance.n_cities == 2
    assert instance.coordinates.dtype == np.float64
    assert instance.coordinates.flags.c_contiguous
    assert not instance.coordinates.flags.writeable
    assert coordinates.flags.writeable
    assert not np.shares_memory(coordinates, instance.coordinates)
    coordinates[0, 0] = 99
    np.testing.assert_array_equal(instance.coordinates, original)
    with pytest.raises(ValueError):
        instance.coordinates[0, 0] = 99
    with pytest.raises(FrozenInstanceError):
        instance.instance_id = "changed"
    with pytest.raises(FrozenInstanceError):
        instance.n_cities = 9


def test_read_only_input_and_default_distance_kind():
    coordinates = np.zeros((3, 2), dtype=np.float64)
    coordinates.flags.writeable = False
    instance = TSPInstance("coincident", coordinates)
    assert instance.distance_kind == "euclidean_2d"
    assert instance.n_cities == 3
    assert not coordinates.flags.writeable
    np.testing.assert_array_equal(coordinates, instance.coordinates)


@pytest.mark.parametrize("coordinates", [
    [[0.0, 0.0], [1.0, 1.0]],
    np.zeros((2, 2), dtype=np.float32),
    np.zeros((2, 2), dtype=np.int64),
    np.zeros((2, 2), dtype=complex),
    np.zeros((2, 2), dtype=object),
    np.zeros((2, 2), dtype=np.dtype(np.float64).newbyteorder("S")),
    np.ma.array([[0.0, 0.0], [np.nan, 1.0]], mask=[[False, False], [True, False]]),
    None,
])
def test_reject_coordinate_types_without_coercion(coordinates):
    with pytest.raises(TypeError):
        validate_coordinates(coordinates)
    with pytest.raises(TypeError):
        TSPInstance("invalid", coordinates)


@pytest.mark.parametrize("shape", [(), (4,), (2, 1), (2, 3), (2, 2, 1), (0, 2), (1, 2)])
def test_reject_coordinate_shapes(shape):
    with pytest.raises(ValueError):
        TSPInstance("invalid", np.zeros(shape, dtype=np.float64))


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_reject_nonfinite_coordinates(value):
    coordinates = np.array([[0.0, 0.0], [value, 1.0]])
    with pytest.raises(ValueError, match="finite"):
        TSPInstance("invalid", coordinates)


@pytest.mark.parametrize("coordinates", [
    np.asfortranarray(np.zeros((3, 2))),
    np.zeros((6, 2))[::2],
    np.zeros((3, 2))[::-1],
])
def test_reject_noncontiguous_coordinates(coordinates):
    assert not coordinates.flags.c_contiguous
    with pytest.raises(ValueError, match="C-contiguous"):
        TSPInstance("invalid", coordinates)


@pytest.mark.parametrize("coordinates", [
    np.zeros((2, 2)),
    np.array([[-2.0, 0.0], [0.0, 0.0], [3.0, 0.0]]),
    np.full((2, 2), np.finfo(np.float64).max),
])
def test_valid_duplicate_collinear_and_large_coordinates(coordinates):
    original = coordinates.copy()
    assert validate_coordinates(coordinates) is None
    np.testing.assert_array_equal(coordinates, original)


@pytest.mark.parametrize("identifier,error", [(None, TypeError), (1, TypeError), ("", ValueError), (" \t", ValueError)])
def test_invalid_instance_identifier(identifier, error):
    with pytest.raises(error):
        TSPInstance(identifier, np.zeros((2, 2)))


@pytest.mark.parametrize("kind,error", [(None, TypeError), (1, TypeError), ("EUC_2D", ValueError), ("unknown", ValueError)])
def test_invalid_distance_kind(kind, error):
    with pytest.raises(error):
        TSPInstance("invalid", np.zeros((2, 2)), kind)


@pytest.mark.parametrize("index", [0, 2, np.int32(1), np.int64(2), np.uint64(1)])
def test_city_index(index):
    assert validate_city_index(index, 3) == int(index)
    assert type(validate_city_index(index, 3)) is int


@pytest.mark.parametrize("index,error", [
    (True, TypeError), (np.bool_(False), TypeError), (1.0, TypeError),
    ("1", TypeError), (None, TypeError), (-1, ValueError), (3, ValueError),
    (2**64, ValueError),
])
def test_invalid_city_index(index, error):
    with pytest.raises(error):
        validate_city_index(index, 3)


@pytest.mark.parametrize("count,error", [
    (True, TypeError), (np.bool_(True), TypeError), (2.0, TypeError),
    (None, TypeError), (-1, ValueError), (0, ValueError), (1, ValueError),
])
def test_invalid_city_count(count, error):
    with pytest.raises(error):
        validate_city_index(0, count)
    with pytest.raises(error):
        validate_tour(np.array([0, 1], dtype=np.int64), count)


def test_all_small_permutations_are_valid_without_reordering():
    for n_cities in range(2, 7):
        for order in permutations(range(n_cities)):
            tour = np.array(order, dtype=np.int64)
            tour.flags.writeable = False
            assert validate_tour(tour, np.int64(n_cities)) is None
            np.testing.assert_array_equal(tour, order)
            assert not tour.flags.writeable


@pytest.mark.parametrize("tour", [
    [0, 1], np.array([0.0, 1.0]), np.array([0, 1], dtype=np.int32),
    np.array([0, 1], dtype=np.uint64), np.array([False, True]),
    np.array([0, 1], dtype=object), np.ma.array([0, 1], dtype=np.int64), None,
])
def test_reject_tour_types(tour):
    with pytest.raises(TypeError):
        validate_tour(tour, 2)


@pytest.mark.parametrize("tour", [
    np.array([], dtype=np.int64), np.array([0], dtype=np.int64),
    np.array([0, 1, 0], dtype=np.int64), np.array([[0, 1]], dtype=np.int64),
    np.array(0, dtype=np.int64), np.array([0, 0], dtype=np.int64),
    np.array([0, 2], dtype=np.int64), np.array([-1, 1], dtype=np.int64),
    np.array([0, 9, 1, 9], dtype=np.int64)[::2],
])
def test_invalid_tour_values_or_layout(tour):
    with pytest.raises(ValueError):
        validate_tour(tour, 2)


def test_out_of_range_tour_checked_before_count_allocation(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("out-of-range city IDs reached bincount")
    monkeypatch.setattr(np, "bincount", unexpected)
    for invalid in (-1, np.iinfo(np.int64).max):
        with pytest.raises(ValueError, match="out-of-range"):
            validate_tour(np.array([0, invalid], dtype=np.int64), 2)
