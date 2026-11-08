"""Unrounded reference agreement, compiled callers, and explicit preparation."""

from dataclasses import FrozenInstanceError
from itertools import permutations
import math

from numba import njit
import numpy as np
import pytest

import tsp_hpc.problem.distances as module
from tsp_hpc.problem.data import TSPInstance
from tsp_hpc.problem.distances import (
    coordinate_edge_kernel, distance_edge_kernel, distance_tour_kernel,
    PreparedDistances, estimated_matrix_bytes, prepare_distances,
)
from tsp_hpc.reproducibility.identity import describe_instance
from tsp_hpc.problem.instances import fixture, fixture_names, uniform_instance, clustered_instance
from tsp_hpc.problem.objective import COST_ATOL, COST_RTOL, NumericalError, edge_cost, tour_cost

MODES = ("dense", "on_demand")


def problem(points):
    return TSPInstance("test", np.array(points, dtype=np.float64))


def assert_cost(actual, expected):
    assert abs(actual - expected) <= COST_ATOL + COST_RTOL * abs(expected)


@pytest.mark.parametrize("name", fixture_names())
def test_all_fixture_edges_and_tours_against_independent_reference(name):
    instance = fixture(name).instance
    descriptor = describe_instance(instance)
    dense = prepare_distances(instance, mode="dense")
    demand = prepare_distances(instance, mode="on_demand")
    for i in range(instance.n_cities):
        for j in range(instance.n_cities):
            assert_cost(dense.edge_cost(i, j), edge_cost(instance, i, j))
            assert dense.edge_cost(i, j) == demand.edge_cost(i, j)
            assert dense.edge_cost(i, j) == dense.edge_cost(j, i)
    for order in permutations(range(instance.n_cities)):
        tour = np.array(order, dtype=np.int64)
        original = tour.copy()
        assert_cost(dense.tour_cost(tour), tour_cost(instance, tour))
        assert dense.tour_cost(tour) == demand.tour_cost(tour)
        np.testing.assert_array_equal(tour, original)
    assert describe_instance(instance) == descriptor
    assert dense.instance is demand.instance is instance


@pytest.mark.parametrize("builder", [uniform_instance, clustered_instance])
def test_generated_edges_and_random_tours(builder):
    instance = builder(32, seed=42, side_length=1000.).instance
    dense = prepare_distances(instance, mode="dense")
    demand = prepare_distances(instance, mode="on_demand")
    for i in range(instance.n_cities):
        for j in range(instance.n_cities):
            assert_cost(dense.edge_cost(i, j), edge_cost(instance, i, j))
            assert dense.edge_cost(i, j) == demand.edge_cost(i, j)
    rng = np.random.Generator(np.random.PCG64(123))
    for _ in range(30):
        tour = rng.permutation(instance.n_cities).astype(np.int64)
        assert_cost(dense.tour_cost(tour), tour_cost(instance, tour))
        assert dense.tour_cost(tour) == demand.tour_cost(tour)


@pytest.mark.parametrize("mode", MODES)
def test_rounded_instances_rejected_before_warmup_or_allocation(mode, monkeypatch):
    instance = fixture("square", distance_kind="tsplib_euc_2d").instance
    def fail(*args, **kwargs):
        raise AssertionError("unsupported convention must fail before preparation")
    monkeypatch.setattr(module, "warmup_distance_kernels", fail)
    monkeypatch.setattr(module.np, "empty", fail)
    with pytest.raises(ValueError, match="only unrounded euclidean_2d"):
        prepare_distances(instance, mode=mode)


@pytest.mark.parametrize("mode", MODES)
def test_distances_are_not_integer_rounded(mode):
    instance = problem([(0, 0), (.5, 0), (1, 0)])
    backend = prepare_distances(instance, mode=mode)
    assert backend.edge_cost(0, 1) == .5
    assert backend.tour_cost(np.arange(3, dtype=np.int64)) == 2.


@pytest.mark.parametrize("mode", MODES)
def test_native_hypot_small_difference_uses_unrounded_reference_tolerance(mode):
    # Implementations may differ in the last bit. Do not round to integers or
    # require bitwise equivalence to CPython's separate math implementation.
    instance = problem([(0, 0), (5865.277089690506, 5153.210738088601)])
    backend = prepare_distances(instance, mode=mode)
    actual = backend.edge_cost(0, 1)
    assert_cost(actual, edge_cost(instance, 0, 1))
    assert 7807.49 < actual < 7807.51
    assert actual != math.floor(actual)


def test_native_norm_across_representative_magnitudes():
    # Keep subnormal/normal and extreme-scale coverage. Integer half boundaries
    # are not special to our unrounded execution path; its known regression is
    # tested separately, and generated-edge tests already cover ordinary inputs.
    for exponent in (-1073, -1022, -1000, -500, -1, 0, 1, 500, 1000, 1023):
        x, y = math.ldexp(.75, exponent), math.ldexp(.625, exponent)
        coordinates = np.array([[0., 0.], [x, y]])
        actual = coordinate_edge_kernel(coordinates, 0, 1)
        expected = math.hypot(x, y)
        # A relative check would underflow for subnormals; allow at most one ULP.
        assert abs(actual - expected) <= math.ulp(expected)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("xy", [(3e200, 4e200), (5e-324, 5e-324), (1e-308, 1e-308)])
def test_norm_avoids_squared_overflow_and_preserves_tiny_edges(mode, xy):
    instance = problem([(0., 0.), xy])
    actual = prepare_distances(instance, mode=mode).edge_cost(0, 1)
    expected = edge_cost(instance, 0, 1)
    assert abs(actual - expected) <= math.ulp(expected)
    assert actual > 0  # The usual absolute tolerance would hide underflow to zero.


@pytest.mark.parametrize("mode", MODES)
def test_diagonal_and_duplicate_extreme_coordinates_are_zero(mode):
    maximum = np.finfo(np.float64).max
    backend = prepare_distances(problem([(maximum, maximum)] * 2), mode=mode)
    assert backend.edge_cost(0, 0) == backend.edge_cost(0, 1) == 0.
    assert backend.tour_cost(np.array([0, 1], dtype=np.int64)) == 0.


@pytest.mark.parametrize("points", [
    [(-np.finfo(np.float64).max, 0), (np.finfo(np.float64).max, 0)],
    [(0, 0), (np.finfo(np.float64).max, np.finfo(np.float64).max)],
])
def test_dense_checks_all_edges_and_on_demand_checks_requested_edges(points):
    instance = problem(points)
    with pytest.raises(NumericalError, match="edge distance"):
        prepare_distances(instance, mode="dense")
    backend = prepare_distances(instance, mode="on_demand")
    assert backend.edge_cost(0, 0) == 0.
    with pytest.raises(NumericalError, match="edge distance"):
        backend.edge_cost(0, 1)
    with pytest.raises(NumericalError):
        backend.tour_cost(np.array([0, 1], dtype=np.int64))


@pytest.mark.parametrize("mode", MODES)
def test_finite_unrounded_edges_can_overflow_total(mode):
    backend = prepare_distances(problem([(0, 0), (np.finfo(np.float64).max, 0)]), mode=mode)
    assert backend.edge_cost(0, 1) == np.finfo(np.float64).max
    with pytest.raises(NumericalError, match="tour total"):
        backend.tour_cost(np.array([0, 1], dtype=np.int64))


@njit(fastmath=False)
def _compiled_client(coordinates, matrix, dense, tour):
    edge = distance_edge_kernel(coordinates, matrix, dense, 0, 1)
    return edge, distance_tour_kernel(coordinates, matrix, dense, tour)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("readonly", [False, True])
def test_compiled_client_and_warmed_signatures(mode, readonly):
    backend = prepare_distances(fixture("triangle").instance, mode=mode)
    tour = np.arange(3, dtype=np.int64)
    tour.flags.writeable = not readonly
    signatures = tuple(distance_tour_kernel.signatures)
    assert backend.tour_cost(tour) == 12.
    assert tuple(distance_tour_kernel.signatures) == signatures
    assert _compiled_client(backend.coordinates, backend.matrix, backend.use_dense, tour) == (3., 12.)
    assert _compiled_client.nopython_signatures


@pytest.mark.parametrize("mode", MODES)
def test_explicit_storage_no_mutation_or_rebuild_during_queries(mode, monkeypatch):
    instance = fixture("square").instance
    backend = prepare_distances(instance, mode=mode)
    assert backend.coordinates is instance.coordinates
    assert backend.matrix.dtype == np.float64 and backend.matrix.flags.c_contiguous
    assert not backend.matrix.flags.writeable
    assert backend.matrix.shape == ((4, 4) if mode == "dense" else (0, 0))
    assert backend.metadata["matrix_bytes"] == (128 if mode == "dense" else 0)
    assert backend.metadata["estimated_matrix_bytes"] == 128
    assert backend.metadata["coordinate_bytes"] == 64
    if mode == "dense":
        np.testing.assert_array_equal(backend.matrix, backend.matrix.T)
        np.testing.assert_array_equal(np.diag(backend.matrix), np.zeros(4))
    matrix = backend.matrix
    tour = np.arange(4, dtype=np.int64)
    def fail(*args, **kwargs):
        raise AssertionError("query must not allocate or prepare another matrix")
    monkeypatch.setattr(module, "prepare_distances", fail)
    monkeypatch.setattr(module, "_fill_dense", fail)
    monkeypatch.setattr(module.np, "empty", fail)
    for _ in range(3):
        assert backend.edge_cost(0, 1) == 1.
        assert backend.tour_cost(tour) == 4.
    assert backend.matrix is matrix
    backend.metadata["distance_mode"] = "changed"
    assert backend.mode == mode
    with pytest.raises(FrozenInstanceError):
        backend.mode = "changed"


def test_memory_limit_checked_before_warmup_or_allocation(monkeypatch):
    def fail():
        raise AssertionError("oversized request must fail before warmup")
    monkeypatch.setattr(module, "warmup_distance_kernels", fail)
    with pytest.raises(MemoryError, match="128 bytes"):
        prepare_distances(fixture("square").instance, mode="dense", max_matrix_bytes=127)
    assert estimated_matrix_bytes(2**40) == 8 * (2**40)**2


def test_platform_address_limit_is_checked_before_allocation(monkeypatch):
    size = int(np.iinfo(np.intp).max) + 1
    monkeypatch.setattr(module, "estimated_matrix_bytes", lambda n: size)
    with pytest.raises(OverflowError, match="address range"):
        prepare_distances(fixture("square").instance, mode="dense", max_matrix_bytes=size)


def test_exact_memory_limit_and_on_demand_zero_limit():
    instance = fixture("square").instance
    assert prepare_distances(instance, mode="dense", max_matrix_bytes=128).matrix.nbytes == 128
    assert prepare_distances(instance, mode="on_demand", max_matrix_bytes=0).matrix.nbytes == 0


def test_timing_separates_warmup_from_preprocessing(monkeypatch):
    times = iter([10., 17., 20., 22.])
    monkeypatch.setattr(module, "perf_counter", lambda: next(times))
    backend = prepare_distances(fixture("square").instance, mode="dense")
    assert backend.metadata["jit_warmup_time_s"] == 7.
    assert backend.metadata["preprocessing_time_s"] == 2.


@pytest.mark.parametrize("index,error,mode", [
    (True, TypeError, "on_demand"), (1., TypeError, "dense"),
    (-1, ValueError, "dense"), (4, ValueError, "on_demand"),
])
def test_wrappers_check_both_city_indices(mode, index, error):
    backend = prepare_distances(fixture("square").instance, mode=mode)
    with pytest.raises(error):
        backend.edge_cost(index, 0)
    with pytest.raises(error):
        backend.edge_cost(0, index)
    assert backend.edge_cost(np.int64(0), np.int32(1)) == 1.


@pytest.mark.parametrize("tour,error,mode", [
    # Validate once at the common wrapper boundary, not every invalid input
    # crossed with every storage representation.
    (np.array([0, 1, 2, 2], dtype=np.int64), ValueError, "dense"),
    (np.array([0, 1, 2, 4], dtype=np.int64), ValueError, "on_demand"),
    (np.array([0, 1, 2, -1], dtype=np.int64), ValueError, "on_demand"),
    (np.array([0, 1, 2, 3, 0], dtype=np.int64), ValueError, "on_demand"),
    (np.arange(4, dtype=np.float64), TypeError, "dense"),
    ([0, 1, 2, 3], TypeError, "on_demand"),
    (np.arange(8, dtype=np.int64)[::2], ValueError, "on_demand"),
])
def test_wrappers_reject_invalid_tours(mode, tour, error):
    with pytest.raises(error):
        prepare_distances(fixture("square").instance, mode=mode).tour_cost(tour)


@pytest.mark.parametrize("kwargs,error", [
    ({"mode": "auto"}, ValueError), ({"mode": None}, TypeError),
    ({"mode": "dense", "max_matrix_bytes": True}, TypeError),
    ({"mode": "dense", "max_matrix_bytes": -1}, ValueError),
    ({"mode": "dense", "max_matrix_bytes": None}, TypeError),
])
def test_invalid_preparation_options(kwargs, error):
    with pytest.raises(error):
        prepare_distances(fixture("square").instance, **kwargs)


def test_invalid_instance_and_size_estimates():
    with pytest.raises(TypeError, match="prepare_distances"):
        PreparedDistances()
    with pytest.raises(TypeError):
        prepare_distances(None, mode="dense")
    for size in (True, 2., np.int64(2)):
        with pytest.raises(TypeError):
            estimated_matrix_bytes(size)
    for size in (0, 1, -1):
        with pytest.raises(ValueError):
            estimated_matrix_bytes(size)
