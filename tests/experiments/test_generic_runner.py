"""Real NN and test-only independent searches through the complete v3 path."""

from copy import deepcopy
import json

import pytest

from tsp_hpc.analysis import read_shards
from tsp_hpc.analysis.core import ShardValidationError
from tsp_hpc.experiments.runner import run_manifest
from tsp_hpc.solvers.registry import default_registry


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("algorithm", ["ga", "sa"])
@pytest.mark.parametrize("history", [False, True])
def test_logical_seeds_ties_aggregation_and_scripted_record_reading(serial_campaign, scripted_registry, algorithm, history):
    _, manifest, bindings, output = serial_campaign(algorithm, history=history)
    registry, calls = scripted_registry(algorithm)
    summary = run_manifest(manifest, instance_paths=bindings, output_path=output, registry=registry)
    assert summary["counts"] == {"completed": 1, "failed": 0}
    trial = records(output)[0]
    plan = manifest.expand()[0]
    assert [call["seed"] for call in calls] == [item.stream.seed for item in plan.logical_searches]
    assert all(call["seed"] != plan.trial_seed for call in calls)
    assert [entry["logical_id"] for entry in trial["logical_results"]] == ["second", "first", "last"]
    result = trial["result"]
    assert result["best_logical_index"] == 0
    assert result["best_tour"] == trial["logical_results"][0]["result"]["best_tour"]
    assert result["work"]["full_tour_evaluations"] == (15 if algorithm == "ga" else 3)
    assert result["history"] is None  # Histories belong to their original logical clocks.
    assert all((entry["result"]["history"] is not None) == history for entry in trial["logical_results"])
    assert trial["budget_scope"] == "per-logical-invocation"
    data = read_shards([output], expected_manifest=manifest)
    assert data.trials["usable"].all() and not data.diagnostics
    assert data.trial(trial["attempt_id"]) == trial
    assert trial["adapter"]["implementation"] not in {
        item["implementation"] for item in default_registry().capabilities()
    }


def test_failure_keeps_accepted_prefix_not_a_partial_success(serial_campaign, scripted_registry):
    _, manifest, bindings, output = serial_campaign("ga", repetitions=[0, 1])
    registry, calls = scripted_registry("ga", fail_at={1})
    summary = run_manifest(manifest, instance_paths=bindings, output_path=output, registry=registry)
    failed, passed, _ = records(output)
    assert summary["counts"] == {"completed": 1, "failed": 1}
    assert failed["result"] is None and failed["error"]["logical_index"] == 1
    assert len(failed["logical_results"]) == 1
    assert len(passed["logical_results"]) == 3 and len(calls) == 5
    data = read_shards([output])
    assert data.trials["usable"].tolist() == [False, True]
    assert data.diagnostics[0]["code"] == "failed_trials"


@pytest.mark.parametrize("budget", [{"start_cities": [2, 0]}, {"start_cities": [2, 0], "time_limit_s": 10},
                                    {"start_cities": [2, 0], "time_limit_s": 1e-12}])
def test_real_nn_history_and_deadline_roundtrip(serial_campaign, budget):
    _, manifest, bindings, output = serial_campaign("nn", budget=budget)
    run_manifest(manifest, instance_paths=bindings, output_path=output)
    data = read_shards([output])
    trial = data.trial(data.trials.iloc[0]["attempt_id"])
    logical = trial["logical_results"][0]
    assert logical["kind"] == "batch" and logical["logical_id"] is None
    assert logical["seed"] == manifest.expand()[0].trial_seed
    assert logical["result"]["history"] is not None
    assert trial["outcome"] == "completed"
    if budget.get("time_limit_s") == 1e-12:
        assert logical["result"]["status"] == "no_solution"
        assert logical["result"]["history"] == []
        assert logical["result"]["termination_reason"] == "time_limit"


@pytest.mark.parametrize("fault", ["seed", "order", "missing", "aggregate", "tie", "timing", "history",
                                  "nested-search", "nested-instance", "nested-plan", "nested-history"])
def test_v3_corruption_is_source_localized(serial_campaign, scripted_registry, fault):
    _, manifest, bindings, output = serial_campaign("ga")
    registry, _ = scripted_registry("ga")
    run_manifest(manifest, instance_paths=bindings, output_path=output, registry=registry)
    rows = records(output)
    trial = rows[0]
    logical = trial["logical_results"][0]
    if fault == "seed": logical["seed"] += 1
    elif fault == "order": trial["logical_results"].reverse()
    elif fault == "missing": trial["logical_results"].pop()
    elif fault == "aggregate": trial["result"]["work"]["full_tour_evaluations"] += 1
    elif fault == "tie": trial["result"]["best_logical_index"] = 1
    elif fault == "timing": logical["timings"]["search_time_s"] = 100
    elif fault == "history": logical["result"]["history"][-1]["best_cost"] += 1
    elif fault == "nested-search": trial["requested_plan"]["search"] = []
    elif fault == "nested-instance": trial["requested_plan"]["search"]["instance"] = []
    elif fault == "nested-plan": trial["requested_plan"] = None
    elif fault == "nested-history": logical["result"]["history"] = {}
    output.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ShardValidationError) as caught:
        read_shards([output])
    assert caught.value.line == 1
    assert caught.value.source_id == str(output)


@pytest.mark.parametrize("algorithm,initialized", [("ga", True), ("sa", True), ("sa", False)])
def test_timed_logical_allowances_and_partial_initialization(serial_campaign, scripted_registry, monkeypatch,
                                                           algorithm, initialized):
    import numpy as np
    import tsp_hpc.experiments.runner as runner
    from tsp_hpc.problem.objective import tour_cost
    from tsp_hpc.solvers.results import ProgressPoint, SolverResult, WorkCounters
    offset = [0.]
    real_clock = runner.perf_counter
    monkeypatch.setattr(runner, "perf_counter", lambda: real_clock() + offset[0])
    def timed_script(instance, request, index):
        # Synthetic elapsed time to test accounting, never a timing measurement.
        seconds = request["budget"]["time_limit_s"]
        offset[0] += seconds
        work = WorkCounters(full_tour_evaluations=1 if initialized else 0)
        tour = np.arange(instance.n_cities, dtype=np.int64) if initialized else None
        cost = tour_cost(instance, tour) if initialized else None
        history = (ProgressPoint(seconds, work, cost),) if initialized else ()
        return SolverResult("ok" if initialized else "no_solution", tour, cost, work, "time_limit",
                            search_time_s=seconds, history=history)
    budget = {"time_limit_s": 1}
    if algorithm == "ga":
        budget["max_generations"] = 0  # Partial initialization still yields a time stop.
    _, manifest, bindings, output = serial_campaign(algorithm, budget=budget)
    registry, _ = scripted_registry(algorithm, builder=timed_script)
    run_manifest(manifest, instance_paths=bindings, output_path=output, registry=registry)
    trial = records(output)[0]
    assert trial["outcome"] == "completed", trial["error"]
    assert trial["timings"]["search_time_s"] == 3
    assert [entry["timings"]["search_time_s"] for entry in trial["logical_results"]] == [1] * 3
    assert trial["result"]["termination_reason"] == "time_limit"
    assert trial["result"]["work"]["full_tour_evaluations"] == (3 if initialized else 0)
    data = read_shards([output])
    assert data.trials["usable"].iloc[0] == initialized


def test_configuration_is_detached_between_invocations(serial_campaign, scripted_registry):
    _, manifest, bindings, output = serial_campaign("sa")
    base_registry, _ = scripted_registry("sa")
    original = base_registry.resolve("sa").factory
    def builder(instance, request, index):
        # The default script uses only its bound metadata; bind a fresh dummy via
        # the actual prepared factory below instead of duplicating scientific rules.
        result = captured[0].solve(instance, **request)
        request["budget"]["max_proposals"] = 999
        request["parameters"]["cooling_factor"] = .5
        return result
    captured = []
    from tsp_hpc.problem.distances import prepare_distances
    from tsp_hpc.problem.instances import fixture
    captured.append(original(prepare_distances(fixture("square").instance, mode="on_demand")))
    registry, calls = scripted_registry("sa", builder=builder)
    before = deepcopy(manifest.to_dict())
    run_manifest(manifest, instance_paths=bindings, output_path=output, registry=registry)
    assert manifest.to_dict() == before
    assert all(call["budget"]["max_proposals"] == 3 for call in calls)
    assert all(call["parameters"]["cooling_factor"] == .999 for call in calls)
    assert read_shards([output]).trials["usable"].all()
