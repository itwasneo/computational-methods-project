"""Strict inputs for the routed API; normalization is not execution authority."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
from pydantic import ValidationError
from tsp_hpc.interface import contracts, requests as r

FIXTURES = Path(__file__).resolve().parents[2] / "examples/interface-contracts"
CONTEXT = dict(api_version=1, request_id="request", workspace_id="workspace", session_id="session")


@pytest.fixture
def configuration():
    return json.loads((FIXTURES / "backend-v1.json").read_text())["examples"]["preflight_request"]["value"]["configuration"]


def requests():
    for filename in ("backend-v1.json", "analysis-v1.json"):
        for name, entry in json.loads((FIXTURES / filename).read_text())["examples"].items():
            if entry["type"].endswith("Request"):
                yield pytest.param(getattr(r, entry["type"]), entry["value"], id=f"{filename}-{name}")
    for model, fields in (
        (r.InstanceLoadRequest, dict(artifact_handle="input", artifact_revision="catalog")),
        (r.SnapshotRequest, dict(instance_snapshot_id="snapshot")),
        (r.JobSubmitRequest, dict(preflight_id="review", submission_key="opaque key / retained")),
        (r.JobStatusRequest, dict(job_id="job")),
        (r.JobsQueryRequest, dict(limit=100, cursor=None)),
        (r.ExecutionControlRequest, dict(job_id="job", expected_revision="revision", action="stop_now", confirm_stop_now=True)),
        (r.QueueContinueRequest, dict(expected_queue_revision="queue")),
    ):
        yield pytest.param(model, {**CONTEXT, **fields}, id=model.__name__)
    for generator in ("uniform-v1", "clustered-v1"):
        yield pytest.param(r.InstanceGenerateRequest, generation(generator), id=generator)


def generation(generator="clustered-v1"):
    parameters = dict(generator=generator, n_cities=4, seed=str(2**64 - 1), side_length=1.)
    if generator == "clustered-v1":
        parameters.update(n_clusters=2, standard_deviation=0.)
    return {**CONTEXT, "generation": parameters}


@pytest.mark.parametrize("model, value", list(requests()))
def test_requests_preserve_normalized_fields_and_reject_missing_or_extra_fields(model, value):
    original = deepcopy(value)
    parsed = model.model_validate(value)
    assert value == original
    assert model.model_validate_json(parsed.model_dump_json()) == parsed
    assert parsed.model_dump().keys() == value.keys()
    assert set(model.model_fields) == set(getattr(contracts, model.__name__).__annotations__)
    for field in value:
        with pytest.raises(ValidationError):
            model.model_validate({k: v for k, v in value.items() if k != field})
    for field, invalid in (("command", "not-authority"), ("output_path", "/tmp/out"), ("pid", 123),
                           ("api_version", True), ("session_id", "")):
        with pytest.raises(ValidationError):
            model.model_validate({**value, field: invalid})


@pytest.mark.parametrize("seed", [True, 1, "", "00", "-1", "1e3", " 1", "1\n", "1\r", "1\u2028", "18446744073709551616"])
def test_generation_and_solver_seeds_share_exact_uint64_string_rules(seed, configuration):
    value = generation()
    value["generation"]["seed"] = seed
    with pytest.raises(ValidationError):
        r.InstanceGenerateRequest.model_validate(value)
    with pytest.raises(ValidationError):
        r.ScientificConfigurationRequest.model_validate({**configuration, "solver_seed": seed})


@pytest.mark.parametrize("seed", [0, 2**53 + 1, 2**64 - 1])
def test_generation_preserves_large_seeds(seed):
    for generator in ("uniform-v1", "clustered-v1"):
        value = generation(generator)
        value["generation"]["seed"] = str(seed)
        assert r.InstanceGenerateRequest.model_validate(value).generation.seed == str(seed)


@pytest.mark.parametrize("field, invalid", [("n_cities", True), ("side_length", float("inf")), ("side_length", 0),
                                           ("n_clusters", 5), ("standard_deviation", -1), ("generator", "manual")])
def test_generation_bounds(field, invalid):
    value = generation()
    value["generation"][field] = invalid
    with pytest.raises(ValidationError):
        r.InstanceGenerateRequest.model_validate(value)


def test_scientific_request_bounds_and_empty_work(configuration):
    value = configuration
    for field, invalid in (("budget", {"start_cities": list(range(contracts.RUN_LIST_MAX + 1))}),
                           ("budget", {"start_cities": [2**53]}), ("budget", {"start_cities": [0, 0]}),
                           ("repetitions", []), ("repetitions", [0, 0]), ("max_matrix_bytes", -1)):
        with pytest.raises(ValidationError):
            r.ScientificConfigurationRequest.model_validate({**value, field: invalid})
    value["budget"] = {"start_cities": []}
    parsed = r.ScientificConfigurationRequest.model_validate(value)
    assert parsed.budget == {"start_cities": []} and parsed.max_matrix_bytes == 0
