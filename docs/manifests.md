# Experiment manifests, identities, and random streams

**Status:** task #6 implements fixed-work CPU manifest validation and planning. It does not generate instances, execute solvers, allocate resources, or write benchmark results. Those remain separate tasks. No new dependencies are required.

## API and example

| API | Purpose |
| --- | --- |
| `tsp_hpc.identity.coordinate_checksum(coordinates)` | Fingerprint canonical coordinate content |
| `make_instance_id(coordinates, distance_kind="euclidean_2d")` | Generate a stable content-based instance ID |
| `describe_instance(instance, instance_seed=None)` | Describe an existing `TSPInstance` for a manifest; preserve its supplied ID |
| `tsp_hpc.manifests.Manifest(document)` | Validate a JSON-compatible dictionary and snapshot resolved defaults |
| `Manifest.from_json(text)` / `Manifest.from_file(path)` | Load strict JSON; reject duplicate keys and non-finite values |
| `manifest.to_dict()` | Return a detached, normalized document |
| `manifest.expand()` | Return immutable `TrialPlan` records in declared order; reject duplicate executions |
| `tsp_hpc.random_streams.derive_stream(root_seed, role, *logical_ids)` | Plan a role-specific stream without consuming RNG state |
| `stream.generator()` | Create fresh, privately owned NumPy `Generator(PCG64(seed))` state |

[The example manifest](../examples/manifests/fixed-work.json) describes the unit square with city rows `[(0, 0), (1, 0), (1, 1), (0, 1)]`. Its checksum matches those exact `float64` coordinates. It expands to seven plans: one NN, two GA repetitions, and two SA repetitions each under serial and multicore execution. These are toy configurations, not tuned parameters or equal-compute comparisons.

Run this from the repository root in the installed environment:

```python
from tsp_hpc.manifests import Manifest

manifest = Manifest.from_file("examples/manifests/fixed-work.json")
plans = manifest.expand()
assert len(plans) == 7

# Same SA repetition and four logical chains, requested on one versus four workers.
serial, multicore = plans[3:5]
assert serial.search_id == multicore.search_id
assert serial.execution_id != multicore.execution_id
assert serial.logical_searches == multicore.logical_searches
assert len(serial.logical_searches) == 4
assert serial.search["budget"]["max_proposals"] == 10_000  # per chain

# Planning random draws, not executing an SA solver.
chain = serial.logical_searches[0]
rng = chain.stream.generator()
first_draw = rng.random()
```

`TrialPlan.search`, `.execution`, and `.to_dict()` return detached dictionaries. Editing them or the original input does not modify the plan or its IDs. `Manifest.expand()` materializes a tuple suitable for initial pilots; it is not a scheduler or a streaming campaign executor.

## Manifest v1 schema

The top-level JSON object requires exactly `schema_version: 1`, `instances`, and `experiments`. Both lists must be non-empty. Unknown fields, missing required fields, duplicate instance IDs, non-finite numbers, and inappropriate types are errors. Use JSON-native Python types; booleans and NumPy scalars are not accepted as integer seeds/counts. JSON tools must preserve integer precision for 64-bit seeds.

### Resolved instance descriptors

Each instance requires:

- `instance_id`: non-blank logical ID, unique in this document.
- `coordinate_checksum`: lowercase 64-character SHA-256 digest from `coordinate_checksum()`.
- `n_cities`: integer, at least two.
- `distance_kind`: `euclidean_2d` or `tsplib_euc_2d`.
- Optional `instance_seed`: the generation seed as a Python integer in `[0, 2**64)`, or `null` for deterministic/imported data. Omission resolves to `null`.

Descriptors refer to **already resolved data**, not generation requests or file locations. Parsing validates metadata, not the existence or content of a data file. A future loader/runner must resolve the ID and compare the actual instance's ID, checksum, size and distance convention before execution. Full generation/source provenance belongs with the instance artifacts from tasks #7/#10.

For new generated data, use `make_instance_id()` when constructing the instance, then `describe_instance()` to create its descriptor. Existing meaningful logical IDs are also allowed: the checksum prevents a changed dataset from retaining the same search identity merely because its label was reused.

### Experiments

| Field | Rule |
| --- | --- |
| `instance_id` | Required reference to an instance in the document |
| `algorithm` | Required: `nn`, `ga`, or `sa` |
| `variant` | Optional; resolves to that algorithm's agreed v1 variant; alternatives are rejected |
| `parameters` | Optional object; resolves every parameter default from the [baseline specification](algorithm-baselines.md) |
| `budget` | Required, explicit algorithm-specific fixed-work limit |
| `solver_seed` | Required root seed, a Python integer in `[0, 2**64)` |
| `repetitions` | Required non-empty list of distinct, non-negative integer repetition IDs, not a count |
| `logical_search_ids` | GA/SA only: ordered distinct non-blank strings; defaults to `["0"]` |
| `record_history` | Optional; only `false` is supported in v1 |
| `executions` | Required non-empty list of requested execution configurations |

Scientific configuration follows the agreed variants without adding implicit restarts or initialization changes:

- **NN:** parameters must be empty. `budget.start_cities` is an explicit ordered list of distinct in-range city IDs, possibly empty. It defines the logical starts; a separate `logical_search_ids` field is rejected.
- **GA:** require `budget.max_generations >= 0`. Defaults are population 128, tournament 3, elites 1, crossover probability 0.9 and per-child mutation probability 0.2. Validate population >= 2, tournament >= 1, `0 <= elites < population`, and probabilities in `[0, 1]`. Sampling with replacement allows tournaments larger than the population. Each logical search ID denotes an **independent population**, not a migrating island.
- **SA:** require `budget.max_proposals >= 0` **per chain**. Defaults are initial temperature 1.0, cooling factor 0.999, temperature floor `1e-12`, and cost-check interval 1024. Temperatures must be finite and positive, the floor must not exceed the initial temperature, cooling must lie in `(0, 1]`, and the check interval must be a positive integer.

Empty NN start lists and zero GA/SA budgets remain explicit plans; they are not dropped. GA/SA initialization and small-instance stopping belong to the future solvers. Time limits, history requests, other variants, and extra parameters are rejected rather than ignored.

### Execution configurations

Each execution requires `backend` (`serial` or `multicore`) and a `resources` object. Optional `distance_mode` is `dense` or `on_demand`, defaulting to `on_demand`.

Resources require a non-blank `profile` label, with positive integer `workers` and `threads_per_worker`, each defaulting to one. Serial execution requires both counts to be one. A multicore request may use one worker/thread as a scaling baseline. No request may increase the number of logical searches implicitly.

A profile is an opaque deployment label, ideally versioned when its meaning changes. It does not prove hardware availability or authorize an allocation. Actual hardware, affinity, memory limits, image/source versions and job information must be recorded by the future runner. MPI/GPU backends and arbitrary resource fields are not supported in v1.

## Identity rules

Three identities have distinct meanings:

1. **Instance content:** `make_instance_id()` hashes coordinate checksum, city count and distance convention. Dense/on-demand representation and resources are excluded.
2. **Scientific search:** `search_id` hashes the complete resolved instance descriptor (including generation-seed provenance), algorithm/variant, effective parameters, per-search budget, solver root seed, repetition, ordered logical searches, history flag, and RNG scheme identifiers. Backend, worker counts, resource profile and distance representation are excluded.
3. **Requested execution:** `execution_id` hashes `search_id` together with the resolved execution configuration.

These are **configuration IDs, not unique run-attempt IDs or result checksums**. Repeating an identical configuration produces the same IDs. Task #18 must add actual execution provenance; retries/campaigns need their own attempt identifiers rather than overwriting previous results under an unchanged configuration ID.

Changing parameters or work budgets changes the search ID. Changing workers, threads, backend, profile or distance representation changes only the execution ID. Reordering experiment/repetition/execution lists changes expansion order, not the corresponding IDs. Inserting unrelated entries does not renumber existing trials.

The order of NN starts and GA/SA logical searches **is** scientific: it defines logical tie-breaking. Reordering that list changes the search ID while preserving the stream attached to each named search. Duplicate expanded execution IDs are rejected by `expand()`; use distinct repetition IDs for intended repeat trials. Nothing is silently deduplicated.

### Encoding contract

The v1 identity serializer uses Python JSON with sorted keys, compact separators, ASCII escaping and non-finite values forbidden. Values are schema-normalized before hashing; real-valued parameters normalize `1`/`1.0` and negative zero, while integer fields remain integers. Labels are literal strings, without Unicode or geometry normalization.

`stable_id(namespace, payload)` hashes the ASCII serialization of:

```text
{"schema": "tsp-identity-v1", "namespace": namespace, "payload": payload}
```

It returns `<namespace>-v1-<sha256 hex>`. `canonical_json()` and `stable_id()` are low-level helpers for already normalized JSON payloads, not substitutes for schema validation.

Coordinate checksums hash `b"tsp-coordinates-f64le-n2-v1\0"`, followed by the city count as an unsigned eight-byte big-endian integer, followed by C-order little-endian float64 coordinate bytes. City order and signed-zero bits are preserved. The convention is excluded from this raw coordinate checksum but included in the instance ID. There is no quadratic allocation.

Golden-vector tests pin these rules. An incompatible change to identity encoding, normalization, seed derivation or generator policy requires an explicit protocol-version decision, not silent ID reuse.

## Random-stream planning

`derive_stream(root_seed, role, *logical_ids)` hashes a framed payload containing `seed_scheme="sha256-uint64-v1"`, the root seed, role, and ordered logical-ID tokens. Tokens are non-blank strings or non-negative Python integers. The stream retains its full SHA-256 ID and uses the first 16 digest hex digits (64 bits, big-endian interpretation) as a PCG64 seed. High seed bits are never truncated to a 32-bit legacy seed.

The manifest first derives a per-repetition `TrialPlan.trial_seed` using:

```text
root: experiment.solver_seed
role: "solver"
IDs: instance-reference ID, variant, repetition ID
```

The instance-reference ID includes logical instance ID, coordinate checksum, size and distance convention, but excludes `instance_seed`. Instance-generation provenance is never consumed as solver randomness. Logical streams then derive from `trial_seed`:

| Search | Role and logical ID |
| --- | --- |
| NN start | `("start", city_id)`; provenance only, since NN is deterministic |
| SA chain | `("chain", chain_id)` |
| Independent GA population | `("population", population_id)` |

The experiment's `solver_seed` is the user-supplied root, `trial_seed` is an intermediate namespace seed, and each logical search's `stream.seed` is its effective seed. Future GA/SA calls must receive the logical population/chain seed, not the root or intermediate seed. NN can receive the trial seed with its complete ordered start list; it does not use randomness.

For GA's future parallel operators, derive from the **population stream's seed** using `("initialization", slot)` and `("offspring", generation, slot)`; generation numbering starts at one. This prevents offspring scheduling order from selecting the random stream. A distinct `"warmup"` role separates warm-up from measured streams. The generic `"island"` role can reserve future island streams, but does not enable an island algorithm or MPI execution.

Parameters, budgets and collection size are deliberately absent from seed derivation. This provides paired initial streams across parameter/budget comparisons; adding a chain does not reseed existing chains. Different algorithms or draw-consumption patterns need not follow the same trajectories. Changing actual instance identity/content, solver root seed, repetition, role or logical ID changes the corresponding streams.

For future stochastic instance generation, derive a separate `"instance"` stream from an independently supplied generation root and generator/sample IDs. Store the actual generator seed in `instance_seed` and retain full generation provenance with the dataset. Never derive instance generation from a solver's mutable RNG state.

Each `stream.generator()` call creates fresh state at the stream's beginning. Retain and advance that object within one search; do not recreate it for every draw. Never share a mutable generator between workers/threads or seed process-global NumPy/Python RNG state. The tests include passing one owned generator to a serial Numba kernel; this is not a claim of parallel thread safety.

Hash-derived 64-bit seeds are practical stream assignments, not proofs of independent or non-overlapping sequences. Expansion detects a seed collision within a logical search collection and fails instead of silently reseeding. Golden vectors and tested distinct assignments do not eliminate the theoretical collision possibility. Bitwise agreement across different runtimes or distribution implementations is not promised; record package and backend versions with actual results.

## Validation

Run `uv run --locked pytest`. Tests cover strict schema boundaries, resolved defaults, zero budgets, detached snapshots, content fingerprints, golden identity/RNG vectors, independent global RNG state, separate starts/chains/populations/islands, per-slot GA stream paths, simulated scheduling reordering, and fresh processes with different `PYTHONHASHSEED` values. These are planning/correctness checks, not benchmark results.
