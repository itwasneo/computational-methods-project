# Usage

**Saved instance + manifest → registered runner → JSONL → analysis/report.**
Use the [development environment](development.md) and run commands from the repository
root. [Contracts](contracts.md) define the scientific rules; [manifests](manifests.md)
define configuration and identity. The [local API](interface.md) delegates to this same pipeline.

## Interactive experiments

For generation → configuration → execution → analysis without writing JSON, use the
[browser application](interface.md#start-the-application). It creates immutable inputs
and the accepted manifest through the shared backend, exposes job controls and displays
verified tours, comparisons and recorded histories.

The CLI workflow below is for scripted campaigns. The first example uses two files
already supplied in the checkout: `examples/instances/square.json` contains city
coordinates and provenance; `examples/manifests/nn-fixed-work.json` describes the
experiments. Neither needs downloading or generation. The manifest references the
instance ID; `--instance ID=PATH` tells this machine where to load the matching artifact.
The ID is stored at `instance.instance_id` in the artifact and is generated from the
ordered coordinate checksum, city count and objective—not its filename. For your own
scripted inputs, use [instance generation](#instances-and-artifacts) and construct the
manifest's instance entry from `record.descriptor`, rather than copying hashes manually.

## Run a manifest

```bash
mkdir -p results
uv run --locked tsp-hpc run examples/manifests/nn-fixed-work.json \
  --instance instance-v1-434c24120a05da693ed0f22582acc664ddef0f054a900905b4cf275ba68f5699=examples/instances/square.json \
  --output results/nn.jsonl
```

This runs two NN implementations in both distance modes: four trials and a campaign
summary. Each returns `[2, 1, 0, 3]`, cost `4.0`, and two completed starts. It is a
correctness example, not a performance measurement. **Use a new output path for each
attempt**; existing files are never overwritten or appended to. Create the parent first.

Repeat `--instance ID=PATH` for more inputs. Paths are relative to the working directory,
not the manifest. The loaded descriptor, including generation seed, must match exactly;
use `InstanceRecord.descriptor`. Canonical artifact IDs are not interchangeable with
planner aliases. No filename discovery, regeneration or TSPLIB loading occurs.
`--max-matrix-bytes` overrides the matrix-only ceiling without changing representation.

The Python entry point is `tsp_hpc.experiments.runner.run_manifest(manifest, *,
instance_paths, output_path, max_matrix_bytes=..., registry=None)`, returning the saved
summary. Malformed arguments, unknown bindings or duplicate plans (including resolved
aliases) fail before output creation. Missing inputs, unavailable capabilities and
solver/validation errors become failed trials; subsequent trials continue. CLI exits:
**0** all trials completed, **1** any failed trial, **2** invocation/preflight/shard-I/O
error. Interrupts propagate and preserve the written prefix.

Each trial reloads input and prepares/warms fresh adapters (**`fresh-per-trial`**).
Within a trial an adapter is reused, but each invocation gets fresh RNG/search state
and detached settings. NN uses the trial seed and ordered starts; independent populations/
chains use logical-stream seeds. Native caches may reduce warm-up cost, not scientific
work. Even an empty runner workload follows full setup policy. Explicit trusted Python
registries support [adapter integration](development.md#solver-registration-and-conformance),
not caller-supplied imports or fallback algorithms.

## Analyze result shards

```bash
uv run --locked --group analysis tsp-hpc analyze results/nn.jsonl \
  --output results/nn-report \
  --manifest examples/manifests/nn-fixed-work.json \
  --baseline visited-scan-v1 \
  --references examples/references/square.json
```

Pass multiple shards to combine campaigns. The output directory must be new with an
existing parent; raw shards are unchanged.

| File | Contents |
| --- | --- |
| `trials.csv` | Scalar observations, identities, settings, phase/receipt timings and exclusion flags; no tour arrays |
| `summary.csv` | Group counts (attempt/failed/no-solution/incomplete/duplicate/usable), count/min/median/mean/max/sample deviation |
| `comparisons.csv` | With a baseline: paired attempts, baseline/target duration ratio or rejection reason |
| `gaps.csv` | With references: matched usable attempts, reference kind/source/cost and gaps |
| `progress.csv` | Recorded logical observations, search clocks, cumulative incumbent/work and eligibility |
| `time-to-target.csv` | With `--target INSTANCE_ID=COST`: observed crossings, censoring and exclusions |
| `report.json` | Source hashes, analysis runtime, configuration, diagnostics, figures and limitations |
| PNG figures | Runtime/quality summaries and applicable ratios/gaps/history observations |

Groups preserve workload, execution and observed runtime, excluding only repetition.
Unknown/dirty source is isolated per campaign. `G1`, `G2`, … are presentation labels
mapped in `summary.csv`, not identities. Plots use eligible observations and never pool
different instances/settings. Statistics are descriptive; singleton sample deviations
are blank, not zero. Overflow-safe aggregates retain large finite values.

Exit codes: **0** no detected issues, **1** failed/incomplete/missing/duplicate observations
or blocked comparisons, **2** invalid records/options or I/O errors. Above
`--max-plot-groups` (default 40), figures are omitted with a note; tables remain complete.
Review paths/provenance before sharing reports.

### Validation and comparison rules

The reader accepts runner envelopes v1/v2 (fixed-work NN) and v3 (logical outcomes).
Shared manifest reconstruction checks IDs/seeds/streams; validation covers tour structure,
actual work, stopping/history, metadata, reduction, timings, summary counts and receipts.
Historical implementations need not be installed. Record validation does **not**
authenticate provenance/reference optimality or recompute costs without original coordinates.

- Malformed complete records, unsupported versions, duplicate attempts/campaigns,
  conflicting IDs and contradictory summaries are errors, not skipped rows.
- A missing summary or unterminated last line leaves an incomplete campaign. Its valid
  prefix remains inspectable but excluded from aggregates.
- Repeated execution IDs are duplicate attempts, not new repetitions; all ambiguous
  copies are excluded. Select intended shards explicitly; no “latest wins” policy exists.
- `--manifest` detects wholly absent/unexpected executions; shards alone cannot reveal them.

`--baseline` names an implementation, matched within each target's distance mode unless
`--baseline-distance-mode` is supplied. `--metric` is `search_time_s` or
`end_to_end_time_s`. Ratios pair **search IDs**, not file order or aggregate medians.
They require fixed-work-only budgets, unique usable attempts, equal logical work and
returned tours/costs, clean known source, compatible observed runtime/resources/setup,
and positive finite durations. Incompatible pairs retain reasons. Timed/combined budgets
measure quality under budget, not implementation-speed ratios.

These are observed ratios, not hardware attestation or controlled speedup evidence.
Cache/order/warm-up effects need experimental controls; overlapping timings cannot be
summed. See [comparison methodology](contracts.md#experimental-comparisons).

### Explicit reference costs

[Reference JSON](../examples/references/square.json) has `schema_version: 1` and a
`references` list: one entry per instance with `instance_id`, `coordinate_checksum`,
`distance_kind`, `kind` (`exact` or `best_known`), finite nonnegative `cost`, and nonempty
`source`. Checksum and objective must match; rounded optima cannot benchmark unrounded runs.

Absolute gap is `observed - reference`; percentage gap is `100 * gap / reference`.
Zero references/unrepresentable percentages yield a blank percentage. Negative best-known
gaps are valid; a solution below a declared exact optimum beyond tolerance is rejected.
Kinds remain declarations, not new optimum proofs. Unmatched observations get no fabricated gap.

### Reusable data API

`tsp_hpc.analysis` exports:

- `read_shards(paths, *, expected_manifest=None, max_record_bytes=...)` → `Analysis`
  with compact pandas `.trials`, structured `.diagnostics` and hashed `.sources`.
- `summary_table(analysis)`, `paired_comparisons(analysis, *, baseline_implementation,
  baseline_distance_mode=None, metric=...)`, `reference_gaps(analysis, document)` → DataFrames.
- `iter_progress`, `progress_table` and `time_to_target`, described below.

The reader retains O(trials) scalar rows and lazy source locators, not tours/plans.
`analysis.trial(attempt_id)` reopens details and checks source metadata/digest. The
64 MiB default line bound is not an RSS limit. `read_sources` applies the same validator
to authorized binary sources with optional bounds/cancellation. Neither reader owns HTTP
or caches. `analysis.reports.write_report` is the separate CSV/Matplotlib layer.

Rows distinguish work/time/combined budgets, nullable work limits, logical invocation
count, history policy/count and termination. `time_overshoot_s` sums each invocation's
nonnegative overrun, not a multi-search duration against one allowance.
`budget_summary_json` omits NN start arrays; `logical_work_id` fingerprints ordered
work/status/termination without retaining whole logical records.

### Histories and observed time-to-target

`progress_table(analysis, *, attempt_ids=None, max_points=100_000)` materializes the
shared `iter_progress` iterator over source-checked records. `elapsed_s` belongs to one
logical invocation; `search_elapsed_s` concatenates logical search durations, **excluding
setup/validation/persistence gaps**. It is not trial wall time. Cumulative work/incumbents
use only evidence available in that order. Unusable prefixes retain their flags; there
is no interpolation, invented tour or conversion of final costs into history.

`time_to_target(analysis, *, instance_id, target_cost, attempt_ids=None, max_points=100_000)`
uses a finite nonnegative target for one instance. It returns first **observed** crossings,
not exact first hitting times. Eligible non-reaching/no-solution trials remain censored
through the observation end; missing histories and failed/incomplete/duplicate attempts
retain exclusion reasons. A crossing in a failed prefix is not eligible success.

Use [nn-timed-history.json](../examples/manifests/nn-timed-history.json) with the same square
and a fresh shard, then analyze with
`--target instance-v1-434c24120a05da693ed0f22582acc664ddef0f054a900905b4cf275ba68f5699=4`.
`write_report(..., target_costs={instance_id: cost})` provides the Python equivalent.
`--max-history-points` / `max_history_points` bounds the **entire selected history**:
excess fails before output creation, never silently samples. History plots show points
only and obey the attempt-count plot ceiling. Targets are goals, not optimality claims.

## JSONL records and timing boundaries

A new shard uses envelope `schema_version: 3` and one fresh `campaign_id`. Envelope,
manifest, solver-variant and HTTP versions are independent; a campaign cannot mix envelopes.

| Record/field | Meaning |
| --- | --- |
| `trial` | Fresh attempt ID and UTC start; requested plan; effective execution/ID; input descriptor/provenance/path; runtime, metadata, timings, outcome/result/error |
| `logical_results` | Ordered `{kind, logical_id, seed, result, timings}`: one NN batch or independent population/chain entries; `budget_scope="per-logical-invocation"` |
| Aggregate `result` | Sum of accepted work; minimum independently recomputed cost, earliest logical entry on exact ties; `best_logical_index` or null; common termination or `mixed`; no aggregate history |
| Failure | Null aggregate; `{phase, type, message, reported_work, logical_index}` error; accepted prefixes retained but not usable; unaccepted work not added |
| `campaign_summary` | Planned/completed/failed counts, setup/persistence/end-to-end time and ordered per-attempt post-write timing receipts |

Omitted implementation labels resolve before effective identity; requested plans remain
unchanged. Unsupported capability trials have null effective execution. Both `ok` and
normal `no_solution` count as completed trials. A summary means all trials were written,
not that all succeeded. Failed invocations stop that trial, not subsequent trials;
interrupts and persistence errors abort the campaign.

Timings are successful phase observations; absent failure fields mean unknown, not zero.
Distance-preparation wall time includes helper warm-up and preprocessing; solve wall time
includes wrapper overhead, search and solver validation. Runner validation is additional.
These nested fields **overlap**. Logical timings sum accepted entries only.

`pre_persistence_time_s` ends before trial serialization. Summary receipts span trial
entry through that line's `fsync`; persistence time includes serialization/write/flush/
fsync. Campaign end-to-end spans runner entry through all trial writes, including planning
and provenance setup, but excludes manifest parsing, process startup/imports and the
summary's own write. Measure whole-command duration externally when needed.

The shard has one writer, exclusive creation and per-line flush/fsync. Interrupted I/O
can leave a torn final line; retain the prefix, never append or invent a footer.
Runtime observations include package/platform/CPU/affinity, compiler, resource and
allowlisted numerical/Slurm settings. Host memory is not a cgroup/allocation limit;
labels and environment settings do not certify resources. Source identity comes from
the package, not the caller's working directory.

### Native execution and compiler observations

V3's paired `runtime.compiler` and `native_execution` observations distinguish declared
native requirements from verification. Resolved settings include JIT, bounds checks,
optimization (`max` versus `3`), vectorization/AVX/debug, CPU targeting and LLVM host
observations. They affect comparison context, not scientific IDs or RNG streams.

Native preparation/warm-up rejects disabled JIT with `NativeExecutionError`. The runner
checks selected warmed CPU dispatchers for nopython signatures and strict serial options,
recording names/signatures/options. `execution_kind` retains the declared requirement;
`native_verified` records actual verification. A warm-up failure cannot supply accepted
logical results and remains in the same workload denominator.

Older v3 and v1/v2 observations remain `legacy-unobserved`, without retroactive native
certification or altered historical IDs. Missing/different compiler observations do not
pair with known ones. Keep settings fixed per process and use a fresh trusted
`NUMBA_CACHE_DIR` when source/compiler/CPU targets change. These checks do not prove cached
machine-code origin, absence of embedded object-mode regions, hardware identity or performance.

## Build provenance

`runtime.build` (v2/v3) contains `schema_version: 1`, `method`, `status` and nullable
`identity`; `runtime.source` retains revision/dirty observations.

| Evidence | Method / status | Treatment |
| --- | --- | --- |
| Package's Git checkout | `git-checkout` / `verified` | Observed revision/dirty state; dirty remains ineligible |
| Matching installed payload | `package-sha256-v1` / `verified` | Source and compact identity from the verified build manifest |
| No metadata/checkout | `unknown` / `unavailable` | Null source/identity; descriptive results only |
| Malformed/conflicting payload evidence | `package-sha256-v1` / `invalid` | Null source/identity; no fallback to a clean claim |

The [controlled producer](development.md#verified-installed-builds) embeds installed-relative
file hashes for the package and original distribution metadata. The manifest itself,
mutable wheel `RECORD`, installer-generated files and `__pycache__` are excluded. Added/
missing package files, symlinks, ambiguous distributions, wrong ownership/version and mixed
import origins invalidate evidence. Runtime checks the bounded manifest (≤1 MiB) and files
at campaign setup, not during search; analysis can only check the recorded identity.

The compact identity contains schema/method/source/package version, `pyproject_sha256`,
`lock_sha256`, `payload_sha256` and `build_id`. Using sorted-key, ASCII, finite compact JSON:

- `payload_sha256 = SHA256(b"tsp-package-files-v1\0" + JSON(inventory))`.
- `build_id = "build-v1-" + SHA256(b"tsp-build-v1\0" + JSON(identity without build_id))`.

The wheel hash lives in its external receipt to avoid self-reference. Image/SIF checksums
are separate and cannot substitute for package identity. Trials retain the compact identity,
not inventories. Tables expose `source_method`, `build_status`, `build_id`; v1 is
`legacy-source` / `reported`. Different build/legacy contexts do not automatically pair.

This is consistency evidence, **not cryptographic authenticity**. Trust the producer,
Git snapshot, interpreter, dependencies and caches; avoid source overlays, concurrent
mutation and monkeypatching. Hashes cannot attest loaded bytecode/JIT state or hostile
operators/OS/hardware. Missing/invalid provenance need not prevent valid science, but
blocks eligible paired ratios. Unknown/dirty evidence is never repaired by filtering.

## Canonical data and independent validation

For library examples, use `uv run --locked python`. `TSPInstance` owns read-only canonical
coordinates and uses object-identity equality. Validators do not coerce, reorder or mutate
inputs; loaders normalize explicitly. NumPy immutability is a contract, not hostile-user protection.

```python
import numpy as np
from tsp_hpc.problem.data import TSPInstance, validate_tour
from tsp_hpc.problem.objective import tour_cost, validate_solution

instance = TSPInstance("triangle", np.array([[0., 0.], [3., 0.], [3., 4.]]))
tour = np.array([0, 2, 1], dtype=np.int64)
validate_tour(tour, instance.n_cities)
assert tour_cost(instance, tour) == validate_solution(instance, tour, reported_cost=12.) == 12.
```

`validate_coordinates` checks representation; `validate_city_index` accepts in-range
Python/NumPy integers except booleans. `edge_cost`, `tour_cost` and `validate_solution`
provide independent ordinary-Python objective checks. Use them at boundaries, not each
hot-loop proposal; [contracts](contracts.md#tour-representation-and-shared-objective)
define accumulation, tolerance and numerical errors.

## Instances and artifacts

`problem.instances` provides `uniform_instance`, `clustered_instance`, `fixture`,
`fixture_names`, `save_instance` and `load_instance`. Constructors return `InstanceRecord`:
owned `.instance`, detached `.descriptor`/`.provenance`, and complete `.to_dict()`.

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from tsp_hpc.problem.instances import uniform_instance, save_instance, load_instance
from tsp_hpc.reproducibility.random_streams import derive_stream

seed = derive_stream(123, "instance", "uniform-v1", "sample-a").seed
record = uniform_instance(32, seed=seed, side_length=100.)
with TemporaryDirectory() as directory:
    path = Path(directory) / "sample-a.json"
    save_instance(record, path)
    assert load_instance(path).to_dict() == record.to_dict()
```

Use saved coordinates across experiments, not regeneration during search. The
[example collection](../examples/instances/README.md) is a repository asset, not an
installed package resource; bind paths explicitly outside the checkout.

### Generation protocols

Counts/seeds are Python integers, lengths built-in int/float; booleans and NumPy scalars
are rejected. Require `n_cities >= 2`, uint64 seed, finite positive `side_length`,
`1 <= n_clusters <= n_cities`, and finite nonnegative `standard_deviation`. Defaults:
side length 1, four clusters, deviation 0.05, objective `euclidean_2d`.

Each call uses private `Generator(PCG64(seed))`; seed is effective, not derived again.

- `uniform-v1`: draw one C-order `(n,2)` `rng.random()` array and multiply by side length.
  The mathematical interval is `[0, side_length)`; scaling can round to its upper boundary.
- `clustered-v1`: draw centres uniformly; shuffle `arange(n) % n_clusters` labels; draw
  `(n,2)` standard normals, scale by absolute deviation and add labelled centres.
  Populations differ by at most one. Offsets are **not clipped**; negative coordinates,
  duplicates and overlap are valid. Zero spread places cities at centres; overflow fails.

Never sort/deduplicate coordinates or derive them from solver/worker state. Changing
objective changes identity, not draws. Retain root/sample derivation separately.

### Correctness fixtures

The natural tour `[0, ..., n-1]` attains these optima under both data/reference conventions:

| Fixture | Coordinates | Optimum |
| --- | --- | --- |
| `two-city` | `(0,0), (3,4)` | 10 |
| `triangle` | `(0,0), (3,0), (3,4)` | 12 |
| `square` | `(0,0), (1,0), (1,1), (0,1)` | 4 |
| `collinear` | `(0,0), (1,0), (3,0), (6,0)` | 12 |
| `duplicates` | `(0,0), (0,0), (3,4), (3,4)` | 10 |
| `coincident` | four copies of `(0,0)` | 0 |

Line/duplicate tours must reach both extrema and return; the square perimeter is optimal
(and rounded distinct-city edges all cost one). Tests exhaustively score tiny permutations
with the independent reference; these are correctness fixtures, not representative benchmarks.

### Artifact guarantees

Artifacts contain exactly version 1, `instance` descriptor, ordered `coordinates` and
`provenance` (generator/settings/seed/bit generator/original runtime). Loading checks
[content identity](manifests.md#encoding-contract), fixture definitions and metadata,
rejecting duplicate/malformed fields, nonfinite values and contradictions. Signed zero
and row order survive JSON round trips; no pickle or automatic regeneration is used.
Checksums are not authenticity or cross-runtime regeneration guarantees.

Artifacts occupy O(n) memory. Saving needs an existing parent, refuses overwrite and
is not crash-atomic; interrupted files must pass validation before use.

## Run nearest-neighbour

```python
from tsp_hpc.problem.instances import fixture
from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.solvers.nn.solver import NearestNeighbourSolver

instance = fixture("square").instance
prepared = prepare_distances(instance, mode="dense", max_matrix_bytes=1024)
solver = NearestNeighbourSolver(prepared, implementation="visited-scan-v1")
result = solver.solve(instance, seed=42, budget={"start_cities": [2, 0]}, parameters={})
assert result.best_tour.tolist() == [2, 1, 0, 3]
assert result.best_cost == 4.
assert result.work.nn_starts_completed == result.work.full_tour_evaluations == 2
assert prepared.metadata["matrix_bytes"] == 128
```

Reuse the adapter only with the **same instance object** used in preparation. It retains
no last-run solution/counters/RNG state. Starts are ordered Python integers; parameters
are empty. [NN rules](algorithm-baselines.md#greedy-nearest-neighbour) define ties and work.
The module-level `solvers.nn.solver.solve` uses on-demand/default execution and includes
setup; an empty convenience request skips native work. An existing adapter's empty solve
reports zero search/validation time; runner setup policy is separate.

### Implementation selection

The two serial implementations follow `nn-fixed-starts-v1` and are listed by `implementation_names()`:

| Implementation | Candidate search |
| --- | --- |
| `visited-scan-v1` (default) | Scan all city IDs, skipping visited entries |
| `remaining-array-v1` | Scan unvisited IDs; swap-remove the winner; explicit `(cost, city ID)` ties |

Both cost O(S n²) time for S starts and O(n) workspace plus O(S) starts. The compact pool
reduces per-start candidate-loop iterations from `n(n-1)` to `n(n-1)/2`; both evaluate the
latter number of candidate distances plus n scoring edges. Raw workspace is `24n-8`
versus `17n` bytes, excluding arrays' overhead/copies and distance storage. These are
operation/storage counts, not measured speedups. Allocation/reset is inside search timing.

Choose a stable implementation ID explicitly; [manifest labels](manifests.md#execution-configurations)
affect execution identity, not scientific work. Register optimized kernels alongside the
baseline; changed search rules require a different variant. Never execute multicore plans
as serial merely because the planner accepts them.

The production registry additionally exposes fixed-work multicore
`visited-scan-threaded-v1`. It requires `backend: multicore`, one process and an
explicit supported thread count; time/history requests are rejected. See
[exact references, native-thread resources and the scaling pilot](exact-and-multicore.md)
for direct reference generation, manifest examples and local application controls.

## Distance preparation and compiled callers

`problem.distances.prepare_distances` returns factory-only `PreparedDistances`, sharing
read-only coordinates. Each call prepares fresh storage, without a global cache/fallback.
Checked `.edge_cost(a,b)` and `.tour_cost(tour)` validate structural inputs.

| Mode | Storage/checks |
| --- | --- |
| `dense` | Read-only C-contiguous float64 `(n,n)`; compute unordered pairs, mirror, zero diagonal; no second quadratic temporary |
| `on_demand` | Shared coordinates and `(0,0)` matrix placeholder; only queried edges are validated |

`estimated_matrix_bytes(n)` is exact `8*n*n`. Default `max_matrix_bytes` is **256 MiB**;
exact-limit allocations pass, oversize fails before warm-up/allocation, and platform
overflow fails explicitly. On-demand permits zero. This bounds matrix elements, not RSS,
JIT, `16n` coordinate bytes or workspace; real allocation can still fail. Finite edges
can overflow a total; [numerical failures](contracts.md#distance-conventions-and-representations)
propagate rather than becoming partial usable solutions.

Compiled callers pass primitive arrays/scalars to `distance_tour_kernel(coordinates,
matrix, use_dense, tour)`, `distance_edge_kernel(..., a, b)` or
`coordinate_edge_kernel(coordinates, a, b)`. Kernels trust canonical finite arrays,
valid indices/permutations and the prepared matrix/flag pairing, not arbitrary weights.
Validate at the Python boundary; retain numerical guards and `fastmath=False`.
Keep complete construction/operator loops compiled instead of dispatching each proposal
from Python. Extra prepared state needs an explicit timed preprocessing boundary.

## Timing and results

The adapter independently verifies its selected native tour/score and returns the
reference cost, without reranking candidates or changing native ties. Finite positive
`budget.time_limit_s` stops NN at complete-start checkpoints; enable `record_history=True`
and optional `history_policy` for observations. Instrumented per-start calls charge their
overhead to search; fixed-work/history-disabled execution uses one native batch call.
[Shared stopping/history rules](contracts.md#solver-results-and-progress) define empty
histories, terminal samples and bound errors; failures are not `no_solution` or infinity.

| Observation | Included work |
| --- | --- |
| `prepared.metadata["jit_warmup_time_s"]` | Tiny distance-helper warm-up, compilation/cache loading |
| `prepared.metadata["preprocessing_time_s"]` | Validation, representation allocation/fill and read-only marking after warm-up |
| `solver.metadata["solver_warmup_time_s"]` | Selected solver's tiny warm-up in both modes |
| `result.search_time_s` | Workspace allocation/reset, search, scoring, best copies and instrumentation |
| `result.validation_time_s` | Independent post-search permutation/cost checks |

Metadata snapshots are detached and retain original setup timings on reuse. Request
validation/marshalling precedes search; result packaging/copies follow validation.
Loading/startup/persistence are outside these fields, so their sum is **not end-to-end**.
Use the [runner timing receipts](#jsonl-records-and-timing-boundaries) for that boundary.
