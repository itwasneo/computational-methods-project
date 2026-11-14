"""Native dispatcher enforcement and structured observation validation."""

from copy import deepcopy

from numba import jit, njit
import numba
import pytest

from tsp_hpc.reproducibility.native import (
    NativeExecutionError, compiler_observation, verify_dispatchers,
    validate_compiler_observation, validate_native_observation,
)


def test_effective_settings_are_resolved_not_environment_reconstruction(monkeypatch):
    monkeypatch.setattr(numba.config, "BOUNDSCHECK", 1)
    monkeypatch.setattr(numba.config, "OPT", 0)
    value = compiler_observation()
    validate_compiler_observation(value)
    assert value["config"]["boundscheck"] is True
    assert value["config"]["optimization"] == "0"
    value["config"]["boundscheck"] = False
    assert compiler_observation()["config"]["boundscheck"] is True


def test_plain_unwarmed_and_unsafe_dispatchers_cannot_claim_native():
    with pytest.raises(NativeExecutionError, match="nopython"):
        verify_dispatchers((lambda value: value,))
    kernel = njit(lambda value: value + 1)
    with pytest.raises(NativeExecutionError, match="nopython"):
        verify_dispatchers((kernel,))
    assert kernel(1) == 2
    object_kernel = jit(forceobj=True)(lambda value: value + 1)
    assert object_kernel(1) == 2
    with pytest.raises(NativeExecutionError, match="nopython"):
        verify_dispatchers((object_kernel,))
    observation = {"required": True, "verified": True, "dispatchers": verify_dispatchers((kernel,))}
    validate_native_observation(observation)
    assert observation["dispatchers"][0]["nopython_signatures"]
    for option in ("fastmath", "parallel"):
        kernel.targetoptions[option] = True
        with pytest.raises(NativeExecutionError, match="serial correctness"):
            verify_dispatchers((kernel,))
        kernel.targetoptions[option] = False
    for dispatchers in ((), [], None):
        with pytest.raises(NativeExecutionError):
            verify_dispatchers(dispatchers)


@pytest.mark.parametrize("fault", ["object", "version", "optimization", "flag", "bounds", "extra"])
def test_malformed_compiler_observations(fault):
    value = deepcopy(compiler_observation())
    if fault == "object": value = []
    elif fault == "version": value["schema_version"] = True
    elif fault == "optimization": value["config"]["optimization"] = "_OptLevel(3)"
    elif fault == "flag": value["config"]["disable_jit"] = 0
    elif fault == "bounds": value["config"]["boundscheck"] = 1
    elif fault == "extra": value["config"]["unknown"] = 1
    with pytest.raises(ValueError):
        validate_compiler_observation(value)


def test_read_only_ingestion_rule_identity_includes_shared_scientific_validators():
    from tsp_hpc.interface.ingestion import RULE_FILES
    assert {"solvers/configuration.py", "solvers/validation.py", "reproducibility/native.py",
            "solvers/results.py", "analysis/progress.py"} <= set(RULE_FILES)
