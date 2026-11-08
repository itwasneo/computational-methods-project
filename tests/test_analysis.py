"""Result ingestion, strict identity checks and scientific comparison boundaries."""

from copy import deepcopy
import json
import math
import subprocess
import sys
from uuid import uuid4

import pandas as pd
import pytest

from tsp_hpc.analysis import (read_shards, summary_table, paired_comparisons,
                              reference_gaps, load_references)
from tsp_hpc.analysis_reports import write_report
from tsp_hpc.identity import stable_id
from tsp_hpc.instances import fixture, save_instance
from tsp_hpc.manifests import Manifest
from tsp_hpc.runner import run_manifest


def save(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


@pytest.fixture(scope="module")
def example(tmp_path_factory):
    directory = tmp_path_factory.mktemp("analysis-input")
    record = fixture("square")
    artifact = directory / "square.json"
    save_instance(record, artifact)
    manifest = Manifest({"schema_version": 1, "instances": [record.descriptor], "experiments": [{
        "instance_id": record.instance.instance_id, "algorithm": "nn", "solver_seed": 42,
        "repetitions": [0, 1], "budget": {"start_cities": [2, 0]},
        "executions": [{"backend": "serial", "implementation": implementation, "distance_mode": mode,
                        "resources": {"profile": "test"}}
                       for mode in ("dense", "on_demand")
                       for implementation in ("visited-scan-v1", "remaining-array-v1")],
    }]})
    shard = directory / "runs.jsonl"
    run_manifest(manifest, instance_paths={record.instance.instance_id: artifact}, output_path=shard)
    original = [json.loads(line) for line in shard.read_text().splitlines()]
    # Synthetic durations/provenance for exact arithmetic assertions, not a benchmark.
    for trial, receipt in zip(original[:-1], original[-1]["trial_timings"]):
        trial["runtime"]["source"] = {"revision": "a" * 40, "dirty": False}
        cost = 2.0 if trial["effective_execution"]["implementation"] == "visited-scan-v1" else 1.0
        trial["timings"] = {key: 0.0 for key in trial["timings"]}
        trial["timings"].update(search_time_s=cost, solve_wall_time_s=cost, pre_persistence_time_s=cost+1)
        for key in ("jit_warmup_time_s", "preprocessing_time_s", "solver_warmup_time_s"):
            trial["adapter"][key] = 0.0
        receipt.update(persistence_time_s=0.1, end_to_end_time_s=cost+1.1)
    original[-1]["timings"] = {"setup_time_s": 1.0, "trial_persistence_time_s": 0.8,
                                "end_to_end_time_s": 1 + sum(r["end_to_end_time_s"] for r in original[-1]["trial_timings"])}
    return original, manifest, record


@pytest.fixture
def rows(example):
    return deepcopy(example[0])


def test_roundtrip_compact_table_lazy_details_and_grouped_statistics(tmp_path, rows, example):
    path = save(tmp_path / "runs.jsonl", rows)
    data = read_shards([path], expected_manifest=example[1])
    assert not data.diagnostics and len(data.trials) == 8
    assert "best_tour" not in data.trials and "requested_plan" not in data.trials
    assert data.trial(rows[0]["attempt_id"]) == rows[0]
    assert data.sources[0]["size_bytes"] == path.stat().st_size
    summary = summary_table(data)
    assert len(summary) == 4 and summary["usable_count"].tolist() == [2] * 4
    assert set(summary["search_time_s_median"]) == {1., 2.}
    pairs = paired_comparisons(data, baseline_implementation="visited-scan-v1")
    assert len(pairs) == 4 and pairs["reason"].isna().all()
    assert pairs["speedup"].tolist() == [2.] * 4
    end = paired_comparisons(data, baseline_implementation="visited-scan-v1", metric="end_to_end_time_s")
    assert end["speedup"].tolist() == pytest.approx([3.1/2.1] * 4)
    modes = paired_comparisons(data, baseline_implementation="visited-scan-v1", baseline_distance_mode="on_demand")
    assert len(modes) == 6 and modes["reason"].isna().all()
    reordered = deepcopy(rows)
    reordered[:-1] = list(reversed(reordered[:-1]))
    reordered[-1]["trial_timings"].reverse()
    other = read_shards([save(tmp_path / "reordered.jsonl", reordered)])
    pd.testing.assert_frame_equal(summary_table(other), summary)
    assert paired_comparisons(other, baseline_implementation="visited-scan-v1")["speedup"].tolist() == [2.] * 4
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed"):
        data.trial(rows[0]["attempt_id"])


def test_omitted_implementation_uses_recorded_resolution_not_current_default(tmp_path, rows, monkeypatch):
    for trial in rows[:-1]:
        if trial["effective_execution"]["implementation"] == "visited-scan-v1":
            plan = trial["requested_plan"]
            del plan["execution"]["implementation"]
            plan["execution_id"] = stable_id("execution", {"search_id": plan["search_id"], "execution": plan["execution"]})
    monkeypatch.setattr("tsp_hpc.nearest_neighbour.DEFAULT_IMPLEMENTATION", "remaining-array-v1")
    data = read_shards([save(tmp_path / "legacy.jsonl", rows)])
    visited = data.trials[data.trials["implementation"] == "visited-scan-v1"]
    assert len(visited) == 4
    assert (visited["requested_execution_id"] != visited["execution_id"]).all()
    assert paired_comparisons(data, baseline_implementation="visited-scan-v1")["speedup"].tolist() == [2.] * 4


@pytest.mark.parametrize("fault", ["version", "version_bool", "requested_id", "effective_id", "seed", "streams", "effective_override",
                                  "effective_type", "provenance", "tour", "work", "cost", "summary_count", "receipt", "summary_duration", "adapter"])
def test_corrupt_complete_records_are_not_accepted(tmp_path, rows, fault):
    trial, summary = rows[0], rows[-1]
    if fault == "version": trial["schema_version"] = 2
    elif fault == "version_bool": trial["schema_version"] = True
    elif fault == "requested_id": trial["requested_plan"]["search_id"] = "search-v1-" + "0" * 64
    elif fault == "effective_id": trial["execution_id"] = "execution-v1-" + "0" * 64
    elif fault == "seed": trial["requested_plan"]["trial_seed"] += 1
    elif fault == "streams": trial["requested_plan"]["logical_searches"][0]["seed"] += 1
    elif fault == "effective_override": trial["effective_execution"]["distance_mode"] = "on_demand"
    elif fault == "effective_type":
        trial["effective_execution"]["resources"]["workers"] = True
        trial["execution_id"] = stable_id("execution", {"search_id": trial["requested_plan"]["search_id"],
                                                       "execution": trial["effective_execution"]})
    elif fault == "provenance": trial["input"]["provenance"]["seed"] = True
    elif fault == "tour": trial["result"]["best_tour"] = [0, 0, 2, 3]
    elif fault == "work": trial["result"]["work"]["nn_starts_completed"] = True
    elif fault == "cost": trial["result"]["best_cost"] = -1
    elif fault == "summary_count": summary["planned_trials"] += 1
    elif fault == "receipt": summary["trial_timings"].reverse()
    elif fault == "summary_duration": summary["timings"]["end_to_end_time_s"] = 0
    elif fault == "adapter": trial["adapter"]["matrix_bytes"] = 0
    with pytest.raises(ValueError, match=r"runs.jsonl:\d+:"):
        read_shards([save(tmp_path / "runs.jsonl", rows)])


@pytest.mark.parametrize("raw", [b'{"schema_version":1,"schema_version":1}\n',
                                b'{"schema_version":NaN}\n', b'{"schema_version":1e999}\n', b'not json\n'])
def test_strict_json_decoder(tmp_path, raw):
    path = tmp_path / "bad.jsonl"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        read_shards([path])


def test_torn_tail_and_missing_summary_preserve_but_exclude_prefix(tmp_path, rows, example):
    path = save(tmp_path / "partial.jsonl", rows[:2])
    with path.open("ab") as stream:
        stream.write(b'{"unfinished":')
    data = read_shards([path], expected_manifest=example[1])
    assert {d["code"] for d in data.diagnostics} == {"unterminated_tail", "incomplete_campaign", "missing_executions"}
    assert len(data.trials) == 2 and not data.trials["usable"].any()
    assert data.trials["end_to_end_time_s"].isna().all()
    assert summary_table(data)["usable_count"].sum() == 0
    assert data.trial(rows[0]["attempt_id"])["result"]["best_cost"] == 4


def test_duplicates_are_distinguished_from_repetitions(tmp_path, rows):
    first = save(tmp_path / "first.jsonl", rows)
    with pytest.raises(ValueError, match="duplicate campaign"):
        read_shards([first, save(tmp_path / "copy.jsonl", rows)])
    other = deepcopy(rows)
    campaign_id = uuid4().hex
    for row in other:
        row["campaign_id"] = campaign_id
    # Distinct campaign alone does not make reused attempt IDs legitimate.
    second = save(tmp_path / "second.jsonl", other)
    with pytest.raises(ValueError, match="duplicate attempt"):
        read_shards([first, second])
    for row, receipt in zip(other[:-1], other[-1]["trial_timings"]):
        row["attempt_id"] = uuid4().hex
        receipt["attempt_id"] = row["attempt_id"]
    data = read_shards([first, save(second, other)])
    assert len(data.trials) == 16 and data.trials["duplicate_execution"].all()
    assert not data.trials["usable"].any()
    assert len([d for d in data.diagnostics if d["code"] == "duplicate_execution"]) == 8
    assert summary_table(data)["duplicate_count"].sum() == 16


def test_unknown_source_is_not_pooled_across_campaigns(tmp_path, rows):
    paths = []
    for index in range(2):
        campaign_id = uuid4().hex
        trials = deepcopy(rows[index*4:index*4+4])
        footer = deepcopy(rows[-1])
        for trial in trials:
            trial["campaign_id"] = campaign_id
            trial["runtime"]["source"] = {"revision": None, "dirty": None}
        footer.update(campaign_id=campaign_id, planned_trials=4, counts={"completed": 4, "failed": 0},
                      trial_timings=footer["trial_timings"][index*4:index*4+4])
        footer["timings"]["trial_persistence_time_s"] = 0.4
        paths.append(save(tmp_path / f"part-{index}.jsonl", [*trials, footer]))
    data = read_shards(paths)
    assert data.trials["usable"].all()
    assert len(summary_table(data)) == 8


@pytest.mark.parametrize("fault,reason", [("dirty", "unknown_or_dirty_source"),
                                        ("unknown", "unknown_or_dirty_source"),
                                        ("runtime", "incompatible_execution_context"),
                                        ("tour", "different_returned_work_or_solution"),
                                        ("zero", "missing_or_zero_duration")])
def test_incompatible_pairs_have_reasons_not_speedups(tmp_path, rows, fault, reason):
    for trial in rows[:-1]:
        if trial["effective_execution"]["implementation"] != "remaining-array-v1":
            continue
        if fault == "dirty": trial["runtime"]["source"]["dirty"] = True
        elif fault == "unknown": trial["runtime"]["source"] = {"revision": None, "dirty": None}
        elif fault == "runtime": trial["runtime"]["versions"]["numpy"] = "different"
        elif fault == "tour": trial["result"]["best_tour"].reverse()
        elif fault == "zero": trial["timings"]["search_time_s"] = 0
    pairs = paired_comparisons(read_shards([save(tmp_path / "runs.jsonl", rows)]), baseline_implementation="visited-scan-v1")
    assert set(pairs["reason"]) == {reason} and pairs["speedup"].isna().all()


def test_summary_statistics_do_not_overflow_finite_costs(tmp_path, rows):
    data = read_shards([save(tmp_path / "runs.jsonl", rows)])
    # Exercise aggregation independently with synthetic extreme values, not tour-cost claims.
    data.trials["best_cost"] = 1e308
    data.trials.loc[[0, 4], "best_cost"] = 1e-320
    data.trials.loc[1, "best_cost"] = 1e-320
    summary = summary_table(data).set_index("group_id")
    for metric in ("min", "max", "mean", "median", "std"):
        assert all(math.isfinite(value) for value in summary[f"best_cost_{metric}"])
    assert summary.loc[data.trials.loc[0, "group_id"], "best_cost_median"] == 1e-320
    assert summary.loc[data.trials.loc[1, "group_id"], "best_cost_median"] == 5e307


def test_missing_baseline_is_not_replaced_with_fastest_observation(tmp_path, rows):
    data = read_shards([save(tmp_path / "runs.jsonl", rows)])
    pairs = paired_comparisons(data, baseline_implementation="__not_present__")
    assert len(pairs) == 8 and set(pairs["reason"]) == {"missing_or_ambiguous_baseline"}


def reference(record, kind="exact", cost=4.):
    return {"schema_version": 1, "references": [{
        "instance_id": record.instance.instance_id,
        "coordinate_checksum": record.descriptor["coordinate_checksum"], "distance_kind": "euclidean_2d",
        "kind": kind, "cost": cost, "source": "unit-square perimeter certificate (test)",
    }]}


def test_references_are_objective_specific_and_exact_is_not_best_known(tmp_path, rows, example):
    data = read_shards([save(tmp_path / "runs.jsonl", rows)])
    ref = reference(example[2])
    gaps = reference_gaps(data, ref)
    assert len(gaps) == 8 and (gaps["percent_gap"] == 0).all()
    assert set(gaps["reference_kind"]) == {"exact"}
    better = reference_gaps(data, reference(example[2], "best_known", 5.))
    assert (better["percent_gap"] == -20).all()
    with pytest.raises(ValueError, match="below"):
        reference_gaps(data, reference(example[2], "exact", 5.))
    ref["references"][0]["distance_kind"] = "tsplib_euc_2d"
    with pytest.raises(ValueError, match="convention"):
        reference_gaps(data, ref)
    path = tmp_path / "ref.json"
    path.write_text('{"schema_version":1,"references":[],"references":[]}')
    with pytest.raises(ValueError, match="duplicate"):
        load_references(path)


def test_zero_reference_has_absolute_gap_not_division_by_zero(tmp_path, rows, example):
    data = read_shards([save(tmp_path / "runs.jsonl", rows)])
    # A declared best-known zero reference is deliberately not an exact certificate.
    gaps = reference_gaps(data, reference(example[2], "best_known", 0.))
    assert (gaps["absolute_gap"] == 4).all() and gaps["percent_gap"].isna().all()


def test_failures_and_zero_work_are_visible_not_quality_observations(tmp_path, example):
    doc = example[1].to_dict()
    doc["experiments"][0]["budget"]["start_cities"] = []
    doc["experiments"][0]["executions"].append({"backend": "multicore", "resources": {"profile": "test"}})
    manifest = Manifest(doc)
    artifact = tmp_path / "input.json"
    save_instance(example[2], artifact)
    path = tmp_path / "runs.jsonl"
    run_manifest(manifest, instance_paths={example[2].instance.instance_id: artifact}, output_path=path)
    data = read_shards([path], expected_manifest=manifest)
    summary = summary_table(data)
    assert summary["no_solution_count"].sum() == 8
    assert summary["failed_count"].sum() == 2 and summary["usable_count"].sum() == 0
    assert summary["best_cost_mean"].isna().all() and summary["best_cost_count"].sum() == 0
    output = tmp_path / "report"
    report = write_report([path], output_dir=output)
    assert report["has_issues"] and report["figures"] == []


def test_size_limit_footer_order_and_empty_shard(tmp_path, rows):
    path = save(tmp_path / "runs.jsonl", rows)
    with pytest.raises(ValueError, match="byte limit"):
        read_shards([path], max_record_bytes=10)
    with path.open("a") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="after campaign"):
        read_shards([path])
    empty = tmp_path / "empty.jsonl"
    empty.touch()
    report = write_report([empty], output_dir=tmp_path / "empty-report")
    assert report["observed_trials"] == 0 and report["has_issues"] and not report["figures"]
    complete = read_shards([save(path, rows)])
    assert list(pd.read_csv(tmp_path / "empty-report/trials.csv").columns) == list(complete.trials.columns)
    assert list(pd.read_csv(tmp_path / "empty-report/summary.csv").columns) == ["label", *summary_table(complete).columns]


def test_report_cli_and_rendering_do_not_replace_inputs_or_output(tmp_path, rows, example):
    source = save(tmp_path / "runs.jsonl", rows)
    before = source.read_bytes()
    ref = tmp_path / "ref.json"
    ref.write_text(json.dumps(reference(example[2])))
    output = tmp_path / "report"
    process = subprocess.run([sys.executable, "-m", "tsp_hpc", "analyze", str(source), "--output", str(output),
                              "--baseline", "visited-scan-v1", "--references", str(ref)],
                             cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stderr
    report = json.loads((output / "report.json").read_text())
    assert set(report["figures"]) == {"runtime.png", "quality.png", "speedup.png", "gaps.png"}
    for name in report["figures"]:
        assert (output / name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert len(pd.read_csv(output / "trials.csv")) == 8
    assert source.read_bytes() == before
    with pytest.raises(FileExistsError):
        write_report([source], output_dir=output)
    assert source.read_bytes() == before
    limited = write_report([source], output_dir=tmp_path / "limited", max_plot_groups=1)
    assert not limited["figures"] and len(pd.read_csv(tmp_path / "limited" / "summary.csv")) == 4
    assert any("exceeds limit" in note for note in limited["notes"])
