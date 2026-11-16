"""Real local worker, immutable resource review and shared publication validation."""
from copy import deepcopy
import json

import pytest

from tsp_hpc.analysis import read_shards
from tsp_hpc.analysis.validation import trial_row
from tsp_hpc.solvers.resources import available_threads, nn_scratch_bytes


def configuration(threads=1):
    return {"backend": "multicore", "threads_per_worker": threads,
            "implementations": ["visited-scan-threaded-v1"], "record_history": False}


def test_native_multicore_review_worker_publication_and_resource_binding(job_client, execution_session):
    _, _, _, _, output = execution_session
    threads = min(2, available_threads())
    plan = job_client.review(**configuration(threads))
    assert plan["estimated_solver_scratch_bytes"] == nn_scratch_bytes(4, 2, threads)
    job = job_client.wait(job_client.submit(plan)["job"]["job_id"])
    assert job["state"] == "completed" and job["failed_trials"] == 0
    source = output / ".tsp-local/jobs" / job["job_id"] / "results.jsonl"
    records = [json.loads(line) for line in source.read_text().splitlines()]
    record = records[0]
    assert record["effective_execution"]["backend"] == "multicore"
    assert record["runtime"]["solver_threads"] == threads
    assert record["runtime"]["runner_workers"] == 1
    assert record["runtime"]["environment"]["NUMBA_NUM_THREADS"] == "1"
    assert record["native_execution"]["verified"]
    assert record["native_execution"]["dispatchers"][0]["options"]["nogil"] is True
    analysis = read_shards([source])
    assert len(analysis.trials) == 1
    from tsp_hpc.analysis import reference_gaps
    from tsp_hpc.problem.exact import exact_reference
    from tsp_hpc.problem.instances import load_instance
    reference = exact_reference(load_instance(source.parent / "instance.json").instance)
    gaps = reference_gaps(analysis, {"schema_version": 1, "references": [reference.reference()]})
    assert len(gaps) == 1 and gaps.iloc[0]["absolute_gap"] >= -1e-10
    query = job_client.post("/api/jobs/analysis", job_id=job["job_id"], expected_revision=job["revision"],
        result_revision=job["result_revision"], query={"table": "trials", "attempt_ids": None, "limit": 100, "cursor": None})
    assert query["total_rows"] == 1
    if threads > 1:
        damaged = deepcopy(record); damaged["runtime"]["solver_threads"] = 1
        with pytest.raises(ValueError, match="thread limit"):
            trial_row(damaged)
    damaged = deepcopy(record)
    damaged["native_execution"]["dispatchers"][0]["options"]["nogil"] = False
    with pytest.raises(ValueError, match="GIL-free"):
        trial_row(damaged)
    assert job_client.submit(plan)["job"]["job_id"] == job["job_id"]  # Durable original-key retry.


@pytest.mark.parametrize("changes", [
    {"record_history": True}, {"budget": {"start_cities": [0], "time_limit_s": .01}},
    {"threads_per_worker": True}, {"threads_per_worker": 9},
    {"backend": "serial", "threads_per_worker": 2},
    {"implementations": ["visited-scan-v1"]},
])
def test_unsupported_resources_or_semantics_fail_before_worker_creation(job_client, execution_session, changes):
    _, app, _, _, output = execution_session
    base = job_client.review(**configuration())["configuration"]
    snapshot = job_client.snapshot()
    caps = job_client.post("/api/runs/capabilities")
    response = job_client.client.post("/api/runs/preflight", json={**job_client.context,
        "instance_snapshot_id": snapshot, "capabilities_revision": caps["capabilities_revision"],
        "configuration": {**base, **changes}})
    assert response.status_code in (400, 413, 422), response.text
    assert app.state.jobs._process is None
    assert not list((output / ".tsp-local/jobs").iterdir())


def test_resource_changes_preserve_scientific_identity(job_client):
    if available_threads() < 2:
        pytest.skip("requires two allowed CPUs")
    snapshot = job_client.snapshot()
    one = job_client.review(snapshot, **configuration(1))
    two = job_client.review(snapshot, **configuration(2))
    assert one["trials"][0]["search_id"] == two["trials"][0]["search_id"]
    assert one["trials"][0]["trial_seed"] == two["trials"][0]["trial_seed"]
    assert one["trials"][0]["invocations"] == two["trials"][0]["invocations"]
    assert one["trials"][0]["requested_execution_id"] != two["trials"][0]["requested_execution_id"]


def test_legacy_serial_review_projection_preserves_private_evidence(job_client, execution_session):
    from tsp_hpc.interface.planning import public_review
    from tsp_hpc.interface.errors import InterfaceError
    _, app, _, _, _ = execution_session
    plan = job_client.review()
    private = app.state.preflights.validate(plan["preflight_id"], job_client.context["session_id"])
    del private["public"]["configuration"]["backend"]
    del private["public"]["configuration"]["threads_per_worker"]
    del private["public"]["estimated_solver_scratch_bytes"]
    original = deepcopy(private)
    projected = public_review(private)
    assert projected["configuration"]["backend"] == "serial"
    assert projected["configuration"]["threads_per_worker"] == 1
    assert private == original
    private["manifest"]["experiments"][0]["executions"][0]["backend"] = "multicore"
    with pytest.raises(InterfaceError, match="inconsistent"):
        public_review(private)


def test_owner_resource_ceiling_overrides_available_host(job_client, execution_session):
    from dataclasses import replace
    _, app, _, _, _ = execution_session
    # Trusted owner policy, not a browser-controlled override.
    app.state.preflights.limits = replace(app.state.preflights.limits, max_solver_threads=1)
    caps = job_client.post("/api/runs/capabilities")
    assert caps["limits"]["max_solver_threads"] == 1
