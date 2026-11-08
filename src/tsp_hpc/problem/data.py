"""Canonical symmetric 2D TSP data and strict boundary validation."""

from dataclasses import dataclass, field
from numbers import Integral
from typing import Literal

import numpy as np
from numpy.typing import NDArray

DistanceKind = Literal["euclidean_2d", "tsplib_euc_2d"]
_DISTANCE_KINDS = ("euclidean_2d", "tsplib_euc_2d")


def _require_array(value: object, dtype: np.dtype, name: str) -> None:
    # Masked arrays can hide invalid values; subclasses must be normalized by callers.
    if type(value) is not np.ndarray or value.dtype != dtype:
        raise TypeError(f"{name} must be a plain numpy.ndarray with dtype {dtype}")


def validate_coordinates(coordinates: NDArray[np.float64]) -> None:
    """Require finite, C-contiguous float64 coordinates of shape (n, 2), n >= 2.

    No conversion, copying, or mutation is performed. Duplicate coordinates and
    zero-length edges are allowed. Derived arithmetic is checked when evaluated.
    """
    _require_array(coordinates, np.dtype(np.float64), "coordinates")
    if coordinates.ndim != 2 or coordinates.shape[1] != 2:
        raise ValueError("coordinates must have shape (n, 2)")
    if coordinates.shape[0] < 2:
        raise ValueError("an instance must contain at least two cities")
    if not coordinates.flags.c_contiguous:
        raise ValueError("coordinates must be C-contiguous")
    if not np.isfinite(coordinates).all():
        raise ValueError("coordinates must contain only finite values")


def _city_count(n_cities: int) -> int:
    if isinstance(n_cities, (bool, np.bool_)) or not isinstance(n_cities, Integral):
        raise TypeError("n_cities must be an integer, not a boolean")
    if n_cities < 2:
        raise ValueError("n_cities must be at least two")
    return int(n_cities)


def validate_city_index(index: int, n_cities: int) -> int:
    """Validate a Python/NumPy integer city ID and return a Python int."""
    n_cities = _city_count(n_cities)
    if isinstance(index, (bool, np.bool_)) or not isinstance(index, Integral):
        raise TypeError("city index must be an integer, not a boolean")
    if not 0 <= index < n_cities:
        raise ValueError("city index is out of range")
    return int(index)


def validate_tour(tour: NDArray[np.int64], n_cities: int) -> None:
    """Require a C-contiguous int64 permutation of 0..n-1 with implicit closure.

    Runs in O(n) time with O(n) temporary storage; does not reorder or modify tour.
    """
    n_cities = _city_count(n_cities)
    _require_array(tour, np.dtype(np.int64), "tour")
    if tour.shape != (n_cities,):
        raise ValueError("tour must have shape (n_cities,) without a repeated closing city")
    if not tour.flags.c_contiguous:
        raise ValueError("tour must be C-contiguous")
    # Check bounds before bincount so an invalid large ID cannot cause a huge allocation.
    if np.any(tour < 0) or np.any(tour >= n_cities):
        raise ValueError("tour contains an out-of-range city index")
    if np.any(np.bincount(tour, minlength=n_cities) != 1):
        raise ValueError("tour must contain every city exactly once")


@dataclass(frozen=True, eq=False)
class TSPInstance:
    """Validated instance with an owned, read-only coordinate snapshot.

    The caller supplies the logical ID; tsp_hpc.reproducibility.identity provides content-based IDs.
    Treat coordinates as immutable, including their NumPy flags and shape.
    Instances use identity equality, not elementwise NumPy array equality.
    """

    instance_id: str
    coordinates: NDArray[np.float64] = field(repr=False)
    distance_kind: DistanceKind = "euclidean_2d"

    def __post_init__(self) -> None:
        if not isinstance(self.instance_id, str):
            raise TypeError("instance_id must be a string")
        if not self.instance_id.strip():
            raise ValueError("instance_id must be non-empty and not whitespace-only")
        if not isinstance(self.distance_kind, str):
            raise TypeError("distance_kind must be a string")
        if self.distance_kind not in _DISTANCE_KINDS:
            raise ValueError(f"unsupported distance_kind: {self.distance_kind!r}")
        validate_coordinates(self.coordinates)
        coordinates = self.coordinates.copy(order="C")
        coordinates.flags.writeable = False
        object.__setattr__(self, "coordinates", coordinates)

    @property
    def n_cities(self) -> int:
        """Derive the city count instead of storing a second, conflicting value."""
        return self.coordinates.shape[0]
