# Shared instance, tour, solver, and result contracts

**Status:** v1 design for task #1; implementation is pending. Task #2 will define algorithm variants, concrete work budgets, and stopping checkpoints. This document does not select GA operators or an SA cooling schedule.

These contracts keep the three required solvers compatible with one objective function and one experiment runner. They describe logical records and boundaries, not a required class hierarchy or a generic solver framework. See the [technology stack](tech-stack.md) for implementation tools and the [resource plan](hpc-resources.md) for execution responsibilities.

## Supported problem and instance representation

The initial scope is complete, symmetric, two-dimensional coordinate-based TSP with **at least two cities**. All cities must be visited, and every pair is connected. Duplicate coordinates, collinear points, and zero-length edges are valid; an instance may have zero-cost tours. Empty and single-city instances are rejected explicitly.

Asymmetric distances, missing edges, arbitrary explicit-weight input matrices, geographical distance conventions, and higher-dimensional coordinates are outside v1. An internally precomputed matrix is a representation of a supported coordinate instance, not a new input problem type.

| Instance field | Contract |
| --- | --- |
| `instance_id` | Non-empty, stable identifier for the logical instance; independent of worker counts, hardware, and distance representation. Identity generation and checksums are specified in task #6. |
| `coordinates` | C-contiguous NumPy `float64` array of shape `(n, 2)`, with finite values and `n >= 2`. Row indices are the internal city IDs `0` through `n - 1`. |
| `distance_kind` | Explicitly either `euclidean_2d` or `tsplib_euc_2d`; never inferred separately by individual solvers. |

Derive the city count from `coordinates.shape[0]`; do not maintain a conflicting independent count. Treat instance arrays as read-only throughout a run. Builders may normalize coordinate inputs to this representation once, before timing search; kernels must not repeatedly convert or copy them.

A loader maps external city labels to contiguous internal IDs in a deterministic order and retains the inverse mapping in provenance. Solver tours always use internal IDs. Source filenames, checksums, instance-generation seeds, and reference optima belong in instance provenance, not in numerical kernels. Reference values must identify their distance convention and whether they are certified optima or merely best-known costs.

### Distance conventions and representations

For cities `i` and `j`, let `r = hypot(x[i] - x[j], y[i] - y[j])`, evaluated in `float64`.

| Convention | Edge cost |
| --- | --- |
| `euclidean_2d` | `r`, without rounding; the default for generated instances |
| `tsplib_euc_2d` | `floor(r + 0.5)`, applied to **each edge**, not to the completed tour; used for the supported TSPLIB `EUC_2D` subset |

Do not use ties-to-even rounding for `EUC_2D`. Both conventions have zero diagonal, symmetric non-negative edge costs, and `float64` storage and accumulation. The TSPLIB loader is a later task; declaring its convention does not imply that arbitrary TSPLIB formats are supported.

The runner may prepare either:

- **On-demand distances:** compute edges from the canonical coordinates and declared convention.
- **Dense distances:** a C-contiguous `float64` array of shape `(n, n)`, derived from exactly the same coordinates and convention. No per-iteration matrix construction is allowed.

The representation must not redefine the objective. Test agreement of edges and tour costs between both paths. Treat dense-matrix construction as preprocessing, and account for its `8 * n * n` bytes separately from solver working storage. Do not require dense allocation merely to validate an on-demand instance.

Reject non-finite derived distances or tour totals rather than allowing overflow to become an infinite score. For rounded distances, reject numerical ranges that cannot preserve integer edge and total costs exactly in `float64`; do not silently lose the benchmark's integer-cost semantics.

## Tour representation and shared objective

A tour is a C-contiguous NumPy `int64` array of shape `(n,)`, containing each internal city ID exactly once. **Closure is implicit:** do not repeat the starting city at the end.

For a tour `p`, its cost is:

```text
cost(p) = sum(distance(p[k], p[(k + 1) % n]) for k in range(n))
```

For example, with coordinates `[(0, 0), (3, 0), (3, 4)]`, `p = [0, 2, 1]` uses edges `0 -> 2`, `2 -> 1`, and `1 -> 0`, with total cost `5 + 4 + 3 = 12` under either convention. A two-city tour includes both the outward and return edge. Distinct city IDs remain distinct visits even when their coordinates coincide.

Any starting city and either orientation are valid. Rotation or reversal normalization is not required at solver boundaries; a solver must not reorder a supplied tour in place merely to canonicalize it. Algorithmic tie-breaking belongs to task #2.

The shared full-tour cost routine is the correctness reference. SA may maintain incremental costs, and GA may cache fitness, but the final returned cost must be recomputed from the returned tour. The runner independently validates the permutation and recomputes its cost before accepting a result.

For unrounded costs, validation uses explicit tolerances (`rtol=1e-10`, `atol=1e-12` initially); these are **not** tie-breaking or acceptance thresholds inside the algorithms. Rounded costs must agree exactly within the supported numeric range. Do not enable relaxed floating-point transformations in the correctness baseline; any later numerical changes must be validated and recorded.

## Logical solver interface

Each algorithm exposes the same logical boundary through a Python wrapper:

```text
solve(instance, *, seed, budget, parameters, record_history=False) -> SolverResult
```

This is an interface specification, not implemented Python code. Wrappers validate records and pass primitive scalars and arrays into Numba kernels; kernels need not accept Python dictionaries or rich objects.

| Input | Contract |
| --- | --- |
| `instance` | A validated instance using the representation and objective above. |
| `seed` | Explicit Python integer in `[0, 2**64)`; booleans are not seeds. Accepted even by a deterministic algorithm so the common call and provenance stay consistent. |
| `budget` | Explicit algorithm-specific work limits, a finite positive search-time limit in seconds, or both; at least one stopping limit is required. Initial implementations support fixed work; time-based requests require the later time-budget extension. |
| `parameters` | Validated scientific configuration: initialization and algorithm-specific choices. Resolve defaults and record the effective values before execution; reject unknown fields rather than silently ignoring them. |
| `record_history` | Disabled by default. Enables optional best-so-far observations when supported; sampling policy is defined in task #19. |

An optional initial tour, if supported by the chosen algorithm, is supplied through its parameters and follows the same tour contract. The solver owns its working copy and does not mutate caller-provided arrays. Returned best tours must not alias mutable solver state or caller-owned buffers.

Use fresh random state for each invocation. Instance-generation seeds and solver seeds are distinct. Derive subsearch streams from stable logical IDs, never thread numbers, worker counts, scheduling order, process-global RNG state, or Python's randomized `hash()`. The concrete stream scheme is defined in task #6; this interface does not promise bitwise equivalence across different runtimes or numerical backends.

Task #2 defines the concrete budget fields, legal zero-work cases, initialization accounting, and safe stopping points. Fixed-work scaling must preserve those budgets and logical searches when resources change. Time limits are checked only at safe points and may overshoot; actual elapsed time must be reported. Unsupported time-budget or history requests must fail explicitly until those capabilities exist.

## Solver results and progress

| Result field | Contract |
| --- | --- |
| `status` | `ok` when a complete valid tour is available; `no_solution` when a normal stopping limit was reached before any complete tour was available. This is not an error-catching mechanism. |
| `best_tour` | Owned `int64` tour containing the best complete solution observed; `None` only for `no_solution`. SA returns its best tour, not necessarily its final current tour. |
| `best_cost` | Finite, non-negative `float64`-precision cost recomputed from `best_tour`; `None` exactly when `best_tour` is `None`. Never use infinity, NaN, or an empty tour as a missing-solution sentinel. |
| `work` | Non-negative integer counters for actual completed work, as below; never substitute the requested limits for observed counts. |
| `termination_reason` | `work_limit`, `time_limit`, or `algorithm_stop`. The last requires an explicit variant-specific stopping rule agreed in task #2; it is not an implicit convergence claim. |
| `history` | `None` when recording is disabled; otherwise an ordered sequence of progress points. An enabled history is empty if no complete solution was observed. |

Use these common counters, with zero for algorithms to which they do not apply:

| Counter | Meaning |
| --- | --- |
| `nn_starts_completed` | Complete nearest-neighbour constructions |
| `ga_generations_completed` | Complete GA generations; initialization accounting is specified separately in task #2 |
| `sa_proposals_evaluated` | SA proposals for which the acceptance decision completed, including rejected proposals |
| `full_tour_evaluations` | Full objective evaluations performed during search, including initialization; excludes JIT warm-up and post-search validation/recomputation |

Incremental move-delta evaluations are not full-tour evaluations. Additional diagnostic counters are permitted if their meanings are documented. For a logical batch of searches, aggregate counters across those searches, not execution threads. Counters are not a claim that a GA generation and an SA proposal have equal computational cost.

A progress point contains `elapsed_s`, a snapshot of `work`, and `best_cost` for a complete tour. Timestamps are monotonic search-relative wall-clock observations, counters do not decrease, and best-so-far costs do not increase beyond validation tolerance. Recording must not consume randomness or change search decisions; its overhead is measured and reported. Sampling intervals, terminal samples, and time-to-target analysis are specified in task #19.

Malformed inputs and execution failures must not masquerade as `ok` or `no_solution`. The runner records failures separately, including their phase and error information. It wraps successful solver results with experiment IDs, effective configuration, seeds, timings, provenance, and execution metadata in the versioned result format from task #18. Numerical solvers do not write result files themselves.

## Validation and failure behavior

Validate once at the Python boundary before entering hot kernels:

- **Types and structure:** reject incompatible dtypes or object types with `TypeError`; reject wrong shapes, unsupported distance kinds, non-finite coordinates, and fewer than two cities with `ValueError`.
- **Tours:** reject missing, repeated, negative, and out-of-range city IDs. A closed array of length `n + 1` is invalid; do not silently truncate it or coerce floating-point indices to integers.
- **Configuration:** reject invalid seeds, unknown parameters, and invalid budgets before search. Legal parameter ranges and small-instance handling for each operator are specified in task #2.
- **Numerical range:** report unsupported range or non-finite arithmetic as a numerical failure, not a usable tour cost.
- **Resources and capabilities:** the runner rejects unsupported execution options or insufficient resources explicitly; it does not silently switch distance conventions, shrink the workload, or choose another algorithm.

Do not repeat full instance or permutation validation on every iteration. Test operator invariants separately, and always validate final results before accepting them as benchmark observations.

## Boundary between search and execution

Keep scientific inputs separate from the execution plan:

| Solver/search responsibility | Runner/adapter responsibility |
| --- | --- |
| Tours, objective, initialization, operators, budgets, and logical random streams | Selecting serial/multicore or an optional MPI/GPU implementation |
| Best-solution tracking and work counters | Selecting and preparing dense/on-demand distances without changing the objective |
| Safe stopping and optional search progress | Worker/thread counts, affinity, NUMA policy, devices, ranks, and Slurm allocations |
| Returning owned in-memory results | JIT warm-up, preprocessing/search/end-to-end timing, independent validation, provenance, and persistence |

The runner selects a compatible solver adapter and prepares its numerical inputs; the adapter passes the chosen arrays and scalars to a specialized kernel. Hardware options and scheduler settings do not become scientific algorithm parameters, and hot kernels do not read environment variables or submit jobs. This separation does not require one dynamic distance callback or a class hierarchy inside Numba.

## Implementation hand-off

- **Task #2:** settle algorithm variants, tie-breaking, budget fields, initialization accounting, and stop precedence/checkpoints against this shared boundary.
- **Tasks #5 and #8:** implement canonical data, validation, shared tour cost, and equivalent distance paths. Cover closing edges, two-city inputs, duplicate coordinates, rounding ties, invalid tours, and numeric boundaries with tests.
- **Tasks #6 and #18:** implement logical identities, random-stream planning, manifests, and persisted result envelopes.
- **Task #19:** implement time limits and progress recording without hiding missing solutions or instrumentation costs.

Task #3 can scaffold the package independently; it does not need to implement these data structures or any solver.
