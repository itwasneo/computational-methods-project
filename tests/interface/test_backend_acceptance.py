"""Resource/authority failure boundaries; functional evidence, not sandbox claims."""

from copy import deepcopy
from dataclasses import replace
import errno
import json
import os
import signal
from time import monotonic, sleep

import pytest

from tsp_hpc.interface.cache import cache_usage
from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.processes import exited, kill_verified, valid_identity
from tsp_hpc.interface.storage import decode


def frozen(api, app, identifier):
    api.wait(identifier, lambda job: job["state"] in {"preparing", "running"})
    with app.state.jobs._lock:
        fd = os.dup(app.state.jobs._pidfd)
        signal.pidfd_send_signal(fd, signal.SIGSTOP)
    assert not exited(fd)
    return fd


@pytest.mark.parametrize("fault", ["duplicate", "nested-duplicate", "nan", "infinity", "overflow", "utf16", "utf8", "surrogate", "depth"])
def test_strict_http_json_rejects_ambiguous_or_unrepresentable_input_before_side_effects(execution_session, fault):
    client, app, context, _, output = execution_session
    body = {**context, "generation": {"generator": "uniform-v1", "n_cities": 4, "seed": "7", "side_length": 1.}}
    text = json.dumps(body)
    if fault == "duplicate":
        text = text.replace('"api_version": 1', '"api_version": 1, "api_version": 1')
    elif fault == "nested-duplicate":
        text = text.replace('"seed": "7"', '"seed": "7", "seed": "8"')
    elif fault in {"nan", "infinity", "overflow"}:
        text = text.replace('"side_length": 1.0', '"side_length": ' + {"nan": "NaN", "infinity": "Infinity", "overflow": "1e999"}[fault])
    elif fault == "surrogate":
        text = text.replace('"request_id": "run"', '"request_id": "\\ud800"')
    elif fault == "depth":
        text = '{"nested":' + '[' * 70 + '0' + ']' * 70 + '}'
    raw = text.encode("utf-16" if fault == "utf16" else "utf-8") if fault != "utf8" else b'{"bad":"\xff"}'
    response = client.post("/api/instances/generate", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 400 and response.json()["code"] == "invalid_request", response.text
    assert not list((output / ".tsp-local/snapshots").iterdir())
    assert app.state.jobs.queue()["active_job_id"] is None


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'{"x":"\\udfff"}', '{}'.encode('utf-16')])
def test_private_state_json_uses_the_same_strict_boundary(raw):
    with pytest.raises(ValueError):
        decode(raw)


def test_pidfd_capability_checks_actual_kernel_permission_and_cleans_probe(execution_session, job_client, monkeypatch):
    from tsp_hpc.interface import runtime
    _, app, _, _, _ = execution_session
    original = os.pidfd_open
    probes = []
    def opened(pid, flags):
        fd = original(pid, flags)
        probes.append(fd)
        return fd
    with monkeypatch.context() as patch:
        patch.setattr(os, "pidfd_open", opened)
        assert runtime.process_handles_available()
    for fd in probes:
        with pytest.raises(OSError):
            os.fstat(fd)
    def denied(*args):
        raise PermissionError(errno.EPERM, "private seccomp policy")
    monkeypatch.setattr(os, "pidfd_open", denied)
    caps = job_client.post("/api/runs/capabilities")
    assert not any(entry["available"] for entry in caps["implementations"])
    assert "private seccomp policy" not in json.dumps(caps)
    assert app.state.jobs._process is None


def test_launch_to_pidfd_failure_kills_and_reaps_only_the_direct_owned_child(execution_session, job_client, monkeypatch):
    from tsp_hpc.interface import jobs
    _, _, _, _, _ = execution_session
    plan = job_client.review()
    original, spawn = os.pidfd_open, jobs.subprocess.Popen
    children = []
    def handles(pid, flags):
        if pid != os.getpid():
            raise OSError(errno.EMFILE, "private descriptor exhaustion")
        return original(pid, flags)
    def launch(*args, **kwargs):
        child = spawn(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(os, "pidfd_open", handles)
    monkeypatch.setattr(jobs.subprocess, "Popen", launch)
    accepted = job_client.submit(plan)
    terminal = job_client.wait(accepted["job"]["job_id"])
    assert terminal["state"] == "failed" and terminal["failure_code"] == "launch_failed"
    assert len(children) == 1 and children[0].wait(timeout=5) is not None
    assert job_client.submit(plan)["deduplicated"] and len(children) == 1


@pytest.mark.parametrize("fault", ["bytes", "entries", "contexts", "depth", "symlink", "fifo", "hardlink"])
def test_cache_scan_is_bounded_and_does_not_follow_or_delete_evidence(execution_session, fault):
    _, app, _, workspace, output = execution_session
    root = output / ".tsp-local/cache"
    limits = app.state.execution.limits
    code = "resource_limit"
    if fault == "bytes":
        (root / "retained").write_bytes(b"x" * 8192)
        limits = replace(limits, max_cache_bytes=4096)
    elif fault == "entries":
        (root / "one").touch(); (root / "two").touch()
        limits = replace(limits, max_cache_entries=1)
    elif fault == "contexts":
        (root / "one").mkdir(); (root / "two").mkdir()
        limits = replace(limits, max_cache_contexts=1)
    elif fault == "depth":
        deep = root
        for _ in range(9):
            deep = deep / "d"; deep.mkdir()
    else:
        code = "invalid_artifact"
        outside = workspace / "untouched"
        outside.write_bytes(b"private original")
        if fault == "symlink":
            (root / "link").symlink_to(outside)
        elif fault == "fifo":
            os.mkfifo(root / "fifo")
        else:
            os.link(outside, root / "link")
    names = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    with app.state.storage.directory("cache") as directory:
        with pytest.raises(InterfaceError) as error:
            cache_usage(directory, limits)
    assert error.value.code == code
    assert sorted(str(path.relative_to(root)) for path in root.rglob("*")) == names


def test_recovery_checks_kernel_executable_not_only_argv_zero(execution_session, job_client, monkeypatch):
    from types import SimpleNamespace
    from tsp_hpc.interface import processes
    _, app, _, _, _ = execution_session
    identifier = job_client.submit(job_client.review())["job"]["job_id"]
    fd = frozen(job_client, app, identifier)
    try:
        identity = deepcopy(app.state.jobs._find(identifier)["worker"])
        assert processes.process_identity(identity["pid"], identifier) == identity
        original = os.stat
        def replaced_executable(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if os.fspath(path) == f"/proc/{identity['pid']}/exe":
                return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino + 1)
            return info
        with monkeypatch.context() as patch:
            patch.setattr(os, "stat", replaced_executable)
            assert processes.process_identity(identity["pid"], identifier) is None
            assert processes.recover_pidfd(identity, identifier) is None
            assert not exited(fd)
    finally:
        kill_verified(fd); os.close(fd)


def test_cache_retention_full_fails_before_launch_and_never_evicts_jobs(execution_session, job_client, monkeypatch):
    from tsp_hpc.interface import jobs
    _, app, _, _, output = execution_session
    root = output / ".tsp-local/cache"
    for index in range(app.state.execution.limits.max_cache_contexts):
        (root / f"old-{index}").mkdir()
    plan = job_client.review()
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("cache-full job must not launch"))
    accepted = job_client.submit(plan)
    failed = job_client.wait(accepted["job"]["job_id"])
    assert failed["state"] == "failed" and failed["failure_code"] == "resource_limit"
    assert len(list(root.iterdir())) == app.state.execution.limits.max_cache_contexts
    assert job_client.submit(plan)["deduplicated"]
    assert (output / ".tsp-local/jobs" / failed["job_id"] / "request.json").exists()


def test_observed_cache_growth_stops_owned_worker_without_fabricating_records(execution_session, job_client):
    _, app, _, _, output = execution_session
    identifier = job_client.submit(job_client.review())["job"]["job_id"]
    fd = frozen(job_client, app, identifier)
    try:
        # Sparse size also counts; this test does not allocate 256 MiB of disk.
        with (output / ".tsp-local/cache/oversized").open("wb") as stream:
            stream.truncate(app.state.execution.limits.max_cache_bytes + 1)
        result = job_client.wait(identifier)
        assert exited(fd) and result["state"] == "failed" and result["failure_code"] == "resource_limit"
        assert result["recorded_trials"] == 0
    finally:
        kill_verified(fd); os.close(fd)


@pytest.mark.parametrize("replace_root", [True, False], ids=["execution-root", "controller-lock"])
def test_observed_authority_replacement_cannot_skip_owned_child_cleanup(execution_session, job_client, replace_root):
    client, app, context, _, output = execution_session
    plan = job_client.review()
    identifier = job_client.submit(plan)["job"]["job_id"]
    fd = frozen(job_client, app, identifier)
    pending = job_client.submit(plan, "pending")["job"]
    original = output if replace_root else output / ".tsp-local/controller.lock"
    preserved = original.with_name(original.name + "-preserved")
    try:
        original.rename(preserved)
        if replace_root:
            original.mkdir()
        else:
            original.touch()
        deadline = monotonic() + 5
        while not exited(fd) and monotonic() < deadline:
            sleep(.01)
        assert exited(fd)
        response = client.post("/api/jobs/status", json={**context, "job_id": identifier})
        assert response.status_code == 503
        evidence = preserved if replace_root else output
        assert not (evidence / ".tsp-local/jobs" / pending["job_id"] / "worker.json").exists()
        if replace_root:
            assert not list(original.iterdir())
    finally:
        if original.exists():
            original.rmdir() if replace_root else original.unlink()
        preserved.rename(original)
        kill_verified(fd); os.close(fd)


@pytest.mark.parametrize("fault", ["owner-boolean-schema", "missing-owner", "state-nonfinite", "completed-without-result", "impossible-pid"])
def test_ambiguous_private_state_fails_without_erasing_admitted_keys(tmp_path, paired_session, native_client_factory, fault):
    workspace, output = tmp_path / "read", tmp_path / "output"
    workspace.mkdir(); output.mkdir()
    with paired_session(workspace, enable_execution=True, execution_root=output) as (client, app, ws, *_):
        api = native_client_factory(client, app, ws)
        with app.state.jobs._lock:
            state = deepcopy(app.state.jobs._state); state["held"] = True; app.state.jobs._commit(state)
        plan = api.review()
        job = api.submit(plan, "opaque key / retained")["job"]
    with paired_session(workspace, enable_execution=True, execution_root=output) as (client, app, ws, *_):
        api = native_client_factory(client, app, ws)
        assert api.submit(plan, "opaque key / retained")["job"]["job_id"] == job["job_id"]
    private = output / ".tsp-local"
    path = private / ("owner.json" if fault in {"owner-boolean-schema", "missing-owner"} else "queue.json")
    value = json.loads(path.read_bytes())
    if fault == "owner-boolean-schema":
        value["schema_version"] = True
    elif fault == "completed-without-result":
        value["jobs"][0]["state"] = "completed"
    elif fault == "impossible-pid":
        value["jobs"][0]["worker"] = {"pid": 2**100, "start_ticks": 0, "boot_id": "00000000-0000-0000-0000-000000000000"}
        assert not valid_identity(value["jobs"][0]["worker"])
    raw = json.dumps(value).replace('"elapsed_time_s": null', '"elapsed_time_s": 1e999') if fault == "state-nonfinite" else json.dumps(value)
    if fault == "missing-owner":
        path.unlink()
    else:
        path.write_text(raw)
    with pytest.raises(InterfaceError) as error:
        with paired_session(workspace, enable_execution=True, execution_root=output):
            pass
    assert error.value.code == "execution_unavailable"
    assert not path.exists() if fault == "missing-owner" else path.read_text() == raw
    assert (private / "jobs" / job["job_id"] / "request.json").exists()


def test_post_rename_durability_failure_never_overwrites_or_duplicates_the_admitted_key(tmp_path, paired_session, native_client_factory, monkeypatch):
    workspace, output = tmp_path / "read", tmp_path / "output"
    workspace.mkdir(); output.mkdir()
    with paired_session(workspace, enable_execution=True, execution_root=output) as (client, app, ws, *_):
        api = native_client_factory(client, app, ws)
        with app.state.jobs._lock:
            state = deepcopy(app.state.jobs._state); state["held"] = True; app.state.jobs._commit(state)
        plan = api.review()
        original = app.state.storage.replace
        def ambiguous(name, data):
            original(name, data)  # Admission has reached disk, acknowledgement has not.
            raise OSError(errno.EIO, "private post-rename fsync failure")
        monkeypatch.setattr(app.state.storage, "replace", ambiguous)
        response = client.post("/api/jobs/submit", json={**api.context, "preflight_id": plan["preflight_id"], "submission_key": "ambiguous"})
        assert response.status_code == 503 and response.json()["code"] == "execution_unavailable"
        persisted = (output / ".tsp-local/queue.json").read_bytes()
        job, = json.loads(persisted)["jobs"]
        again = client.post("/api/jobs/submit", json={**api.context, "preflight_id": plan["preflight_id"], "submission_key": "ambiguous"})
        assert again.status_code == 503 and app.state.jobs._process is None
        assert (output / ".tsp-local/queue.json").read_bytes() == persisted
    assert (output / ".tsp-local/queue.json").read_bytes() == persisted  # Shutdown must not write stale memory.
    with paired_session(workspace, enable_execution=True, execution_root=output) as (client, app, ws, *_):
        api = native_client_factory(client, app, ws)
        retry = api.submit(plan, "ambiguous")
        assert retry["deduplicated"] and retry["job"]["job_id"] == job["job_id"] and retry["job"]["state"] == "held"
        assert len(list((output / ".tsp-local/jobs").iterdir())) == 1 and app.state.jobs._process is None


def test_retained_torn_private_state_has_a_directory_entry_bound(tmp_path, paired_session):
    from tsp_hpc.interface.storage import MAX_PRIVATE_ENTRIES
    workspace, output = tmp_path / "read", tmp_path / "output"
    workspace.mkdir(); output.mkdir()
    with paired_session(workspace, enable_execution=True, execution_root=output):
        pass
    private = output / ".tsp-local"
    for index in range(MAX_PRIVATE_ENTRIES):
        (private / f".tmp-retained-{index}").write_bytes(b"unpublished operational evidence")
    before = sorted(path.name for path in private.iterdir())
    with pytest.raises(InterfaceError) as error:
        with paired_session(workspace, enable_execution=True, execution_root=output):
            pass
    assert error.value.code == "resource_limit"
    assert sorted(path.name for path in private.iterdir()) == before
