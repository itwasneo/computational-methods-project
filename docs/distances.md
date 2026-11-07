# Distance backends and explicit preprocessing

**Status:** task #8 implements compiled CPU distance preparation, edge queries and full-tour evaluation. The [serial NN solver](nearest-neighbour.md) uses these backends; GA/SA, runner integration, performance campaigns and cluster validation remain separate work. The [independent Python objective](data-and-objective.md) remains the correctness reference and is unchanged.

## Public API

Functions live in `tsp_hpc.distances`:

| API | Purpose |
| --- | --- |
| `prepare_distances(instance, *, mode, max_matrix_bytes=256*1024*1024)` | Explicitly prepare `dense` or `on_demand` distances |
| `estimated_matrix_bytes(n_cities)` | Exact `8*n*n` matrix storage using Python integers |
| `warmup_distance_kernels()` | Compile/exercise the supported helper signatures on tiny fixed inputs |
| `prepared.edge_cost(a, b)` | Validate city IDs, then evaluate a distance |
| `prepared.tour_cost(tour)` | Validate a complete permutation, then sum its closed-tour cost |
| `prepared.metadata` | Detached dictionary describing representation, memory and setup timing |

`PreparedDistances` objects are constructed only through the preparation function. They retain the original `TSPInstance` and share its owned read-only coordinates. The matrix is also read-only. As with the [data contract](contracts.md#supported-problem-and-instance-representation), this guards accidental writes, not deliberate changes to NumPy flags or array shape. Treat all prepared inputs as immutable.

```python
import numpy as np
from tsp_hpc.distances import prepare_distances
from tsp_hpc.instances import fixture

instance = fixture("square").instance
candidate = np.arange(instance.n_cities, dtype=np.int64)

dense = prepare_distances(instance, mode="dense", max_matrix_bytes=1024)
on_demand = prepare_distances(instance, mode="on_demand")

assert dense.instance is on_demand.instance is instance
assert dense.tour_cost(candidate) == on_demand.tour_cost(candidate) == 4.0
assert dense.edge_cost(0, 2) == on_demand.edge_cost(0, 2)
assert dense.metadata["matrix_bytes"] == 128
assert on_demand.metadata["matrix_bytes"] == 0
```

Neither representation changes coordinates, city order, distance convention, instance ID, solver seeds, or scientific search identity. A [manifest](manifests.md) already declares the execution's `distance_mode`; the [serial NN adapter](nearest-neighbour.md#explicit-preparation-and-reuse) can consume its explicitly prepared representation, while automatic runner dispatch remains future work. The memory ceiling is a preparation/deployment option, not a new scientific algorithm parameter or manifest schema extension.

## Representations and memory

**Dense:** allocate one C-contiguous float64 `(n, n)` matrix, evaluate each unordered pair once, mirror it exactly, and set the diagonal to zero. Construction is serial in this baseline. No second quadratic temporary is created. Each subsequent edge query is a matrix lookup.

**On-demand:** retain the coordinates and calculate only requested distances. A read-only `(0, 0)` float64 array supplies the unused matrix argument to the common compiled interface; it stores zero matrix elements. There is no problem-sized matrix. Warm-up uses constant two-city inputs and a tiny 2×2 matrix for both modes; this is separate from actual-instance preprocessing.

The default dense ceiling is **256 MiB**, checked before warm-up or allocation. Set an explicit non-negative Python integer `max_matrix_bytes` to use another ceiling. A request exactly at the ceiling is permitted; oversized requests raise `MemoryError`, with no silent fallback to on-demand. An impossible platform address size is rejected separately. Allocation can still fail below the ceiling if memory is unavailable.

| Cities | Matrix bytes | Approximate storage |
| --- | --- | --- |
| 1,000 | 8,000,000 | 7.63 MiB |
| 5,000 | 200,000,000 | 190.73 MiB |
| 10,000 | 800,000,000 | 762.94 MiB |

The ceiling and estimate cover **only matrix elements**, not total process RSS, interpreter/JIT memory, coordinates, validation temporaries, solvers or allocator overhead. Coordinates occupy `16*n` bytes and are shared rather than copied during preparation. A full input validation scan has O(n) temporary storage. Multiple processes or independent preparations can each allocate a matrix, so total job memory must account for all copies. No host-memory or Slurm-allocation limit is inferred automatically. On-demand accepts a zero matrix ceiling, since it has no actual matrix elements.

Every call to `prepare_distances()` prepares a fresh representation. There is no hidden global instance/matrix cache. Reuse the returned object for repeated queries; do not call preparation inside candidate or iteration loops.

## Timing semantics

`prepared.metadata` records:

- `instance_id`, `distance_kind`, `distance_mode`;
- `estimated_matrix_bytes` for a hypothetical dense matrix under either mode;
- actual `matrix_bytes`, shared `coordinate_bytes`, and requested `max_matrix_bytes`;
- `jit_warmup_time_s`: wall time for helper warm-up, including cache loading/compilation if needed and tiny dispatches;
- `preprocessing_time_s`: wall time for validating coordinates, allocating/filling the actual representation, and marking its matrix read-only.

Warm-up runs before the preprocessing timer. These fields exclude source loading, module/process startup, solver execution, and independent final solution validation. Warm-up is not necessarily compilation on every call, and it does not warm solver kernels; the NN adapter performs its own separate warm-up. Both writable and read-only int64 tour signatures are exercised.

These are observed setup durations, **not performance claims** or speedup measurements. Metadata retains the object's original construction times when it is reused. The future runner must record whether preprocessing is charged once per instance, once per trial, or amortized across trials; it must not silently hide or double-count reuse. Report setup separately from search time, as well as whichever total-time comparison the experiment protocol requires.

## Numerical semantics

The current execution scope is **unrounded `euclidean_2d` only**, as chosen by the project owner. Both paths use native Numba `math.hypot`, no relaxed `fastmath`, and the same sequential float64 tour accumulation order, including the closing edge. Distances are never converted to integer weights. Non-finite differences, distances or totals raise the shared `NumericalError`.

`prepare_distances()` rejects `tsplib_euc_2d` before warm-up or allocation. The earlier data, artifact, manifest and independent-reference APIs still recognize that convention for compatibility; recognizing a descriptor does not imply execution support. Nothing silently reinterprets a rounded instance as unrounded. Supporting integer-rounded benchmarks is outside the current execution plan.

Dense construction checks every pair eagerly and returns no partial representation on failure. On-demand checks only queried edges; preparing it does not certify that every possible edge/tour is representable. Even a successfully built dense matrix can contain finite edges whose combined tour total overflows. Such totals fail when evaluated, just as in the independent reference.

### Native calculation and the independent reference

The Python reference retains its separate `math.hypot` implementation. Native Numba calculations may differ from it in the last floating-point bits. For example, the vector `(5865.277089690506, 5153.210738088601)` produced `7807.5` in native compiled code and `7807.499999999999` in the reference on the development runtime. Without integer rounding, this is a tiny numerical difference within the existing unrounded validation rule:

```text
abs(actual - reference) <= 1e-12 + 1e-10 * abs(reference)
```

These are validation tolerances, not search tie-breaking or acceptance rules. Dense and on-demand paths use the same native edge calculation and are checked for exact agreement on the tested inputs; bitwise agreement with the independent Python implementation is not required. `hypot` also avoids the intermediate squared overflow of a naive `sqrt(dx*dx + dy*dy)` calculation.

No copied numerical helper, custom LLVM intrinsic or additional bundled third-party license is included in this implementation. Existing dependency licenses remain applicable to Python, NumPy and Numba.

Tests check reference agreement near half-integers, exponent extremes, subnormal values, and a generic CPU target in fresh processes with cache reload. Runtime changes require rerunning the checks; cluster compatibility still needs owner-operated validation.

## Compiled solver boundary

Future compiled callers pass primitive arrays and flags, not the Python record:

```python
import numpy as np
from numba import njit
from tsp_hpc.distances import (
    prepare_distances, distance_edge_kernel, distance_tour_kernel,
)
from tsp_hpc.instances import fixture

@njit(fastmath=False)
def evaluate_candidate(coordinates, matrix, use_dense, tour):
    first_edge = distance_edge_kernel(
        coordinates, matrix, use_dense, tour[0], tour[1]
    )
    total = distance_tour_kernel(coordinates, matrix, use_dense, tour)
    return first_edge, total

prepared = prepare_distances(fixture("square").instance, mode="on_demand")
tour = np.arange(4, dtype=np.int64)  # Known valid permutation for this fixture.
assert evaluate_candidate(
    prepared.coordinates, prepared.matrix, prepared.use_dense, tour
) == (1.0, 4.0)
```

Use `prepared.coordinates`, `.matrix` and `.use_dense` as the first three arguments. `coordinate_edge_kernel(coordinates, a, b)` is also available for a direct coordinate-only query. All raw kernels implement unrounded Euclidean distances only.

**Kernel preconditions matter:** raw kernels trust canonical finite float64 arrays, valid city indices, a valid int64 permutation, and the matrix/flags supplied by preparation. They do not perform permutation or bounds validation and must not receive arbitrary external weight matrices. Use the checked Python methods at untrusted boundaries; solver wrappers must validate their inputs before entering kernels. The public tour method's permutation validation allocates O(n) scratch, while the compiled scoring loop allocates no matrices or tour copies. Numerical arithmetic checks remain active in the kernels. Compiled callers must also keep fastmath disabled for this baseline.

Run `uv run --locked pytest -W error` for fixture/generated-instance comparisons against the independent reference, numerical boundaries, memory guards, timing separation, compiled callers, and read-only/storage invariants. The full suite also includes [serial NN search tests](nearest-neighbour.md#validation). No benchmark campaign is run by these checks.
