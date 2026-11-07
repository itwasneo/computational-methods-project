"""Independent-reference agreement, compiled callers, and explicit preparation."""

from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext
from importlib.resources import files
from itertools import permutations
import math
import os
import subprocess
import sys

from numba import njit
import numpy as np
import pytest

import tsp_hpc.distances as module
from tsp_hpc._hypot import hypot_2d
from tsp_hpc.data import TSPInstance
from tsp_hpc.distances import (
    coordinate_edge_kernel, distance_edge_kernel, distance_tour_kernel,
    PreparedDistances, estimated_matrix_bytes, prepare_distances,
)
from tsp_hpc.identity import describe_instance
from tsp_hpc.instances import fixture, fixture_names, uniform_instance, clustered_instance
from tsp_hpc.objective import COST_ATOL, COST_RTOL, MAX_EXACT_INTEGER, NumericalError, edge_cost, tour_cost

KINDS = ("euclidean_2d", "tsplib_euc_2d")
MODES = ("dense", "on_demand")


def problem(points, kind="euclidean_2d"):
    return TSPInstance("test", np.array(points, dtype=np.float64), kind)


def assert_cost(actual, expected, kind):
    if kind == "tsplib_euc_2d":
        assert actual == expected
    else:
        assert abs(actual - expected) <= COST_ATOL + COST_RTOL * abs(expected)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", fixture_names())
def test_all_fixture_edges_and_tours_against_independent_reference(name, kind):
    instance = fixture(name, distance_kind=kind).instance
    descriptor = describe_instance(instance)
    dense = prepare_distances(instance, mode="dense")
    demand = prepare_distances(instance, mode="on_demand")
    for i in range(instance.n_cities):
        for j in range(instance.n_cities):
            reference = edge_cost(instance, i, j)
            assert_cost(dense.edge_cost(i, j), reference, kind)
            assert dense.edge_cost(i, j) == demand.edge_cost(i, j)
            assert dense.edge_cost(i, j) == dense.edge_cost(j, i)
    for order in permutations(range(instance.n_cities)):
        tour = np.array(order, dtype=np.int64)
        original = tour.copy()
        assert_cost(dense.tour_cost(tour), tour_cost(instance, tour), kind)
        assert dense.tour_cost(tour) == demand.tour_cost(tour)
        np.testing.assert_array_equal(tour, original)
    assert describe_instance(instance) == descriptor
    assert dense.instance is demand.instance is instance


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("builder", [uniform_instance, clustered_instance])
def test_generated_edges_and_random_tours(builder, kind):
    instance = builder(32, seed=42, side_length=1000., distance_kind=kind).instance
    dense = prepare_distances(instance, mode="dense")
    demand = prepare_distances(instance, mode="on_demand")
    for i in range(instance.n_cities):
        for j in range(instance.n_cities):
            assert_cost(dense.edge_cost(i, j), edge_cost(instance, i, j), kind)
            assert dense.edge_cost(i, j) == demand.edge_cost(i, j)
    rng = np.random.Generator(np.random.PCG64(123))
    for _ in range(30):
        tour = rng.permutation(instance.n_cities).astype(np.int64)
        assert_cost(dense.tour_cost(tour), tour_cost(instance, tour), kind)
        assert dense.tour_cost(tour) == demand.tour_cost(tour)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("distance,expected", [
    (0., 0.), (np.nextafter(.5, 0.), 0.), (.5, 1.), (np.nextafter(.5, math.inf), 1.),
    (1.5, 2.), (2.5, 3.), (np.nextafter(2.5, 0.), 2.),
    (np.nextafter(2.5, math.inf), 3.), (float(2**52 + 1), float(2**52 + 1)),
    (float(MAX_EXACT_INTEGER), float(MAX_EXACT_INTEGER)),
])
def test_half_up_rounding_without_premature_addition(mode, distance, expected):
    instance = problem([(0., 0.), (distance, 0.)], "tsplib_euc_2d")
    backend = prepare_distances(instance, mode=mode)
    assert backend.edge_cost(0, 1) == edge_cost(instance, 0, 1) == expected


@pytest.mark.parametrize("mode", MODES)
def test_round_each_edge_not_the_completed_tour(mode):
    instance = problem([(0, 0), (.5, 0), (1, 0)], "tsplib_euc_2d")
    assert prepare_distances(instance, mode=mode).tour_cost(np.arange(3, dtype=np.int64)) == 3.


@pytest.mark.parametrize("mode", MODES)
def test_native_hypot_rounding_regression(mode):
    # On the development runtime, native compiled hypot rounded this up to 7807.5,
    # while CPython's compensated norm is the preceding float. That changed a
    # rounded edge from 7807 to 7808. Do not rely on platform libc hypot here.
    x, y = 5865.277089690506, 5153.210738088601
    assert math.hypot(x, y) == 7807.499999999999
    instance = problem([(0, 0), (x, y)], "tsplib_euc_2d")
    backend = prepare_distances(instance, mode=mode)
    assert backend.edge_cost(0, 1) == edge_cost(instance, 0, 1) == 7807.


def test_compensated_norm_half_integer_neighbourhoods_and_exponents():
    rng = np.random.Generator(np.random.PCG64(987))
    for _ in range(3000):
        angle = rng.uniform(0., math.pi / 2)
        radius = float(rng.integers(0, 100000)) + .5
        x, y = radius * math.cos(angle), radius * math.sin(angle)
        for neighbour in (np.nextafter(x, 0.), x, np.nextafter(x, math.inf)):
            reference = math.hypot(neighbour, y)
            actual = hypot_2d(float(neighbour), float(y))
            assert actual == reference
            coordinates = np.array([[0., 0.], [neighbour, y]])
            rounded = math.floor(reference) + int(reference - math.floor(reference) >= .5)
            assert coordinate_edge_kernel(coordinates, 0, 1, True) == rounded
    for exponent in range(-1073, 1024, 13):
        x = math.ldexp(.75, exponent)
        y = math.ldexp(.625, exponent)
        assert hypot_2d(x, y) == math.hypot(x, y)


@pytest.mark.parametrize("xy", [
    (5865.277089690506, 5153.210738088601), (0.3, 0.4),
    (3e200, 4e200), (5e-324, 5e-324), (1e-308, 1e-308),
    (float(2**52), 1.), (float(2**53), 1.),
])
def test_compensated_norm_against_high_precision_decimal_oracle(xy):
    with localcontext() as context:
        context.prec = 200
        x, y = map(Decimal.from_float, xy)
        expected = float((x*x + y*y).sqrt())
    assert hypot_2d(*xy) == expected


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("xy", [(3e200, 4e200), (5e-324, 5e-324), (1e-308, 1e-308)])
def test_norm_does_not_square_overflow_or_discard_subnormal_edges(mode, xy):
    instance = problem([(0., 0.), xy])
    actual = prepare_distances(instance, mode=mode).edge_cost(0, 1)
    assert actual == edge_cost(instance, 0, 1)  # Absolute tolerances would hide subnormal errors.
    assert actual > 0


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", MODES)
def test_diagonal_and_duplicate_extreme_coordinates_are_zero(kind, mode):
    maximum = np.finfo(np.float64).max
    instance = problem([(maximum, maximum)] * 2, kind)
    backend = prepare_distances(instance, mode=mode)
    assert backend.edge_cost(0, 0) == backend.edge_cost(0, 1) == 0.
    assert backend.tour_cost(np.array([0, 1], dtype=np.int64)) == 0.


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("points", [
    [(-np.finfo(np.float64).max, 0), (np.finfo(np.float64).max, 0)],
    [(0, 0), (np.finfo(np.float64).max, np.finfo(np.float64).max)],
])
def test_dense_checks_all_edges_and_on_demand_checks_requested_edges(kind, points):
    instance = problem(points, kind)
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
    instance = problem([(0, 0), (np.finfo(np.float64).max, 0)])
    backend = prepare_distances(instance, mode=mode)
    assert backend.edge_cost(0, 1) == np.finfo(np.float64).max
    with pytest.raises(NumericalError, match="tour total"):
        backend.tour_cost(np.array([0, 1], dtype=np.int64))


@pytest.mark.parametrize("distance", [np.nextafter(float(2**53), math.inf), 1e200])
def test_rounded_edge_limit_checked_before_machine_integer_conversion(distance):
    instance = problem([(0, 0), (distance, 0)], "tsplib_euc_2d")
    with pytest.raises(NumericalError, match="rounded edge"):
        prepare_distances(instance, mode="dense")
    with pytest.raises(NumericalError, match="rounded edge"):
        prepare_distances(instance, mode="on_demand").edge_cost(0, 1)


@pytest.mark.parametrize("mode", MODES)
def test_rounded_total_at_exact_limit_and_single_unit_overflow(mode):
    instance = problem([(0, 0), (2**52, 0)], "tsplib_euc_2d")
    backend = prepare_distances(instance, mode=mode)
    assert backend.tour_cost(np.array([0, 1], dtype=np.int64)) == 2**53
    instance = problem([(0, 0), (2**52, 0), (0, 1)], "tsplib_euc_2d")
    with pytest.raises(NumericalError, match="rounded tour total"):
        prepare_distances(instance, mode=mode).tour_cost(np.arange(3, dtype=np.int64))


@njit
def _compiled_client(coordinates, matrix, dense, rounded, tour):
    edge = distance_edge_kernel(coordinates, matrix, dense, rounded, 0, 1)
    return edge, distance_tour_kernel(coordinates, matrix, dense, rounded, tour)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("readonly", [False, True])
def test_compiled_client_and_warmed_signatures(mode, readonly):
    backend = prepare_distances(fixture("triangle").instance, mode=mode)
    tour = np.arange(3, dtype=np.int64)
    tour.flags.writeable = not readonly
    signatures = tuple(distance_tour_kernel.signatures)
    assert backend.tour_cost(tour) == 12.
    assert tuple(distance_tour_kernel.signatures) == signatures
    assert _compiled_client(backend.coordinates, backend.matrix, backend.use_dense, backend.rounded, tour) == (3., 12.)
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
    instance = fixture("square").instance
    def fail():
        raise AssertionError("oversized request must fail before warmup")
    monkeypatch.setattr(module, "warmup_distance_kernels", fail)
    with pytest.raises(MemoryError, match="128 bytes"):
        prepare_distances(instance, mode="dense", max_matrix_bytes=127)
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


@pytest.mark.parametrize("index,error", [(True, TypeError), (1., TypeError), (-1, ValueError), (4, ValueError)])
@pytest.mark.parametrize("mode", MODES)
def test_wrappers_check_both_city_indices(mode, index, error):
    backend = prepare_distances(fixture("square").instance, mode=mode)
    with pytest.raises(error):
        backend.edge_cost(index, 0)
    with pytest.raises(error):
        backend.edge_cost(0, index)
    assert backend.edge_cost(np.int64(0), np.int32(1)) == 1.


@pytest.mark.parametrize("tour,error", [
    (np.array([0, 1, 2, 2], dtype=np.int64), ValueError),
    (np.array([0, 1, 2, 4], dtype=np.int64), ValueError),
    (np.array([0, 1, 2, -1], dtype=np.int64), ValueError),
    (np.array([0, 1, 2, 3, 0], dtype=np.int64), ValueError),
    (np.arange(4, dtype=np.float64), TypeError), ([0, 1, 2, 3], TypeError),
    (np.arange(8, dtype=np.int64)[::2], ValueError),
])
@pytest.mark.parametrize("mode", MODES)
def test_wrappers_reject_invalid_tours(mode, tour, error):
    backend = prepare_distances(fixture("square").instance, mode=mode)
    with pytest.raises(error):
        backend.tour_cost(tour)


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


def test_cpython_notice_is_available_as_package_data():
    text = files("tsp_hpc").joinpath("licenses/CPython.txt").read_text()
    assert "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2" in text
    assert "All Rights Reserved" in text


def test_generic_cpu_cold_compile_and_cache_reload(tmp_path):
    program = (
        "from tsp_hpc.distances import prepare_distances; "
        "from tsp_hpc.data import TSPInstance; import numpy as np; "
        "i=TSPInstance('probe',np.array([[0.,0.],[5865.277089690506,5153.210738088601]]),'tsplib_euc_2d'); "
        "assert all(prepare_distances(i,mode=m).edge_cost(0,1)==7807 for m in ('dense','on_demand')); "
        "print('generic CPU passed')"
    )
    environment = {
        **os.environ, "NUMBA_CPU_NAME": "generic", "NUMBA_CPU_FEATURES": "",
        "NUMBA_CACHE_DIR": str(tmp_path / "cache"), "NUMBA_DEBUG_CACHE": "1",
    }
    for attempt in range(2):
        result = subprocess.run([sys.executable, "-B", "-c", program], cwd=tmp_path,
                                env=environment, capture_output=True, text=True,
                                check=True, timeout=90)
        assert "generic CPU passed" in result.stdout
        expected_cache_event = "data saved" if attempt == 0 else "data loaded"
        assert f"[cache] {expected_cache_event}" in result.stdout
