"""Control is intent; actual process exit and scientific prefixes are separate."""

from copy import deepcopy
import json
import os
import signal
from time import monotonic, sleep

import pytest

pytest.importorskip("fastapi")
from tsp_hpc.analysis import read_shards
from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.processes import exited, kill_verified, process_identity, recover_pidfd


def hold(queue):
    with queue._lock:
        state = deepcopy(queue._state)
        state["held"] = True
        queue._commit(state)


def active_fd(api, app, identifier):
    api.wait(identifier, lambda job: job["state"] in {"preparing", "running"})
    with app.state.jobs._lock:
        assert app.state.jobs._pidfd is not None
        return os.dup(app.state.jobs._pidfd)


def freeze(api, app, identifier):
    fd = active_fd(api, app, identifier)
    signal.pidfd_send_signal(fd, signal.SIGSTOP)
    assert not exited(fd)
    return fd


def control(api, identifier, action, *, confirm=None, revision=None):
    job = api.status(identifier)
    return api.client.post("/api/jobs/control", json={**api.context, "job_id": identifier,
        "expected_revision": revision or job["revision"], "action": action,
        "confirm_stop_now": (action == "stop_now") if confirm is None else confirm})


def test_queued_cancel_never_starts_worker_and_controls_are_revision_bound(job_client, execution_session):
    _, app, _, _, output = execution_session
    hold(app.state.jobs)
    plan = job_client.review()
    job = job_client.submit(plan)["job"]
    response = control(job_client, job["job_id"], "cancel_queued", revision="stale")
    assert response.status_code == 409 and response.json()["code"] == "job_stale"
    response = control(job_client, job["job_id"], "cancel_queued", revision=job["revision"])
    assert response.status_code == 200 and response.json()["job"]["state"] == "cancelled"
    assert response.json()["job"]["recorded_trials"] == 0
    assert app.state.jobs._process is None
    assert {path.name for path in (output / ".tsp-local/jobs" / job["job_id"]).iterdir()} == {"instance.json", "request.json"}
    repeated = control(job_client, job["job_id"], "cancel_queued", revision=job["revision"])
    assert repeated.status_code == 409 and repeated.json()["code"] == "job_stale"
    assert job_client.submit(plan)["job"]["state"] == "cancelled"  # A retry is never a rerun.


def test_graceful_stop_is_not_terminal_until_worker_exits(job_client, execution_session):
    _, app, _, _, output = execution_session
    identifier = job_client.submit(job_client.review(repetitions=[0, 1]))["job"]["job_id"]
    fd = freeze(job_client, app, identifier)
    try:
        response = control(job_client, identifier, "stop_after_trial")
        assert response.status_code == 200
        assert response.json()["job"]["state"] == "stop_requested"
        sleep(.1)
        assert not exited(fd) and job_client.status(identifier)["state"] == "stop_requested"
        repeated = control(job_client, identifier, "stop_after_trial")
        assert repeated.status_code == 200 and repeated.json()["job"]["revision"] == response.json()["job"]["revision"]
        signal.pidfd_send_signal(fd, signal.SIGCONT)
        result = job_client.wait(identifier)
        assert exited(fd) and result["state"] == "cancelled"
        raw = (output / ".tsp-local/jobs" / identifier / "results.jsonl").read_text()
        assert "campaign_summary" not in raw
        data = read_shards([output / ".tsp-local/jobs" / identifier / "results.jsonl"])
        assert not data.trials["campaign_complete"].any()
    finally:
        kill_verified(fd)
        os.close(fd)


@pytest.fixture(params=["serial", "multicore"])
def resource_options(request):
    if request.param == "serial":
        return {}
    from tsp_hpc.solvers.resources import available_threads
    return {"backend": "multicore", "threads_per_worker": min(2, available_threads()),
            "implementations": ["visited-scan-threaded-v1"], "record_history": False}


def test_graceful_stop_preserves_complete_native_trial_prefix(job_client, execution_session, resource_options):
    _, _, _, _, output = execution_session
    instance = job_client.snapshot(n_cities=1024)
    plan = job_client.review(instance, budget={"start_cities": list(range(128))}, repetitions=list(range(32)),
                             **{**resource_options, "record_history": False})
    identifier = job_client.submit(plan)["job"]["job_id"]
    job_client.wait(identifier, lambda job: job["recorded_trials"] > 0)
    # A progress notification can race the request; refresh optimistic revisions.
    for _ in range(10):
        response = control(job_client, identifier, "stop_after_trial")
        if response.status_code == 200:
            break
        assert response.json()["code"] == "job_stale"
    assert response.status_code == 200
    terminal = job_client.wait(identifier)
    assert terminal["state"] == "cancelled" and 1 <= terminal["recorded_trials"] < 32
    source = output / ".tsp-local/jobs" / identifier / "results.jsonl"
    data = read_shards([source])
    assert len(data.trials) == terminal["recorded_trials"]
    assert data.trials["outcome"].eq("completed").all()
    assert not data.trials["campaign_complete"].any() and not data.trials["usable"].any()
    assert {json.loads(line)["record_type"] for line in source.read_text().splitlines()} == {"trial"}


def test_stop_now_requires_confirmation_and_only_reports_exit_after_pidfd_observation(job_client, execution_session):
    _, app, _, _, _ = execution_session
    identifier = job_client.submit(job_client.review())["job"]["job_id"]
    fd = freeze(job_client, app, identifier)
    try:
        denied = control(job_client, identifier, "stop_now", confirm=False)
        assert denied.status_code == 400 and not exited(fd)
        result = control(job_client, identifier, "stop_now")
        assert result.status_code == 200 and result.json()["job"]["state"] == "stopping"
        terminal = job_client.wait(identifier)
        assert exited(fd) and terminal["state"] == "cancelled"
        assert terminal["stop_mode"] == "now"
        assert terminal["recorded_trials"] == 0
    finally:
        kill_verified(fd)
        os.close(fd)


def test_stop_now_interrupts_native_work_without_inventing_partial_trial(job_client, execution_session, resource_options):
    _, app, _, _, output = execution_session
    instance = job_client.snapshot(n_cities=4096)
    plan = job_client.review(instance, budget={"start_cities": list(range(128))},
                             **{**resource_options, "record_history": False})
    identifier = job_client.submit(plan)["job"]["job_id"]
    fd = active_fd(job_client, app, identifier)
    try:
        deadline = monotonic() + 30
        cache = output / ".tsp-local/cache"
        pattern = "*threaded.chunk_kernel*.nbc" if resource_options else "*visited_scan*.nbc"
        while not list(cache.rglob(pattern)) and monotonic() < deadline:
            assert not exited(fd)
            sleep(.01)
        assert list(cache.rglob(pattern)), "native warm-up did not publish its private cache"
        # The fixed-work multi-start NN call is one native invocation, not a
        # Python polling loop. This is cancellation acceptance, not a benchmark.
        sleep(.05)
        assert not exited(fd)
        result = control(job_client, identifier, "stop_now")
        assert result.status_code == 200
        terminal = job_client.wait(identifier)
        assert exited(fd) and terminal["state"] == "cancelled" and terminal["recorded_trials"] == 0
        path = output / ".tsp-local/jobs" / identifier / "results.jsonl"
        assert path.read_bytes() == b""  # No fictional tour/history/footer for the killed call.
    finally:
        kill_verified(fd)
        os.close(fd)


def test_completed_work_wins_over_late_stop_and_solver_deadline_is_not_job_cancellation(job_client, execution_session):
    _, _, _, _, output = execution_session
    plan = job_client.review(budget={"start_cities": [0, 1], "time_limit_s": 1e-12})
    result = job_client.wait(job_client.submit(plan)["job"]["job_id"])
    assert result["state"] == "completed" and result["stop_mode"] is None
    record = json.loads((output / ".tsp-local/jobs" / result["job_id"] / "results.jsonl").read_text().splitlines()[0])
    assert record["logical_results"][0]["result"]["termination_reason"] == "time_limit"
    response = control(job_client, result["job_id"], "stop_now")
    assert response.status_code == 409 and response.json()["code"] == "job_state_invalid"
    assert job_client.status(result["job_id"]) == result


def test_wall_resource_ceiling_is_external_failure_not_solver_termination(job_client, execution_session, monkeypatch):
    from tsp_hpc.interface import jobs
    _, app, _, _, _ = execution_session
    identifier = job_client.submit(job_client.review())["job"]["job_id"]
    fd = freeze(job_client, app, identifier)
    try:
        future = monotonic() + app.state.execution.limits.max_job_seconds + 1
        monkeypatch.setattr(jobs, "monotonic", lambda: future)
        result = job_client.wait(identifier)
        assert exited(fd) and result["state"] == "failed" and result["failure_code"] == "resource_limit"
        assert result["recorded_trials"] == 0 and result["stop_mode"] is None
    finally:
        kill_verified(fd)
        os.close(fd)


def test_recovery_adopts_surviving_worker_holds_pending_and_requires_fresh_campaign(tmp_path, paired_session, native_client_factory, resource_options):
    workspace, output = tmp_path / "read", tmp_path / "write"
    workspace.mkdir(); output.mkdir()
    process = fd = None
    try:
        with paired_session(workspace, enable_execution=True, execution_root=output) as (client, app, ws, _, _):
            api = native_client_factory(client, app, ws)
            plan = api.review(**resource_options)
            first = api.submit(plan)["job"]
            fd = freeze(api, app, first["job_id"])
            second = api.submit(plan, "pending")["job"]
            queue = app.state.jobs
            # Simulate loss of controller memory/FDs without orderly cleanup.
            # The actual isolated child and its inherited OS lease survive.
            queue._stop.set(); queue._wake.set(); queue._thread.join()
            with queue._lock:
                process, queue._process = queue._process, None
                os.close(queue._pidfd); queue._pidfd = None
                queue._closed = True
        assert process.poll() is None and not exited(fd)
        with paired_session(workspace, enable_execution=True, execution_root=output) as (client, app, ws, _, _):
            api = native_client_factory(client, app, ws)
            api.wait(first["job_id"], lambda job: job["state"] in {"preparing", "running"})
            assert app.state.jobs._process is None  # Recovered, not a replacement child.
            assert api.status(second["job_id"])["state"] == "held"
            state = api.post("/api/queue/status")
            assert state["dispatch"] == "held" and state["active_job_id"] == first["job_id"]
            response = control(api, first["job_id"], "stop_now")
            assert response.status_code == 200
            assert api.wait(first["job_id"])["state"] == "cancelled"
            assert exited(fd)
            process.wait(timeout=5)
            assert api.status(second["job_id"])["state"] == "held"
            stale = api.client.post("/api/queue/continue", json={**api.context, "expected_queue_revision": state["queue_revision"]})
            assert stale.status_code == 409 and stale.json()["code"] == "queue_stale"
            current = api.post("/api/queue/status")
            api.post("/api/queue/continue", expected_queue_revision=current["queue_revision"])
            finished = api.wait(second["job_id"])
            assert finished["state"] == "completed"
            accepted = api.post("/api/jobs/review", job_id=first["job_id"])["plan"]
            renewed = api.review(accepted["instance_snapshot_id"], **accepted["configuration"])
            fresh = api.wait(api.submit(renewed, "deliberate-rerun")["job"]["job_id"])
            assert fresh["state"] == "completed"
            assert len({first["campaign_id"], finished["campaign_id"], fresh["campaign_id"]}) == 3
            assert not set(fresh["attempt_ids"]) & set(finished["attempt_ids"])
            assert renewed["trials"] == plan["trials"]  # Same science/seeds, not continuation.
    finally:
        if fd is not None:
            kill_verified(fd); os.close(fd)
        if process is not None:
            process.wait(timeout=5)


def test_recovery_refuses_stale_pid_boot_and_wrong_job_marker(job_client, execution_session):
    _, app, _, _, _ = execution_session
    identifier = job_client.submit(job_client.review())["job"]["job_id"]
    fd = freeze(job_client, app, identifier)
    try:
        identity = process_identity(app.state.jobs._process.pid, identifier)
        assert identity is not None
        for stale in ({**identity, "start_ticks": identity["start_ticks"] + 1},
                      {**identity, "boot_id": "00000000-0000-0000-0000-000000000000"},
                      {**identity, "start_ticks": True}, {**identity, "pid": True}):
            assert recover_pidfd(stale, identifier) is None
        assert recover_pidfd(identity, "f" * 32) is None
        assert not exited(fd)
        verified = recover_pidfd(identity, identifier)
        assert verified is not None
        os.close(verified)
    finally:
        kill_verified(fd); os.close(fd)


def test_unknown_live_identity_holds_queue_and_never_signals_a_stale_pid(job_client, execution_session):
    _, app, _, _, _ = execution_session
    identifier = job_client.submit(job_client.review())["job"]["job_id"]
    fd = freeze(job_client, app, identifier)
    queue = app.state.jobs
    process = queue._process
    try:
        queue._stop.set(); queue._wake.set(); queue._thread.join()
        identity = process_identity(process.pid, identifier)
        with queue._lock:
            queue._change(identifier, worker={**identity, "start_ticks": identity["start_ticks"] + 1})
            os.close(queue._pidfd); queue._pidfd = None; queue._process = None
        queue._tick()
        assert job_client.status(identifier)["state"] == "recovering" and not exited(fd)
        assert job_client.post("/api/queue/status")["dispatch"] == "held"
        denied = control(job_client, identifier, "stop_now")
        assert denied.status_code == 409 and denied.json()["code"] == "job_state_invalid"
        assert not exited(fd)
        kill_verified(fd)  # Test retains actual ownership; backend did not guess.
        process.wait(timeout=5)
        queue._tick()
        assert job_client.status(identifier)["state"] == "interrupted"
    finally:
        kill_verified(fd); os.close(fd)
        process.wait(timeout=5)


def test_torn_terminal_receipt_does_not_erase_real_scientific_records(job_client, execution_session):
    _, app, _, _, output = execution_session
    identifier = job_client.submit(job_client.review())["job"]["job_id"]
    fd = freeze(job_client, app, identifier)
    queue = app.state.jobs
    try:
        queue._stop.set(); queue._wake.set(); queue._thread.join()
        signal.pidfd_send_signal(fd, signal.SIGCONT)
        queue._process.wait(timeout=30)
        (output / ".tsp-local/jobs" / identifier / "receipt.json").write_bytes(b"{")
        queue._tick()
        job = job_client.status(identifier)
        assert job["state"] == "failed" and job["failure_code"] == "invalid_artifact"
        assert job["recorded_trials"] == 1 and job["result_handle"] is not None
        assert read_shards([output / ".tsp-local/jobs" / identifier / "results.jsonl"]).trials["campaign_complete"].all()
    finally:
        kill_verified(fd); os.close(fd)


def test_owner_repairing_revokes_browser_requests_not_the_owned_worker(job_client, execution_session):
    import secrets
    client, app, _, _, output = execution_session
    plan = job_client.review()
    identifier = job_client.submit(plan)["job"]["job_id"]
    fd = freeze(job_client, app, identifier)
    try:
        pid = app.state.jobs._process.pid
        old_token = client.headers["Authorization"]
        old_session = job_client.context["session_id"]
        fresh = secrets.token_urlsafe(32)
        app.state.pairing.rearm(fresh)
        denied = client.post("/api/jobs/status", json={**job_client.context, "job_id": identifier})
        assert denied.status_code == 403 and not exited(fd)
        session = client.post("/api/session", json={"api_version": 1, "request_id": "pair", "secret": fresh}).json()
        client.headers["Authorization"] = "Bearer " + session["session_token"]
        job_client.context["session_id"] = session["session_id"]
        assert session["session_id"] != old_session
        assert app.state.jobs._process.pid == pid and not exited(fd)
        assert job_client.submit(plan)["deduplicated"]
        request = (output / ".tsp-local/jobs" / identifier / "request.json").read_text()
        assert old_token.removeprefix("Bearer ") not in request and old_session not in request
        signal.pidfd_send_signal(fd, signal.SIGCONT)
        assert job_client.wait(identifier)["state"] == "completed"
    finally:
        kill_verified(fd); os.close(fd)


@pytest.mark.parametrize("damage", ["missing_state", "worker_lease", "controller_lease", "schema", "counts", "old_review_schema"])
def test_ambiguous_or_changed_persistence_never_forgets_admission(tmp_path, paired_session, native_client_factory, damage):
    workspace, output = tmp_path / "read", tmp_path / "write"
    workspace.mkdir(); output.mkdir()
    with paired_session(workspace, enable_execution=True, execution_root=output) as (client, app, ws, _, _):
        api = native_client_factory(client, app, ws)
        hold(app.state.jobs)
        identifier = api.submit(api.review())["job"]["job_id"]
    root = output / ".tsp-local"
    queue = root / "queue.json"
    original = queue.read_bytes()
    if damage == "missing_state":
        queue.unlink()
    elif damage == "worker_lease":
        (root / "worker.lock").rename(root / "old-worker.lock")
        (root / "worker.lock").write_bytes(b"")
    elif damage == "controller_lease":
        # A symlink is never opened as a controller lock, even on restart.
        (root / "controller.lock").unlink()
        (root / "controller.lock").symlink_to(workspace / "elsewhere")
    else:
        state = json.loads(original)
        if damage in ("schema", "old_review_schema"):
            state["schema_version"] = 999 if damage == "schema" else 1
        else:
            state["jobs"][0]["recorded_trials"] = True
        queue.write_text(json.dumps(state))
    with pytest.raises(InterfaceError) as error:
        with paired_session(workspace, enable_execution=True, execution_root=output):
            pytest.fail("corrupt state was accepted")
    assert error.value.code == "execution_unavailable"
    assert (root / "jobs" / identifier / "request.json").exists()
    assert not (root / "jobs" / identifier / "results.jsonl").exists()
