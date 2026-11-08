# Serial nearest-neighbour baseline

**Status:** task #12 implements `nn-fixed-starts-v1`, our first solver, using compiled serial search loops and unrounded Euclidean distances. GA, SA, parallel NN, time limits, histories and experiment-runner dispatch remain separate work. This is a constructive heuristic, not a guarantee of an optimal tour.

## Basic use

The convenience function follows the [shared solver interface](contracts.md#logical-solver-interface) and uses on-demand distances:

```python
from tsp_hpc.instances import uniform_instance
from tsp_hpc.nearest_neighbour import solve
from tsp_hpc.objective import validate_solution

instance = uniform_instance(40, seed=123, side_length=100.0).instance
result = solve(
    instance,
    seed=42,
    budget={"start_cities": [0, 7, 12]},
    parameters={},
)
assert result.status == "ok"
assert result.work.nn_starts_completed == 3
assert result.work.full_tour_evaluations == 3
assert validate_solution(instance, result.best_tour, result.best_cost) == result.best_cost
```

Run these Python examples in the installed environment, for example with `uv run --locked python`. The CLI still provides help/version information only; there is no experiment-running CLI command yet. An instance loaded with `load_instance(path).instance` works in exactly the same way.

`seed` is required and validated as a Python integer in `[0, 2**64)`, but this deterministic NN variant does not consume it or any RNG state. The caller/runner retains that input in experiment provenance. `parameters` must be an empty dictionary. `budget` must contain exactly an ordered `start_cities` list of distinct in-range Python integers; booleans and NumPy scalar IDs are rejected at this manifest-compatible boundary. Starts are never sorted, sampled, repeated, or inferred from worker counts.

Unsupported parameters, time-limit fields, history requests, rounded instances and malformed inputs fail explicitly. NN is not an implicit dispatcher for a manifest requesting multicore execution.

## Explicit preparation and reuse

To select dense storage, set its memory ceiling, retain setup metadata, or reuse a prepared instance, create a serial adapter:

```python
from tsp_hpc.distances import prepare_distances
from tsp_hpc.instances import fixture
from tsp_hpc.nearest_neighbour import NearestNeighbourSolver

instance = fixture("square").instance
prepared = prepare_distances(instance, mode="dense", max_matrix_bytes=1024)
solver = NearestNeighbourSolver(
    prepared, implementation="visited-scan-v1"
)  # Warm-up of the selected implementation happens here, before search.

result = solver.solve(
    instance, seed=42, budget={"start_cities": [2, 0]}, parameters={}
)
assert result.best_tour.tolist() == [2, 1, 0, 3]
assert result.best_cost == 4.0
assert solver.metadata["variant"] == "nn-fixed-starts-v1"
assert solver.metadata["backend"] == "serial"
assert solver.metadata["implementation"] == "visited-scan-v1"
assert solver.metadata["distance_mode"] == "dense"
```

The adapter is bound to the **same instance object** supplied to distance preparation, not merely its label. Another object, even one with the same ID, requires a new adapter; this prevents accidental use of a stale matrix for different coordinates. Follow the [distance backend](distances.md) immutability and resource rules.

The adapter stores no last-run best tour, counters, or RNG state. Each call initializes private working buffers and returns an owned read-only tour. A returned result does not alias later search workspace. Its metadata is a detached snapshot containing distance preparation information, the serial backend/variant labels and `solver_warmup_time_s`.

## Scientific variants versus implementation versions

`algorithm="nn"` names the family. `variant="nn-fixed-starts-v1"` names the scientific rules. `implementation="visited-scan-v1"` names the concrete implementation of those rules. Dense/on-demand remains a separate representation choice.

The original kernel is preserved in `src/tsp_hpc/nn_visited_scan.py`. Add future optimized kernels alongside it under new stable implementation IDs rather than replacing the baseline. `implementation_names()` lists executable serial NN implementations; currently it returns only `("visited-scan-v1",)`. Unknown or unavailable IDs fail before solver warm-up, with no silent fallback. The adapter warms and executes the selected kernel, and reports its ID in `.metadata`.

An execution entry can explicitly select it:

```json
{
  "backend": "serial",
  "implementation": "visited-scan-v1",
  "distance_mode": "dense",
  "resources": {"profile": "local-cpu"}
}
```

The manifest planner accepts nonempty implementation labels without certifying availability, just as it can plan not-yet-implemented execution backends. An explicit label participates in `execution_id`, but not `search_id` or RNG derivation. Different implementations can therefore share the same scientific workload without colliding as execution configurations. For compatibility, omitted labels remain omitted and retain existing plan IDs; such legacy plans do **not** identify an implementation. The adapter and convenience API default to `visited-scan-v1`, but comparison manifests should always specify the label explicitly. A future runner must resolve legacy omissions before recording the effective execution identity, check availability/backend compatibility, and persist the actual implementation ID and Git revision. An unspecified legacy ID must not be reused to label a resolved implementation comparison.

Pass an explicit plan label as `NearestNeighbourSolver(prepared, implementation=plan.execution["implementation"])`; implementation selection is not an algorithm parameter. The module-level `solve()` retains the common scientific interface and always uses the default implementation. Manifest execution and persisted benchmark envelopes are still future work.

Future implementations must pass the same scientific conformance tests, including tours, exact tie rules, counters and deterministic replay. Changes to search rules require a different scientific variant, not merely an implementation ID. The current dispatch boundary supports kernels taking coordinates, a prepared distance matrix, its mode flag and ordered starts. An optimization requiring additional preprocessed state will need an explicit adapter/preprocessing extension and separate timing; do not hide that work in warm-up or omit it from end-to-end costs. No second optimized implementation or performance claim is introduced here.

## Search and tie rules

For each start in the declared order:

1. Reset the visited array, place the start first and mark it visited.
2. Scan unvisited cities in ascending internal city-ID order. Replace the nearest candidate only on strict improvement of the computed native edge cost. Exact ties therefore choose the lowest city ID.
3. Repeat until every city appears exactly once. Closure is implicit; do not append the starting city again.
4. Evaluate the complete tour once with the compiled distance backend, including the closing edge.
5. Replace the saved best only on strict improvement. Equal complete costs retain the earliest start-list entry, not the lowest start ID.

Validation tolerances are not used for search comparisons. No local improvement, rotation/orientation normalization, convergence test, or early exit at zero cost is added. All starts complete under a valid fixed-work request. Duplicate/coincident coordinates and small instances remain valid.

After search, the selected tour and its native score are independently checked using `validate_solution()`. The returned `best_cost` is that tour's recomputed Python-reference cost. Native/reference values may differ slightly within the established unrounded tolerance. This post-search step **does not rerank candidates or redefine the native tie rules**. Dense and on-demand searches use the same native edge arithmetic and are tested for identical tours/counters.

For `S` starts and `n` cities, search takes O(S n²) time with O(n) working arrays: one candidate tour, a visited array, and a separate saved-best tour, reused across starts. There is also an O(S) start-array snapshot. Dense distance storage is separate preprocessing. No matrix is built inside a construction or iteration.

## Results and counters

`tsp_hpc.results` provides the shared in-memory types:

- `WorkCounters`: immutable non-negative integer counters named `nn_starts_completed`, `ga_generations_completed`, `sa_proposals_evaluated`, and `full_tour_evaluations`; `.to_dict()` returns a detached dictionary.
- `SolverResult`: `status`, owned `best_tour`, finite non-negative `best_cost`, `work`, `termination_reason`, and `history`, plus phase observations `search_time_s` and `validation_time_s`.

For successful NN search over `S > 0` starts:

```text
status = "ok"
termination_reason = "work_limit"
nn_starts_completed = S
full_tour_evaluations = S
ga_generations_completed = sa_proposals_evaluated = 0
history = None
```

An empty start list returns `no_solution`, `None` tour/cost/history, zero counters, `work_limit`, and zero search/validation durations. The convenience function skips distance preparation and JIT warm-up for empty work. An explicit adapter may already have incurred setup costs, but its empty solve performs no search or final validation.

Warm-up tours and the independent post-search evaluation are excluded from search counters. Numerical failures, allocation failures and validation errors propagate as exceptions; they are not converted into `no_solution`, infinity, or a partial best result. The future runner will record failures separately.

The result constructor checks structure and owns its tour snapshot; by itself it cannot certify a cost against an instance. The solver performs that independent check before constructing the record. The common result type recognizes the contract's termination labels, but this NN implementation emits only `work_limit`. History is currently restricted to `None`. These are in-memory records, not the persisted run-envelope schema from task #18.

## Timing boundaries

- Distance JIT warm-up and preprocessing remain in `prepared.metadata`.
- Adapter construction calls `warmup_nearest_neighbour(implementation=...)` on fixed tiny inputs for both representations. `solver_warmup_time_s` includes helper dispatch, compilation/cache loading if needed, and tiny warm-up searches.
- Request validation and start-list marshalling occur before the search clock.
- `search_time_s` covers the warmed compiled call, including allocation/reset of candidate/visited/best workspace, all constructions, full-tour scoring, best-tour copying and completed-work accounting.
- `validation_time_s` covers independent post-search permutation/cost checking.

Result packaging/ownership copies, request validation, source loading, and outer-call setup are not included in the two result phase durations. They must appear in future end-to-end measurements; summing only these fields is not an end-to-end measurement. The convenience function performs preparation/warm-up inside its overall call even though they are excluded from `search_time_s`. Prefer an explicit reusable adapter for steady-state comparisons and setup accounting.

These timers observe completed phases. They do not implement time-budget stopping or benchmark protocols. Actual worker affinity, runtime/source provenance and persistence belong to the runner, not the numerical solver.

## Validation

Run `uv run --locked pytest -W error`. Tests compare greedy constructions with a separate Python implementation, check all fixture starts, generated-instance backend equivalence, exact and near ties, best-tour retention, zero-cost/empty work, invalid capabilities, numerical failures, owned results and counters, setup/timing separation, manifest integration, global RNG isolation, and fresh-process replay/cache loading under a generic CPU target.

The tests establish baseline correctness evidence, not optimality or performance/scaling claims. No cluster operations or benchmark campaigns are performed.
