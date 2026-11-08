# Library usage

Use the installed [development environment](development.md) and run examples from the repository root with `uv run --locked python`. The [README](../README.md#current-capabilities) lists available features. [Contracts](contracts.md) define the data/objective/result rules; this guide explains how to use their APIs.

## Canonical data and independent validation

`TSPInstance` takes a caller-supplied logical ID and owns a read-only coordinate copy. Its frozen fields use object-identity equality. The validators in [data.py](../src/tsp_hpc/data.py) check canonical arrays without converting, reordering or mutating them. Normalize external lists, strided/non-native arrays or subclasses explicitly in a loader; do not coerce invalid solver tours into validity. Masked arrays are unsupported. Treat coordinates, flags and shape as immutable: NumPy read-only flags prevent accidental writes, not deliberate bypass.

```python
import numpy as np
from tsp_hpc.data import TSPInstance, validate_tour
from tsp_hpc.objective import tour_cost, validate_solution

instance = TSPInstance("triangle", np.array([[0., 0.], [3., 0.], [3., 4.]]))
tour = np.array([0, 2, 1], dtype=np.int64)
validate_tour(tour, instance.n_cities)
assert tour_cost(instance, tour) == 12.0
assert validate_solution(instance, tour, reported_cost=12.0) == 12.0
```

`validate_coordinates()` checks the coordinate representation; `validate_city_index()` accepts in-range Python/NumPy integers except booleans and returns a Python `int`. [objective.py](../src/tsp_hpc/objective.py) supplies `edge_cost()`, `tour_cost()` and `validate_solution()` independently of compiled execution. Use them at boundaries, not on every hot-loop proposal. See the [numerical contract](contracts.md#tour-representation-and-shared-objective) for accumulation, tolerance and error rules.

## Instances and artifacts

[instances.py](../src/tsp_hpc/instances.py) provides `uniform_instance()`, `clustered_instance()`, `fixture()`, `fixture_names()`, `save_instance()` and `load_instance()`. Constructors return an `InstanceRecord` with:

- `.instance`: the owned canonical TSP instance;
- `.descriptor`: a detached descriptor for a [manifest](manifests.md), including the effective generation seed or `None`;
- `.provenance`: detached generation settings and original runtime versions;
- `.to_dict()`: a detached complete artifact document.

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from tsp_hpc.instances import uniform_instance, save_instance, load_instance
from tsp_hpc.random_streams import derive_stream

seed = derive_stream(123, "instance", "uniform-v1", "sample-a").seed
record = uniform_instance(32, seed=seed, side_length=100.0)
with TemporaryDirectory() as directory:
    path = Path(directory) / "sample-a.json"
    save_instance(record, path)
    loaded = load_instance(path)
    assert loaded.to_dict() == record.to_dict()
```

Use the saved coordinates for shared experiments rather than regenerating during search. The [committed collection](../examples/instances/README.md) contains six fixtures and two eight-city samples. Those files are repository assets, not installed package resources; supply paths explicitly outside the checkout.

### Generation protocols

Use Python integer counts, unsigned 64-bit Python integer seeds, and built-in int/float lengths; booleans and NumPy scalars are rejected. Require `n_cities >= 2`, `1 <= n_clusters <= n_cities`, finite positive `side_length`, and finite non-negative `standard_deviation`. Defaults are side length `1.0`, four clusters and standard deviation `0.05`; smaller clustered inputs must specify fewer clusters. Both generators accept `distance_kind` with default `euclidean_2d`.

Each invocation uses a fresh private `Generator(PCG64(seed))`, independent of global or solver RNG state. The argument is the effective seed, not a generation root to be derived again:

- **`uniform-v1`:** draw one C-order `(n, 2)` array with `rng.random()` and multiply by `side_length`. The mathematical interval is `[0, side_length)` per axis; scaling may round to its upper boundary. Do not reject, deduplicate or sort cities.
- **`clustered-v1`:** draw centres uniformly in that square; create labels `arange(n) % n_clusters` and shuffle them; then draw an `(n, 2)` standard-normal array, multiply by the absolute standard deviation and add labelled centres. Cluster populations differ by at most one. Offsets are isotropic Gaussians, **not clipped** to the square; coordinates can be negative. Zero spread places cities at their assigned centres. Labels define clusters, not guaranteed visible separation. Duplicates/overlap are valid; arithmetic overflow is rejected.

Changing the distance convention changes identity/objective, not coordinate draws. Unit-scale rounded data can have many zero edges and is not necessarily a useful benchmark. If effective seeds come from a root and sample IDs, retain that derivation context separately in campaign provenance; never derive cities from solver state, worker IDs, arrival order or repetition-dependent solver seeds.

### Correctness fixtures

City IDs follow row order. The natural tour `[0, ..., n-1]` attains each certified optimum under both retained data/reference conventions.

| Name | Coordinates | Optimum |
| --- | --- | --- |
| `two-city` | `(0,0), (3,4)` | 10 |
| `triangle` | `(0,0), (3,0), (3,4)` | 12 |
| `square` | `(0,0), (1,0), (1,1), (0,1)` | 4 |
| `collinear` | `(0,0), (1,0), (3,0), (6,0)` | 12 |
| `duplicates` | `(0,0), (0,0), (3,4), (3,4)` | 10 |
| `coincident` | four copies of `(0,0)` | 0 |

The line/duplicate optima require travel between extrema and back; the square perimeter is optimal, while its rounded distinct-city edges all cost one. Tests also exhaustively score these tiny tours with the independent reference. This is correctness evidence, not a fourth solver or a representative campaign.

### Artifact guarantees

The v1 JSON artifact contains exactly `schema_version: 1`, `instance` (ID/checksum/count/convention/effective seed), ordered `coordinates`, and `provenance` (generator protocol, complete effective parameters, seed, bit-generator name and original Python/NumPy/package versions). Content IDs follow the [identity encoding](manifests.md#encoding-contract); filenames and provenance are not coordinate content.

Loading rejects unknown/missing fields, duplicate keys, invalid/non-finite coordinates, unsupported versions, conflicting seeds/counts, stale IDs/checksums and mismatched named fixtures. It verifies actual coordinate bytes; row order and signed zero survive JSON round trips. It never loads pickle or regenerates coordinates.

Checksums detect mismatch, not authenticity. Generation provenance is validated structurally, not proven by regeneration; original runtime metadata is preserved. Regenerate only against the recorded protocol/runtime, with no cross-version distribution-equivalence promise. Incompatible generator changes require a protocol-version decision.

This inspectable small-input format materializes JSON/coordinates in O(n) space, not a matrix. Parent directories must exist; saving refuses overwrites and is not crash-atomic. An interrupted file is unusable unless loading/verification succeeds.

## Run nearest-neighbour

NN is a constructive heuristic, not an optimality guarantee. For explicit representation, implementation selection and setup reuse:

```python
from tsp_hpc.instances import fixture
from tsp_hpc.distances import prepare_distances
from tsp_hpc.nearest_neighbour import NearestNeighbourSolver

instance = fixture("square").instance
prepared = prepare_distances(instance, mode="dense", max_matrix_bytes=1024)
solver = NearestNeighbourSolver(prepared, implementation="visited-scan-v1")
result = solver.solve(
    instance, seed=42, budget={"start_cities": [2, 0]}, parameters={}
)
assert result.best_tour.tolist() == [2, 1, 0, 3]
assert result.best_cost == 4.0
assert result.work.nn_starts_completed == result.work.full_tour_evaluations == 2
assert solver.metadata["implementation"] == "visited-scan-v1"
assert prepared.metadata["matrix_bytes"] == 128
```

The adapter requires the **same instance object** used in preparation, not just a matching ID. Reuse it for repeated solves; it retains no last-run solution, counters or RNG state. Requests use an ordered list of Python integer starts (not booleans or NumPy scalars) and an empty parameter dictionary. The [NN specification](algorithm-baselines.md#greedy-nearest-neighbour) owns start/tie rules, seed behavior and work accounting. Unsupported capabilities fail explicitly, not by changing the requested workload.

The module-level `tsp_hpc.nearest_neighbour.solve(instance, *, seed, budget, parameters, record_history=False)` is a convenience wrapper using on-demand distances and the default implementation. Its overall call includes setup; prefer the explicit adapter for setup accounting and reuse. Empty convenience work skips preparation/warm-up; an existing adapter's empty solve skips search/final validation and reports zero phase durations.

### Implementation selection

The adapter follows scientific variant `nn-fixed-starts-v1`. `implementation_names()` lists executable serial implementations; the default is `visited-scan-v1`, preserved in [nn_visited_scan.py](../src/tsp_hpc/nn_visited_scan.py). It scans city IDs, skipping visited entries, in O(S n²) time for S starts, using O(n) candidate/visited/best arrays and an O(S) start snapshot. Distance storage is separate.

Register optimized kernels explicitly alongside that baseline in [nearest_neighbour.py](../src/tsp_hpc/nearest_neighbour.py), under new stable IDs. The adapter resolves, warms and executes the selected callable and exposes its ID in metadata; unavailable IDs fail before warm-up. There is no filename-based discovery. Implementations must share scientific conformance tests; changes to search rules require a different variant, not an optimization label.

For a plan with an explicit label, pass `implementation=plan.execution["implementation"]` to the adapter. Labels, omission compatibility and scientific-versus-execution identity are specified once in [manifest execution configurations](manifests.md#execution-configurations). Planning does not authorize or dispatch execution; callers must check the requested backend rather than treating a multicore plan as serial.

## Distance preparation and compiled callers

[distances.py](../src/tsp_hpc/distances.py) creates factory-only `PreparedDistances` records sharing the instance's read-only coordinates. Each call prepares fresh storage; no hidden global cache or fallback exists. Reuse the result outside iteration loops. Checked `.edge_cost(a, b)` and `.tour_cost(tour)` methods validate structural inputs before querying it.

| Mode | Storage and checks |
| --- | --- |
| `dense` | One read-only C-contiguous float64 `(n,n)` matrix; compute unordered pairs once, mirror, zero diagonal. Serial preparation validates every edge, with no second quadratic temporary. |
| `on_demand` | Shared coordinates and a read-only `(0,0)` matrix placeholder. Validate only queried edges; preparation does not certify all possible edges or tours. |

`estimated_matrix_bytes(n)` returns exact Python-integer `8*n*n`. The default ceiling is **256 MiB**, configurable with non-negative Python integer `max_matrix_bytes`; exact-limit allocations are permitted. Oversize requests raise `MemoryError` before warm-up/allocation, platform address overflow raises `OverflowError`, and real allocation may still fail below the ceiling. On-demand permits a zero ceiling. The ceiling covers matrix elements only, not RSS, JIT, coordinates (`16*n` shared bytes), O(n) validation scratch, solver buffers or extra process copies; no host/Slurm limit is inferred.

Both paths use native `hypot` and sequential scoring under the [numerical contract](contracts.md#tour-representation-and-shared-objective). Dense/on-demand edges agree on tested inputs, but reference last-bit differences are allowed. Even finite matrix edges can overflow a tour total. Numerical failures propagate rather than returning partial usable results.

Compiled callers pass primitive inputs, not Python records:

```python
import numpy as np
from numba import njit
from tsp_hpc.instances import fixture
from tsp_hpc.distances import prepare_distances, distance_tour_kernel

@njit(fastmath=False)
def evaluate(coordinates, matrix, use_dense, tour):
    return distance_tour_kernel(coordinates, matrix, use_dense, tour)

prepared = prepare_distances(fixture("square").instance, mode="on_demand")
tour = np.arange(4, dtype=np.int64)  # Known valid permutation.
assert evaluate(prepared.coordinates, prepared.matrix, prepared.use_dense, tour) == 4.0
```

`distance_edge_kernel(coordinates, matrix, use_dense, a, b)` and `coordinate_edge_kernel(coordinates, a, b)` provide edge queries. Raw kernels trust canonical finite arrays, valid indices/permutations and the prepared matrix/flag pair; they are not an arbitrary weight-matrix interface. Structural validation belongs at the Python boundary. Numerical guards remain active, `fastmath` must stay disabled, and the compiled scoring loop allocates no matrix or tour copy.

NN kernels currently take coordinates, matrix, mode flag and ordered starts. Keep the complete construction loop compiled; similarly, avoid Python dispatch per SA proposal and keep GA operator outputs disjoint. Additional optimization-specific prepared state requires an explicit adapter/preprocessing extension, not hidden untimed work.

## Timing and results

The solver independently checks its selected native score/tour with `validate_solution()` and returns the recomputed reference cost. This **does not rerank candidates or redefine native tie rules**. [Shared result contracts](contracts.md#solver-results-and-progress) define owned tours, statuses and counters; constructing a result alone is not an objective certificate. NN emits `work_limit`, with history disabled (`None`). Errors remain exceptions, not `no_solution` or infinity.

| Observation | Included work |
| --- | --- |
| `prepared.metadata["jit_warmup_time_s"]` | Fixed tiny distance-helper dispatches, including compilation/cache loading if needed; writable/read-only tour signatures |
| `prepared.metadata["preprocessing_time_s"]` | Coordinate validation, actual representation allocation/fill and read-only marking, after helper warm-up |
| `solver.metadata["solver_warmup_time_s"]` | Selected solver warm-up on tiny inputs for both distance modes; separate from distance-helper warm-up |
| `result.search_time_s` | Warmed compiled call, including private workspace allocation/reset, constructions, scoring, saved-best copies and work accounting |
| `result.validation_time_s` | Independent post-search permutation/cost checking |

Prepared metadata also reports instance/convention/mode, hypothetical dense bytes, actual matrix/coordinate bytes and ceiling. Solver metadata adds backend/variant/implementation. Metadata snapshots are detached and retain original setup timings when reused.

Request validation/start marshalling occur before the search clock; result packaging/ownership copies occur after validation timing. Loading, process startup and persistence are also outside these phase fields. Their sum is **not end-to-end time**. Use the [comparison methodology](contracts.md#experimental-comparisons) to account for omitted overhead and preparation reuse. These are phase observations, not time-budget stopping or a persisted run envelope.
