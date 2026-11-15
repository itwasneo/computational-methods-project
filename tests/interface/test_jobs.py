"""Real installed-module workers, durable admission and exclusive serial outputs."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from time import monotonic, sleep

import pytest

pytest.importorskip("fastapi")
from tsp_hpc.analysis import read_shards
from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.jobs import TERMINAL


def test_real_worker_uses_shared_runner_and_records_native_results(job_client, execution_session):
    _, app, _, _, output = execution_session
    plan = job_client.review()
    submitted = job_client.submit(plan)
    assert submitted["deduplicated"] is False
    identifier = submitted["job"]["job_id"]
    result = job_client.wait(identifier)
    folder = output / ".tsp-local/jobs" / identifier
    receipt = json.loads((folder / "receipt.json").read_text()) if (folder / "receipt.json").exists() else None
    assert result["state"] == "completed", (result, receipt)
    from tsp_hpc.interface.contracts import JobRecord
    assert result.keys() == JobRecord.__required_keys__
    assert result["recorded_trials"] == 1 and result["failed_trials"] == 0
    rows = [json.loads(line) for line in (folder / "results.jsonl").read_text().splitlines()]
    assert rows[0]["native_execution"]["verified"]
    assert rows[0]["runtime"]["solver_threads"] == 1
    assert rows[0]["runtime"]["memory"]["address_space_soft_limit_bytes"] <= app.state.execution.limits.max_worker_memory_bytes
    assert rows[0]["campaign_id"] == result["campaign_id"]
    assert rows[0]["attempt_id"] == result["attempt_ids"][0]
    assert rows[0]["logical_results"][0]["result"]["history"]
    assert rows[0]["input"]["path"] == "instance.json"
    assert read_shards([folder / "results.jsonl"]).trials["usable"].all()
    assert str(output) not in json.dumps(result)
    assert job_client.submit(plan)["job"]["job_id"] == identifier
    assert job_client.submit(plan)["deduplicated"] is True
    assert len(list((output / ".tsp-local/jobs").iterdir())) == 1


def test_concurrent_duplicate_admission_is_durable_and_does_not_duplicate_workers(job_client, execution_session):
    _, app, context, _, output = execution_session
    plan = job_client.review()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: app.state.jobs.submit(plan["preflight_id"], "same-key", context["session_id"]), range(8)))
    assert len({job["job_id"] for job, _ in results}) == 1
    assert sum(not repeated for _, repeated in results) == 1
    identifier = results[0][0]["job_id"]
    assert job_client.wait(identifier)["state"] == "completed"
    state = json.loads((output / ".tsp-local/queue.json").read_text())
    assert len(state["jobs"]) == 1
    another = job_client.review(snapshot=plan["instance_snapshot_id"])
    with pytest.raises(InterfaceError) as error:
        app.state.jobs.submit(another["preflight_id"], "same-key", context["session_id"])
    assert error.value.code == "submission_conflict"


def test_serial_queue_has_one_active_process_and_fresh_ids(job_client, execution_session):
    _, app, _, _, output = execution_session
    plan = job_client.review()
    first = job_client.submit(plan, "first")["job"]
    second = job_client.submit(plan, "second")["job"]
    assert first["job_id"] != second["job_id"] and first["campaign_id"] != second["campaign_id"]
    deadline = monotonic() + 60
    while monotonic() < deadline:
        with app.state.jobs._lock:
            active = [job for job in app.state.jobs._state["jobs"] if job["state"] not in TERMINAL | {"queued"}]
            assert len(active) <= 1
        if job_client.status(second["job_id"])["state"] in TERMINAL:
            break
        sleep(.025)
    a, b = job_client.wait(first["job_id"]), job_client.wait(second["job_id"])
    assert a["state"] == b["state"] == "completed"
    assert not set(a["attempt_ids"]) & set(b["attempt_ids"])
    paths = [output / ".tsp-local/jobs" / job["job_id"] / "results.jsonl" for job in (a, b)]
    merged = read_shards(paths)
    assert merged.trials["duplicate_execution"].all()  # No implicit latest-success-wins.


def test_queue_capacity_stale_plan_and_root_policy_fail_before_acceptance(job_client, execution_session):
    from dataclasses import replace
    _, app, context, _, _ = execution_session
    plan = job_client.review()
    queue = app.state.jobs
    with queue._lock:
        state = deepcopy(queue._state); state["held"] = True; queue._commit(state)
        queue.limits = replace(queue.limits, max_pending=1)
    job = job_client.submit(plan)["job"]
    assert job["state"] == "held"
    response = job_client.client.post("/api/jobs/submit", json={**context, "preflight_id": plan["preflight_id"], "submission_key": "overflow"})
    assert response.status_code == 409 and response.json()["code"] == "queue_full"
    assert len(queue._state["jobs"]) == 1
    # Retry identity does not create work even when the queue is full or review expired.
    app.state.preflights._plans.clear()
    assert job_client.submit(plan)["deduplicated"] is True


def test_worker_launch_failure_preserves_submission_without_retry(job_client, execution_session, monkeypatch):
    from tsp_hpc.interface import jobs
    _, app, _, _, output = execution_session
    plan = job_client.review()
    def fail(*args, **kwargs):
        raise OSError("private failure should not leak")
    monkeypatch.setattr(jobs.subprocess, "Popen", fail)
    result = job_client.submit(plan)["job"]
    terminal = job_client.wait(result["job_id"])
    assert terminal["state"] == "failed"
    assert terminal["recorded_trials"] == 0
    assert (output / ".tsp-local/jobs" / result["job_id"] / "request.json").exists()
    assert "private failure" not in json.dumps(terminal)
    assert app.state.jobs._process is None


def test_restart_holds_queue_and_deduplicates_without_ephemeral_preflight(tmp_path, paired_session, native_client_factory):
    workspace, output = tmp_path / "read", tmp_path / "write"
    workspace.mkdir(); output.mkdir()
    with paired_session(workspace, enable_execution=True, execution_root=output) as (client, app, ws, _, _):
        api = native_client_factory(client, app, ws)
        plan = api.review()
        job = api.submit(plan)["job"]
        finished = api.wait(job["job_id"])
        assert finished["state"] == "completed"
    before = sorted(str(path) for path in (output / ".tsp-local/jobs").rglob("*"))
    with paired_session(workspace, enable_execution=True, execution_root=output) as (client, app, ws, _, _):
        api = native_client_factory(client, app, ws)
        assert not app.state.preflights._plans
        assert api.post("/api/queue/status")["dispatch"] == "held"
        retry = api.submit(plan)
        assert retry["deduplicated"] and retry["job"]["job_id"] == finished["job_id"]
        assert retry["job"]["result_revision"] == finished["result_revision"]
        assert sorted(str(path) for path in (output / ".tsp-local/jobs").rglob("*")) == before
        page = api.post("/api/jobs/query", limit=1, cursor=None)
        assert len(page["items"]) == 1 and page["next_cursor"] is None


def test_readonly_job_routes_never_accept_execution(session):
    client, app, workspace, _, _ = session
    body = {"api_version": 1, "request_id": "no", "workspace_id": workspace["workspace_id"], "session_id": app.state.pairing._session_id}
    response = client.post("/api/jobs/submit", json={**body, "preflight_id": "missing", "submission_key": "key"})
    assert response.status_code == 403 and response.json()["code"] == "execution_disabled"
    assert app.state.jobs is None
