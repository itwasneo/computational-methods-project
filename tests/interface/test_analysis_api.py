"""Thin shared-analysis transport, including non-production GA/SA-shaped evidence."""

from dataclasses import replace
import hashlib
import json
from time import monotonic, sleep

import numpy as np
import pytest

from tsp_hpc.analysis import paired_comparisons, progress_table, read_shards, reference_gaps, summary_table, time_to_target
from tsp_hpc.experiments.manifests import Manifest
from tsp_hpc.experiments.runner import run_manifest
from tsp_hpc.interface.analysis_api import _project


def ingest(client, ws, paths, *, expected_state="ready"):
    context = {"api_version": 1, "request_id": "analysis", "workspace_id": ws["workspace_id"]}
    catalog = client.post("/api/artifacts/query", json={**context, "limit": 100, "cursor": None}).json()
    by_name = {row["display_name"]: row["artifact_handle"] for row in catalog["items"]}
    started = client.post("/api/datasets", json={**context, "artifact_catalog_revision": catalog["revision"],
        "result_handles": [by_name[path.name] for path in paths], "expected_manifest_handle": None})
    assert started.status_code == 202, started.text
    context["dataset_id"] = started.json()["dataset_id"]
    deadline = monotonic() + 20
    status = {}
    while monotonic() < deadline:
        response = client.post("/api/datasets/status", json=context)
        status = response.json()
        if response.status_code == 409 and status.get("code") == "catalog_busy" and status.get("retryable") is True:
            sleep(.01)  # Publication contention is transient, not changed source evidence.
            continue
        assert response.status_code == 200, response.text
        if status["state"] != "loading":
            break
        sleep(.01)
    assert status.get("state") == expected_state, status
    return {**context, "revision": status["revision"]}, catalog, by_name


@pytest.mark.parametrize("status_code,code,retryable", [
    (409, "catalog_busy", True), (409, "stale_revision", True), (500, "internal_error", False),
])
def test_ingest_helper_handles_only_documented_transient_catalog_contention(tmp_path, status_code, code, retryable):
    from types import SimpleNamespace

    def response(status_code, body):
        return SimpleNamespace(status_code=status_code, text=json.dumps(body), json=lambda: body)

    responses = iter([
        response(200, {"revision": "catalog-r", "items": [{"display_name": "runs.jsonl", "artifact_handle": "h"}]}),
        response(202, {"dataset_id": "d"}),
        response(status_code, {"code": code, "retryable": retryable}),
        response(200, {"state": "ready", "revision": "r"}),
    ])
    client = SimpleNamespace(post=lambda *_args, **_kwargs: next(responses))
    if code == "catalog_busy":
        context, _, _ = ingest(client, {"workspace_id": "w"}, [tmp_path / "runs.jsonl"])
        assert context["revision"] == "r"
    else:
        with pytest.raises(AssertionError, match=code):
            ingest(client, {"workspace_id": "w"}, [tmp_path / "runs.jsonl"])


def query(client, context, table, **options):
    value = {"table": table, "attempt_ids": None, "limit": 100, "cursor": None, **options}
    return client.post("/api/analysis/query", json={**context, "query": value})


def rows(frame):
    return [_project(row) for row in frame.to_dict("records")]


@pytest.mark.parametrize("algorithm", ["nn", "ga", "sa"])
def test_shared_tables_progress_targets_facets_and_seed_transport(serial_campaign, scripted_registry, paired_session, algorithm):
    record, manifest, bindings, output = serial_campaign(algorithm, repetitions=[0, 1])
    registry = None if algorithm == "nn" else scripted_registry(algorithm)[0]
    run_manifest(manifest, instance_paths=bindings, output_path=output, registry=registry)
    original = output.read_bytes()
    data = read_shards([output])
    with paired_session(output.parent) as (client, app, ws, *_):
        context, _, _ = ingest(client, ws, [output])
        for table, expected in [("trials", data.trials), ("summary", summary_table(data)), ("progress", progress_table(data)),
                                ("targets", time_to_target(data, instance_id=record.instance.instance_id, target_cost=4))]:
            options = {"max_points": 1000} if table in {"progress", "targets"} else {}
            if table == "targets":
                options.update(instance_id=record.instance.instance_id, target_cost=4)
            response = query(client, context, table, **options)
            assert response.status_code == 200, response.text
            assert response.json()["items"] == rows(expected)
            assert response.json()["total_rows"] == len(expected)
            assert response.json()["validation"] == "shared-records"
            assert str(output.parent) not in response.text and '"shard"' not in response.text
        trials = query(client, context, "trials").json()["items"]
        assert all(type(row["solver_seed"]) is type(row["trial_seed"]) is str for row in trials)
        facet = query(client, context, "facets", field="algorithm").json()
        assert facet["items"] == [{"value": algorithm, "count": 2}]
        assert not app.state.execution.enabled
    assert output.read_bytes() == original


def test_owned_job_uses_same_analysis_and_exact_revisions(execution_session, job_client):
    client, app, context, _, _ = execution_session
    job = job_client.wait(job_client.submit(job_client.review(repetitions=[0, 1]))["job"]["job_id"])
    request = {**context, "job_id": job["job_id"], "expected_revision": job["revision"], "result_revision": job["result_revision"],
               "query": {"table": "summary", "attempt_ids": None, "limit": 10, "cursor": None}}
    response = client.post("/api/jobs/analysis", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["instance_snapshot_id"] == job["instance_snapshot_id"]
    assert response.json()["items"][0]["attempt_count"] == 2
    with app.state.results.job_analysis(job["job_id"], job["revision"], job["result_revision"]) as (data, _):
        assert response.json()["items"] == rows(summary_table(data))
    for field in ("expected_revision", "result_revision"):
        bad = client.post("/api/jobs/analysis", json={**request, field: "wrong"})
        assert bad.status_code == 409


def test_progress_pages_stream_shared_iterator_with_bound_context(serial_campaign, scripted_registry, paired_session, monkeypatch):
    _, manifest, bindings, output = serial_campaign("ga", repetitions=[0, 1])
    registry, _ = scripted_registry("ga")
    run_manifest(manifest, instance_paths=bindings, output_path=output, registry=registry)
    expected = rows(progress_table(read_shards([output])))
    with paired_session(output.parent) as (client, _, ws, *_):
        context, _, _ = ingest(client, ws, [output])
        def forbidden(*args, **kwargs):
            raise AssertionError("HTTP must stream shared observations, not materialize a full progress table")
        monkeypatch.setattr("tsp_hpc.analysis.progress_table", forbidden)
        first = query(client, context, "progress", max_points=10, limit=1).json()
        assert first["items"] == expected[:1] and first["total_rows"] == len(expected)
        assert first["clock"] == "logical_and_concatenated_search"
        collected, cursor = first["items"], first["next_cursor"]
        while cursor:
            page = query(client, context, "progress", max_points=10, limit=1, cursor=cursor)
            assert page.status_code == 200, page.text
            collected.extend(page.json()["items"])
            cursor = page.json()["next_cursor"]
        assert collected == expected
        for changed in ({"limit": 2}, {"max_points": 11}, {"attempt_ids": [expected[0]["attempt_id"]]}, {"cursor": "foreign"}):
            invalid = query(client, context, "progress", **{"max_points": 10, "limit": 1, "cursor": first["next_cursor"], **changed})
            assert invalid.status_code == 409 and invalid.json()["code"] == "cursor_invalid"
        assert query(client, context, "progress", max_points=1, limit=1).status_code == 413
        assert query(client, {**context, "revision": "stale"}, "summary").status_code == 409
        assert query(client, context, "trials", attempt_ids=["unknown"]).status_code == 400
        assert query(client, context, "progress", max_points=1, attempt_ids=[]).json()["items"] == []


@pytest.mark.parametrize("history,budget,reason,eligible", [
    (False, {"start_cities": [0]}, "history_not_recorded", False),
    (True, {"start_cities": []}, "target_not_observed", True),
])
def test_missing_history_is_not_rebuilt_from_final_cost_and_no_solution_is_censored(serial_campaign, paired_session, history, budget, reason, eligible):
    record, manifest, bindings, output = serial_campaign("nn", history=history, budget=budget)
    run_manifest(manifest, instance_paths=bindings, output_path=output)
    with paired_session(output.parent) as (client, _, ws, *_):
        context, _, _ = ingest(client, ws, [output])
        page = query(client, context, "progress", max_points=10).json()
        assert page["items"] == [] and page["history_coverage"]["missing_attempts"] == int(not history)
        target = query(client, context, "targets", max_points=10, instance_id=record.instance.instance_id, target_cost=10).json()["items"][0]
        assert target["reason"] == reason and target["eligible"] == eligible
        assert not target["reached"] and target["censored"] == eligible and target["observed_time_s"] is None


def test_synthetic_logical_clocks_are_preserved_without_interpolation(serial_campaign, scripted_registry, paired_session, monkeypatch):
    import tsp_hpc.experiments.runner as runner
    from tsp_hpc.problem.objective import tour_cost
    from tsp_hpc.solvers.results import ProgressPoint, SolverResult, WorkCounters
    offset, clock = [0.], runner.perf_counter
    monkeypatch.setattr(runner, "perf_counter", lambda: clock() + offset[0])
    def script(instance, request, index):
        offset[0] += 2
        tour = np.arange(instance.n_cities, dtype=np.int64)
        cost = tour_cost(instance, tour)
        initial = tour_cost(instance, np.array([0, 2, 1, 3], dtype=np.int64))
        work = WorkCounters(sa_proposals_evaluated=3, full_tour_evaluations=1)
        return SolverResult("ok", tour, cost, work, "work_limit", search_time_s=2,
                            history=(ProgressPoint(.5, WorkCounters(full_tour_evaluations=1), initial), ProgressPoint(1.5, work, cost)))
    record, manifest, bindings, output = serial_campaign("sa")
    registry, _ = scripted_registry("sa", builder=script)
    run_manifest(manifest, instance_paths=bindings, output_path=output, registry=registry)
    with paired_session(output.parent) as (client, _, ws, *_):
        context, _, _ = ingest(client, ws, [output])
        points = query(client, context, "progress", max_points=10).json()["items"]
        assert [p["elapsed_s"] for p in points] == [.5, 1.5] * 3
        assert [p["search_elapsed_s"] for p in points] == [.5, 1.5, 2.5, 3.5, 4.5, 5.5]
        target = query(client, context, "targets", max_points=10, instance_id=record.instance.instance_id, target_cost=4).json()["items"][0]
        assert target["observed_time_s"] == 1.5 and target["observation_end_s"] == 6
        assert len(points) == 6  # No invented intermediate times or tours.
        missed = query(client, context, "targets", max_points=10, instance_id=record.instance.instance_id, target_cost=3).json()["items"][0]
        assert missed["eligible"] and missed["censored"] and not missed["reached"]
        assert missed["observed_time_s"] is None and missed["reason"] == "target_not_observed"


@pytest.mark.parametrize("case", ["failed", "incomplete", "duplicate"])
def test_exclusions_and_diagnostics_survive_selection(serial_campaign, scripted_registry, paired_session, case):
    record, manifest, bindings, output = serial_campaign("sa")
    registry, _ = scripted_registry("sa", fail_at={1} if case == "failed" else ())
    run_manifest(manifest, instance_paths=bindings, output_path=output, registry=registry)
    paths = [output]
    if case == "incomplete":
        output.write_bytes(output.read_bytes().splitlines(keepends=True)[0])
    if case == "duplicate":
        second = output.with_name("second.jsonl")
        run_manifest(manifest, instance_paths=bindings, output_path=second, registry=scripted_registry("sa")[0])
        paths.append(second)
    data = read_shards(paths)
    with paired_session(output.parent) as (client, _, ws, *_):
        context, _, _ = ingest(client, ws, paths)
        attempt = data.trials.iloc[0]["attempt_id"]
        summary = query(client, context, "summary", attempt_ids=[attempt]).json()["items"][0]
        assert summary["usable_count"] == 0 and summary["attempt_count"] == 1
        target = query(client, context, "targets", attempt_ids=[attempt], max_points=100,
                       instance_id=record.instance.instance_id, target_cost=4).json()["items"][0]
        assert target["reached"] and not target["eligible"] and not target["censored"]
        assert target["reason"] == {"failed": "failed_trial", "incomplete": "incomplete_campaign", "duplicate": "duplicate_execution"}[case]
        response = query(client, context, "diagnostics")
        assert response.status_code == 200 and response.json()["items"]
        assert str(output) not in response.text and '"path"' not in response.text
        assert query(client, context, "diagnostics", attempt_ids=[attempt]).status_code == 400


@pytest.mark.parametrize("timed,incompatible", [(False, False), (True, False), (False, True)])
def test_comparisons_delegate_context_and_budget_eligibility(serial_campaign, paired_session, timed, incompatible):
    budget = {"start_cities": [0], **({"time_limit_s": 10} if timed else {})}
    _, manifest, bindings, output = serial_campaign("nn", budget=budget)
    document = manifest.to_dict()
    base = document["experiments"][0]["executions"][0]
    document["experiments"][0]["executions"] = [{**base, "implementation": name} for name in ("visited-scan-v1", "remaining-array-v1")]
    run_manifest(Manifest(document), instance_paths=bindings, output_path=output)
    records = [json.loads(line) for line in output.read_text().splitlines()]
    for record in records[:-1]:
        record["runtime"]["source"] = {"revision": "a" * 40, "dirty": False}  # Synthetic eligibility, NOT performance evidence.
    if incompatible:
        records[1]["runtime"]["compiler"]["config"]["boundscheck"] = True
    output.write_text("".join(json.dumps(record) + "\n" for record in records))
    expected = paired_comparisons(read_shards([output]), baseline_implementation="visited-scan-v1")
    with paired_session(output.parent) as (client, _, ws, *_):
        context, _, _ = ingest(client, ws, [output])
        response = query(client, context, "comparisons", baseline_implementation="visited-scan-v1", baseline_distance_mode=None, metric="search_time_s")
        assert response.status_code == 200, response.text
        assert response.json()["items"] == rows(expected)
        assert response.json()["items"][0]["reason"] == ("time_limited_quality_run" if timed else "incompatible_execution_context" if incompatible else None)


@pytest.mark.parametrize("fault", [None, "checksum", "objective", "duplicate", "below-exact", "nonfinite", "duplicate-key", "outside"])
def test_captured_authorized_references_share_validation_and_do_not_return_source_paths(serial_campaign, paired_session, tmp_path, fault):
    record, manifest, bindings, output = serial_campaign("nn")
    run_manifest(manifest, instance_paths=bindings, output_path=output)
    ref = {"instance_id": record.instance.instance_id, "coordinate_checksum": record.descriptor["coordinate_checksum"],
           "distance_kind": "euclidean_2d", "kind": "exact", "cost": 4., "source": "/private/reference/path/never-publish"}
    if fault == "checksum":
        ref["coordinate_checksum"] = "0" * 64
    if fault == "objective":
        ref["distance_kind"] = "tsplib_euc_2d"
    if fault == "below-exact":
        ref["cost"] = 8
    document = {"schema_version": 1, "references": [ref, ref] if fault == "duplicate" else [ref]}
    raw = json.dumps(document)
    if fault == "nonfinite":
        raw = raw.replace('"cost": 4.0', '"cost": NaN')
    if fault == "duplicate-key":
        raw = raw.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
    reference = output.with_name("reference.json")
    reference.write_text(raw)
    with paired_session(output.parent) as (client, _, ws, *_):
        context, catalog, handles = ingest(client, ws, [output])
        response = query(client, context, "references", reference_handle=str(tmp_path / "outside") if fault == "outside" else handles[reference.name],
                         artifact_catalog_revision=catalog["revision"])
        if fault:
            assert response.status_code in {404, 422}, response.text
        else:
            assert response.status_code == 200, response.text
            assert response.json()["items"] == rows(reference_gaps(read_shards([output]), document))
            assert response.json()["reference"]["sha256"] == hashlib.sha256(raw.encode()).hexdigest()
            assert "/private/" not in response.text and '"reference_source"' not in response.text


def test_large_exact_observations_and_float64_target_spellings(serial_campaign, scripted_registry, paired_session):
    count = 2**53 + 1
    record, manifest, bindings, output = serial_campaign("ga", budget={"max_generations": count}, repetitions=[0, 1])
    run_manifest(manifest, instance_paths=bindings, output_path=output, registry=scripted_registry("ga")[0])
    with paired_session(output.parent) as (client, _, ws, *_):
        context, _, _ = ingest(client, ws, [output])
        response = query(client, context, "trials").json()
        assert response["items"][0]["budget_count"] == response["items"][0]["budget"]["max_generations"] == str(count)
        assert response["items"][0]["work"]["ga_generations_completed"] == str(count * 3)
        first = query(client, context, "targets", limit=1, max_points=10, instance_id=record.instance.instance_id, target_cost=10**20).json()
        second = query(client, context, "targets", limit=1, cursor=first["next_cursor"], max_points=10,
                       instance_id=record.instance.instance_id, target_cost=1e20)
        assert second.status_code == 200 and second.json()["query_key"] == first["query_key"]
        assert query(client, context, "targets", max_points=10, instance_id="missing", target_cost=0).status_code == 400


def test_resource_limits_source_changes_and_publication_rules_fail_closed(serial_campaign, paired_session, monkeypatch):
    _, manifest, bindings, output = serial_campaign("nn", repetitions=[0, 1])
    run_manifest(manifest, instance_paths=bindings, output_path=output)
    with paired_session(output.parent) as (client, app, ws, *_):
        context, _, _ = ingest(client, ws, [output])
        limits = app.state.analysis_queries.limits
        for changes in ({"max_analysis_rows": 1}, {"max_analysis_bytes": 1}, {"max_response_bytes": 1024}):
            app.state.analysis_queries.limits = replace(limits, **changes)
            assert query(client, context, "summary").status_code == 413
        app.state.analysis_queries.limits = limits
        with monkeypatch.context() as patch:
            patch.setattr("tsp_hpc.interface.ingestion.rule_identity", lambda: "changed")
            assert query(client, context, "summary").status_code == 409
    with paired_session(output.parent) as (client, _, ws, *_):
        context, _, _ = ingest(client, ws, [output])
        output.write_text(output.read_text() + "\n")
        assert query(client, context, "progress", max_points=10).status_code == 409


def test_large_history_pages_do_not_expand_to_a_full_http_table(serial_campaign, scripted_registry, paired_session, monkeypatch):
    import tsp_hpc.experiments.runner as runner
    from tsp_hpc.problem.objective import tour_cost
    from tsp_hpc.solvers.results import ProgressPoint, SolverResult, WorkCounters
    offset, clock = [0.], runner.perf_counter
    monkeypatch.setattr(runner, "perf_counter", lambda: clock() + offset[0])
    def script(instance, request, index):
        offset[0] += 1000  # Synthetic fixture clock, not algorithm/performance evidence.
        tour = np.arange(instance.n_cities, dtype=np.int64)
        cost = tour_cost(instance, tour)
        points = tuple(ProgressPoint(i / 10, WorkCounters(ga_generations_completed=i, full_tour_evaluations=3 + i * 2), cost)
                       for i in range(5001))
        return SolverResult("ok", tour, cost, points[-1].work, "work_limit", search_time_s=1000, history=points)
    _, manifest, bindings, output = serial_campaign("ga", budget={"max_generations": 5000})
    document = manifest.to_dict()
    document["experiments"][0]["history_policy"] = {"interval_s": .00001, "max_points": 8192}
    run_manifest(Manifest(document), instance_paths=bindings, output_path=output, registry=scripted_registry("ga", builder=script)[0])
    with paired_session(output.parent) as (client, _, ws, *_):
        context, _, _ = ingest(client, ws, [output])
        page = query(client, context, "progress", limit=2, max_points=20000)
        assert page.status_code == 200, page.text
        assert page.json()["total_rows"] == 15003 and len(page.json()["items"]) == 2
        assert len(page.content) < 10000
        def forbidden(*args):
            raise AssertionError("oversized selection must fail before lazy record materialization")
        monkeypatch.setattr("tsp_hpc.analysis.Analysis.trial", forbidden)
        assert query(client, context, "progress", limit=1, max_points=10000).status_code == 413


def test_mutation_during_shared_summary_cannot_publish_a_stale_page(serial_campaign, paired_session, monkeypatch):
    _, manifest, bindings, output = serial_campaign("nn")
    run_manifest(manifest, instance_paths=bindings, output_path=output)
    with paired_session(output.parent) as (client, _, ws, *_):
        context, _, _ = ingest(client, ws, [output])
        def changed(data):
            result = summary_table(data)
            output.write_bytes(output.read_bytes() + b"\n")
            return result
        monkeypatch.setattr("tsp_hpc.analysis.summary_table", changed)
        assert query(client, context, "summary").status_code == 409


def test_malformed_nested_record_cannot_reach_analysis(serial_campaign, paired_session):
    _, manifest, bindings, output = serial_campaign("nn")
    run_manifest(manifest, instance_paths=bindings, output_path=output)
    records = [json.loads(line) for line in output.read_text().splitlines()]
    records[0]["logical_results"][0]["result"]["history"][0]["work"] = ["/private/raw/failure"]
    output.write_text("".join(json.dumps(record) + "\n" for record in records))
    with paired_session(output.parent) as (client, _, ws, *_):
        context, _, _ = ingest(client, ws, [output], expected_state="failed")
        response = query(client, {**context, "revision": "not-ready"}, "progress", max_points=100)
        assert response.status_code == 409 and response.json()["code"] == "dataset_not_ready"
        status = client.post("/api/datasets/status", json={key: value for key, value in context.items() if key != "revision"})
        assert status.json()["diagnostics"][0]["code"] == "invalid_artifact"
        assert "/private/" not in status.text


def test_nullable_logical_indices_and_unrepresentable_text_keep_exact_wire_rules(serial_campaign):
    from tsp_hpc.interface.errors import InterfaceError
    from tsp_hpc.interface.wire import wire_value
    paths = []
    for repetition, history in enumerate([False, True]):
        record, manifest, bindings, output = serial_campaign("nn", history=history, repetitions=[repetition])
        run_manifest(manifest, instance_paths=bindings, output_path=output)
        paths.append(output)
    values = time_to_target(read_shards(paths), instance_id=record.instance.instance_id, target_cost=10)["logical_index"].tolist()
    assert values == [None, 0] and type(values[1]) is int
    for value in ("\ud800", {"\ud800": 0}):
        with pytest.raises(InterfaceError) as error:
            wire_value(value)
        assert error.value.code == "invalid_artifact"


def test_empty_dataset_and_strict_irrelevant_options(tmp_path, paired_session):
    output = tmp_path / "empty.jsonl"
    output.write_text("")
    with paired_session(tmp_path) as (client, _, ws, *_):
        context, _, _ = ingest(client, ws, [output])
        for table in ("trials", "summary", "diagnostics", "progress"):
            response = query(client, context, table, **({"max_points": 10} if table == "progress" else {}))
            assert response.status_code == 200, response.text
            assert response.json()["next_cursor"] is None
            if table == "diagnostics":
                assert response.json()["items"][0]["code"] == "incomplete_campaign"
            else:
                assert response.json()["items"] == []
        for options in ({"max_points": 5}, {"path": "/etc/passwd"}, {"limit": True}, {"attempt_ids": ["a", "a"]}):
            assert query(client, context, "summary", **options).status_code == 400
