"""Schema boundaries, deterministic expansion and fixed-work stream invariants."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import os
import subprocess
import sys

import numpy as np
import pytest

import tsp_hpc.manifests as manifest_module
from tsp_hpc.manifests import Manifest
from tsp_hpc.random_streams import derive_stream


def make_document(algorithm="sa"):
    budget = {"nn": {"start_cities": [0, 2]}, "ga": {"max_generations": 10}, "sa": {"max_proposals": 100}}[algorithm]
    experiment = {
        "instance_id": "square", "algorithm": algorithm, "budget": budget,
        "solver_seed": 42, "repetitions": [0, 1],
        "executions": [
            {"backend": "serial", "resources": {"profile": "test-cpu"}},
            {"backend": "multicore", "resources": {"profile": "test-cpu", "workers": 8}},
        ],
    }
    if algorithm != "nn":
        experiment["logical_search_ids"] = ["a", "b"]
    return {
        "schema_version": 1,
        "instances": [{
            "instance_id": "square", "n_cities": 4, "distance_kind": "euclidean_2d",
            "coordinate_checksum": "5bd594629b558cec3174cb988c57d2c4dc5e8ef38a0d352d32ee9e535ddc5275",
        }],
        "experiments": [experiment],
    }


@pytest.fixture
def document():
    return make_document()


def replace(document, path, value):
    result = deepcopy(document)
    target = result
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return result


@pytest.mark.parametrize("algorithm", ["nn", "ga", "sa"])
def test_resolved_roundtrip_and_declared_expansion_order(algorithm, tmp_path):
    document = make_document(algorithm)
    manifest = Manifest(document)
    normalized = manifest.to_dict()
    assert Manifest(normalized).to_dict() == normalized
    assert Manifest.from_json(json.dumps(normalized)).expand() == manifest.expand()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert Manifest.from_file(path).expand() == manifest.expand()
    plans = manifest.expand()
    assert len(plans) == 4
    assert [(p.search["repetition"], p.execution["backend"]) for p in plans] == [
        (0, "serial"), (0, "multicore"), (1, "serial"), (1, "multicore"),
    ]
    assert len({p.search_id for p in plans}) == 2
    assert len({p.execution_id for p in plans}) == 4
    assert plans[0].search["variant"].endswith("-v1")
    assert plans[0].search["record_history"] is False
    assert plans[0].search["seed_scheme"] == "sha256-uint64-v1"
    assert plans[0].search["bit_generator"] == "numpy-pcg64"
    assert plans[0].trial_seed != plans[2].trial_seed


def test_trial_identity_golden_vectors(document):
    plan = Manifest(document).expand()[0]
    assert plan.search_id == "search-v1-c2bb8c335a781201649de6a2906d129320fce75c1c1e6382eb9e3089dc65912d"
    assert plan.execution_id == "execution-v1-74330bbe5ac25b27bd57baf6cd23d4e3f987ea45b12b143083e230bab49c0aa2"
    assert plan.trial_seed == 16366595147742765693
    assert [item.stream.seed for item in plan.logical_searches] == [18439878323326467108, 2533266083522485445]


def test_stream_collision_fails_instead_of_silently_reseeding(document, monkeypatch):
    def collide(seed, role, *ids):
        if role == "chain":
            return derive_stream(seed, role, "forced-test-collision")
        return derive_stream(seed, role, *ids)
    monkeypatch.setattr(manifest_module, "derive_stream", collide)
    with pytest.raises(ValueError, match="seed collision"):
        Manifest(document).expand()


def test_default_parameters_and_numeric_normalization(document):
    manifest = Manifest(document)
    parameters = manifest.expand()[0].search["parameters"]
    assert parameters == {"initial_temperature": 1.0, "cooling_factor": 0.999,
                          "minimum_temperature": 1e-12, "cost_check_interval": 1024}
    explicit = manifest.to_dict()
    explicit["experiments"][0]["parameters"]["initial_temperature"] = 1
    assert Manifest(explicit).expand() == manifest.expand()
    ga = Manifest(make_document("ga")).expand()[0]
    assert ga.search["parameters"] == {"population_size": 128, "tournament_size": 3,
                                      "elite_count": 1, "crossover_probability": 0.9,
                                      "mutation_probability": 0.2}


@pytest.mark.parametrize("algorithm", ["nn", "ga", "sa"])
def test_zero_work_is_preserved_not_skipped(algorithm):
    document = make_document(algorithm)
    budget = {"nn": {"start_cities": []}, "ga": {"max_generations": 0}, "sa": {"max_proposals": 0}}[algorithm]
    document["experiments"][0]["budget"] = budget
    plans = Manifest(document).expand()
    assert len(plans) == 4
    assert plans[0].search["budget"] == budget
    assert len(plans[0].logical_searches) == (0 if algorithm == "nn" else 2)


def test_single_population_or_chain_is_the_resolved_default():
    for algorithm in ("ga", "sa"):
        document = make_document(algorithm)
        del document["experiments"][0]["logical_search_ids"]
        plan = Manifest(document).expand()[0]
        assert plan.search["logical_search_ids"] == ["0"]
        assert len(plan.logical_searches) == 1


def test_execution_changes_preserve_scientific_work_and_streams(document):
    original = Manifest(document).expand()[0]
    for execution in (
        {"backend": "multicore", "resources": {"profile": "test-cpu", "workers": 8}},
        {"backend": "multicore", "resources": {"profile": "test-cpu", "threads_per_worker": 4}},
        {"backend": "serial", "distance_mode": "dense", "resources": {"profile": "test-cpu"}},
        {"backend": "serial", "resources": {"profile": "other-cpu"}},
    ):
        modified = replace(document, ("experiments", 0, "executions"), [execution])
        plan = Manifest(modified).expand()[0]
        assert plan.execution_id != original.execution_id
        assert plan.search_id == original.search_id
        assert plan.search == original.search
        assert plan.trial_seed == original.trial_seed
        assert plan.logical_searches == original.logical_searches
        assert len(plan.logical_searches) == 2  # Eight workers must not create eight chains.


def test_key_order_and_scheduling_order_do_not_change_identities(document):
    original = Manifest(document).expand()
    reordered = deepcopy(document)
    reordered["experiments"][0]["repetitions"].reverse()
    reordered["experiments"][0]["executions"].reverse()
    reordered = dict(reversed(list(reordered.items())))
    plans = Manifest(reordered).expand()
    assert plans != original
    assert {p.execution_id: p.to_dict() for p in plans} == {p.execution_id: p.to_dict() for p in original}
    # Simulate dispatch in different orders; each logical chain owns its own RNG.
    def draws(order):
        return {s.logical_id: s.stream.generator().integers(0, 1000, 10).tolist() for s in order}
    same_trial = next(plan for plan in plans if plan.execution_id == original[0].execution_id)
    assert draws(original[0].logical_searches) == draws(reversed(same_trial.logical_searches))


def test_unrelated_entries_do_not_renumber_existing_streams(document):
    expected = Manifest(document).expand()
    document["instances"].insert(0, {**document["instances"][0], "instance_id": "unused"})
    document["experiments"].insert(0, make_document("ga")["experiments"][0])
    actual = Manifest(document).expand()
    assert {p.execution_id for p in expected} < {p.execution_id for p in actual}
    assert all(p in actual for p in expected)


def test_logical_search_order_is_scientific_but_streams_follow_ids(document):
    original = Manifest(document).expand()[0]
    reordered = replace(document, ("experiments", 0, "logical_search_ids"), ["b", "a"])
    plan = Manifest(reordered).expand()[0]
    assert plan.search_id != original.search_id
    assert plan.logical_searches == tuple(reversed(original.logical_searches))
    extended = replace(document, ("experiments", 0, "logical_search_ids"), ["a", "b", "c"])
    plan = Manifest(extended).expand()[0]
    assert plan.search_id != original.search_id
    assert plan.logical_searches[:2] == original.logical_searches


def test_nn_order_and_start_seed_assignment():
    document = make_document("nn")
    original = Manifest(document).expand()[0]
    assert [s.kind for s in original.logical_searches] == ["start", "start"]
    assert [s.logical_id for s in original.logical_searches] == [0, 2]
    document["experiments"][0]["budget"]["start_cities"] = [2, 0]
    plan = Manifest(document).expand()[0]
    assert plan.search_id != original.search_id
    assert plan.logical_searches == tuple(reversed(original.logical_searches))


def test_parameter_and_budget_changes_change_identity_but_keep_paired_streams(document):
    original = Manifest(document).expand()[0]
    for path, value in (
        (("experiments", 0, "parameters"), {"initial_temperature": 2.0}),
        (("experiments", 0, "budget"), {"max_proposals": 200}),
    ):
        plan = Manifest(replace(document, path, value)).expand()[0]
        assert plan.search_id != original.search_id
        assert plan.execution_id != original.execution_id
        assert plan.logical_searches == original.logical_searches


def test_instance_seed_is_provenance_not_solver_randomness(document):
    original = Manifest(document).expand()[0]
    document["instances"][0]["instance_seed"] = 999
    plan = Manifest(document).expand()[0]
    assert plan.search_id != original.search_id  # The recorded provenance changed.
    assert plan.logical_searches == original.logical_searches
    document["instances"][0]["coordinate_checksum"] = "0" * 64
    changed_data = Manifest(document).expand()[0]
    assert changed_data.search_id != plan.search_id
    assert changed_data.logical_searches != plan.logical_searches


def test_root_seed_and_repetition_separate_searches(document):
    original = Manifest(document).expand()
    assert original[0].logical_searches != original[2].logical_searches
    document["experiments"][0]["solver_seed"] = 2**64 - 1
    changed = Manifest(document).expand()[0]
    assert changed.search_id != original[0].search_id
    assert changed.logical_searches != original[0].logical_searches


def test_ga_initialization_offspring_and_future_island_namespaces():
    plan = Manifest(make_document("ga")).expand()[0]
    population = plan.logical_searches[0]
    assert population.kind == "population"
    init = [derive_stream(population.stream.seed, "initialization", slot) for slot in range(6)]
    offspring = [derive_stream(population.stream.seed, "offspring", generation, slot)
                 for generation in (1, 2) for slot in range(6)]
    islands = [plan.stream("island", island) for island in ("a", "b")]
    assert len({s.seed for s in init + offspring + islands}) == 20
    assert plan.stream("warmup", "a") != population.stream


def test_manifest_and_plan_snapshots_do_not_alias_mutable_inputs(document):
    manifest = Manifest(document)
    plan = manifest.expand()[0]
    expected = plan.to_dict()
    document["experiments"][0]["budget"]["max_proposals"] = 0
    manifest.to_dict()["experiments"][0]["budget"]["max_proposals"] = 0
    plan.search["budget"]["max_proposals"] = 0
    plan.execution["resources"]["workers"] = 123
    plan.to_dict()["logical_searches"].clear()
    assert manifest.expand()[0].to_dict() == expected
    assert plan.to_dict() == expected
    with pytest.raises(FrozenInstanceError):
        plan.trial_seed = 123


@pytest.mark.parametrize("path,value,error", [
    (("schema_version",), True, TypeError), (("schema_version",), 2, ValueError),
    (("instances",), [], ValueError), (("experiments",), [], ValueError),
    (("instances", 0, "instance_id"), " ", ValueError),
    (("instances", 0, "coordinate_checksum"), "ABC", ValueError),
    (("instances", 0, "coordinate_checksum"), "A" * 64, ValueError),
    (("instances", 0, "n_cities"), True, TypeError),
    (("instances", 0, "n_cities"), 1, ValueError),
    (("instances", 0, "distance_kind"), "GEO", ValueError),
    (("instances", 0, "instance_seed"), False, TypeError),
    (("instances", 0, "instance_seed"), 2**64, ValueError),
    (("experiments", 0, "instance_id"), "missing", ValueError),
    (("experiments", 0, "algorithm"), "unknown", ValueError),
    (("experiments", 0, "algorithm"), [], TypeError),
    (("experiments", 0, "variant"), "sa-other-v1", ValueError),
    (("experiments", 0, "solver_seed"), True, TypeError),
    (("experiments", 0, "solver_seed"), np.int64(42), TypeError),
    (("experiments", 0, "solver_seed"), -1, ValueError),
    (("experiments", 0, "solver_seed"), 2**64, ValueError),
    (("experiments", 0, "repetitions"), 3, TypeError),
    (("experiments", 0, "repetitions"), [], ValueError),
    (("experiments", 0, "repetitions"), [0, 0], ValueError),
    (("experiments", 0, "repetitions"), [-1], ValueError),
    (("experiments", 0, "repetitions"), [False], TypeError),
    (("experiments", 0, "logical_search_ids"), [], ValueError),
    (("experiments", 0, "logical_search_ids"), ["a", "a"], ValueError),
    (("experiments", 0, "logical_search_ids"), [""], ValueError),
    (("experiments", 0, "logical_search_ids"), [0], TypeError),
    (("experiments", 0, "budget"), {}, ValueError),
    (("experiments", 0, "budget"), {"max_proposals": True}, TypeError),
    (("experiments", 0, "budget"), {"max_proposals": -1}, ValueError),
    (("experiments", 0, "budget"), {"time_limit_s": 1}, ValueError),
    (("experiments", 0, "budget"), {"max_proposals": 1, "time_limit_s": 1}, ValueError),
    (("experiments", 0, "parameters"), {"initial_tour": [0, 1, 2, 3]}, ValueError),
    (("experiments", 0, "record_history"), True, ValueError),
    (("experiments", 0, "record_history"), 0, TypeError),
    (("experiments", 0, "executions"), [], ValueError),
    (("experiments", 0, "executions", 0, "backend"), "gpu", ValueError),
    (("experiments", 0, "executions", 0, "backend"), "mpi", ValueError),
    (("experiments", 0, "executions", 0, "distance_mode"), "auto", ValueError),
    (("experiments", 0, "executions", 0, "resources"), {}, ValueError),
    (("experiments", 0, "executions", 0, "resources", "profile"), "", ValueError),
    (("experiments", 0, "executions", 0, "resources", "workers"), 2, ValueError),
    (("experiments", 0, "executions", 0, "resources", "threads_per_worker"), 2, ValueError),
    (("experiments", 0, "executions", 1, "resources", "workers"), True, TypeError),
    (("experiments", 0, "executions", 1, "resources", "workers"), 0, ValueError),
    (("experiments", 0, "executions", 1, "resources", "threads_per_worker"), -1, ValueError),
])
def test_invalid_manifest_fields(document, path, value, error):
    with pytest.raises(error):
        Manifest(replace(document, path, value))


@pytest.mark.parametrize("algorithm,parameters,error", [
    ("nn", {"start_city": 0}, ValueError),
    ("ga", {"population_size": 1}, ValueError),
    ("ga", {"population_size": 2.0}, TypeError),
    ("ga", {"tournament_size": 0}, ValueError),
    ("ga", {"elite_count": -1}, ValueError),
    ("ga", {"elite_count": 128}, ValueError),
    ("ga", {"mutation_probability": True}, TypeError),
    ("ga", {"mutation_probability": -0.1}, ValueError),
    ("ga", {"crossover_probability": 1.1}, ValueError),
    ("ga", {"crossover_probability": float("nan")}, ValueError),
    ("sa", {"initial_temperature": 0}, ValueError),
    ("sa", {"initial_temperature": float("inf")}, ValueError),
    ("sa", {"initial_temperature": 10**1000}, ValueError),
    ("sa", {"minimum_temperature": 2}, ValueError),
    ("sa", {"minimum_temperature": 0}, ValueError),
    ("sa", {"cooling_factor": 0}, ValueError),
    ("sa", {"cooling_factor": 1.1}, ValueError),
    ("sa", {"cost_check_interval": False}, TypeError),
    ("sa", {"cost_check_interval": 0}, ValueError),
])
def test_invalid_algorithm_parameters(algorithm, parameters, error):
    document = make_document(algorithm)
    document["experiments"][0]["parameters"] = parameters
    with pytest.raises(error):
        Manifest(document)


def test_legal_parameter_boundaries_and_degenerate_instance_sizes():
    ga = make_document("ga")
    ga["instances"][0]["n_cities"] = 2
    ga["experiments"][0]["parameters"] = {
        "population_size": 4, "tournament_size": 5, "elite_count": 0,
        "crossover_probability": 0, "mutation_probability": 1,
    }
    Manifest(ga).expand()  # Sampling with replacement permits K > P and P > n!.
    sa = make_document("sa")
    sa["instances"][0]["n_cities"] = 2
    sa["experiments"][0]["parameters"] = {"cooling_factor": 1, "minimum_temperature": 1}
    Manifest(sa).expand()  # Empty-neighborhood stopping belongs to the solver.


@pytest.mark.parametrize("starts,error", [([0, 0], ValueError), ([-1], ValueError), ([4], ValueError),
                                         ([True], TypeError), ([1.0], TypeError), (None, TypeError)])
def test_invalid_nn_starts(starts, error):
    document = make_document("nn")
    document["experiments"][0]["budget"]["start_cities"] = starts
    with pytest.raises(error):
        Manifest(document)


def test_nn_cannot_override_logical_ids():
    document = make_document("nn")
    document["experiments"][0]["logical_search_ids"] = ["extra"]
    with pytest.raises(ValueError, match="start_cities"):
        Manifest(document)


@pytest.mark.parametrize("path", [(), ("instances", 0), ("experiments", 0),
                                  ("experiments", 0, "executions", 0),
                                  ("experiments", 0, "executions", 0, "resources")])
def test_unknown_fields_are_not_silently_ignored(document, path):
    target = document
    for key in path:
        target = target[key]
    target["typo"] = 1
    with pytest.raises(ValueError, match="unknown fields"):
        Manifest(document)


def test_duplicate_instances_and_execution_plans_fail(document):
    repeated = deepcopy(document)
    repeated["instances"].append(deepcopy(repeated["instances"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        Manifest(repeated)
    repeated = deepcopy(document)
    repeated["experiments"].append(deepcopy(repeated["experiments"][0]))
    with pytest.raises(ValueError, match="duplicate expanded execution"):
        Manifest(repeated).expand()
    repeated = deepcopy(document)
    repeated["experiments"][0]["executions"].append(deepcopy(repeated["experiments"][0]["executions"][0]))
    with pytest.raises(ValueError, match="duplicate expanded execution"):
        Manifest(repeated).expand()


@pytest.mark.parametrize("text", ['{"schema_version":1,"schema_version":1}',
                                  '{"x":{"a":1,"a":2}}', '{"x":NaN}', '{"x":Infinity}',
                                  '{"x":-Infinity}', 'not json', '[]'])
def test_strict_json_loading(text):
    with pytest.raises((ValueError, TypeError)):
        Manifest.from_json(text)


def test_identities_are_independent_of_python_hash_seed(document, tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    program = (
        "import json,sys; from tsp_hpc.manifests import Manifest; "
        "print(json.dumps([p.to_dict() for p in Manifest.from_file(sys.argv[1]).expand()],sort_keys=True))"
    )
    outputs = []
    for seed in ("1", "987654"):
        result = subprocess.run([sys.executable, "-B", "-c", program, str(path)], cwd=tmp_path,
                                env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True,
                                text=True, check=True, timeout=15)
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]
    assert json.loads(outputs[0]) == [p.to_dict() for p in Manifest(document).expand()]
