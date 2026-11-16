"""All production registrations automatically join the same handoff suite.

Registering GA/SA must not require edits here, to runner or to analysis. Their
operator/scientific tests remain separate; tiny optima are not universal heuristic
guarantees. No test adapter is installed in the production registry.
"""

import json

import numpy as np
import pytest

from tsp_hpc.analysis import read_shards, progress_table
from tsp_hpc.experiments.manifests import Manifest
from tsp_hpc.experiments.runner import run_manifest
from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.problem.instances import fixture, fixture_names, uniform_instance
from tsp_hpc.solvers.conformance import check_adapter
from tsp_hpc.solvers.registry import default_registry

REGISTRY = default_registry()
SPECS = [REGISTRY.resolve(item["algorithm"], implementation=item["implementation"], backend=item["backend"]) for item in REGISTRY.capabilities()]


def budget_for(algorithm, n, zero=False):
    return {"nn": {"start_cities": [] if zero else [n - 1, 0]},
            "ga": {"max_generations": 0 if zero else 2},
            "sa": {"max_proposals": 0 if zero else 4}}[algorithm]


def parameters_for(algorithm):
    return {"nn": {}, "ga": {"population_size": 4, "tournament_size": 2},
            "sa": {"cost_check_interval": 2}}[algorithm]


@pytest.mark.parametrize("spec", SPECS, ids=lambda item: f"{item.algorithm}-{item.implementation}")
@pytest.mark.parametrize("mode", ["dense", "on_demand"])
@pytest.mark.parametrize("name", fixture_names())
@pytest.mark.parametrize("zero", [False, True])
def test_registered_fixed_work_ownership_replay_and_history(spec, mode, name, zero):
    instance = fixture(name).instance
    if spec.backend == "serial":
        assert spec.supports_time and spec.supports_history, "serial baseline requires time/history"
    result = check_adapter(spec, instance, seed=2**63 + 7, budget=budget_for(spec.algorithm, instance.n_cities, zero),
                           parameters=parameters_for(spec.algorithm), distance_mode=mode)
    assert result.status == ("no_solution" if spec.algorithm == "nn" and zero else "ok")


@pytest.mark.parametrize("spec", SPECS, ids=lambda item: f"{item.algorithm}-{item.implementation}")
@pytest.mark.parametrize("mode", ["dense", "on_demand"])
@pytest.mark.parametrize("limit", [1e-12, 1.])
def test_registered_time_only_and_combined_stops(spec, mode, limit):
    instance = fixture("square").instance
    budget = budget_for(spec.algorithm, instance.n_cities)
    if spec.algorithm != "nn" and limit == 1e-12:
        budget = {}  # NN deliberately always retains its finite start list.
    if not spec.supports_time or not spec.supports_history:
        with pytest.raises(ValueError, match="fixed work"):
            check_adapter(spec, instance, seed=7, budget={**budget, "time_limit_s": limit},
                          record_history=True)
        return
    result = check_adapter(spec, instance, seed=7, budget={**budget, "time_limit_s": limit},
                           parameters=parameters_for(spec.algorithm), distance_mode=mode,
                           record_history=True, history_policy={"interval_s": 0, "max_points": 4096})
    assert result.history is not None
    if result.termination_reason == "time_limit":
        assert result.search_time_s >= limit


@pytest.mark.parametrize("spec", SPECS, ids=lambda item: f"{item.algorithm}-{item.implementation}")
@pytest.mark.parametrize("change", ["seed", "parameters", "budget", "deadline", "history_flag", "history_policy"])
def test_registered_adapters_reject_bad_direct_requests(spec, change):
    instance = fixture("square").instance
    adapter = spec.factory(prepare_distances(instance, mode="on_demand"))
    request = {"seed": 7, "budget": budget_for(spec.algorithm, 4), "parameters": parameters_for(spec.algorithm)}
    if change == "seed": request["seed"] = True
    elif change == "parameters": request["parameters"]["unknown"] = 1
    elif change == "budget": request["budget"]["unknown"] = 1
    elif change == "deadline": request["budget"]["time_limit_s"] = float("inf")
    elif change == "history_flag": request["record_history"] = 1
    elif change == "history_policy": request["history_policy"] = {"max_points": 1}
    with pytest.raises((TypeError, ValueError)):
        adapter.solve(instance, **request)


@pytest.mark.parametrize("spec", SPECS, ids=lambda item: f"{item.algorithm}-{item.implementation}")
@pytest.mark.parametrize("mode", ["dense", "on_demand"])
def test_registered_manifest_to_history_pipeline(spec, mode, serial_campaign):
    _, manifest, bindings, output = serial_campaign(spec.algorithm)
    document = manifest.to_dict()
    document["experiments"][0]["executions"][0].update(implementation=spec.implementation, distance_mode=mode, backend=spec.backend)
    if not spec.supports_history:
        document["experiments"][0]["record_history"] = False
        document["experiments"][0].pop("history_policy", None)
    manifest = Manifest(document)
    summary = run_manifest(manifest, instance_paths=bindings, output_path=output)
    assert summary["counts"] == {"completed": 1, "failed": 0}
    analysis = read_shards([output], expected_manifest=manifest)
    assert analysis.trials["usable"].all() and not analysis.diagnostics
    points = progress_table(analysis)
    if not spec.supports_history:
        assert points.empty
        return
    assert not points.empty
    trial = analysis.trial(analysis.trials.iloc[0]["attempt_id"])
    assert json.loads(points.iloc[-1]["cumulative_work_json"]) == trial["result"]["work"]


def test_conformance_is_bounded_not_a_benchmark():
    with pytest.raises(ValueError, match="tiny fixtures"):
        check_adapter(SPECS[0], uniform_instance(65, seed=7).instance, seed=7, budget={"start_cities": [0]})


def test_harness_detects_global_rng_use_and_request_mutation(scripted_registry):
    instance = fixture("square").instance
    base, _ = scripted_registry("sa")
    adapter = base.resolve("sa").factory(prepare_distances(instance, mode="on_demand"))
    state = np.random.get_state()
    def bad_rng(instance, request, index):
        np.random.random()
        return adapter.solve(instance, **request)
    registry, _ = scripted_registry("sa", builder=bad_rng)
    try:
        with pytest.raises(AssertionError, match="global RNG"):
            check_adapter(registry.resolve("sa"), instance, seed=7, budget={"max_proposals": 0})
    finally:
        np.random.set_state(state)
    def bad_mutation(instance, request, index):
        result = adapter.solve(instance, **request)
        request["parameters"]["cooling_factor"] = .5
        return result
    registry, _ = scripted_registry("sa", builder=bad_mutation)
    with pytest.raises(AssertionError, match="mutated caller"):
        check_adapter(registry.resolve("sa"), instance, seed=7, budget={"max_proposals": 0})
