"""Observed histories/targets, not inferred trajectories or scientific GA/SA tests."""

import json
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from tsp_hpc.analysis import read_shards, summary_table, progress_table, time_to_target, paired_comparisons
from tsp_hpc.analysis.core import AnalysisLimitError
from tsp_hpc.analysis.reports import budget_label, write_report
from tsp_hpc.experiments.manifests import Manifest
import tsp_hpc.experiments.runner as runner
from tsp_hpc.problem.objective import tour_cost
from tsp_hpc.solvers.results import ProgressPoint, SolverResult, WorkCounters


@pytest.mark.parametrize("algorithm", ["ga", "sa"])
def test_logical_clocks_observed_crossings_censoring_and_budget_labels(serial_campaign, scripted_registry, monkeypatch, algorithm):
    offset = [0.]
    clock = runner.perf_counter
    monkeypatch.setattr(runner, "perf_counter", lambda: clock() + offset[0])
    def script(instance, request, index):
        offset[0] += 2
        tour = np.arange(instance.n_cities, dtype=np.int64)
        cost = tour_cost(instance, tour)
        initial_cost = tour_cost(instance, np.array([0, 2, 1, 3], dtype=np.int64))
        work = WorkCounters(ga_generations_completed=1, full_tour_evaluations=5) if algorithm == "ga" else WorkCounters(sa_proposals_evaluated=3, full_tour_evaluations=1)
        history = (ProgressPoint(.5, WorkCounters(full_tour_evaluations=1), initial_cost),
                   ProgressPoint(1.5, work, cost))
        return SolverResult("ok", tour, cost, work, "work_limit", search_time_s=2, history=history)
    budget = {"max_generations" if algorithm == "ga" else "max_proposals": 1 if algorithm == "ga" else 3,
              "time_limit_s": 1}
    record, manifest, bindings, output = serial_campaign(algorithm, budget=budget)
    registry, _ = scripted_registry(algorithm, builder=script)
    runner.run_manifest(manifest, instance_paths=bindings, output_path=output, registry=registry)
    data = read_shards([output])
    trial = data.trials.iloc[0]
    assert trial["budget_mode"] == "work_and_time"
    assert trial["time_limit_s"] == 1 and trial["time_overshoot_s"] == 3
    assert trial["logical_search_count"] == trial["completed_logical_searches"] == 3
    assert trial["history_points"] == 6
    points = progress_table(data)
    assert points["elapsed_s"].tolist() == [.5, 1.5] * 3
    assert points["search_elapsed_s"].tolist() == [.5, 1.5, 2.5, 3.5, 4.5, 5.5]
    assert points["trial_best_cost"].tolist()[1:] == [4.] * 5
    assert json.loads(points.iloc[-1]["cumulative_work_json"])["full_tour_evaluations"] == (15 if algorithm == "ga" else 3)
    target = time_to_target(data, instance_id=record.instance.instance_id, target_cost=4).iloc[0]
    assert target["eligible"] and target["reached"] and not target["censored"]
    assert target["observed_time_s"] == 1.5 and target["observation_end_s"] == 6
    missed = time_to_target(data, instance_id=record.instance.instance_id, target_cost=3).iloc[0]
    assert missed["eligible"] and missed["censored"] and not missed["reached"]
    assert missed["observed_time_s"] is None and missed["reason"] == "target_not_observed"
    summary = summary_table(data)
    label = budget_label(summary.iloc[0])
    assert "starts" not in label and "time=1s" in label and "calls=3" in label
    assert summary.iloc[0]["time_overshoot_s_mean"] == 3


def test_no_solution_is_a_retained_censored_trial_and_final_cost_is_not_history(serial_campaign):
    for history, budget, reason, eligible in [
        (True, {"start_cities": [0], "time_limit_s": 1e-12}, "target_not_observed", True),
        (False, {"start_cities": [0]}, "history_not_recorded", False),
    ]:
        record, manifest, bindings, output = serial_campaign("nn", history=history, budget=budget)
        runner.run_manifest(manifest, instance_paths=bindings, output_path=output)
        data = read_shards([output])
        target = time_to_target(data, instance_id=record.instance.instance_id, target_cost=10).iloc[0]
        assert target["reason"] == reason and target["eligible"] == eligible
        assert not target["reached"] and target["censored"] == eligible
        assert progress_table(data).empty


def test_failed_prefix_is_inspectable_but_not_an_eligible_target_success(serial_campaign, scripted_registry):
    record, manifest, bindings, output = serial_campaign("sa")
    registry, _ = scripted_registry("sa", fail_at={1})
    runner.run_manifest(manifest, instance_paths=bindings, output_path=output, registry=registry)
    data = read_shards([output])
    points = progress_table(data)
    assert len(points) == 1 and not points["usable"].any()
    target = time_to_target(data, instance_id=record.instance.instance_id, target_cost=4).iloc[0]
    assert target["reached"] and not target["eligible"] and not target["censored"]
    assert target["reason"] == "failed_trial"


def test_bounds_selection_and_stale_history_sources(serial_campaign, scripted_registry):
    record, manifest, bindings, output = serial_campaign("ga")
    registry, _ = scripted_registry("ga")
    runner.run_manifest(manifest, instance_paths=bindings, output_path=output, registry=registry)
    data = read_shards([output])
    with pytest.raises(AnalysisLimitError):
        progress_table(data, max_points=2)
    for bound in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            progress_table(data, max_points=bound)
    with pytest.raises(ValueError, match="unknown attempt"):
        progress_table(data, attempt_ids=["missing"])
    with pytest.raises(TypeError):
        progress_table(data, attempt_ids="not-a-collection")
    for target in (True, -1, float("inf")):
        with pytest.raises(ValueError):
            time_to_target(data, instance_id=record.instance.instance_id, target_cost=target)
    with pytest.raises(ValueError, match="unknown target instance"):
        time_to_target(data, instance_id="missing", target_cost=4)
    assert progress_table(data, attempt_ids=[]).empty
    output.write_text(output.read_text() + "\n")
    with pytest.raises(ValueError, match="changed"):
        progress_table(data)


def test_time_limited_results_are_not_implementation_speedup_evidence(serial_campaign):
    _, manifest, bindings, output = serial_campaign("nn", budget={"start_cities": [0], "time_limit_s": 10})
    document = manifest.to_dict()
    base = document["experiments"][0]["executions"][0]
    document["experiments"][0]["executions"] = [{**base, "implementation": name}
        for name in ("visited-scan-v1", "remaining-array-v1")]
    runner.run_manifest(Manifest(document), instance_paths=bindings, output_path=output)
    pairs = paired_comparisons(read_shards([output]), baseline_implementation="visited-scan-v1")
    assert pairs["reason"].tolist() == ["time_limited_quality_run"]
    assert pairs["speedup"].isna().all()


def test_large_work_counts_do_not_round_when_time_only_rows_are_present(serial_campaign, scripted_registry):
    count = 2**53 + 1
    _, manifest, bindings, output = serial_campaign("ga", budget={"max_generations": count})
    registry, _ = scripted_registry("ga")
    runner.run_manifest(manifest, instance_paths=bindings, output_path=output, registry=registry)
    _, timed, timed_bindings, timed_output = serial_campaign("sa", budget={"time_limit_s": 1})
    # Unavailable production SA is still a truthful planned, failed observation.
    runner.run_manifest(timed, instance_paths=timed_bindings, output_path=timed_output)
    data = read_shards([output, timed_output])
    assert data.trials["budget_count"].tolist() == [count, None]
    assert json.loads(data.trials.iloc[0]["budget_summary_json"])["max_generations"] == count
    assert data.trials.iloc[1]["budget_mode"] == "time"
    assert "generations=" + str(count) in budget_label(data.trials.iloc[0])
    assert "time=1s" in budget_label(data.trials.iloc[1])
    assert "proposals" not in budget_label(data.trials.iloc[1])


def test_history_report_cli_is_bounded_and_retains_targets(serial_campaign, tmp_path):
    record, manifest, bindings, output = serial_campaign("nn")
    runner.run_manifest(manifest, instance_paths=bindings, output_path=output)
    before = output.read_bytes()
    report_path = tmp_path / "history-report"
    process = subprocess.run([sys.executable, "-m", "tsp_hpc", "analyze", str(output), "--output", str(report_path),
                              "--target", f"{record.instance.instance_id}=4"],
                             cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stderr
    report = json.loads((report_path / "report.json").read_text())
    assert "history-1.png" in report["figures"]
    assert (report_path / "history-1.png").read_bytes().startswith(b"\x89PNG")
    assert len(pd.read_csv(report_path / "progress.csv")) >= 1
    assert pd.read_csv(report_path / "time-to-target.csv")["reached"].all()
    assert report["configuration"]["target_costs"] == {record.instance.instance_id: 4.}
    assert output.read_bytes() == before
    with pytest.raises(AnalysisLimitError):
        write_report([output], output_dir=tmp_path / "too-small", max_history_points=1)
    assert not (tmp_path / "too-small").exists()
