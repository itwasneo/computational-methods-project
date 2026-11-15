"""Shared, explicitly non-scientific adapters for infrastructure tests only."""

from copy import deepcopy

import numpy as np
import pytest

from tsp_hpc.problem.objective import tour_cost
from tsp_hpc.problem.instances import fixture, save_instance
from tsp_hpc.experiments.manifests import Manifest
from tsp_hpc.solvers.configuration import VARIANTS
from tsp_hpc.solvers.registry import SolverRegistry, SolverSpec
from tsp_hpc.solvers.results import ProgressPoint, SolverResult, WorkCounters


@pytest.fixture
def serial_campaign(tmp_path):
    counter = 0
    def make(algorithm, *, budget=None, history=True, repetitions=None, instance_name="square"):
        nonlocal counter
        counter += 1
        record = fixture(instance_name)
        source = tmp_path / f"input-{counter}.json"
        save_instance(record, source)
        defaults = {"nn": {"start_cities": [2, 0]}, "ga": {"max_generations": 1}, "sa": {"max_proposals": 3}}
        experiment = {"instance_id": record.instance.instance_id, "algorithm": algorithm,
                      "solver_seed": 2**63 + 7, "budget": defaults[algorithm] if budget is None else budget,
                      "record_history": history, "repetitions": [0] if repetitions is None else repetitions,
                      "executions": [{"backend": "serial", "resources": {"profile": "test-only"}}]}
        if algorithm != "nn":
            experiment["logical_search_ids"] = ["second", "first", "last"]
        if algorithm == "ga":
            experiment["parameters"] = {"population_size": 3}
        manifest = Manifest({"schema_version": 1, "instances": [record.descriptor], "experiments": [experiment]})
        return record, manifest, {record.instance.instance_id: source}, tmp_path / f"runs-{counter}.jsonl"
    return make


@pytest.fixture
def scripted_registry():
    def make(algorithm, *, builder=None, fail_at=()):
        calls = []
        implementation = f"test-only-{algorithm}"

        class ScriptedAdapter:
            def __init__(self, prepared):
                self.prepared = prepared

            @property
            def metadata(self):
                return {**self.prepared.metadata, "algorithm": algorithm, "variant": VARIANTS[algorithm],
                        "implementation": implementation, "backend": "serial", "solver_warmup_time_s": 0.}

            def solve(self, instance, **request):
                index = len(calls)
                calls.append(deepcopy(request))
                if index in fail_at:
                    raise ArithmeticError("test-only injected logical failure")
                if builder is not None:
                    return builder(instance, request, index)
                # Scripted counters/tours are NOT GA/SA implementations or evidence.
                tour = np.roll(np.arange(instance.n_cities, dtype=np.int64), request["seed"] % instance.n_cities)
                cost = tour_cost(instance, tour)
                budget, parameters = request["budget"], request["parameters"]
                if algorithm == "ga":
                    count = budget["max_generations"]
                    full = parameters["population_size"] + count * (parameters["population_size"] - parameters["elite_count"])
                    work = WorkCounters(ga_generations_completed=count, full_tour_evaluations=full)
                else:
                    count = budget["max_proposals"] if instance.n_cities >= 4 else 0
                    work = WorkCounters(sa_proposals_evaluated=count,
                                        full_tour_evaluations=1 + count // parameters["cost_check_interval"])
                reason = "algorithm_stop" if algorithm == "sa" and instance.n_cities < 4 and budget["max_proposals"] else "work_limit"
                history = (ProgressPoint(0., work, cost),) if request["record_history"] else None
                return SolverResult("ok", tour, cost, work, reason, history=history)

        spec = SolverSpec(algorithm, implementation, ScriptedAdapter, "independent",
                          supports_time=True, supports_history=True, native=False)
        return SolverRegistry([spec], defaults={algorithm: implementation}), calls
    return make
