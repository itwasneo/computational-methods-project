# Shared instance, tour, solver, and result contracts

This is the reference for data, objective, result and experiment-methodology rules. [Algorithm specifications](algorithm-baselines.md) define search behavior; [manifests](manifests.md) define identities and streams. See the [README](../README.md#current-capabilities) for implementation status and [usage guide](usage.md) for executable examples. These are logical boundaries, not a required class hierarchy.

## Supported problem and instance representation

The initial scope is complete, symmetric, two-dimensional coordinate-based TSP with **at least two cities**. All cities must be visited, and every pair is connected. Duplicate coordinates, collinear points, and zero-length edges are valid; an instance may have zero-cost tours. Empty and single-city instances are rejected explicitly.

Asymmetric distances, missing edges, arbitrary explicit-weight input matrices, geographical distance conventions, and higher-dimensional coordinates are outside v1. An internally precomputed matrix is a representation of a supported coordinate instance, not a new input problem type.

| Instance field | Contract |
| --- | --- |
| `instance_id` | Non-empty, stable identifier for the logical instance; independent of worker counts, hardware, and distance representation. Content ID and checksum helpers follow the implemented [manifest identity rules](manifests.md#identity-rules). |
| `coordinates` | Plain, C-contiguous NumPy array of native `float64` dtype and shape `(n, 2)`, with finite values and `n >= 2`. Row indices are the internal city IDs `0` through `n - 1`. |
| `distance_kind` | Explicitly either `euclidean_2d` or `tsplib_euc_2d`; never inferred separately by individual solvers. |

Derive the city count from `coordinates.shape[0]`; do not maintain a conflicting independent count. Treat instance arrays as read-only throughout a run. Builders may normalize coordinate inputs to this representation once, before timing search; kernels must not repeatedly convert or copy them.

A loader maps external city labels to contiguous internal IDs in a deterministic order and retains the inverse mapping in provenance. Solver tours always use internal IDs. Source filenames, checksums, instance-generation seeds, and reference optima belong in instance provenance, not in numerical kernels. Reference values must identify their distance convention and whether they are certified optima or merely best-known costs.

### Distance conventions and representations

**Execution scope:** unrounded `euclidean_2d`. Compiled distance preparation and solver execution reject `tsplib_euc_2d`; data, artifacts, manifests and the independent reference retain it for compatibility. Never reinterpret a rounded request or compare unrounded costs with integer-rounded benchmark optima.

For cities `i` and `j`, let `r = hypot(x[i] - x[j], y[i] - y[j])`, evaluated in `float64`.

| Convention | Edge cost |
| --- | --- |
| `euclidean_2d` | `r`, without rounding; the default for generated instances |
| `tsplib_euc_2d` | `floor(r + 0.5)`, applied to **each edge**, not to the completed tour; TSPLIB `EUC_2D` reference compatibility |

Do not use ties-to-even rounding for `EUC_2D`. Interpret `floor(r + 0.5)` as mathematical half-up rounding of the computed `r`; avoid premature floating-point rounding of the intermediate addition at half-integer or large-integer boundaries. Both conventions have zero diagonal, symmetric non-negative edge costs, and `float64` storage and accumulation. Declaring this convention does not imply a TSPLIB importer or support for arbitrary TSPLIB formats.

The runner may prepare either:

- **On-demand distances:** compute edges from the canonical coordinates and declared convention.
- **Dense distances:** a C-contiguous `float64` array of shape `(n, n)`, derived from exactly the same coordinates and convention. No per-iteration matrix construction is allowed.

The representation must not redefine the objective. Test agreement of edges and tour costs between both paths. Treat dense-matrix construction as preprocessing, and account for its `8 * n * n` bytes separately from solver working storage. Do not require dense allocation merely to validate an on-demand instance.

Reject non-finite derived differences, distances or totals with `NumericalError` (an `ArithmeticError` subclass), not an infinite score. Rounded edges/totals are restricted to `[0, 2**53]`, even if some larger integers are representable. Check the remaining range before addition so a one-unit overflow cannot disappear. Finite coordinates alone do not certify every pair or tour; check derived values when evaluated.

## Tour representation and shared objective

A tour is a plain, C-contiguous NumPy array of native `int64` dtype and shape `(n,)`, containing each internal city ID exactly once. **Closure is implicit:** do not repeat the starting city at the end.

For a tour `p`, its cost is:

```text
cost(p) = sum(distance(p[k], p[(k + 1) % n]) for k in range(n))
```

For example, with coordinates `[(0, 0), (3, 0), (3, 4)]`, `p = [0, 2, 1]` uses edges `0 -> 2`, `2 -> 1`, and `1 -> 0`, with total cost `5 + 4 + 3 = 12` under either convention. A two-city tour includes both the outward and return edge. Distinct city IDs remain distinct visits even when their coordinates coincide.

Any starting city and either orientation are valid. Rotation or reversal normalization is not required at solver boundaries; a solver must not reorder a supplied tour in place merely to canonicalize it. Algorithmic tie-breaking follows the [baseline decisions](algorithm-baselines.md#common-decisions).

The independent ordinary-Python reference uses coordinate-based `math.hypot` and sequential float64 accumulation in stored-tour order, returning Python floats. It depends on no matrix, JIT kernel, fitness cache or move delta. Tour validation takes O(n) time/temporary space; costing takes O(n). SA may maintain incremental costs and GA may cache fitness, but a returned cost must be recomputed from its tour and independently checked before acceptance.

Reports must be real, finite and non-negative, excluding booleans. Unrounded validation requires `abs(reported - reference) <= 1e-12 + 1e-10 * abs(reference)`; these are **not** tie-breaking or acceptance thresholds. Rounded reports must agree exactly, including integer reports near the float64 boundary. Validation returns the recomputed value, not the approximate report.

Native compiled `hypot` may differ from Python's implementation in the last bits; bitwise reference agreement is not required. Dense and on-demand paths must preserve the objective. Keep `fastmath` disabled in correctness baselines; later numerical changes require validation and explicit identification.

## Logical solver interface

Each algorithm exposes the same logical boundary through a Python wrapper:

```text
solve(instance, *, seed, budget, parameters, record_history=False) -> SolverResult
```

Wrappers validate records and pass primitive scalars and arrays into compiled kernels; kernels need not accept dictionaries or rich objects. Implementation and hardware selection belong to the adapter, not scientific parameters.

| Input | Contract |
| --- | --- |
| `instance` | A validated instance using the representation and objective above. |
| `seed` | Explicit Python integer in `[0, 2**64)`; booleans are not seeds. Accepted even by a deterministic algorithm so the common call and provenance stay consistent. |
| `budget` | Explicit algorithm-specific work limits, a finite positive search-time limit in seconds, or both; at least one stopping limit is required. Initial implementations support fixed work; time-based requests require the later time-budget extension. |
| `parameters` | Validated scientific configuration: initialization and algorithm-specific choices. Resolve defaults and record the effective values before execution; reject unknown fields rather than silently ignoring them. |
| `record_history` | Disabled by default. Enables best-so-far observations only when the implementation supports a defined sampling policy. |

An optional initial tour, if supported by the chosen algorithm, is supplied through its parameters and follows the same tour contract. The solver owns its working copy and does not mutate caller-provided arrays. Returned best tours must not alias mutable solver state or caller-owned buffers.

Use fresh random state for each invocation. Instance-generation seeds and solver seeds are distinct. Derive subsearch streams from stable logical IDs, never thread numbers, worker counts, scheduling order, process-global RNG state, or Python's randomized `hash()`. The [versioned stream scheme](manifests.md#random-stream-planning) is implemented; this interface does not promise bitwise equivalence across different runtimes or numerical backends.

The [budget and stopping rules](algorithm-baselines.md#budgets-and-stopping) define concrete budget fields, legal zero-work cases, initialization accounting, and safe stopping points. Fixed-work scaling must preserve those budgets and logical searches when resources change. Time limits are checked only at safe points and may overshoot; actual elapsed time must be reported. Unsupported time-budget or history requests must fail explicitly until those capabilities exist.

## Solver results and progress

| Result field | Contract |
| --- | --- |
| `status` | `ok` when a complete valid tour is available; `no_solution` when a normal stopping limit was reached before any complete tour was available. This is not an error-catching mechanism. |
| `best_tour` | Owned `int64` tour containing the best complete solution observed; `None` only for `no_solution`. SA returns its best tour, not necessarily its final current tour. |
| `best_cost` | Finite, non-negative `float64`-precision cost recomputed from `best_tour`; `None` exactly when `best_tour` is `None`. Never use infinity, NaN, or an empty tour as a missing-solution sentinel. |
| `work` | Non-negative integer counters for actual completed work, as below; never substitute the requested limits for observed counts. |
| `termination_reason` | `work_limit`, `time_limit`, or `algorithm_stop`. The last requires an explicit variant-specific rule, such as the baseline SA's empty neighborhood for fewer than four cities; it is not an implicit convergence claim. |
| `history` | `None` when recording is disabled; otherwise an ordered sequence of progress points. An enabled history is empty if no complete solution was observed. |

`tsp_hpc.solvers.results.SolverResult` owns a read-only tour snapshot and uses immutable `WorkCounters` (`.to_dict()` returns a detached dictionary). The constructor validates structure, not the objective against an instance. Phase fields `search_time_s` and `validation_time_s` follow the [API timing boundaries](usage.md#timing-and-results); they are not an end-to-end provenance envelope.

Use these common counters, with zero for algorithms to which they do not apply:

| Counter | Meaning |
| --- | --- |
| `nn_starts_completed` | Complete nearest-neighbour constructions |
| `ga_generations_completed` | Complete GA generations; initial-population evaluation is counted separately, as specified in the [GA baseline](algorithm-baselines.md#genetic-algorithm) |
| `sa_proposals_evaluated` | SA proposals for which the acceptance decision completed, including rejected proposals |
| `full_tour_evaluations` | Full objective evaluations performed during search, including initialization; excludes JIT warm-up and post-search validation/recomputation |

Incremental move-delta evaluations are not full-tour evaluations. Additional diagnostic counters are permitted if their meanings are documented. For a logical batch of searches, aggregate counters across those searches, not execution threads. Counters are not a claim that a GA generation and an SA proposal have equal computational cost.

A progress point contains `elapsed_s`, a snapshot of `work`, and `best_cost` for a complete tour. Timestamps are monotonic search-relative wall-clock observations, counters do not decrease, and best-so-far costs do not increase beyond validation tolerance. Recording must not consume randomness or alter proposal, selection, or acceptance rules. Its overhead can change how much work fits within a time limit and must be measured and reported. Sampling intervals and terminal-sample policy must be defined before enabling history or reporting time-to-target.

Malformed inputs and execution failures must not masquerade as `ok` or `no_solution`. The runner records failures separately, including their phase and error information. It wraps successful solver results with experiment IDs, effective configuration, seeds, timings, provenance, and execution metadata in a versioned run envelope. Numerical solvers do not write result files themselves.

## Validation and failure behavior

Validate once at the Python boundary before entering hot kernels:

- **Types and structure:** reject incompatible dtypes or object types with `TypeError`; reject wrong shapes, unsupported distance kinds, non-finite coordinates, and fewer than two cities with `ValueError`.
- **Tours:** reject missing, repeated, negative, and out-of-range city IDs. A closed array of length `n + 1` is invalid; do not silently truncate it or coerce floating-point indices to integers.
- **Configuration:** reject invalid seeds, unknown parameters, and invalid budgets before search. Legal parameter ranges and small-instance handling follow the [algorithm baselines](algorithm-baselines.md).
- **Numerical range:** report unsupported range or non-finite arithmetic as a numerical failure, not a usable tour cost.
- **Resources and capabilities:** the runner rejects unsupported execution options or insufficient resources explicitly; it does not silently switch distance conventions, shrink the workload, or choose another algorithm.

Do not repeat full instance or permutation validation on every iteration. Test operator invariants separately, and always validate final results before accepting them as benchmark observations.

## Boundary between search and execution

Keep scientific inputs separate from the execution plan:

| Solver/search responsibility | Runner/adapter responsibility |
| --- | --- |
| Tours, objective, initialization, operators, budgets, and logical random streams | Selecting implementation versions and serial/multicore or optional MPI/GPU execution |
| Best-solution tracking and work counters | Selecting and preparing dense/on-demand distances without changing the objective |
| Safe stopping and optional search progress | Worker/thread counts, affinity, NUMA policy, devices, ranks, and Slurm allocations |
| Returning owned in-memory results | JIT warm-up, preprocessing/search/end-to-end timing, independent validation, provenance, and persistence |

The runner selects a compatible solver adapter and prepares its numerical inputs; the adapter passes the chosen arrays and scalars to a specialized kernel. Hardware options and scheduler settings do not become scientific algorithm parameters, and hot kernels do not read environment variables or submit jobs. This separation does not require one dynamic distance callback or a class hierarchy inside Numba.

## Experimental comparisons

Separate these questions rather than using one speedup number for all of them:

| Question | Controlled comparison |
| --- | --- |
| Implementation speed or parallel scaling | Preserve data, variant, ordered logical searches, seeds and total work; change implementation/resources |
| Quality under limited time | Use a fixed wall-clock budget and report resources and completed work |
| Time to a useful solution | Predefine the quality target; retain trials that never reach it rather than silently excluding them |

More chains, a larger GA population or different island counts change search effort, not merely execution. Equal generation/proposal counts across algorithms are not equal compute budgets. Give stochastic algorithms comparable tuning opportunities on a tuning split, then evaluate on held-out inputs. Name algorithmic changes separately from implementation optimizations.

Report tour cost, gap to an objective-compatible certified optimum or explicitly labelled best-known reference, trial-to-trial variation, memory and wall time. For equivalent workloads report `S_p = T_1 / T_p` and `E_p = S_p / p`. Distinguish batched experiment throughput from acceleration of one fixed search. Parallel floating-point changes can alter trajectories; document precision and validate rather than assuming bitwise replay across runtimes.

Measure startup/imports, compilation/cache loading, preprocessing, steady-state search and end-to-end cost separately. State whether preparation is charged per instance, per trial or amortized; do not hide or double-count reuse. Include initialization and instrumentation in search; include final validation and persistence in end-to-end time. Account for communication, synchronization and device transfers when applicable; GPU timings need appropriate synchronization and an optimized, resource-labelled CPU comparison.

A runner must resolve/verify shared input artifacts, select compatible adapters, prepare and warm them, execute fresh logical searches, independently validate results, and persist successes, incomplete trials and failures with their phases. Record instance/configuration IDs, seeds, effective settings, implementation ID, source revision, runtime/compiler settings and actual resources. [Identity rules](manifests.md#identity-rules) separate configuration IDs from attempt IDs. Slurm owns allocations; the [operations guide](hpc-resources.md) owns launching, placement and storage policy.
