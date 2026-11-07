# Shared TSP data and objective

**Status:** task #5 implements the instance, coordinate/tour validation, and independent objective portion of the [shared contracts](contracts.md). [Manifest and identity planning](manifests.md) is implemented separately in task #6. Solvers, result records, loaders, and optimized distance backends remain separate work.

## Public API

| Symbol | Responsibility |
| --- | --- |
| `tsp_hpc.data.TSPInstance(instance_id, coordinates, distance_kind="euclidean_2d")` | Validate metadata and coordinates, then take an owned, read-only coordinate copy |
| `validate_coordinates(coordinates)` | Require a plain, C-contiguous, native `float64` NumPy array with shape `(n, 2)`, `n >= 2`, and finite values |
| `validate_city_index(index, n_cities)` | Accept an in-range Python/NumPy integer city ID; reject booleans; return a Python `int` |
| `validate_tour(tour, n_cities)` | Require a plain, C-contiguous, native `int64` NumPy permutation of `0..n-1`, shape `(n,)` |
| `tsp_hpc.objective.edge_cost(instance, city_a, city_b)` | Validate indices and evaluate one edge under the instance's convention |
| `tour_cost(instance, tour)` | Validate the tour and independently accumulate all edges, including the closing edge |
| `validate_solution(instance, tour, reported_cost)` | Check a reported cost against the independent reference and return the recomputed cost |

Validation functions do not convert, reorder, or mutate arrays. Normalize lists, array subclasses (including memory maps), non-native dtypes, or strided layouts explicitly in a builder/loader before calling this API. Do not coerce an invalid solver tour into validity. Masked arrays are not supported: masks could hide invalid values.

`TSPInstance.n_cities` is derived from the coordinate shape. The caller supplies a non-blank logical ID; `tsp_hpc.identity.make_instance_id()` provides content-based IDs under the [manifest identity rules](manifests.md#identity-rules). The frozen record prevents field reassignment and uses object-identity equality rather than elementwise NumPy equality. Its coordinate copy isolates it from subsequent changes to the caller's array. Treat that copy as immutable, including its flags and shape; NumPy's read-only flag is an accidental-write guard, not protection against deliberate bypass.

Duplicate coordinates, collinear cities, and zero-cost tours are valid. Different city IDs remain different visits even when their coordinates coincide. A two-city tour counts both the outward and return edge. Tours have implicit closure; an array of length `n + 1` repeating the start is rejected.

## Example

```python
import numpy as np

from tsp_hpc.data import TSPInstance
from tsp_hpc.objective import tour_cost, validate_solution

instance = TSPInstance(
    "triangle-3-4-5",
    np.array([[0.0, 0.0], [3.0, 0.0], [3.0, 4.0]], dtype=np.float64),
)
tour = np.array([0, 2, 1], dtype=np.int64)

assert tour_cost(instance, tour) == 12.0  # 5 + 4 + closing edge 3
assert validate_solution(instance, tour, reported_cost=12.0) == 12.0
```

## Distance conventions and numerical boundaries

- **`euclidean_2d`:** unrounded `hypot(dx, dy)` in float64 precision. Using `hypot` avoids unnecessary overflow from squaring large but otherwise representable components.
- **`tsplib_euc_2d`:** mathematical `floor(r + 0.5)` applied to each edge, never to the tour total. The implementation separates the integer and fractional parts to avoid premature floating-point rounding of `r + 0.5`. For example, the float immediately below `0.5` rounds to zero, while `2.5` rounds to three. Large integral distances are not accidentally incremented.
- Costs are returned as Python floats and accumulated sequentially in stored-tour order with float64 precision. Rounded edge costs and totals must lie in `[0, 2**53]`, the contiguous exact-integer range. Even representable larger integers are outside this deliberately conservative supported range. Check the remaining range **before** each addition so a one-unit overflow cannot silently disappear.
- Instance construction checks finite coordinates, not every possible pairwise edge. Derived-distance and total checks run when those values are evaluated; no quadratic preprocessing or allocation is required.
- Non-finite derived distances/totals or unsupported rounded ranges raise `NumericalError`, an `ArithmeticError` subclass. Incompatible input types raise `TypeError`; invalid shapes, values, permutations, or reported costs raise `ValueError`.

Reported rounded costs must match exactly, including integer-valued reports near the float64 boundary. Unrounded reports use:

```text
abs(reported - reference) <= 1e-12 + 1e-10 * abs(reference)
```

These are validation tolerances only; they must not be reused for solver ties or SA acceptance. Reported costs must be real, finite and non-negative; booleans are rejected. Validation returns the recomputed value, not the possibly approximate reported value.

## Reference versus optimized computation

This is a deliberately independent, ordinary-Python coordinate reference. It does not call solver fitness caches, SA deltas, or a future distance backend. It allocates no dense distance matrix and performs no JIT compilation. Permutation validation takes O(n) time and temporary space; complete tour costing is O(n).

Use these checks at input/output boundaries, not on every hot-loop proposal. Task #8 will add Numba-compatible dense/on-demand paths and test them against this reference. Search work counters, timing, and persistence belong to solvers and the runner, not these utilities.

## Tests

Run the full suite with `uv run --locked pytest`, or focus on these utilities:

```bash
uv run --locked pytest tests/test_data.py tests/test_objective.py
```

Coverage includes hand-calculated tours, all small permutations, deterministic distance ties, closing edges, duplicate coordinates, zero costs, strict dtypes/layouts, invalid indices/permutations, half-integer rounding boundaries, extreme finite coordinates, arithmetic overflow, exact-integer limits, and reported-cost tolerances. The same suite runs in CI.
