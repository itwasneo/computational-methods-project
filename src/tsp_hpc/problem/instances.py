"""Seeded development instances, known fixtures, and verified JSON artifacts."""

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import platform

import numpy as np

from tsp_hpc import __version__
from tsp_hpc.problem.data import TSPInstance
from tsp_hpc.reproducibility.identity import canonical_json, describe_instance, make_instance_id
from tsp_hpc.reproducibility.random_streams import BIT_GENERATOR, validate_seed

_FIXTURES = {
    "two-city": ((0., 0.), (3., 4.)),
    "triangle": ((0., 0.), (3., 0.), (3., 4.)),
    "square": ((0., 0.), (1., 0.), (1., 1.), (0., 1.)),
    "collinear": ((0., 0.), (1., 0.), (3., 0.), (6., 0.)),
    "duplicates": ((0., 0.), (0., 0.), (3., 4.), (3., 4.)),
    "coincident": ((0., 0.), (0., 0.), (0., 0.), (0., 0.)),
}


def _distance_kind(value: object) -> None:
    if type(value) is not str:
        raise TypeError("distance_kind must be a string")
    if value not in ("euclidean_2d", "tsplib_euc_2d"):
        raise ValueError("unsupported distance_kind")


def _integer(value: object, minimum: int, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be a Python integer, not a boolean")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _real(value: object, *, positive: bool, name: str) -> float:
    if type(value) not in (int, float):
        raise TypeError(f"{name} must be a Python number, not a boolean")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} is outside the float64 range") from error
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'non-negative'}")
    return 0.0 if result == 0 else result


def _object(value: object, keys: set[str], name: str) -> dict:
    if type(value) is not dict:
        raise TypeError(f"{name} must be a JSON object")
    if value.keys() != keys:
        raise ValueError(f"{name} must contain exactly {sorted(keys)}")
    return value


def _parameters(generator: str, value: object) -> dict:
    if generator == "fixture-v1":
        item = _object(value, {"name"}, "fixture parameters")
        if type(item["name"]) is not str or item["name"] not in _FIXTURES:
            raise ValueError("unknown fixture name")
        return {"name": item["name"]}
    keys = {"n_cities", "side_length"}
    if generator == "clustered-v1":
        keys |= {"n_clusters", "standard_deviation"}
    elif generator != "uniform-v1":
        raise ValueError("unsupported generator version")
    item = _object(value, keys, "generator parameters")
    result = {
        "n_cities": _integer(item["n_cities"], 2, "n_cities"),
        "side_length": _real(item["side_length"], positive=True, name="side_length"),
    }
    if generator == "clustered-v1":
        result["n_clusters"] = _integer(item["n_clusters"], 1, "n_clusters")
        if result["n_clusters"] > result["n_cities"]:
            raise ValueError("n_clusters must not exceed n_cities")
        result["standard_deviation"] = _real(
            item["standard_deviation"], positive=False, name="standard_deviation"
        )
    return result


def _provenance(value: object) -> dict:
    item = _object(value, {"generator", "parameters", "seed", "bit_generator", "runtime"}, "provenance")
    generator = item["generator"]
    if type(generator) is not str:
        raise TypeError("generator must be a string")
    parameters = _parameters(generator, item["parameters"])
    if generator == "fixture-v1":
        if item["seed"] is not None or item["bit_generator"] is not None:
            raise ValueError("deterministic fixtures have no RNG seed or bit generator")
    else:
        validate_seed(item["seed"])
        if item["bit_generator"] != BIT_GENERATOR:
            raise ValueError("unsupported bit generator")
    runtime = _object(item["runtime"], {"python", "numpy", "tsp_hpc"}, "runtime")
    if any(type(value) is not str or not value.strip() for value in runtime.values()):
        raise ValueError("runtime versions must be non-blank strings")
    return {**item, "parameters": parameters, "runtime": dict(runtime)}


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _parse(text: str) -> object:
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


@dataclass(frozen=True, eq=False)
class InstanceRecord:
    """Immutable provenance snapshot paired with owned canonical coordinates.

    Use the generators, fixture(), or load_instance() to construct records.
    As with TSPInstance, read-only NumPy flags guard accidental mutations only.
    """

    instance: TSPInstance
    _provenance_json: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.instance, TSPInstance):
            raise TypeError("instance must be a TSPInstance")
        provenance = _provenance(_parse(self._provenance_json))
        parameters = provenance["parameters"]
        actual_id = make_instance_id(self.instance.coordinates, self.instance.distance_kind)
        if self.instance.instance_id != actual_id:
            raise ValueError("instance ID does not match its coordinate content")
        if provenance["generator"] == "fixture-v1":
            expected = np.array(_FIXTURES[parameters["name"]], dtype=np.float64)
            if actual_id != make_instance_id(expected, self.instance.distance_kind):
                raise ValueError("coordinates do not match the named fixture")
        elif parameters["n_cities"] != self.instance.n_cities:
            raise ValueError("generation city count does not match the coordinates")
        object.__setattr__(self, "_provenance_json", canonical_json(provenance))

    @property
    def provenance(self) -> dict:
        return json.loads(self._provenance_json)

    @property
    def descriptor(self) -> dict:
        """Resolved descriptor accepted directly by Manifest."""
        return describe_instance(self.instance, instance_seed=self.provenance["seed"])

    def to_dict(self) -> dict:
        """Return a detached, self-contained v1 artifact document."""
        return {
            "schema_version": 1,
            "instance": self.descriptor,
            "coordinates": self.instance.coordinates.tolist(),
            "provenance": self.provenance,
        }


def _record(coordinates: np.ndarray, distance_kind: str, generator: str,
            parameters: dict, seed: int | None) -> InstanceRecord:
    instance = TSPInstance(make_instance_id(coordinates, distance_kind), coordinates, distance_kind)
    provenance = {
        "generator": generator, "parameters": parameters, "seed": seed,
        "bit_generator": None if seed is None else BIT_GENERATOR,
        "runtime": {"python": platform.python_version(), "numpy": np.__version__, "tsp_hpc": __version__},
    }
    return InstanceRecord(instance, canonical_json(provenance))


def uniform_instance(n_cities: int, *, seed: int, side_length: float = 1.0,
                     distance_kind: str = "euclidean_2d") -> InstanceRecord:
    """Uniform cities in a square anchored at (0, 0), using one private PCG64.

    Draw a C-order (n, 2) array in [0, 1), then multiply by side_length.
    Floating-point scaling can round a coordinate to the upper boundary.
    """
    _distance_kind(distance_kind)
    parameters = _parameters("uniform-v1", dict(n_cities=n_cities, side_length=side_length))
    validate_seed(seed)
    rng = np.random.Generator(np.random.PCG64(seed))
    coordinates = rng.random((n_cities, 2)) * parameters["side_length"]
    return _record(coordinates, distance_kind, "uniform-v1", parameters, seed)


def clustered_instance(n_cities: int, *, seed: int, n_clusters: int = 4,
                       side_length: float = 1.0, standard_deviation: float = 0.05,
                       distance_kind: str = "euclidean_2d") -> InstanceRecord:
    """Balanced isotropic Gaussian clusters around uniformly sampled centres.

    Draw centres first, shuffle cycling labels second, draw (n, 2) standard
    normal offsets third. Cluster sizes differ by at most one; no clipping,
    rejection sampling, deduplication or final city sorting is performed.
    """
    _distance_kind(distance_kind)
    parameters = _parameters("clustered-v1", dict(
        n_cities=n_cities, n_clusters=n_clusters, side_length=side_length,
        standard_deviation=standard_deviation,
    ))
    validate_seed(seed)
    rng = np.random.Generator(np.random.PCG64(seed))
    centres = rng.random((n_clusters, 2)) * parameters["side_length"]
    labels = np.arange(n_cities, dtype=np.int64) % n_clusters
    rng.shuffle(labels)
    try:
        with np.errstate(over="raise", invalid="raise"):
            coordinates = centres[labels] + rng.standard_normal((n_cities, 2)) * parameters["standard_deviation"]
    except FloatingPointError as error:
        raise ValueError("cluster generation overflowed float64 coordinates") from error
    return _record(coordinates, distance_kind, "clustered-v1", parameters, seed)


def fixture_names() -> tuple[str, ...]:
    return tuple(_FIXTURES)


def fixture(name: str, *, distance_kind: str = "euclidean_2d") -> InstanceRecord:
    """Return a fresh deterministic fixture; see docs/usage.md#correctness-fixtures."""
    _distance_kind(distance_kind)
    parameters = _parameters("fixture-v1", {"name": name})
    coordinates = np.array(_FIXTURES[name], dtype=np.float64)
    return _record(coordinates, distance_kind, "fixture-v1", parameters, None)


def save_instance(record: InstanceRecord, path: str | Path) -> None:
    """Save complete JSON coordinates/provenance, refusing to overwrite any file.

    Intended for small development/pilot inputs, not streaming large datasets.
    Parent directories must exist. A failed write can leave a partial file;
    successful loading always validates the complete document and checksum.
    """
    if not isinstance(record, InstanceRecord):
        raise TypeError("record must be an InstanceRecord")
    # Recheck invariants in case a caller deliberately changed NumPy flags/data.
    InstanceRecord(record.instance, record._provenance_json)
    text = canonical_json(record.to_dict()) + "\n"
    with Path(path).open("x", encoding="utf-8") as output:
        output.write(text)


def load_instance(path: str | Path) -> InstanceRecord:
    """Load a v1 artifact, verifying content identity, checksum and metadata.

    This does not regenerate data, trust pickle, or import TSPLIB. Recorded
    generation provenance is not cryptographically authenticated.
    """
    document = _object(_parse(Path(path).read_text(encoding="utf-8")),
                       {"schema_version", "instance", "coordinates", "provenance"}, "artifact")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("unsupported artifact schema_version")
    descriptor = _object(document["instance"],
                         {"instance_id", "coordinate_checksum", "n_cities", "distance_kind", "instance_seed"},
                         "instance descriptor")
    _integer(descriptor["n_cities"], 2, "n_cities")
    if descriptor["instance_seed"] is not None:
        validate_seed(descriptor["instance_seed"])
    rows = document["coordinates"]
    if type(rows) is not list or len(rows) != descriptor["n_cities"]:
        raise ValueError("coordinate row count does not match the descriptor")
    for row in rows:
        if type(row) is not list or len(row) != 2:
            raise ValueError("each coordinate row must contain two numbers")
        if any(type(value) not in (int, float) for value in row):
            raise TypeError("coordinates must be JSON numbers, not booleans or strings")
    try:
        coordinates = np.array(rows, dtype=np.float64, order="C")
    except OverflowError as error:
        raise ValueError("coordinates are outside the float64 range") from error
    instance = TSPInstance(descriptor["instance_id"], coordinates, descriptor["distance_kind"])
    record = InstanceRecord(instance, canonical_json(_provenance(document["provenance"])))
    if record.descriptor != descriptor:
        raise ValueError("instance descriptor/checksum does not match the saved content and seed")
    return record
