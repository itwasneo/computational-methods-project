"""Preflight is bounded planning, never a second execution path."""

from dataclasses import replace
import json

import pytest

pytest.importorskip("fastapi")
from tsp_hpc.experiments.manifests import Manifest
from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.requests import ScientificConfigurationRequest
from tsp_hpc.interface.planning import PreflightStore
from tsp_hpc.solvers.registry import default_registry


def configuration(algorithm="nn", **changes):
    return {"algorithm": algorithm, "parameters": {},
            "budget": {"nn": {"start_cities": [2, 0], "time_limit_s": .1}, "ga": {"max_generations": 0}, "sa": {"time_limit_s": .1}}[algorithm],
            "logical_search_ids": None if algorithm == "nn" else ["first", "second"],
            "implementations": [None], "distance_modes": ["on_demand"], "repetitions": [0],
            "solver_seed": str(2**64 - 1), "max_matrix_bytes": 0,
            "record_history": True, "history_policy": None, **changes}


def snapshot(session):
    client, _, context, _, _ = session
    value = client.post("/api/instances/generate", json={**context, "generation": {
        "generator": "uniform-v1", "n_cities": 4, "seed": "7", "side_length": 1.}})
    assert value.status_code == 201, value.text
    return value.json()["instance_snapshot_id"]


def request(session, identifier, config=None):
    client, _, context, _, _ = session
    caps = client.post("/api/runs/capabilities", json=context)
    assert caps.status_code == 200, caps.text
    return {**context, "instance_snapshot_id": identifier,
            "capabilities_revision": caps.json()["capabilities_revision"], "configuration": config or configuration()}


def test_capabilities_and_preflight_do_not_prepare_warm_generate_or_solve(execution_session, monkeypatch):
    client, app, context, _, output = execution_session
    identifier = snapshot(execution_session)
    from tsp_hpc.problem import distances, instances
    from tsp_hpc.solvers.ga.solver import GeneticAlgorithmSolver
    from tsp_hpc.solvers.nn import solver
    def forbidden(*args, **kwargs):
        raise AssertionError("preflight must not execute numerical work")
    monkeypatch.setattr(distances, "prepare_distances", forbidden)
    monkeypatch.setattr(instances, "uniform_instance", forbidden)
    monkeypatch.setattr(solver.NearestNeighbourSolver, "__init__", forbidden)
    monkeypatch.setattr(GeneticAlgorithmSolver, "__init__", forbidden)
    before = sorted(str(p) for p in output.rglob("*"))
    body = request(execution_session, identifier)
    capabilities = client.post("/api/runs/capabilities", json=context).json()
    assert {item["algorithm"] for item in capabilities["implementations"]} == {"nn", "ga"}
    assert all(item["available"] for item in capabilities["implementations"])
    result = client.post("/api/runs/preflight", json=body)
    assert result.status_code == 200, result.text
    plan = result.json()
    assert plan["budget_scope"] == "per-logical-invocation"
    assert plan["configuration"]["implementations"] == ["visited-scan-v1"]
    assert plan["summed_search_allowance_s"] == .1
    assert plan["maximum_history_points"] == 2
    assert sorted(str(p) for p in output.rglob("*")) == before
    stored = app.state.preflights.validate(plan["preflight_id"], context["session_id"])
    trial, = Manifest(stored["manifest"]).expand()
    assert plan["trials"][0]["trial_seed"] == str(trial.trial_seed)
    assert plan["trials"][0]["invocations"] == [{**trial.invocations()[0], "seed": str(trial.trial_seed)}]
    assert trial.search["solver_seed"] == 2**64 - 1
    stored["manifest"].clear()
    assert app.state.preflights.validate(plan["preflight_id"], context["session_id"])["manifest"]


@pytest.mark.parametrize("config,code", [
    (configuration("ga", implementations=["unknown"]), "unsupported_execution"),
    (configuration("sa"), "unsupported_execution"),
    (configuration(implementations=["unknown"]), "unsupported_execution"),
    (configuration(implementations=[None, "visited-scan-v1"]), "invalid_plan"),
    (configuration(budget={"start_cities": [4]}), "invalid_plan"),
    (configuration(distance_modes=["dense"]), "resource_limit"),
    (configuration(budget={"start_cities": [0], "time_limit_s": 301.}), "resource_limit"),
    (configuration(repetitions=list(range(33))), "resource_limit"),
])
def test_unavailable_or_invalid_plans(execution_session, config, code):
    identifier = snapshot(execution_session)
    client, _, _, _, _ = execution_session
    result = client.post("/api/runs/preflight", json=request(execution_session, identifier, config))
    assert result.status_code in (413, 422), result.text
    assert result.json()["code"] == code


def test_expansion_is_bounded_before_materialization(execution_session, monkeypatch):
    identifier = snapshot(execution_session)
    client, _, _, _, _ = execution_session
    body = request(execution_session, identifier, configuration(repetitions=list(range(33))))
    def forbidden(*_):
        raise AssertionError("must not expand")
    monkeypatch.setattr(Manifest, "expand", forbidden)
    assert client.post("/api/runs/preflight", json=body).status_code == 413


def test_stale_snapshot_capabilities_and_session_and_expiration(execution_session, monkeypatch):
    identifier = snapshot(execution_session)
    client, app, context, _, output = execution_session
    body = request(execution_session, identifier)
    result = client.post("/api/runs/preflight", json=body).json()
    store = app.state.preflights
    with pytest.raises(InterfaceError, match="Session"):
        store.validate(result["preflight_id"], "other")
    from tsp_hpc.interface import runtime
    monkeypatch.setattr(runtime, "code_identity", lambda: "changed-code")
    assert client.post("/api/runs/preflight", json=body).json()["code"] == "preflight_stale"
    with pytest.raises(InterfaceError, match="capabilities"):
        store.validate(result["preflight_id"], context["session_id"])
    monkeypatch.undo()
    store._clock = lambda: float("inf")
    with pytest.raises(InterfaceError, match="expired"):
        store.validate(result["preflight_id"], context["session_id"])
    (output / ".tsp-local/snapshots" / identifier / "instance.json").write_text("{}")
    assert client.post("/api/runs/preflight", json=body).json()["code"] == "snapshot_stale"


def test_disabled_jit_is_a_reported_preflight_rejection(execution_session, monkeypatch):
    import numba
    identifier = snapshot(execution_session)
    client, _, context, _, _ = execution_session
    monkeypatch.setattr(numba.config, "DISABLE_JIT", 1)
    caps = client.post("/api/runs/capabilities", json=context).json()
    assert all(not item["available"] and item["unavailable_reason"] == "native_disabled" for item in caps["implementations"])
    body = request(execution_session, identifier)
    assert client.post("/api/runs/preflight", json=body).json()["code"] == "unsupported_execution"


def test_independent_logical_plans_use_logical_not_trial_seeds(execution_session, scripted_registry):
    identifier = snapshot(execution_session)
    _, app, context, _, _ = execution_session
    for algorithm in ("ga", "sa"):
        registry = default_registry() if algorithm == "ga" else scripted_registry(algorithm)[0]
        planner = PreflightStore(app.state.execution, app.state.snapshots, registry=registry)
        caps = planner.capabilities()
        config = ScientificConfigurationRequest.model_validate(configuration(algorithm, history_policy={"max_points": 1024}))
        plan = planner.preflight(identifier, config, caps["capabilities_revision"], context["session_id"])
        trial, = plan["trials"]
        assert [item["logical_id"] for item in trial["invocations"]] == ["first", "second"]
        assert all(item["seed"] != trial["trial_seed"] for item in trial["invocations"])
        if algorithm == "ga":
            assert trial["implementation"] == registry.resolve("ga").implementation
        else:
            assert plan["summed_search_allowance_s"] == .2
