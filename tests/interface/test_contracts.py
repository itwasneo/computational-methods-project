"""Golden wire compatibility checks, not production request validation."""

import base64
import json
import math
from pathlib import Path
import struct
import types
from typing import Literal, Union, get_args, get_origin, get_type_hints, is_typeddict

import numpy as np
import pytest

from tsp_hpc.interface import contracts as c
from tsp_hpc.problem.data import TSPInstance
from tsp_hpc.problem.objective import tour_cost
from tsp_hpc.reproducibility.identity import coordinate_checksum

FIXTURE = Path(__file__).resolve().parents[2] / "examples/interface-contracts/read-only-v1.json"


def matches(value, annotation):
    """Test-only recursive shape check for the mirrored design definitions."""
    if is_typeddict(annotation):
        fields = get_type_hints(annotation)
        return type(value) is dict and value.keys() == fields.keys() and all(
            matches(value[key], kind) for key, kind in fields.items()
        )
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Literal:
        return any(type(value) is type(item) and value == item for item in args)
    if origin in (types.UnionType, Union):
        return any(matches(value, item) for item in args)
    if origin is list:
        return type(value) is list and all(matches(item, args[0]) for item in value)
    if annotation is float:
        return type(value) in (int, float) and math.isfinite(value)
    return type(value) is annotation


@pytest.fixture
def wire():
    return json.loads(FIXTURE.read_text())


def test_wire_examples_match_python_design_types(wire):
    for name, kind in {
        "workspace": c.WorkspaceResponse, "catalog": c.ArtifactPage, "query": c.TrialQuery, "page": c.TrialPage,
        "details": c.TrialDetails, "missing_details": c.TrialDetails,
        "geometry": c.Geometry, "stale_error": c.ErrorResponse,
    }.items():
        assert matches(wire[name], kind), name
    assert not matches({**wire["query"], "api_version": 2}, c.TrialQuery)
    assert not matches({**wire["details"], "solver_seed": 2**64 - 1}, c.TrialDetails)
    assert not matches({**wire["query"], "path": "/arbitrary/file"}, c.TrialQuery)
    assert c.PAGE_DEFAULT == 100 and c.PAGE_MAX == 500
    assert c.GEOMETRY_BYTES_MAX == 16 * 1024 * 1024


def test_float64_wire_preserves_bytes_identity_duplicates_and_zero_cost(wire):
    geometry = wire["geometry"]
    raw = base64.b64decode(geometry["coordinates"]["data"], validate=True)
    count = geometry["instance"]["n_cities"]
    assert len(raw) == count * 16
    coordinates = np.frombuffer(raw, dtype="<f8").astype(np.float64).reshape(count, 2)
    assert np.signbit(coordinates[0, 1])
    assert not np.signbit(coordinates[0, 0])
    assert np.array_equal(coordinates[0], coordinates[1])
    assert coordinate_checksum(coordinates) == geometry["instance"]["coordinate_checksum"]
    assert coordinates.astype("<f8").tobytes() == raw
    route_raw = base64.b64decode(geometry["tour"]["data"], validate=True)
    assert len(route_raw) == count * 4
    route = np.array(struct.unpack(f"<{count}I", route_raw), dtype=np.int64)
    assert route.tolist() == [2, 0, 3, 1]
    instance = TSPInstance(geometry["instance"]["instance_id"], coordinates)
    assert tour_cost(instance, route) == wire["details"]["row"]["best_cost"] == 0


def test_large_seeds_missing_data_and_unknown_source_remain_explicit(wire):
    assert int(wire["details"]["solver_seed"]) == 2**64 - 1
    assert int(wire["details"]["trial_seed"]) == 2**53 + 1
    assert wire["details"]["source"] == {"revision": None, "dirty": None}
    failed = wire["missing_details"]
    assert failed["artifact_state"] == "missing"
    assert failed["snapshot_id"] is None and failed["artifact_handle"] is None
    assert failed["row"]["best_cost"] is None
    assert failed["row"]["search_time_s"] is None
    assert failed["row"]["usable"] is False
    assert wire["page"]["items"][0]["best_cost"] == 0
