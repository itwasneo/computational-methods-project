"""Bounded exhaustive reference, independent of heuristic/native kernels.

This enumerates every oriented tour rooted at city zero in lexical order. Reverse
orientations are deliberately retained: stored-order float64 summation can differ
in its last bits. 'Exact' means exhaustive combinatorial search under the declared
objective, not an arbitrary-precision certificate for Euclidean distances.
"""

from dataclasses import dataclass
from itertools import permutations

import numpy as np

from tsp_hpc.problem.data import TSPInstance, validate_coordinates
from tsp_hpc.problem.objective import tour_cost
from tsp_hpc.reproducibility.identity import coordinate_checksum

MAX_EXACT_CITIES = 9


@dataclass(frozen=True)
class ExactReference:
    instance_id: str
    distance_kind: str
    coordinate_checksum: str
    tour: tuple[int, ...]
    cost: float
    tours_evaluated: int
    method: str = "exhaustive-rooted-oriented-v1"

    def reference(self):
        """One reference row for the shared reference_gaps analysis API."""
        return {"instance_id": self.instance_id, "distance_kind": self.distance_kind,
                "coordinate_checksum": self.coordinate_checksum,
                "kind": "exact", "cost": self.cost, "source": self.method}


def exact_reference(instance: TSPInstance) -> ExactReference:
    """Return a deterministic optimum for at most nine cities; never raise the cap.

    No JIT, heuristic calls, random draws, matrix preparation or persistence. Equal
    computed costs keep the first lexical rooted tour. The returned tuple owns no
    writable array aliases. Numerical failures propagate instead of skipping tours.
    """
    if not isinstance(instance, TSPInstance):
        raise TypeError("instance must be a TSPInstance")
    if instance.n_cities > MAX_EXACT_CITIES:
        raise ValueError(f"exact reference is limited to {MAX_EXACT_CITIES} cities")
    validate_coordinates(instance.coordinates)
    best, cost, evaluated = None, None, 0
    for tail in permutations(range(1, instance.n_cities)):
        tour = (0, *tail)
        candidate = tour_cost(instance, np.array(tour, dtype=np.int64))
        evaluated += 1
        if cost is None or candidate < cost:
            best, cost = tour, candidate
    assert best is not None and cost is not None  # TSPInstance requires at least two cities.
    return ExactReference(instance.instance_id, instance.distance_kind,
                          coordinate_checksum(instance.coordinates), best, cost, evaluated)
