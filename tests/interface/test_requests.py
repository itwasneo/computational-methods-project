"""Input validation for planned local operations, not a job service."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
from pydantic import ValidationError
from tsp_hpc.interface import requests as r
from tsp_hpc.interface.contracts import RUN_LIST_MAX

FIXTURE = Path(__file__).resolve().parents[2] / "examples/interface-contracts/local-runs-v1.json"
WIRE = json.loads(FIXTURE.read_text())


def example(name):
    return deepcopy(WIRE["examples"][name]["value"])


def model(name):
    return getattr(r, WIRE["examples"][name]["type"])


def test_requests_round_trip_without_coercing_seeds_or_dropping_fields():
    for name, entry in WIRE["examples"].items():
        if not entry["type"].endswith("Request"):
            continue
        value = example(name)
        parsed = model(name).model_validate(value)
        assert json.loads(parsed.model_dump_json()) == value, name
        assert model(name).model_validate_json(parsed.model_dump_json()) == parsed
        for field in value:
            missing = {k: v for k, v in value.items() if k != field}
            with pytest.raises(ValidationError):
                model(name).model_validate(missing)
        with pytest.raises(ValidationError):
            model(name).model_validate({**value, "command": "not-an-execution-endpoint"})


@pytest.mark.parametrize("case", [case for case in WIRE["invalid"]
                                 if case["base"] == "preflight" and case["path"][0] == "configuration"],
                         ids=lambda case: ".".join(case["path"]))
def test_invalid_configuration_is_rejected_at_the_request_boundary(case):
    value = example("preflight_request")
    value["configuration"][case["path"][-1]] = case["value"]
    with pytest.raises(ValidationError):
        r.PreflightRequest.model_validate(value)


def test_request_bounds_supported_choices_and_no_path_or_process_authority():
    for base, field, value in [
        ("submit", "configuration", {}), ("submit", "output_path", "/tmp/elsewhere"),
        ("submit", "api_version", True), ("submit", "session_id", ""),
        ("stop", "pid", 123), ("stop", "action", "resume_solver"),
        ("continue_queue", "expected_queue_revision", ""),
        ("load", "path", "../outside.json"), ("load", "artifact_revision", None),
        ("generate", "generation", {"generator": "manual", "coordinates": [[0, 0], [1, 1]]}),
    ]:
        with pytest.raises(ValidationError):
            model(base).model_validate({**example(base), field: value})
    for name in ("generate", "generate_clustered"):
        for seed in (True, 1, "", "00", "-1", "1e3", " 1", "1\n", "1\r", "1\u2028", "18446744073709551616"):
            value = example(name)
            value["generation"]["seed"] = seed
            with pytest.raises(ValidationError):
                r.InstanceGenerateRequest.model_validate(value)
    for field, invalid in (("n_cities", True), ("side_length", float("inf")), ("side_length", 0),
                           ("n_clusters", 5), ("standard_deviation", -1)):
        value = example("generate_clustered")
        value["generation"][field] = invalid
        with pytest.raises(ValidationError):
            r.InstanceGenerateRequest.model_validate(value)
    for field, invalid in (("start_cities", list(range(RUN_LIST_MAX + 1))),
                           ("repetitions", []), ("repetitions", [True]),
                           ("start_cities", [2**53]), ("max_matrix_bytes", -1)):
        value = example("preflight_request")
        value["configuration"][field] = invalid
        with pytest.raises(ValidationError):
            r.PreflightRequest.model_validate(value)


def test_configuration_preserves_shared_manifest_semantics_without_executing():
    from tsp_hpc.experiments.manifests import Manifest

    root = FIXTURE.parents[2]
    manifest = json.loads((root / "examples/manifests/nn-fixed-work.json").read_text())
    config = r.CampaignConfigurationRequest.model_validate(example("preflight_request")["configuration"])
    exp = manifest["experiments"][0]
    exp["budget"]["start_cities"] = config.start_cities
    exp["repetitions"] = config.repetitions
    exp["solver_seed"] = int(config.solver_seed)
    execution = exp["executions"][0]
    exp["executions"] = [{**execution, "implementation": implementation, "distance_mode": mode}
                         for implementation in config.implementations for mode in config.distance_modes]
    plans = Manifest(manifest).expand()
    assert len(plans) == example("preflight")["trial_count"] == 8
    assert all(p.search["solver_seed"] == 2**64 - 1 for p in plans)
    assert all(p.search["budget"]["start_cities"] == [2, 0] for p in plans)
    assert {p.search["repetition"] for p in plans} == {0, 3}
    assert all(p.execution["resources"]["workers"] == p.execution["resources"]["threads_per_worker"] == 1 for p in plans)
    # Empty work/zero matrix ceilings stay representable; concrete executability
    # and advertised limits belong to preflight/runner, not wire validation.
    value = example("preflight_request")
    value["configuration"]["start_cities"] = []
    value["configuration"]["max_matrix_bytes"] = 0
    assert r.PreflightRequest.model_validate(value).configuration.start_cities == []
    submitted = r.JobSubmitRequest.model_validate(example("submit"))
    retried = r.JobSubmitRequest.model_validate({**example("submit"), "request_id": "submit-2"})
    assert submitted.submission_key == retried.submission_key
    assert submitted.preflight_id == retried.preflight_id  # Actual deduplication remains service work.


def test_models_do_not_register_routes_or_change_pairing_or_workspace(session, tmp_path):
    client, _, workspace, secret, _ = session
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    for path in ("/api/jobs/submit", "/api/jobs/stop", "/api/instances/generate", "/api/session/reset"):
        response = client.post(path, json={"api_version": 1, "request_id": "unavailable"})
        assert response.status_code == 404
    assert client.get("/api/workspace").json()["workspace"] == workspace
    assert workspace["access"] == "read_only"
    assert client.post("/api/session", json={"api_version": 1, "request_id": "pair-again", "secret": secret}).status_code == 403
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before
