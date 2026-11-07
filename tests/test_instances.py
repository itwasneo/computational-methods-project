"""Generated data reproducibility, known optima, and artifact integrity."""

from dataclasses import FrozenInstanceError
from itertools import permutations
import json
import os
from pathlib import Path
import random
import subprocess
import sys

import numpy as np
import pytest

from tsp_hpc.identity import coordinate_checksum
from tsp_hpc.instances import (
    clustered_instance, fixture, fixture_names, load_instance, save_instance,
    uniform_instance,
)
from tsp_hpc.manifests import Manifest
from tsp_hpc.objective import tour_cost
from tsp_hpc.random_streams import derive_stream


@pytest.mark.parametrize("builder,kwargs", [
    (uniform_instance, {}),
    (clustered_instance, {"n_clusters": 3}),
])
def test_reproducible_owned_data_without_global_rng_changes(builder, kwargs):
    before_numpy = np.random.get_state()
    before_python = random.getstate()
    first = builder(20, seed=2**64 - 1, **kwargs)
    other = builder(20, seed=2**64 - 1, **kwargs)
    assert first.to_dict() == other.to_dict()
    assert first.instance is not other.instance
    assert not np.shares_memory(first.instance.coordinates, other.instance.coordinates)
    assert not first.instance.coordinates.flags.writeable
    assert first.instance.coordinates.flags.c_contiguous
    assert first.instance.coordinates.dtype == np.float64
    assert first.instance.coordinates.shape == (20, 2)
    assert first.descriptor != builder(20, seed=0, **kwargs).descriptor
    after_numpy = np.random.get_state()
    assert before_numpy[0] == after_numpy[0]
    np.testing.assert_array_equal(before_numpy[1], after_numpy[1])
    assert before_numpy[2:] == after_numpy[2:]
    assert before_python == random.getstate()


@pytest.mark.parametrize("builder,identifier,checksum", [
    (uniform_instance,
     "instance-v1-9182dd456c69fe2c1b210d91da9f98798cc715eab9ae9d9ee671c79d62892b4a",
     "1bc7968a78472de99ecdd41b42181daf7bfcff38c8da2e1be60df53889446603"),
    (clustered_instance,
     "instance-v1-0676e1c9c1b008de13f58eca31ceb8836d4f2d666fc6182fab3c1f3502157a4e",
     "0bfaf6615e4531d292e2aa77bb7d5b50f856e97f530aa48b0f849aa193e11096"),
])
def test_generator_golden_content_vectors(builder, identifier, checksum):
    record = builder(8, seed=42)
    assert record.instance.instance_id == identifier
    assert record.descriptor["coordinate_checksum"] == checksum


def test_save_rejects_deliberately_modified_content_before_creating_file(tmp_path):
    record = uniform_instance(8, seed=42)
    record.instance.coordinates.flags.writeable = True
    record.instance.coordinates[0, 0] = 999
    path = tmp_path / "corrupted.json"
    with pytest.raises(ValueError, match="ID"):
        save_instance(record, path)
    assert not path.exists()


def test_committed_shared_collection_matches_generation_protocols():
    root = Path(__file__).resolve().parents[1]
    directory = root / "examples" / "instances"
    if not (root / ".git").exists() and not directory.exists():
        pytest.skip("repository sample assets are not installed with wheels/containers")
    expected = {name: fixture(name) for name in fixture_names()}
    expected.update({"uniform-8": uniform_instance(8, seed=42),
                     "clustered-8": clustered_instance(8, seed=42)})
    assert {path.stem for path in directory.glob("*.json")} == set(expected)
    for name, record in expected.items():
        saved = load_instance(directory / f"{name}.json")
        assert saved.descriptor == record.descriptor
        assert saved.instance.coordinates.tobytes() == record.instance.coordinates.tobytes()
        for key in ("generator", "parameters", "seed", "bit_generator"):
            assert saved.provenance[key] == record.provenance[key]
        # Runtime strings describe the original artifact, not this test process.


def test_uniform_draw_order_and_scale():
    record = uniform_instance(8, seed=42, side_length=10)
    expected = np.random.Generator(np.random.PCG64(42)).random((8, 2)) * 10
    np.testing.assert_array_equal(record.instance.coordinates, expected)
    assert (expected >= 0).all() and (expected <= 10).all()
    assert record.provenance["parameters"] == {"n_cities": 8, "side_length": 10.0}


def test_cluster_draw_order_balanced_labels_and_unclipped_coordinates():
    rng = np.random.Generator(np.random.PCG64(42))
    centres = rng.random((3, 2))
    labels = np.arange(11) % 3
    rng.shuffle(labels)
    expected = centres[labels] + rng.standard_normal((11, 2)) * 10
    record = clustered_instance(11, seed=42, n_clusters=3, standard_deviation=10)
    np.testing.assert_array_equal(record.instance.coordinates, expected)
    assert np.any(expected < 0) and np.any(expected > 1)
    collapsed = clustered_instance(11, seed=42, n_clusters=3, standard_deviation=0)
    points, counts = np.unique(collapsed.instance.coordinates, axis=0, return_counts=True)
    assert points.shape == (3, 2)
    assert sorted(counts.tolist()) == [3, 4, 4]
    same = clustered_instance(4, seed=42, n_clusters=1, standard_deviation=0)
    assert len(np.unique(same.instance.coordinates, axis=0)) == 1


@pytest.mark.parametrize("name,cost", [
    ("two-city", 10.), ("triangle", 12.), ("square", 4.),
    ("collinear", 12.), ("duplicates", 10.), ("coincident", 0.),
])
@pytest.mark.parametrize("kind", ["euclidean_2d", "tsplib_euc_2d"])
def test_fixture_known_optima_under_both_conventions(name, cost, kind):
    problem = fixture(name, distance_kind=kind).instance
    tour = np.arange(problem.n_cities, dtype=np.int64)
    assert tour_cost(problem, tour) == cost
    # Tiny exhaustive test oracle, not an integrated TSP solver or benchmark.
    minimum = min(tour_cost(problem, np.array((0, *tail), dtype=np.int64))
                  for tail in permutations(range(1, problem.n_cities)))
    assert minimum == cost


def test_fixture_collection_and_mutation_isolation():
    assert fixture_names() == ("two-city", "triangle", "square", "collinear", "duplicates", "coincident")
    record = fixture("square")
    expected = record.to_dict()
    record.provenance["parameters"]["name"] = "triangle"
    record.to_dict()["coordinates"][0][0] = 123
    assert record.to_dict() == expected
    assert record.descriptor["instance_seed"] is None
    assert record.provenance["bit_generator"] is None
    with pytest.raises(FrozenInstanceError):
        record.instance = fixture("triangle").instance
    with pytest.raises(ValueError):
        record.instance.coordinates[0, 0] = 123


def test_distance_kind_changes_identity_not_coordinates():
    raw = uniform_instance(5, seed=7)
    rounded = uniform_instance(5, seed=7, distance_kind="tsplib_euc_2d")
    np.testing.assert_array_equal(raw.instance.coordinates, rounded.instance.coordinates)
    assert raw.descriptor["coordinate_checksum"] == rounded.descriptor["coordinate_checksum"]
    assert raw.instance.instance_id != rounded.instance.instance_id


@pytest.mark.parametrize("builder", [uniform_instance, clustered_instance])
@pytest.mark.parametrize("seed,error", [(True, TypeError), (np.int64(1), TypeError),
                                        (None, TypeError), (1.0, TypeError),
                                        (-1, ValueError), (2**64, ValueError)])
def test_invalid_seeds(builder, seed, error):
    with pytest.raises(error):
        builder(8, seed=seed)


@pytest.mark.parametrize("builder", [uniform_instance, clustered_instance])
@pytest.mark.parametrize("n,error", [(True, TypeError), (2.0, TypeError), (np.int64(4), TypeError),
                                     (0, ValueError), (1, ValueError), (-2, ValueError)])
def test_invalid_sizes(builder, n, error):
    with pytest.raises(error):
        builder(n, seed=42)


@pytest.mark.parametrize("builder", [uniform_instance, clustered_instance])
@pytest.mark.parametrize("side,error", [(True, TypeError), ("1", TypeError), (0, ValueError),
                                        (-1, ValueError), (float("nan"), ValueError),
                                        (float("inf"), ValueError), (10**1000, ValueError)])
def test_invalid_square_sizes(builder, side, error):
    with pytest.raises(error):
        builder(8, seed=42, side_length=side)


@pytest.mark.parametrize("kwargs,error", [
    ({"n_clusters": 0}, ValueError), ({"n_clusters": 9}, ValueError),
    ({"n_clusters": True}, TypeError), ({"n_clusters": 2.0}, TypeError),
    ({"standard_deviation": -1}, ValueError), ({"standard_deviation": True}, TypeError),
    ({"standard_deviation": float("nan")}, ValueError),
    ({"standard_deviation": float("inf")}, ValueError),
])
def test_invalid_cluster_parameters(kwargs, error):
    with pytest.raises(error):
        clustered_instance(8, seed=42, **kwargs)


def test_cluster_coordinate_overflow_fails():
    with pytest.raises(ValueError, match="overflowed"):
        clustered_instance(100, seed=42, standard_deviation=1.7e308)


def test_unknown_fixture_and_distance_conventions():
    with pytest.raises(ValueError):
        fixture("unknown")
    for builder in (uniform_instance, clustered_instance):
        with pytest.raises(ValueError):
            builder(8, seed=42, distance_kind="GEO")
        with pytest.raises(TypeError):
            builder(8, seed=42, distance_kind=None)


@pytest.mark.parametrize("record", [
    uniform_instance(8, seed=2**64 - 1),
    clustered_instance(8, seed=0), fixture("square"),
    fixture("duplicates", distance_kind="tsplib_euc_2d"),
])
def test_artifact_roundtrip_preserves_exact_bytes_and_metadata(record, tmp_path):
    path = tmp_path / "input.json"
    save_instance(record, path)
    loaded = load_instance(path)
    assert loaded.to_dict() == record.to_dict()
    assert loaded.instance.coordinates.tobytes() == record.instance.coordinates.tobytes()
    assert not loaded.instance.coordinates.flags.writeable
    assert coordinate_checksum(loaded.instance.coordinates) == record.descriptor["coordinate_checksum"]
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        save_instance(fixture("triangle"), path)
    assert path.read_bytes() == before


def test_zero_standard_deviation_normalizes_signed_zero():
    first = clustered_instance(5, seed=42, n_clusters=1, standard_deviation=0)
    second = clustered_instance(5, seed=42, n_clusters=1, standard_deviation=-0.0)
    assert first.to_dict() == second.to_dict()


def test_manifest_integration_and_generation_stream_separation(tmp_path):
    seed = derive_stream(123, "instance", "uniform-v1", "sample-a").seed
    record = uniform_instance(8, seed=seed)
    path = tmp_path / "input.json"
    save_instance(record, path)
    descriptor = load_instance(path).descriptor
    manifest = Manifest({
        "schema_version": 1, "instances": [descriptor], "experiments": [{
            "instance_id": descriptor["instance_id"], "algorithm": "sa",
            "budget": {"max_proposals": 100}, "solver_seed": 42, "repetitions": [0],
            "executions": [{"backend": "serial", "resources": {"profile": "test-cpu"}}],
        }],
    })
    plan = manifest.expand()[0]
    assert plan.search["instance"] == descriptor
    assert plan.search["instance"]["instance_seed"] == seed
    plan.logical_searches[0].stream.generator().random(100)
    assert uniform_instance(8, seed=seed).to_dict() == record.to_dict()


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("schema_version",), 2),
    (("extra",), "unknown"), (("coordinates",), []),
    (("coordinates", 0), [0]), (("coordinates", 0, 0), True),
    (("coordinates", 0, 0), "0"), (("coordinates", 0, 0), 123),
    (("coordinates", 0, 0), 10**1000), (("coordinates", 0, 0), float("inf")),
    (("instance", "instance_id"), "wrong"), (("instance", "coordinate_checksum"), "0" * 64),
    (("instance", "n_cities"), True), (("instance", "n_cities"), 7),
    (("instance", "instance_seed"), True), (("instance", "instance_seed"), 7),
    (("instance", "distance_kind"), "GEO"),
    (("provenance", "generator"), "uniform-v2"),
    (("provenance", "seed"), None), (("provenance", "seed"), 7),
    (("provenance", "bit_generator"), "unknown"),
    (("provenance", "parameters", "n_cities"), 7),
    (("provenance", "parameters", "side_length"), -1),
    (("provenance", "parameters", "typo"), 1),
    (("provenance", "runtime", "numpy"), ""),
])
def test_corrupt_or_inconsistent_artifacts_fail(path, value, tmp_path):
    document = uniform_instance(8, seed=42).to_dict()
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    file = tmp_path / "bad.json"
    file.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises((ValueError, TypeError)):
        load_instance(file)


def test_fixture_provenance_and_data_must_agree(tmp_path):
    document = fixture("square").to_dict()
    document["provenance"]["parameters"]["name"] = "collinear"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="fixture"):
        load_instance(path)


@pytest.mark.parametrize("text", ['{"schema_version":1,"schema_version":1}',
                                  '{"a":{"x":1,"x":2}}', '{"a":NaN}', '[]', 'invalid'])
def test_strict_json_reader(text, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises((TypeError, ValueError)):
        load_instance(path)


def test_runtime_metadata_is_preserved_not_replaced_when_loading(tmp_path):
    document = uniform_instance(8, seed=42).to_dict()
    document["provenance"]["runtime"]["numpy"] = "historical-runtime"
    path = tmp_path / "input.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    record = load_instance(path)
    assert record.to_dict() == document  # Loading consumes saved bytes, not a new generation.


def test_generation_is_independent_of_python_hash_seed(tmp_path):
    program = (
        "from tsp_hpc.instances import uniform_instance,clustered_instance; "
        "import json; print(json.dumps([uniform_instance(8,seed=42).to_dict(),"
        "clustered_instance(8,seed=42).to_dict()],sort_keys=True))"
    )
    outputs = []
    for seed in ("1", "76543"):
        outputs.append(subprocess.run(
            [sys.executable, "-B", "-c", program], cwd=tmp_path,
            env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True,
            text=True, check=True, timeout=15,
        ).stdout)
    assert outputs[0] == outputs[1]
