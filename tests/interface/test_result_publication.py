"""Real final geometry, not artifact-path authority or raw-record publication."""

import base64
from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import numpy as np
import pytest

pytest.importorskip("fastapi")
from tsp_hpc.experiments.runner import run_manifest
from tsp_hpc.interface.contracts import VerifiedDetails, VerifiedGeometry
from tsp_hpc.interface.storage import encode
from tsp_hpc.problem.instances import fixture, load_instance, save_instance
from tsp_hpc.problem.objective import validate_solution


def publication(api, job, **changes):
    return {**api.context, "job_id": job["job_id"], "expected_revision": job["revision"],
            "result_revision": job["result_revision"], "campaign_id": job["campaign_id"],
            "instance_snapshot_id": job["instance_snapshot_id"], "attempt_id": job["attempt_ids"][0], **changes}


def ingest(client, app, workspace, shard, instance):
    context = {"api_version": 1, "request_id": "inspect", "workspace_id": workspace["workspace_id"]}
    catalog = client.post("/api/artifacts/query", json={**context, "limit": 500, "cursor": None}).json()
    handles = {item["display_name"]: item["artifact_handle"] for item in catalog["items"]}
    accepted = client.post("/api/datasets", json={**context, "artifact_catalog_revision": catalog["revision"],
        "result_handles": [handles[shard.name]], "expected_manifest_handle": None})
    assert accepted.status_code == 202, accepted.text
    app.state.datasets._future.result(timeout=20)
    ready = client.post("/api/datasets/status", json={**context, "dataset_id": accepted.json()["dataset_id"]}).json()
    assert ready["state"] == "ready", ready
    first = json.loads(shard.read_text().splitlines()[0])
    return {**context, "dataset_id": ready["dataset_id"], "revision": ready["revision"],
            "instance_handle": handles[instance.name], "artifact_catalog_revision": catalog["revision"],
            "campaign_id": first["campaign_id"], "attempt_id": first["attempt_id"]}


def decode_geometry(value):
    n = value["instance"]["n_cities"]
    coords = np.frombuffer(base64.b64decode(value["coordinates"]["data"], validate=True), dtype="<f8").reshape(n, 2)
    tour = None if value["tour"] is None else np.frombuffer(base64.b64decode(value["tour"]["data"], validate=True), dtype="<u4")
    return coords, tour


@pytest.fixture
def finished_job(job_client):
    return job_client.wait(job_client.submit(job_client.review())["job"]["job_id"])


def test_owned_result_has_exact_original_coordinates_and_independently_checked_final_tour(job_client, execution_session, finished_job):
    client, app, _, _, output = execution_session
    request = publication(job_client, finished_job)
    response = client.post("/api/jobs/result", json=request)
    assert response.status_code == 200, response.text
    details = response.json()["details"]
    assert details.keys() == VerifiedDetails.__required_keys__
    assert details["native_verified"] is True and details["row"]["usable"]
    assert details["solver_seed"] == str(2**64 - 1) and type(details["logical_results"][0]["seed"]) is str
    result = client.post("/api/jobs/geometry", json={**request, "geometry_revision": details["geometry_revision"]})
    assert result.status_code == 200, result.text
    geometry = result.json()["geometry"]
    assert geometry.keys() == VerifiedGeometry.__required_keys__
    coordinates, tour = decode_geometry(geometry)
    original, _, metadata = app.state.snapshots.read(finished_job["instance_snapshot_id"])
    assert coordinates.tobytes() == original.instance.coordinates.tobytes()
    assert len(tour) == original.instance.n_cities  # Never a closed n+1 route.
    assert sorted(tour.tolist()) == list(range(len(tour)))
    assert validate_solution(original.instance, tour.astype(np.int64), details["result"]["best_cost"]) == details["result"]["verified_cost"]
    assert details["snapshot_sha256"] == geometry["snapshot_sha256"] == metadata["sha256"]
    assert str(output) not in response.text + result.text
    assert '"input"' not in response.text and '"runtime"' not in response.text and '"best_tour"' not in response.text


@pytest.mark.parametrize("field,code", [("expected_revision", "job_stale"), ("result_revision", "stale_revision"),
    ("instance_snapshot_id", "snapshot_mismatch"), ("campaign_id", "snapshot_mismatch"), ("attempt_id", "snapshot_mismatch")])
def test_exact_job_context_rejects_substitution(job_client, finished_job, field, code):
    response = job_client.client.post("/api/jobs/result", json=publication(job_client, finished_job, **{field: "other"}))
    assert response.status_code == 409 and response.json()["code"] == code


def test_changed_results_and_geometry_revisions_are_rejected(job_client, execution_session, finished_job):
    client, _, _, _, output = execution_session
    request = publication(job_client, finished_job)
    details = client.post("/api/jobs/result", json=request).json()["details"]
    wrong = client.post("/api/jobs/geometry", json={**request, "geometry_revision": "old"})
    assert wrong.status_code == 409 and wrong.json()["code"] == "snapshot_mismatch"
    path = output / ".tsp-local/jobs" / finished_job["job_id"] / "results.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    stale = client.post("/api/jobs/geometry", json={**request, "geometry_revision": details["geometry_revision"]})
    assert stale.status_code == 409 and stale.json()["code"] == "stale_revision"


@pytest.mark.parametrize("damage", ["cost", "permutation", "closed_tour", "record_path", "duplicate_attempt"])
def test_known_hash_is_not_an_independent_solution_certificate(job_client, execution_session, finished_job, damage):
    client, app, _, _, output = execution_session
    path = output / ".tsp-local/jobs" / finished_job["job_id"] / "results.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    trial = rows[0]
    if damage == "cost":
        trial["result"]["best_cost"] *= 2
        for entry in trial["logical_results"]:
            entry["result"]["best_cost"] *= 2
            for point in entry["result"]["history"]:
                point["best_cost"] *= 2
    elif damage in ("permutation", "closed_tour"):
        for value in (trial["result"], trial["logical_results"][0]["result"]):
            if damage == "permutation":
                value["best_tour"][1] = value["best_tour"][0]
            else:
                value["best_tour"].append(value["best_tour"][0])
    elif damage == "record_path":
        trial["input"]["path"] = "/outside/never-follow-this.json"
    else:
        rows.insert(1, deepcopy(trial))
    raw = b"".join(encode(row) for row in rows)
    path.write_bytes(raw)
    # Fault injection: even a known/stored digest cannot certify objective or
    # permutation truth. This is NOT a claim of same-user tamper attestation.
    with app.state.jobs._lock:
        app.state.jobs._change(finished_job["job_id"], result_revision=hashlib.sha256(raw).hexdigest())
    job = job_client.status(finished_job["job_id"])
    response = client.post("/api/jobs/result", json=publication(job_client, job))
    assert response.status_code in (409, 413, 422), response.text
    assert response.json()["code"] in {"invalid_artifact", "snapshot_mismatch", "resource_limit"}
    assert "outside" not in response.text


@pytest.mark.parametrize("damage", ["missing_original", "symlink_original", "changed_copy", "missing_copy"])
def test_missing_or_changed_instance_binding_is_not_replaced_by_a_convenient_copy(job_client, execution_session, finished_job, damage):
    client, _, _, _, output = execution_session
    snapshot = output / ".tsp-local/snapshots" / finished_job["instance_snapshot_id"] / "instance.json"
    copied = output / ".tsp-local/jobs" / finished_job["job_id"] / "instance.json"
    if damage == "missing_original":
        snapshot.unlink()
    elif damage == "symlink_original":
        snapshot.unlink(); snapshot.symlink_to(copied)
    elif damage == "changed_copy":
        copied.write_bytes(b"{}")
    else:
        copied.unlink()
    result = client.post("/api/jobs/result", json=publication(job_client, finished_job))
    assert result.status_code == 409 and result.json()["code"] in {"snapshot_stale", "snapshot_mismatch"}


def test_no_solution_is_coordinates_without_a_fabricated_tour(job_client):
    job = job_client.wait(job_client.submit(job_client.review(budget={"start_cities": []}))["job"]["job_id"])
    request = publication(job_client, job)
    details = job_client.client.post("/api/jobs/result", json=request).json()["details"]
    assert details["result"]["status"] == "no_solution" and details["result"]["verified_cost"] is None
    geometry = job_client.client.post("/api/jobs/geometry", json={**request, "geometry_revision": details["geometry_revision"]}).json()["geometry"]
    assert decode_geometry(geometry)[1] is None
    assert details["logical_results"][0]["history_points"] == 0


def test_observed_input_mutation_during_validation_cannot_publish_old_geometry(job_client, execution_session, finished_job, monkeypatch):
    from tsp_hpc.problem import objective
    client, _, _, _, output = execution_session
    original = objective.validate_solution
    copied = output / ".tsp-local/jobs" / finished_job["job_id"] / "instance.json"
    def changed(*args, **kwargs):
        reference = original(*args, **kwargs)
        copied.write_bytes(b"{}")
        return reference
    monkeypatch.setattr(objective, "validate_solution", changed)
    response = client.post("/api/jobs/result", json=publication(job_client, finished_job))
    assert response.status_code == 409 and response.json()["code"] == "snapshot_mismatch"
    assert "details" not in response.json()


def test_publication_bounds_and_rule_changes_are_explicit(job_client, execution_session, finished_job, monkeypatch):
    from tsp_hpc.interface import results
    client, app, _, _, _ = execution_session
    request = publication(job_client, finished_job)
    details = client.post("/api/jobs/result", json=request).json()["details"]
    app.state.results.limits = replace(app.state.results.limits, max_response_bytes=4096)
    limited = client.post("/api/jobs/geometry", json={**request, "geometry_revision": details["geometry_revision"]})
    assert limited.status_code == 413 and limited.json()["code"] == "geometry_too_large"
    monkeypatch.setattr(results, "rule_identity", lambda: "changed")
    stale = client.post("/api/jobs/result", json=request)
    assert stale.status_code == 409 and stale.json()["code"] == "stale_revision"


@pytest.mark.parametrize("algorithm", ["nn", "ga", "sa"])
def test_readonly_cli_and_nonproduction_adapter_shapes_use_same_publication_path(tmp_path, paired_session, serial_campaign, scripted_registry, algorithm):
    record, manifest, bindings, path = serial_campaign(algorithm)
    options = {} if algorithm == "nn" else {"registry": scripted_registry(algorithm)[0]}
    run_manifest(manifest, instance_paths=bindings, output_path=path, **options)
    instance = next(iter(bindings.values()))
    before = set(tmp_path.rglob("*"))
    with paired_session(tmp_path) as (client, app, workspace, _, _):
        request = ingest(client, app, workspace, path, instance)
        details_response = client.post("/api/trials/details", json=request)
        assert details_response.status_code == 200, details_response.text
        details = details_response.json()["details"]
        assert details["row"]["algorithm"] == algorithm and details["result"]["verified_cost"] == 4.
        assert len(details["logical_results"]) == (1 if algorithm == "nn" else 3)
        geometry_response = client.post("/api/trials/geometry", json={**request, "geometry_revision": details["geometry_revision"]})
        assert geometry_response.status_code == 200, geometry_response.text
        coordinates, tour = decode_geometry(geometry_response.json()["geometry"])
        assert coordinates.tobytes() == record.instance.coordinates.tobytes()
        validate_solution(record.instance, tour.astype(np.int64), details["result"]["best_cost"])
        assert app.state.jobs is None and app.state.snapshots is None
        assert str(tmp_path) not in details_response.text + geometry_response.text
        assert set(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("version", [1, 2, 3])
def test_legacy_results_are_verified_without_retroactive_native_certification(tmp_path, paired_session, serial_campaign, version):
    _, manifest, bindings, path = serial_campaign("nn", history=False)
    run_manifest(manifest, instance_paths=bindings, output_path=path)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:
        row["schema_version"] = version
        if row["record_type"] == "trial":
            row.pop("native_execution"); row["runtime"].pop("compiler")
            if version < 3:
                row.pop("budget_scope"); row.pop("logical_results"); row["result"].pop("best_logical_index")
            if version == 1:
                row["runtime"].pop("build")
    path.write_bytes(b"".join(encode(row) for row in rows))
    with paired_session(tmp_path) as (client, app, workspace, _, _):
        request = ingest(client, app, workspace, path, next(iter(bindings.values())))
        response = client.post("/api/trials/details", json=request)
        assert response.status_code == 200, response.text
        details = response.json()["details"]
        assert details["execution_kind"] == "legacy-unobserved" and details["native_verified"] is None
        assert details["result"]["verified_cost"] == 4.
        if version < 3:
            assert details["logical_results"] is None and details["budget_scope"] == "legacy-nn-batch"


def test_binary_projection_preserves_signed_zero_and_city_order(session):
    from tsp_hpc.problem.data import TSPInstance
    from tsp_hpc.reproducibility.identity import describe_instance, make_instance_id
    _, app, _, _, _ = session
    # A transport unit fixture, not a fabricated solver/generator observation.
    points = np.array([[0., -0.], [1., 1.]], dtype=np.float64)
    instance = TSPInstance(make_instance_id(points, "euclidean_2d"), points, "euclidean_2d")
    details = {"campaign_id": "a" * 32, "attempt_id": "b" * 32, "instance": describe_instance(instance),
               "snapshot_sha256": "c" * 64, "geometry_revision": "d" * 64}
    value = app.state.results._response((details, points, np.array([1, 0], dtype=np.int64)), "d" * 64)
    decoded, tour = decode_geometry(value)
    assert decoded.tobytes() == points.tobytes() and np.signbit(decoded[0, 1])
    assert tour.tolist() == [1, 0]


def test_readonly_failed_trial_keeps_real_logical_prefix_but_no_aggregate_tour(tmp_path, paired_session, serial_campaign, scripted_registry):
    _, manifest, bindings, path = serial_campaign("ga")
    registry, _ = scripted_registry("ga", fail_at={1})
    run_manifest(manifest, instance_paths=bindings, output_path=path, registry=registry)
    with paired_session(tmp_path) as (client, app, workspace, _, _):
        request = ingest(client, app, workspace, path, next(iter(bindings.values())))
        response = client.post("/api/trials/details", json=request)
        assert response.status_code == 200, response.text
        details = response.json()["details"]
        assert details["result"]["status"] == "failed" and details["result"]["best_cost"] is None
        assert len(details["logical_results"]) == 1 and details["logical_results"][0]["verified_cost"] == 4.
        geometry = client.post("/api/trials/geometry", json={**request, "geometry_revision": details["geometry_revision"]}).json()["geometry"]
        assert geometry["tour"] is None and details["error_phase"] == "solve"


def test_readonly_binding_never_follows_persisted_paths_or_chooses_another_instance(tmp_path, paired_session, serial_campaign):
    _, manifest, bindings, path = serial_campaign("nn")
    run_manifest(manifest, instance_paths=bindings, output_path=path)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["input"]["path"] = "/outside/not-authority.json"
    path.write_bytes(b"".join(encode(row) for row in rows))
    wrong = tmp_path / "rounded.json"
    save_instance(fixture("square", distance_kind="tsplib_euc_2d"), wrong)
    with paired_session(tmp_path) as (client, app, workspace, _, _):
        request = ingest(client, app, workspace, path, next(iter(bindings.values())))
        assert client.post("/api/trials/details", json=request).status_code == 200
        wrong_request = ingest(client, app, workspace, path, wrong)
        mismatch = client.post("/api/trials/details", json=wrong_request)
        assert mismatch.status_code == 409 and mismatch.json()["code"] == "snapshot_mismatch"
        missing = client.post("/api/trials/details", json={**wrong_request, "instance_handle": "../../outside"})
        assert missing.status_code == 404 and "outside" not in missing.text
        # A source edit invalidates the dataset/binding rather than reusing geometry.
        next(iter(bindings.values())).write_text("{}")
        changed = client.post("/api/trials/details", json=wrong_request)
        assert changed.status_code == 409 and changed.json()["code"] == "stale_revision"
