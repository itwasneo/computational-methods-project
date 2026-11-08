"""One cold/cache-reload integration check using production numerical kernels."""

import os
import subprocess
import sys


def test_generic_cpu_replay_cache_and_rng_isolation(tmp_path):
    program = """
import math
import random
import numpy as np
from tsp_hpc.data import TSPInstance
from tsp_hpc.distances import prepare_distances
from tsp_hpc.instances import fixture
from tsp_hpc.nearest_neighbour import NearestNeighbourSolver, implementation_names, solve
from tsp_hpc.objective import edge_cost

numpy_before, python_before = np.random.get_state(), random.getstate()
# Nonzero native-hypot regression: a coincident NN fixture alone would not
# detect numerical problems on the generic CPU target.
probe = TSPInstance('probe', np.array([[0., 0.], [5865.277089690506, 5153.210738088601]]))
costs = [prepare_distances(probe, mode=mode).edge_cost(0, 1)
         for mode in ('dense', 'on_demand')]
assert costs[0] == costs[1]
assert math.isclose(costs[0], edge_cost(probe, 0, 1), rel_tol=1e-10, abs_tol=1e-12)

instance = fixture('coincident').instance
for implementation in implementation_names():
    for mode in ('dense', 'on_demand'):
        solver = NearestNeighbourSolver(prepare_distances(instance, mode=mode),
                                        implementation=implementation)
        # Inspect the actually selected dispatcher, not a baseline module alias.
        kernel = solver._kernel
        signatures = tuple(kernel.signatures)
        assert kernel.nopython_signatures
        result = solver.solve(instance, seed=42, budget={'start_cities': [3, 1]}, parameters={})
        assert tuple(kernel.signatures) == signatures
        assert result.best_tour.tolist() == [3, 0, 1, 2] and result.best_cost == 0
        assert result.work.nn_starts_completed == result.work.full_tour_evaluations == 2

# Include the convenience entry point in the same RNG-isolation check.
result = solve(fixture('square').instance, seed=42, budget={'start_cities': [3, 0]}, parameters={})
assert result.best_tour.tolist() == [3, 0, 1, 2] and result.best_cost == 4
numpy_after = np.random.get_state()
assert numpy_before[0] == numpy_after[0]
assert (numpy_before[1] == numpy_after[1]).all()
assert numpy_before[2:] == numpy_after[2:]
assert python_before == random.getstate()
print('native runtime passed')
"""
    for attempt, hash_seed in enumerate(("1", "76543")):
        result = subprocess.run(
            [sys.executable, "-B", "-c", program], cwd=tmp_path,
            env={**os.environ, "PYTHONHASHSEED": hash_seed, "NUMBA_CPU_NAME": "generic",
                 "NUMBA_CPU_FEATURES": "", "NUMBA_CACHE_DIR": str(tmp_path / "cache"),
                 "NUMBA_DEBUG_CACHE": "1"},
            capture_output=True, text=True, check=True, timeout=90,
        )
        assert "native runtime passed" in result.stdout
        event = "data saved" if attempt == 0 else "data loaded"
        assert f"[cache] {event}" in result.stdout
