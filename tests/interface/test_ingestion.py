"""Background lifecycle/authority evidence using one small, real runner campaign."""

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
import threading
from uuid import uuid4

import pandas as pd
import pytest

from tsp_hpc.analysis import read_shards
from tsp_hpc.experiments.manifests import Manifest
from tsp_hpc.experiments.runner import run_manifest
from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.ingestion import DatasetStore, IngestionLimits
from tsp_hpc.interface.workspace import MAX_DATASET_BYTES, WorkspaceCatalog
from tsp_hpc.problem.instances import fixture, save_instance


@pytest.fixture(scope="module")
def campaign(tmp_path_factory):
    root = tmp_path_factory.mktemp("ingestion-runner")
    record = fixture("square")
    artifact = root / "square.json"
    save_instance(record, artifact)
    manifest = Manifest({"schema_version": 1, "instances": [record.descriptor], "experiments": [{
        "instance_id": record.instance.instance_id, "algorithm": "nn", "solver_seed": 42,
        "repetitions": [0, 1], "budget": {"start_cities": [0]},
        "executions": [
            {"backend": "serial", "distance_mode": "on_demand", "resources": {"profile": "test"}},
            {"backend": "multicore", "resources": {"profile": "test"}},
        ],
    }]})
    shard = root / "runs.jsonl"
    run_manifest(manifest, instance_paths={record.instance.instance_id: artifact}, output_path=shard)
    return shard.read_bytes(), manifest


@pytest.fixture
def workspace(tmp_path, campaign):
    raw, manifest = campaign
    (tmp_path / "runs.jsonl").write_bytes(raw)
    (tmp_path / "expected.json").write_text(json.dumps(manifest.to_dict()))
    return tmp_path


@contextmanager
def store_for(root, **options):
    catalog = WorkspaceCatalog(root)
    catalog.refresh()
    page = catalog.page()
    handles = {item["display_name"]: item["artifact_handle"] for item in page["items"]}
    store = DatasetStore(catalog, **options)
    try:
        yield store, handles, page["revision"]
    finally:
        store.close()
        catalog.close()


def start(store, handles, revision, *, shards=("runs.jsonl",), manifest="expected.json"):
    return store.submit([handles[name] for name in shards], handles[manifest] if manifest else None, revision, "start")


def finish(store, accepted):
    store._future.result(timeout=10)
    return store.status(accepted["dataset_id"], "poll")


def gated_reader(monkeypatch):
    from tsp_hpc.analysis import read_sources
    entered, release = threading.Event(), threading.Event()

    def gated(*args, **kwargs):
        entered.set()
        assert release.wait(10), "test did not release ingestion"
        return read_sources(*args, **kwargs)

    monkeypatch.setattr("tsp_hpc.analysis.read_sources", gated)
    return entered, release


def test_real_output_compact_metadata_lazy_authorized_details_and_unchanged_evidence(workspace, campaign):
    expected = read_shards([workspace / "runs.jsonl"], expected_manifest=campaign[1])
    before = {path.name: path.read_bytes() for path in workspace.iterdir()}
    with store_for(workspace) as (store, handles, revision):
        accepted = start(store, handles, revision)
        assert accepted["state"] == "loading" and accepted["revision"] is None
        ready = finish(store, accepted)
        assert ready["state"] == "ready" and ready["revision"].startswith("dataset-v1-")
        assert ready["diagnostics"][0]["code"] == "failed_trials"
        with store.analysis_snapshot(ready["dataset_id"], ready["revision"]) as data:
            pd.testing.assert_frame_equal(data.trials.drop(columns="shard"), expected.trials.drop(columns="shard"))
            assert data.diagnostics == expected.diagnostics
            assert set(data.trials["shard"]) == {handles["runs.jsonl"]}
            assert "best_tour" not in data.trials and "requested_plan" not in data.trials
            attempt = data.trials.iloc[0]["attempt_id"]
            assert data.trial(attempt) == expected.trial(attempt)
            assert data.sources[0]["sha256"] == hashlib.sha256(campaign[0]).hexdigest()
            assert data.sources[0]["path"] == handles["runs.jsonl"]
        assert str(workspace) not in json.dumps(ready)
    assert {path.name: path.read_bytes() for path in workspace.iterdir()} == before


def test_loading_is_observable_off_request_path_without_queue_or_partial_index(workspace, monkeypatch):
    entered, release = gated_reader(monkeypatch)
    with store_for(workspace) as (store, handles, revision):
        try:
            accepted = start(store, handles, revision)
            assert entered.wait(10)
            assert store.status(accepted["dataset_id"], "poll")["state"] == "loading"
            assert store._entry.analysis is None
            assert store.catalog.page()["items"]
            with pytest.raises(InterfaceError) as error:
                start(store, handles, revision)
            assert error.value.code == "ingestion_busy"
            with pytest.raises(InterfaceError) as error:
                with store.analysis_snapshot(accepted["dataset_id"], "invented"):
                    pytest.fail("loading data must not be queryable")
            assert error.value.code == "dataset_not_ready"
        finally:
            release.set()
        assert finish(store, accepted)["state"] == "ready"


@pytest.mark.parametrize("change", ["modify", "delete", "symlink", "refresh", "manifest", "same_size_mtime"])
def test_changed_sources_invalidate_ready_metadata_and_lazy_details(workspace, change):
    with store_for(workspace) as (store, handles, revision):
        ready = finish(store, start(store, handles, revision))
        with store.analysis_snapshot(ready["dataset_id"], ready["revision"]) as data:
            attempt = data.trials.iloc[0]["attempt_id"]
        path = workspace / "runs.jsonl"
        if change == "refresh":
            store.catalog.refresh()
        elif change == "manifest":
            (workspace / "expected.json").write_text("{}")
        elif change == "modify":
            path.write_bytes(path.read_bytes() + b" ")
        elif change == "same_size_mtime":
            info = path.stat()
            path.write_bytes(b"x" + path.read_bytes()[1:])
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        else:
            path.unlink()
            if change == "symlink":
                path.symlink_to(workspace / "expected.json")
        stale = store.status(ready["dataset_id"], "poll")
        assert stale["state"] == "stale" and stale["revision"] is None
        assert store._entry.analysis is None
        with pytest.raises(InterfaceError):
            data.trial(attempt)
        with pytest.raises(InterfaceError):
            with store.analysis_snapshot(ready["dataset_id"], ready["revision"]):
                pytest.fail("stale dataset must not be queryable")


def test_change_after_validation_before_publication_never_exposes_ready(workspace, monkeypatch):
    from tsp_hpc.analysis import read_sources

    def changed(*args, **kwargs):
        data = read_sources(*args, **kwargs)
        (workspace / "runs.jsonl").unlink()
        return data

    monkeypatch.setattr("tsp_hpc.analysis.read_sources", changed)
    with store_for(workspace) as (store, handles, revision):
        assert finish(store, start(store, handles, revision))["state"] == "stale"
        assert store._entry.analysis is None


def test_query_context_rechecks_revision_after_use(workspace):
    with store_for(workspace) as (store, handles, revision):
        ready = finish(store, start(store, handles, revision))
        with pytest.raises(InterfaceError) as error:
            with store.analysis_snapshot(ready["dataset_id"], ready["revision"]):
                store.catalog.refresh()
        assert error.value.code == "stale_revision"


def test_refresh_cancels_loading_and_prevents_late_publication(workspace, monkeypatch):
    entered, release = gated_reader(monkeypatch)
    with store_for(workspace) as (store, handles, revision):
        try:
            accepted = start(store, handles, revision)
            assert entered.wait(10)
            store.catalog.refresh()
            store.invalidate()
            assert store.status(accepted["dataset_id"], "poll")["state"] == "stale"
        finally:
            release.set()
        assert finish(store, accepted)["state"] == "stale"
        assert store._entry.analysis is None


def test_shutdown_cooperatively_interrupts_ingestion_and_joins_worker(workspace, monkeypatch):
    entered, release = gated_reader(monkeypatch)
    with store_for(workspace) as (store, handles, revision):
        start(store, handles, revision)
        assert entered.wait(10)
        entry = store._entry
        closer = threading.Thread(target=store.close)
        closer.start()
        try:
            assert entry.cancel.wait(10)
        finally:
            release.set()
            closer.join(10)
        assert not closer.is_alive()
        assert store._future.done() and store._entry is None and entry.analysis is None
        with pytest.raises(InterfaceError):
            start(store, handles, revision)


def test_torn_prefix_and_missing_executions_match_shared_reader(workspace, campaign):
    first = campaign[0].splitlines(keepends=True)[0]
    (workspace / "runs.jsonl").write_bytes(first + b'{"unfinished":')
    expected = read_shards([workspace / "runs.jsonl"], expected_manifest=campaign[1])
    with store_for(workspace) as (store, handles, revision):
        ready = finish(store, start(store, handles, revision))
        assert ready["state"] == "ready"
        assert {item["code"] for item in ready["diagnostics"]} == {"unterminated_tail", "incomplete_campaign", "missing_executions"}
        with store.analysis_snapshot(ready["dataset_id"], ready["revision"]) as data:
            pd.testing.assert_frame_equal(data.trials.drop(columns="shard"), expected.trials.drop(columns="shard"))
            assert not data.trials["usable"].any()
            assert data.sources[0]["sha256"] == hashlib.sha256(first + b'{"unfinished":').hexdigest()


def test_duplicate_observation_policy_and_explicit_diagnostic_truncation(workspace, campaign):
    rows = [json.loads(line) for line in campaign[0].splitlines()]
    copied = deepcopy(rows)
    campaign_id = uuid4().hex
    for row in copied:
        row["campaign_id"] = campaign_id
    for trial, receipt in zip(copied[:-1], copied[-1]["trial_timings"]):
        trial["attempt_id"] = receipt["attempt_id"] = uuid4().hex
    (workspace / "copy.jsonl").write_text("".join(json.dumps(row) + "\n" for row in copied))
    expected = read_shards([workspace / "runs.jsonl", workspace / "copy.jsonl"])
    with store_for(workspace, limits=IngestionLimits(max_diagnostics=1)) as (store, handles, revision):
        ready = finish(store, start(store, handles, revision, shards=("runs.jsonl", "copy.jsonl")))
        assert ready["state"] == "ready" and ready["diagnostics_truncated"]
        assert ready["diagnostics_total"] == len(expected.diagnostics) > 1
        assert len(ready["diagnostics"]) == 1
        with store.analysis_snapshot(ready["dataset_id"], ready["revision"]) as data:
            assert data.diagnostics == expected.diagnostics
            assert data.trials["duplicate_execution"].all() and not data.trials["usable"].any()


@pytest.mark.parametrize("fault", ["malformed", "duplicate_campaign", "invalid_manifest", "unexpected_exception"])
def test_failed_ingestion_is_atomic_sanitized_and_observable(workspace, fault, monkeypatch, caplog):
    shards = ("runs.jsonl",)
    if fault == "malformed":
        (workspace / "runs.jsonl").write_text(f'{workspace}/PRIVATE\n')
    elif fault == "duplicate_campaign":
        (workspace / "copy.jsonl").write_bytes((workspace / "runs.jsonl").read_bytes())
        shards = ("runs.jsonl", "copy.jsonl")
    elif fault == "invalid_manifest":
        (workspace / "expected.json").write_text('{"path":"PRIVATE"}')
    else:
        def fail(*args, **kwargs):
            raise RuntimeError(f"{workspace}/PRIVATE")
        monkeypatch.setattr("tsp_hpc.analysis.read_sources", fail)
    with store_for(workspace) as (store, handles, revision):
        failed = finish(store, start(store, handles, revision, shards=shards))
        assert failed["state"] == "failed" and failed["revision"] is None
        assert store._entry.analysis is None
        assert failed["diagnostics"][0]["code"] == ("internal_error" if fault == "unexpected_exception" else "invalid_artifact")
        assert str(workspace) not in json.dumps(failed) and "PRIVATE" not in json.dumps(failed) + caplog.text
        if fault == "malformed":
            assert failed["diagnostics"][0]["artifact_handle"] == handles["runs.jsonl"]
            assert "record 1" in failed["diagnostics"][0]["message"]


@pytest.mark.parametrize("bound", ["max_trials", "max_record_bytes", "max_summary_bytes", "max_table_bytes",
                                   "max_manifest_plans", "max_manifest_logical_items", "max_manifest_plan_bytes"])
def test_explicit_ingestion_bounds_fail_without_partial_results(workspace, bound):
    with store_for(workspace, limits=IngestionLimits(**{bound: 1})) as (store, handles, revision):
        failed = finish(store, start(store, handles, revision))
        assert failed["state"] == "failed" and store._entry.analysis is None
        assert failed["diagnostics"][0]["code"] == "dataset_limit_exceeded"


def test_manifest_amplification_is_rejected_before_plan_materialization(workspace, campaign, monkeypatch):
    document = campaign[1].to_dict()
    experiment = document["experiments"][0]
    experiment["repetitions"] = list(range(500))
    experiment["executions"] = experiment["executions"][:1]
    experiment["executions"][0]["resources"]["profile"] = "x" * 40_000
    (workspace / "expected.json").write_text(json.dumps(document))

    def forbidden(_self):
        pytest.fail("oversized expansion must be rejected before creating plans")

    monkeypatch.setattr(Manifest, "expand", forbidden)
    with store_for(workspace) as (store, handles, revision):
        failed = finish(store, start(store, handles, revision))
        assert failed["state"] == "failed"
        assert failed["diagnostics"][0]["code"] == "dataset_limit_exceeded"


def test_source_limits_are_checked_before_work_is_submitted(workspace):
    with (workspace / "large.jsonl").open("wb") as stream:
        stream.truncate(MAX_DATASET_BYTES + 1)
    with store_for(workspace) as (store, handles, revision):
        for names, manifest, code in [(("large.jsonl",), None, "dataset_limit_exceeded"),
                                       (("expected.json",), None, "invalid_artifact"),
                                       (("runs.jsonl",), "runs.jsonl", "invalid_artifact"),
                                       (("runs.jsonl", "runs.jsonl"), None, "invalid_request")]:
            with pytest.raises(InterfaceError) as error:
                start(store, handles, revision, shards=names, manifest=manifest)
            assert error.value.code == code
            assert store._future is None and store._entry is None
        with pytest.raises(InterfaceError) as error:
            store.submit(["/private/path.jsonl"], None, revision, "request")
        assert error.value.code == "artifact_not_found"


def test_revisions_bind_manifest_limits_rules_content_and_session(workspace, monkeypatch):
    with store_for(workspace) as (store, handles, revision):
        first = finish(store, start(store, handles, revision))
        second = finish(store, start(store, handles, revision, manifest=None))
        assert first["revision"] != second["revision"]
        with pytest.raises(InterfaceError) as error:
            store.status(first["dataset_id"], "poll")
        assert error.value.code == "dataset_not_found"
        store.limits = IngestionLimits(max_trials=9999)
        third = finish(store, start(store, handles, revision, manifest=None))
        assert third["revision"] != second["revision"]
        monkeypatch.setattr("tsp_hpc.interface.ingestion.rule_identity", lambda: "different-rules")
        assert store.status(third["dataset_id"], "poll")["state"] == "stale"
        with pytest.raises(InterfaceError):
            start(store, handles, revision)
    with store_for(workspace) as (other, handles, revision):
        new = finish(other, start(other, handles, revision))
        assert new["revision"] != first["revision"]
        with pytest.raises(InterfaceError):
            other.status(first["dataset_id"], "poll")


def test_catalog_contention_is_not_misreported_as_staleness(workspace):
    with store_for(workspace) as (store, handles, revision):
        ready = finish(store, start(store, handles, revision))
        with store.catalog._lock:
            with pytest.raises(InterfaceError) as error:
                store.status(ready["dataset_id"], "poll")
        assert error.value.code == "catalog_busy"
        assert store.status(ready["dataset_id"], "poll")["state"] == "ready"


@pytest.mark.parametrize("change", ["modify", "refresh"])
def test_stream_rechecks_authority_on_exit_and_closes_descriptor(workspace, change):
    with store_for(workspace) as (store, handles, revision):
        sources, _ = store.catalog.select_sources([handles["runs.jsonl"]], None, revision)
        with pytest.raises(InterfaceError) as error:
            with sources[0].open() as stream:
                assert stream.read(1) == b"{"
                if change == "refresh":
                    store.catalog.refresh()
                else:
                    (workspace / "runs.jsonl").write_bytes(b"changed")
        assert error.value.code == "stale_revision" and stream.closed


def test_manifest_and_combined_source_byte_limits_precede_validation(workspace):
    from tsp_hpc.interface.workspace import MAX_READ_BYTES
    (workspace / "expected.json").write_bytes(b" " * (MAX_READ_BYTES + 1))
    for name in ("large-a.jsonl", "large-b.jsonl"):
        with (workspace / name).open("wb") as stream:
            stream.truncate(MAX_DATASET_BYTES // 2 + 1)
    with store_for(workspace) as (store, handles, revision):
        for names, manifest in [(("runs.jsonl",), "expected.json"), (("large-a.jsonl", "large-b.jsonl"), None)]:
            with pytest.raises(InterfaceError) as error:
                start(store, handles, revision, shards=names, manifest=manifest)
            assert error.value.code == "dataset_limit_exceeded" and store._future is None


def test_worker_waits_for_short_catalog_operations_instead_of_failing_busy(workspace, monkeypatch):
    entered, release = gated_reader(monkeypatch)
    checking = threading.Event()
    with store_for(workspace) as (store, handles, revision):
        original = store.catalog.source_stamp

        def stamp(*args):
            checking.set()
            return original(*args)

        monkeypatch.setattr(store.catalog, "source_stamp", stamp)
        try:
            accepted = start(store, handles, revision)
            assert entered.wait(10)
            with store.catalog._lock:
                release.set()
                assert checking.wait(10)
                assert not store._future.done()
            assert finish(store, accepted)["state"] == "ready"
        finally:
            release.set()
