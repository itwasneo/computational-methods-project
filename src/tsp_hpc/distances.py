"""Explicitly prepared dense/on-demand CPU distances with checked Python boundaries.

The *_kernel functions are for compiled callers with already validated primitive
arrays/indices. They do not validate permutations, allocate matrices, or accept
arbitrary external weight matrices. Numerical arithmetic remains checked.
"""

from dataclasses import dataclass, field
import math
from time import perf_counter

from numba import njit
import numpy as np

from tsp_hpc._hypot import hypot_2d
from tsp_hpc.data import TSPInstance, validate_city_index, validate_coordinates, validate_tour
from tsp_hpc.objective import MAX_EXACT_INTEGER, NumericalError

DEFAULT_MAX_MATRIX_BYTES = 256 * 1024 * 1024


@njit(cache=True, fastmath=False)
def coordinate_edge_kernel(coordinates, city_a, city_b, rounded):
    """Finite float64 coordinates, valid city indices, boolean convention flag."""
    dx = coordinates[city_a, 0] - coordinates[city_b, 0]
    dy = coordinates[city_a, 1] - coordinates[city_b, 1]
    if not math.isfinite(dx) or not math.isfinite(dy):
        raise NumericalError("derived edge distance is not finite")
    distance = hypot_2d(dx, dy)
    if not math.isfinite(distance):
        raise NumericalError("derived edge distance is not finite")
    if rounded:
        # Guard BEFORE floor: Numba floor produces a bounded machine integer.
        if distance > MAX_EXACT_INTEGER:
            raise NumericalError("rounded edge exceeds the exact float64 integer range")
        whole = math.floor(distance)
        return float(whole) + (1.0 if distance - whole >= 0.5 else 0.0)
    return distance


@njit(cache=True, fastmath=False)
def distance_edge_kernel(coordinates, matrix, use_dense, rounded, city_a, city_b):
    """Use a prepared matrix or coordinates; all structural inputs are trusted."""
    if use_dense:
        return matrix[city_a, city_b]
    return coordinate_edge_kernel(coordinates, city_a, city_b, rounded)


@njit(cache=True, fastmath=False)
def distance_tour_kernel(coordinates, matrix, use_dense, rounded, tour):
    """Sequential float64 sum for a validated int64 permutation; implicit closure."""
    total = 0.0
    n = len(tour)
    for position in range(n):
        edge = distance_edge_kernel(
            coordinates, matrix, use_dense, rounded,
            tour[position], tour[(position + 1) % n],
        )
        if rounded and edge > MAX_EXACT_INTEGER - total:
            raise NumericalError("rounded tour total exceeds the exact float64 integer range")
        total += edge
        if not math.isfinite(total):
            raise NumericalError("derived tour total is not finite")
    return total


@njit(cache=True, fastmath=False)
def _fill_dense(coordinates, rounded, matrix):
    # A single n*n allocation is owned by the Python preparation boundary.
    # Compute each unordered pair once and mirror, with no n*n temporaries.
    for i in range(len(coordinates)):
        matrix[i, i] = 0.0
        for j in range(i + 1, len(coordinates)):
            value = coordinate_edge_kernel(coordinates, i, j, rounded)
            matrix[i, j] = value
            matrix[j, i] = value


def estimated_matrix_bytes(n_cities: int) -> int:
    """Exact float64 dense storage, 8*n*n, using overflow-free Python integers."""
    if type(n_cities) is not int:
        raise TypeError("n_cities must be a Python integer, not a boolean")
    if n_cities < 2:
        raise ValueError("n_cities must be at least two")
    return 8 * n_cities * n_cities


def warmup_distance_kernels() -> None:
    """Compile/exercise the supported signatures using constant tiny inputs.

    This warms distance helpers only, not future solver kernels or their caches.
    No global RNG state or caller data is consumed. Repeated calls still perform
    tiny dispatches; they need not compile again if signatures are already loaded.
    """
    coordinates = np.array([[0., 0.], [3., 4.]], dtype=np.float64)
    coordinates.flags.writeable = False
    matrix = np.empty((2, 2), dtype=np.float64)
    _fill_dense(coordinates, False, matrix)
    matrix.flags.writeable = False
    candidate = np.array([0, 1], dtype=np.int64)
    for use_dense in (False, True):
        for rounded in (False, True):
            distance_edge_kernel(coordinates, matrix, use_dense, rounded, 0, 1)
            distance_tour_kernel(coordinates, matrix, use_dense, rounded, candidate)
    candidate.flags.writeable = False
    distance_tour_kernel(coordinates, matrix, True, True, candidate)


@dataclass(frozen=True, eq=False, init=False)
class PreparedDistances:
    """Factory-created immutable metadata with shared read-only instance data.

    Construct through prepare_distances(), not this low-level record constructor.
    As with TSPInstance, NumPy read-only flags prevent accidental writes only.
    """

    instance: TSPInstance
    mode: str
    matrix: np.ndarray = field(repr=False)
    preprocessing_time_s: float
    jit_warmup_time_s: float
    max_matrix_bytes: int

    def __init__(self, *args, **kwargs):
        raise TypeError("use prepare_distances() to construct a validated representation")

    @property
    def coordinates(self) -> np.ndarray:
        return self.instance.coordinates

    @property
    def use_dense(self) -> bool:
        return self.mode == "dense"

    @property
    def rounded(self) -> bool:
        return self.instance.distance_kind == "tsplib_euc_2d"

    @property
    def metadata(self) -> dict:
        return {
            "instance_id": self.instance.instance_id,
            "distance_kind": self.instance.distance_kind,
            "distance_mode": self.mode,
            "estimated_matrix_bytes": estimated_matrix_bytes(self.instance.n_cities),
            "matrix_bytes": self.matrix.nbytes,
            "max_matrix_bytes": self.max_matrix_bytes,
            "coordinate_bytes": self.coordinates.nbytes,
            "preprocessing_time_s": self.preprocessing_time_s,
            "jit_warmup_time_s": self.jit_warmup_time_s,
        }

    def edge_cost(self, city_a: int, city_b: int) -> float:
        """Validate both city IDs before entering unchecked-index numerical code."""
        city_a = validate_city_index(city_a, self.instance.n_cities)
        city_b = validate_city_index(city_b, self.instance.n_cities)
        return distance_edge_kernel(
            self.coordinates, self.matrix, self.use_dense, self.rounded, city_a, city_b
        )

    def tour_cost(self, tour: np.ndarray) -> float:
        """Validate a complete permutation once, then evaluate it without allocation."""
        validate_tour(tour, self.instance.n_cities)
        return distance_tour_kernel(
            self.coordinates, self.matrix, self.use_dense, self.rounded, tour
        )


def prepare_distances(instance: TSPInstance, *, mode: str,
                      max_matrix_bytes: int = DEFAULT_MAX_MATRIX_BYTES) -> PreparedDistances:
    """Prepare a representation explicitly; never silently switch representations.

    Dense preparation requires 8*n*n <= max_matrix_bytes (default 256 MiB).
    This ceiling covers the matrix only, not total process/Numba/coordinate RSS.
    Warm-up is measured separately; preprocessing includes coordinate validation,
    matrix allocation/filling and read-only marking. It excludes source loading,
    warm-up, solver execution, and final independent solution validation.
    """
    if not isinstance(instance, TSPInstance):
        raise TypeError("instance must be a TSPInstance")
    if type(mode) is not str:
        raise TypeError("mode must be a string")
    if mode not in ("dense", "on_demand"):
        raise ValueError("mode must be dense or on_demand")
    if type(max_matrix_bytes) is not int:
        raise TypeError("max_matrix_bytes must be a Python integer, not a boolean")
    if max_matrix_bytes < 0:
        raise ValueError("max_matrix_bytes must be non-negative")
    estimate = estimated_matrix_bytes(instance.n_cities)
    if mode == "dense":
        if estimate > max_matrix_bytes:
            raise MemoryError(f"dense matrix requires {estimate} bytes; limit is {max_matrix_bytes}")
        if estimate > np.iinfo(np.intp).max:
            raise OverflowError("dense matrix exceeds the platform array address range")
    start = perf_counter()
    warmup_distance_kernels()
    warmup_time = perf_counter() - start
    start = perf_counter()
    validate_coordinates(instance.coordinates)
    rounded = instance.distance_kind == "tsplib_euc_2d"
    if mode == "dense":
        matrix = np.empty((instance.n_cities, instance.n_cities), dtype=np.float64)
        _fill_dense(instance.coordinates, rounded, matrix)
    else:
        matrix = np.empty((0, 0), dtype=np.float64)
    matrix.flags.writeable = False
    preprocessing_time = perf_counter() - start
    prepared = object.__new__(PreparedDistances)
    for name, value in (
        ("instance", instance), ("mode", mode), ("matrix", matrix),
        ("preprocessing_time_s", preprocessing_time),
        ("jit_warmup_time_s", warmup_time), ("max_matrix_bytes", max_matrix_bytes),
    ):
        object.__setattr__(prepared, name, value)
    return prepared
