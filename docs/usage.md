# Usage

The working scientific path is **saved instance + manifest → NN runner → JSONL → analysis/report**. Start with the CLI workflow below; Python library examples follow. Use the installed [development environment](development.md) and run commands from the repository root. The [README](../README.md#current-capabilities) lists available features; [contracts](contracts.md) define scientific rules.

## Run a manifest

The [sequential runner](../src/tsp_hpc/experiments/runner.py) turns resolved plans and saved artifacts into a new JSONL shard. From the repository root, run the [NN example](../examples/manifests/nn-fixed-work.json):

```bash
mkdir -p results
uv run --locked tsp-hpc run examples/manifests/nn-fixed-work.json \
  --instance instance-v1-434c24120a05da693ed0f22582acc664ddef0f054a900905b4cf275ba68f5699=examples/instances/square.json \
  --output results/nn.jsonl
```

It executes both NN implementations in both distance modes, producing four trial records and one campaign summary. Each result has tour `[2, 1, 0, 3]`, cost `4.0`, and two completed starts. This is an integration example, not performance evidence. Use a **new output path** for another attempt; existing files are never overwritten or appended to.

Repeat `--instance ID=PATH` to bind more inputs. Paths are relative to the current working directory, not the manifest directory. No filename discovery, regeneration or TSPLIB loading occurs. The full loaded descriptor—including generation seed—must match the manifest; build descriptors from `InstanceRecord.descriptor`. This artifact-based runner requires canonical artifact IDs; it does not automatically rebind the generic planner's logical aliases. `--max-matrix-bytes` overrides the [matrix-only ceiling](#distance-preparation-and-compiled-callers) without allowing fallback to another representation.

The Python boundary is `run_manifest(manifest, *, instance_paths={instance_id: artifact_path}, output_path=..., max_matrix_bytes=...)` in `tsp_hpc.experiments.runner`; it returns the saved summary. Create the output parent first. Manifest parsing is separate; unknown bindings, malformed arguments, duplicate requested plans or collisions after default-implementation resolution fail before output creation. A missing binding/file, invalid artifact, unavailable capability or solver/validation error becomes a failed trial and execution continues in declared order. The CLI exits **0** if all trials completed, **1** if any trial failed, and **2** for invocation/preflight or shard-I/O errors. Interrupts propagate.

Each trial reloads its input and prepares/warms fresh adapters (**`fresh-per-trial`** policy). Native caches may make later warm-ups cheaper, but no mutable search state or prepared matrix is reused. NN receives the plan's trial seed and ordered starts; it consumes no randomness. Future stochastic dispatch must use the planned logical chain/population seeds, not this NN seed convention. Even an empty NN workload follows runner setup policy; its solver result is a normal `no_solution`, not a failed trial.

## Analyze result shards

Use the existing NN run as a small correctness example, not a speedup experiment:

```bash
uv run --locked --group analysis tsp-hpc analyze results/nn.jsonl \
  --output results/nn-report \
  --manifest examples/manifests/nn-fixed-work.json \
  --baseline visited-scan-v1 \
  --references examples/references/square.json
```

Supply multiple shard paths to combine campaigns. The output directory must be new and its parent must exist. The command leaves raw shards unchanged and creates:

| File | Contents |
| --- | --- |
| `trials.csv` | Compact observations, identities, configuration/context grouping keys, phase/receipt timings, outcomes and exclusion flags; no tour arrays |
| `summary.csv` | Per-group attempt/failure/no-solution/incomplete/duplicate/usable counts and count/min/median/mean/max/sample-standard-deviation statistics |
| `comparisons.csv` | When a baseline is supplied: paired attempt IDs, selected metric, baseline/target duration ratio or a rejection reason |
| `gaps.csv` | When references are supplied: matched usable attempts, reference kind/source/cost, absolute and percentage gaps |
| `report.json` | Source file hashes, analysis runtime versions/provenance, manifest/reference/plot configuration, diagnostics, requested comparison, available figures and limitations |
| PNG figures | Runtime summaries and quality distributions; speed ratios and reference gaps only when applicable |

Groups preserve workload (instance, parameters, ordered starts and solver root seed), execution and observed runtime, excluding only repetition. Unknown/dirty source is isolated per campaign; paired ratios require a clean known revision. `G1`, `G2`, … map to full group IDs in `summary.csv`; they are presentation labels, not identities. Quality plots include only usable observations and never pool different instances/configurations. Statistics are descriptive, not independence/significance evidence. Scaled means/deviations and overflow-safe medians support large finite values; singleton sample deviations are blank, not zero.

Exit codes are **0** without detected issues, **1** for failed/incomplete/missing/duplicate observations or blocked comparisons, and **2** for invalid records/options or I/O errors. Above `--max-plot-groups` (default 40), figures are omitted with a note; tables remain complete. Narrow large campaigns explicitly rather than silently sampling charts. Review paths/provenance before sharing reports.

### Validation and comparison rules

The reader accepts runner-v1 serial NN trials and recorded failures. Shared `Manifest` reconstruction verifies IDs/seeds/streams; record checks cover effective execution, tour structure, NN work, adapter metadata, timings, summary counts and receipt linkage. It does **not** authenticate provenance/reference optimality or independently recompute costs without original instances.

Malformed complete records, unsupported versions, duplicate attempts/campaigns, conflicting IDs and contradictory summaries are errors, not silently skipped rows. A missing summary or unterminated final line is an incomplete campaign: retain the validated prefix for inspection, but exclude it from numerical aggregates. Repeated execution IDs with distinct attempt IDs are retries/duplicate observations—not new statistical repetitions—and all ambiguous copies are excluded. Select the intended shard(s) explicitly; there is no automatic “latest wins” policy. `--manifest` detects entirely absent or unexpected requested executions; absent runs cannot be inferred from the available shards alone.

`--baseline` names an explicit implementation. By default it is matched within the target's distance mode; `--baseline-distance-mode on_demand` can explicitly compare representations too. `--metric` selects `search_time_s` (default) or `end_to_end_time_s` from the post-write receipts. Each ratio is paired by **search ID**, not file order or a ratio of aggregate medians. Require unique usable attempts, equal actual work and returned tours/costs (within objective tolerance), clean known source revision, and matching observed runtime/resources/setup. Incompatible or missing baselines get reasons, never a substitute chosen for being fastest.

These are **observed ratios**, not controlled speedup evidence: cache state, warm-up and order can dominate, and context matching is not hardware attestation. Timings overlap. Parallel efficiency and time/history plots require actual parallel/history evidence; never fabricate progress from final costs.

### Explicit reference costs

The [square reference](../examples/references/square.json) illustrates the versioned JSON format: `schema_version: 1` and a `references` list, each with `instance_id`, `coordinate_checksum`, `distance_kind`, `kind` (`exact` or `best_known`), finite non-negative `cost`, and a non-empty `source` citation/explanation. At most one reference per instance is accepted. References must match both coordinate checksum and objective; rounded optima cannot be used for unrounded executions.

Absolute gap is `observed_cost - reference_cost`; percentage gap is `100 * absolute_gap / reference_cost`. Zero references or unrepresentable percentages produce a blank percentage, retaining the absolute gap. A negative best-known gap is valid; a cost below a declared exact optimum beyond validation tolerance is rejected. The reference kind is preserved in tables/plots rather than turning every supplied value into an optimum claim. Unmatched references/observations are not assigned fabricated gaps.

### Reusable data API

`tsp_hpc.analysis` supplies `read_shards(paths, *, expected_manifest=None, max_record_bytes=...)`, `summary_table(analysis)`, `paired_comparisons(analysis, *, baseline_implementation, baseline_distance_mode=None, metric=...)`, and `reference_gaps(analysis, document)`. The first returns an `Analysis` with a compact pandas `.trials` table, structured `.diagnostics` and hashed `.sources`; the other functions return DataFrames. `tsp_hpc.analysis.reports.write_report(...)` is a separate Matplotlib/CSV presentation layer, not part of the numerical calculation API.

The reader retains O(trial count) scalar summaries/locators, discarding validated tours/plans. Its default encoded-line limit is 64 MiB, not an RSS ceiling. `analysis.trial(attempt_id)` reads details lazily and rechecks source metadata/record digest; changed sources require reload. `read_sources` uses the same validator over authorized binary sources for the interface, with additional optional bounds/cancellation. Neither reader owns HTTP or caches.

## JSONL records and timing boundaries

Every line has `schema_version: 1`, `record_type` and a fresh `campaign_id`. Treat these as runner-envelope versions, separate from manifest and solver variants.

- **`trial`:** fresh `attempt_id`, UTC start timestamp, complete `requested_plan` (science, seeds, streams and requested IDs), `effective_execution` and its `execution_id`, input path/descriptor/provenance, runtime observations, adapter metadata, matrix ceiling, setup policy, timings, outcome, result and error. Omitted implementation labels are resolved before effective identity assignment; requested plans stay unchanged. Unsupported capability records have null effective execution/ID rather than claiming execution occurred.
- **Results:** `outcome="completed"` carries the [shared result fields](contracts.md#solver-results-and-progress) as JSON, with a list tour and independently recomputed cost. Both `ok` and normal `no_solution` count as completed. `outcome="failed"` has a null result and `{phase, type, message, reported_work}` error; `reported_work` preserves returned counters if a result reached the runner, otherwise it is null. It does not invent counters or report a partial solution. A `solve` error may come from search or the adapter's own final validation. The runner independently validates every returned complete tour again before persistence.
- **`campaign_summary`:** planned-trial count, completed/failed counts, setup time, total trial persistence time and campaign end-to-end time. Its ordered `trial_timings` entries join by `attempt_id` and record each trial's persistence and end-to-end durations after its line was flushed/fsynced. Its presence means all planned trial records were written, not that every trial succeeded. The API returns this same dictionary.

`timings` records successful phase observations; missing fields on failures mean **unknown/not completed**, not zero. Distance-preparation wall time includes helper warm-up plus preprocessing. Solve wall time includes request/packaging overhead and the solver's search/validation phases. Runner validation is additional, not hidden in search counters. These nested fields overlap: do not sum all of them.

A trial's `pre_persistence_time_s` ends before its JSON serialization/write. Its summary timing entry measures from just before trial execution through that line's `fsync`, including input loading, fresh setup, solving, validation and persistence (but not shared campaign setup). Summary `trial_persistence_time_s` sums serialization, write, flush and `fsync` of trial lines. Summary `end_to_end_time_s` spans **runner API entry through all trial writes**, including planning, provenance collection, loading, setup, solving and validation. It excludes manifest parsing, process startup/imports, and the summary's own serialization/write; a record cannot include its own final write duration. Measure whole-command time externally if that boundary is needed.

Runtime observations are sampled at campaign start and include package/compiler-library versions, platform/CPU/affinity observations, one runner worker/serial solver thread, host physical memory and address-space soft limit where available, and an allowlist of numerical/Slurm environment settings. Host memory is not a container/cgroup or job allocation limit, and profile labels/environment variables do not certify resources. Source revision/dirty state come only from the package's source checkout; unavailable provenance (e.g. an ordinary installed wheel) is null, never inferred from an unrelated working directory. Retain image/build provenance separately for packaged deployments.

The shard has one writer, exclusive creation, and a flush/`fsync` after each line. Serialization finishes before writing, but I/O failure or interruption can still leave a partial last line. Failed writes abort; completed prefixes are retained. A missing summary marks an incomplete campaign. This is not transactional storage, automatic retry or resume support; do not append to or silently reuse an incomplete shard.

## Optional local workspace backend

The [interface guide](interface.md#browser-local-results) covers browser pairing, authorized shard ingestion and real trial tables. The API remains read-only; installed frontend serving, real-data geometry and solver jobs are not implemented.

For the library examples below, use `uv run --locked python` from the repository root.

## Canonical data and independent validation

`TSPInstance` owns a read-only coordinate copy under a logical ID and uses object-identity equality. [Data validators](../src/tsp_hpc/problem/data.py) check without conversion, reordering or mutation. Loaders must explicitly normalize noncanonical arrays; masked arrays are unsupported. Never coerce invalid tours into validity. Coordinates/flags/shape are immutable by contract, not protection against deliberate NumPy bypass.

```python
import numpy as np
from tsp_hpc.problem.data import TSPInstance, validate_tour
from tsp_hpc.problem.objective import tour_cost, validate_solution

instance = TSPInstance("triangle", np.array([[0., 0.], [3., 0.], [3., 4.]]))
tour = np.array([0, 2, 1], dtype=np.int64)
validate_tour(tour, instance.n_cities)
assert tour_cost(instance, tour) == 12.0
assert validate_solution(instance, tour, reported_cost=12.0) == 12.0
```

`validate_coordinates()` checks the coordinate representation; `validate_city_index()` accepts in-range Python/NumPy integers except booleans and returns a Python `int`. [objective.py](../src/tsp_hpc/problem/objective.py) supplies `edge_cost()`, `tour_cost()` and `validate_solution()` independently of compiled execution. Use them at boundaries, not on every hot-loop proposal. See the [numerical contract](contracts.md#tour-representation-and-shared-objective) for accumulation, tolerance and error rules.

## Instances and artifacts

[instances.py](../src/tsp_hpc/problem/instances.py) provides `uniform_instance()`, `clustered_instance()`, `fixture()`, `fixture_names()`, `save_instance()` and `load_instance()`. Constructors return an `InstanceRecord` with:

- `.instance`: the owned canonical TSP instance;
- `.descriptor`: a detached descriptor for a [manifest](manifests.md), including the effective generation seed or `None`;
- `.provenance`: detached generation settings and original runtime versions;
- `.to_dict()`: a detached complete artifact document.

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from tsp_hpc.problem.instances import uniform_instance, save_instance, load_instance
from tsp_hpc.reproducibility.random_streams import derive_stream

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

Artifacts contain exactly `schema_version: 1`, `instance` (ID/checksum/count/convention/effective seed), ordered `coordinates`, and `provenance` (generator protocol, effective parameters, seed, bit generator and original runtime versions). IDs follow the [identity encoding](manifests.md#encoding-contract), not filenames/provenance.

Loading verifies coordinate bytes, metadata and named fixtures; it rejects malformed/duplicate fields, unsupported versions, non-finite coordinates and conflicting identities/seeds/counts. Row order and signed zero survive JSON round trips. No pickle or automatic regeneration is used. Checksums are not authenticity; provenance is structurally validated, with no cross-runtime regeneration guarantee. Incompatible generators require a protocol-version decision.

JSON/coordinates occupy O(n) memory. Saving requires an existing parent, refuses overwrites and is not crash-atomic; interrupted files must pass loading/verification before use.

## Run nearest-neighbour

NN is a constructive heuristic, not an optimality guarantee. For explicit representation, implementation selection and setup reuse:

```python
from tsp_hpc.problem.instances import fixture
from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.solvers.nn.solver import NearestNeighbourSolver

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

The module-level `tsp_hpc.solvers.nn.solver.solve(instance, *, seed, budget, parameters, record_history=False)` is a convenience wrapper using on-demand distances and the default implementation. Its overall call includes setup; prefer the explicit adapter for setup accounting and reuse. Empty convenience work skips preparation/warm-up; an existing adapter's empty solve skips search/final validation and reports zero phase durations.

### Implementation selection

Both serial implementations follow `nn-fixed-starts-v1` and are listed by `implementation_names()`:

| Implementation | Candidate search |
| --- | --- |
| `visited-scan-v1` (unchanged default) | [Baseline](../src/tsp_hpc/solvers/nn/visited_scan.py): scan all city IDs, skipping visited entries |
| `remaining-array-v1` | [Compact pool](../src/tsp_hpc/solvers/nn/remaining_array.py): scan only unvisited IDs and remove the winner by swapping with the last active entry; explicit `(cost, city ID)` comparison preserves ties despite reordered storage |

Select the alternative with `NearestNeighbourSolver(prepared, implementation="remaining-array-v1")`. Both take O(S n²) time for S starts and O(n) working storage, plus an O(S) start snapshot. Per start, the compact pool reduces candidate-loop iterations from `n*(n-1)` to `n*(n-1)/2`; both evaluate that same latter number of candidate distances plus n edges for final tour scoring. It trades a boolean visited mask for int64 remaining IDs: raw working-array elements occupy `24*n-8` bytes versus `17*n` in the baseline, excluding starts, array overhead, result copies and separately prepared distance storage. All workspace allocation/reset stays inside search timing. These are operation/storage counts, **not a measured speedup**.

Register optimized kernels explicitly alongside that baseline in [solver.py](../src/tsp_hpc/solvers/nn/solver.py), under new stable IDs. The adapter resolves, warms and executes the selected callable and exposes its ID in metadata; unavailable IDs fail before warm-up. There is no filename-based discovery. Implementations must share scientific conformance tests; changes to search rules require a different variant, not an optimization label.

For a plan with an explicit label, pass `implementation=plan.execution["implementation"]` to the adapter. Labels, omission compatibility and scientific-versus-execution identity are specified once in [manifest execution configurations](manifests.md#execution-configurations). Planning does not authorize or dispatch execution; callers must check the requested backend rather than treating a multicore plan as serial.

## Distance preparation and compiled callers

[distances.py](../src/tsp_hpc/problem/distances.py) creates factory-only `PreparedDistances` records sharing the instance's read-only coordinates. Each call prepares fresh storage; no hidden global cache or fallback exists. Reuse the result outside iteration loops. Checked `.edge_cost(a, b)` and `.tour_cost(tour)` methods validate structural inputs before querying it.

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
from tsp_hpc.problem.instances import fixture
from tsp_hpc.problem.distances import prepare_distances, distance_tour_kernel

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
