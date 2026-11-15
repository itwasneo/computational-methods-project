"""Immutable snapshot authority, actual generator/loader data and exclusive files."""

from dataclasses import replace
import hashlib
import json
import os

import numpy as np
import pytest

pytest.importorskip("fastapi")
from tsp_hpc.interface.execution import ExecutionAuthority, ExecutionLimits
from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.storage import LocalStorage, Directory, encode
from tsp_hpc.interface.snapshots import SnapshotStore
from tsp_hpc.problem.instances import fixture, load_instance, save_instance, uniform_instance


def generation(**options):
    return {"generator": "uniform-v1", "n_cities": 4, "seed": str(2**64 - 1), "side_length": 1., **options}


def generated(session):
    client, _, context, _, _ = session
    response = client.post("/api/instances/generate", json={**context, "generation": generation()})
    assert response.status_code == 201, response.text
    return response.json()


def test_generation_export_and_immutable_fresh_snapshots(execution_session, tmp_path):
    client, _, context, _, output = execution_session
    first, second = generated(execution_session), generated(execution_session)
    assert first["instance_snapshot_id"] != second["instance_snapshot_id"]
    assert first["instance"] == second["instance"]
    assert first["instance"]["instance_seed"] == str(2**64 - 1)
    body = {**context, "instance_snapshot_id": first["instance_snapshot_id"]}
    raw = client.post("/api/instances/export", json=body)
    assert raw.status_code == 200
    assert raw.headers["content-type"] == "application/octet-stream"
    assert hashlib.sha256(raw.content).hexdigest() == first["content_sha256"]
    path = tmp_path / "export.json"
    path.write_bytes(raw.content)
    loaded = load_instance(path)
    expected = uniform_instance(4, seed=2**64 - 1)
    assert loaded.instance.coordinates.tobytes() == expected.instance.coordinates.tobytes()
    assert loaded.provenance == expected.provenance
    assert str(output) not in json.dumps(first)
    assert client.post("/api/instances/snapshot", json=body).json() == first


def test_load_captures_actual_content_not_filename_and_ignores_later_source_edits(execution_session):
    client, _, context, workspace, _ = execution_session
    record = fixture("square")
    save_instance(record, workspace / "not-a-square-name.json")
    refreshed = client.post("/api/artifacts/refresh", json={k:v for k,v in context.items() if k != "session_id"})
    assert refreshed.status_code == 200
    page = client.post("/api/artifacts/query", json={k:v for k,v in context.items() if k != "session_id"} | {"limit": 10, "cursor": None}).json()
    item, = page["items"]
    response = client.post("/api/instances/load", json={**context, "artifact_handle": item["artifact_handle"], "artifact_revision": item["revision"]})
    assert response.status_code == 201, response.text
    snapshot = response.json()
    assert snapshot["instance"]["instance_id"] == record.instance.instance_id
    (workspace / "not-a-square-name.json").write_text("changed source, not snapshot")
    export = client.post("/api/instances/export", json={**context, "instance_snapshot_id": snapshot["instance_snapshot_id"]})
    assert export.status_code == 200
    assert json.loads(export.content)["coordinates"] == record.instance.coordinates.tolist()
    assert client.post("/api/instances/load", json={**context, "artifact_handle": item["artifact_handle"], "artifact_revision": item["revision"]}).status_code == 409


@pytest.mark.parametrize("fault", ["content", "metadata", "symlink", "fifo", "directory", "missing"])
def test_corrupted_or_replaced_snapshots_never_publish(execution_session, fault):
    client, _, context, _, output = execution_session
    snapshot = generated(execution_session)
    base = output / ".tsp-local/snapshots" / snapshot["instance_snapshot_id"]
    target = base / ("metadata.json" if fault == "metadata" else "instance.json")
    if fault in ("metadata", "content"):
        target.write_text("{}")
    else:
        target.unlink()
        if fault == "symlink": target.symlink_to(base / "metadata.json")
        elif fault == "fifo": os.mkfifo(target)
        elif fault == "directory": target.mkdir()
    response = client.post("/api/instances/export", json={**context, "instance_snapshot_id": snapshot["instance_snapshot_id"]})
    assert response.status_code == 409
    assert response.json()["code"] == "snapshot_stale"
    assert str(output) not in response.text


def test_readonly_and_foreign_handles_never_authorize_writes(session, execution_session):
    reader, _, workspace, _, _ = session
    current = reader.app.state.pairing._session_id
    body = dict(api_version=1, request_id="denied", workspace_id=workspace["workspace_id"], session_id=current)
    assert reader.post("/api/instances/generate", json={**body, "generation": generation()}).json()["code"] == "execution_disabled"
    client, _, context, _, output = execution_session
    for identifier in ("missing", "../owner.json", str(output)):
        response = client.post("/api/instances/export", json={**context, "instance_snapshot_id": identifier})
        assert response.status_code in (400, 404)


def test_limits_before_generation_and_array_allocation(execution_session, monkeypatch):
    client, app, context, _, _ = execution_session
    from tsp_hpc.problem import instances
    def forbidden(*args, **kwargs):
        raise AssertionError("must not allocate")
    monkeypatch.setattr(instances, "uniform_instance", forbidden)
    response = client.post("/api/instances/generate", json={**context, "generation": generation(n_cities=app.state.execution.limits.max_cities + 1)})
    assert response.status_code == 413
    document = fixture("square").to_dict()
    document["instance"]["n_cities"] = 1_000_000
    monkeypatch.setattr(instances.np, "array", forbidden)
    with pytest.raises(ValueError, match="city limit"):
        instances.instance_from_json(json.dumps(document), max_cities=4)


def test_private_state_is_exclusive_and_survives_reopen(tmp_path):
    workspace, root = tmp_path / "read", tmp_path / "write"
    workspace.mkdir(); root.mkdir()
    authority = ExecutionAuthority(workspace, enabled=True, output_root=root,
                                   limits=replace(ExecutionLimits(), max_snapshots=1))
    storage = LocalStorage(authority, workspace)
    try:
        snapshots = SnapshotStore(storage)
        first = snapshots.generate(generation())
        with pytest.raises(InterfaceError) as caught:
            LocalStorage(authority, workspace)
        assert caught.value.code == "execution_busy"
        with pytest.raises(InterfaceError) as caught:
            snapshots.generate(generation())
        assert caught.value.code == "resource_limit"
    finally:
        storage.close()
    storage = LocalStorage(authority, workspace)
    try:
        assert SnapshotStore(storage).describe(first["instance_snapshot_id"]) == first
    finally:
        storage.close(); authority.close()


def test_private_file_primitives_are_no_follow_and_exclusive(tmp_path):
    directory = Directory(os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY))
    try:
        directory.write_new("one.json", encode({"a": 1}))
        with pytest.raises(FileExistsError):
            directory.write_new("one.json", b"overwrite")
        for name in ("../escape", "/absolute", ".", ".."):
            with pytest.raises(InterfaceError):
                directory.write_new(name, b"bad")
        (tmp_path / "link.json").symlink_to(tmp_path / "one.json")
        with pytest.raises(OSError):
            directory.read("link.json", 1024)
        with pytest.raises(InterfaceError):
            directory.replace("link.json", b"bad")
        directory.replace("one.json", b"updated")
        assert directory.read("one.json", 1024) == b"updated"
    finally:
        directory.close()
