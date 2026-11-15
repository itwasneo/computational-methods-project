"""Registration describes availability, not a second scientific schema."""

import subprocess
import sys

import pytest

from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.problem.instances import fixture
from tsp_hpc.solvers.configuration import normalize_budget, normalize_parameters
from tsp_hpc.solvers.registry import SolverRegistry, SolverSpec, default_registry


def test_registry_is_lightweight():
    process = subprocess.run([sys.executable, "-c", "import sys; import tsp_hpc.solvers.registry; "
                              "assert not {'numpy', 'numba', 'fastapi', 'pandas'} & sys.modules.keys()"],
                             capture_output=True, text=True, check=True)
    assert not process.stderr


def test_production_registry_exposes_only_real_nn_and_detached_capabilities():
    registry = default_registry()
    descriptions = registry.capabilities()
    assert [entry["implementation"] for entry in descriptions] == ["visited-scan-v1", "remaining-array-v1"]
    assert registry.resolve("nn").implementation == "visited-scan-v1"
    descriptions[0]["distance_modes"].clear()
    assert registry.capabilities()[0]["distance_modes"] == ["dense", "on_demand"]
    for algorithm in ("ga", "sa"):
        with pytest.raises(ValueError, match="unavailable"):
            registry.resolve(algorithm)


@pytest.mark.parametrize("changes", [
    {"implementation": "missing"}, {"backend": "multicore"},
    {"distance_kind": "tsplib_euc_2d"}, {"distance_mode": "auto"},
    {"timed": True}, {"record_history": True},
])
def test_unavailable_capabilities_fail_explicitly(changes):
    with pytest.raises(ValueError):
        default_registry().resolve("nn", **changes)


@pytest.mark.parametrize("mode", ["dense", "on_demand"])
def test_registered_factory_executes_real_adapter(mode):
    instance = fixture("square").instance
    prepared = prepare_distances(instance, mode=mode)
    for item in default_registry().capabilities():
        spec = default_registry().resolve("nn", implementation=item["implementation"], distance_mode=mode)
        adapter = spec.factory(prepared)
        result = adapter.solve(instance, seed=7, budget={"start_cities": [2, 0]}, parameters={})
        assert result.best_cost == 4
        assert result.best_tour.tolist() == [2, 1, 0, 3]
        assert adapter.metadata["implementation"] == spec.implementation


def test_registry_rejects_ambiguous_defaults_and_logical_units():
    spec = SolverSpec("ga", "test-only", lambda prepared: None, "independent", native=False)
    with pytest.raises(ValueError, match="duplicate"):
        SolverRegistry([spec, spec], defaults={"ga": "test-only"})
    for defaults in ({}, {"ga": "missing"}, {"ga": "test-only", "sa": "other"}):
        with pytest.raises(ValueError):
            SolverRegistry([spec], defaults=defaults)
    with pytest.raises(ValueError, match="logical mode"):
        SolverSpec("sa", "test-only", lambda prepared: None, "batch")
    defaults = {"ga": "test-only"}
    registry = SolverRegistry([spec], defaults=defaults)
    defaults.clear()
    assert registry.resolve("ga") is spec
    assert all(item["algorithm"] == "nn" for item in default_registry().capabilities())


def test_shared_configuration_owns_defaults_and_order():
    parameters = normalize_parameters("ga", {})
    assert parameters["population_size"] == 128
    parameters["population_size"] = 2
    assert normalize_parameters("ga", {})["population_size"] == 128
    original = {"start_cities": [2, 0]}
    budget = normalize_budget("nn", original, 4)
    budget["start_cities"].reverse()
    assert original == {"start_cities": [2, 0]}
