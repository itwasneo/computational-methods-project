"""Versioned content identities, independent of Python hash() and file paths."""

import hashlib
import json

import numpy as np
from numpy.typing import NDArray

from tsp_hpc.problem.data import TSPInstance, validate_coordinates


def canonical_json(value: object) -> str:
    """Serialize normalized JSON data using the project's v1 encoding rules."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def stable_id(namespace: str, payload: object) -> str:
    """Hash a domain-separated canonical payload; this is not Python hash()."""
    if not isinstance(namespace, str) or not namespace.strip():
        raise ValueError("identity namespace must be a non-blank string")
    encoded = canonical_json({"schema": "tsp-identity-v1", "namespace": namespace, "payload": payload})
    digest = hashlib.sha256(encoded.encode("ascii")).hexdigest()
    return f"{namespace}-v1-{digest}"


def coordinate_checksum(coordinates: NDArray[np.float64]) -> str:
    """SHA-256 of shape-framed, little-endian float64 coordinate bytes.

    Row order and signed-zero bits are preserved. No geometry normalization or
    quadratic distance allocation is performed.
    """
    validate_coordinates(coordinates)
    digest = hashlib.sha256(b"tsp-coordinates-f64le-n2-v1\0")
    digest.update(coordinates.shape[0].to_bytes(8, "big"))
    digest.update(coordinates.astype("<f8", copy=False).tobytes(order="C"))
    return digest.hexdigest()


def make_instance_id(coordinates: NDArray[np.float64], distance_kind: str = "euclidean_2d") -> str:
    """Generate a content-based ID before constructing a TSPInstance."""
    if not isinstance(distance_kind, str):
        raise TypeError("distance_kind must be a string")
    if distance_kind not in ("euclidean_2d", "tsplib_euc_2d"):
        raise ValueError("unsupported distance_kind")
    checksum = coordinate_checksum(coordinates)
    return stable_id("instance", {
        "coordinate_checksum": checksum,
        "n_cities": coordinates.shape[0],
        "distance_kind": distance_kind,
    })


def describe_instance(instance: TSPInstance, *, instance_seed: int | None = None) -> dict:
    """Describe resolved data for a manifest; instance_seed is generation provenance."""
    if not isinstance(instance, TSPInstance):
        raise TypeError("instance must be a TSPInstance")
    if instance_seed is not None:
        # Local import avoids an import cycle with stream ID derivation.
        from tsp_hpc.reproducibility.random_streams import validate_seed

        validate_seed(instance_seed)
    # Normalize string subclasses to native JSON strings for the strict manifest API.
    return json.loads(canonical_json({
        "instance_id": instance.instance_id,
        "coordinate_checksum": coordinate_checksum(instance.coordinates),
        "n_cities": instance.n_cities,
        "distance_kind": instance.distance_kind,
        "instance_seed": instance_seed,
    }))
