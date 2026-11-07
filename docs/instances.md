# Generated instances and correctness fixtures

**Status:** task #7 implements reproducible input generation, a small shared collection, and verified saved-instance artifacts. No solver, benchmark campaign, TSPLIB importer, or cluster operation is implemented here.

## API

The public functions live in `tsp_hpc.instances`:

| Function | Purpose |
| --- | --- |
| `uniform_instance(n_cities, *, seed, side_length=1.0, distance_kind="euclidean_2d")` | Uniform cities in a square anchored at the origin |
| `clustered_instance(n_cities, *, seed, n_clusters=4, side_length=1.0, standard_deviation=0.05, distance_kind="euclidean_2d")` | Balanced Gaussian clusters around sampled centres |
| `fixture_names()` / `fixture(name, *, distance_kind="euclidean_2d")` | Enumerate and construct the six deterministic fixtures |
| `save_instance(record, path)` | Save coordinates, descriptor and generation provenance in one JSON file; never overwrite an existing path |
| `load_instance(path)` | Load and verify that artifact without regenerating coordinates |

Generators and fixture constructors return an `InstanceRecord`:

- `.instance`: the canonical, owned, read-only [TSPInstance](data-and-objective.md).
- `.descriptor`: a detached instance descriptor accepted by [Manifest](manifests.md), including the effective generation seed or `null`.
- `.provenance`: detached generation settings and original runtime versions.
- `.to_dict()`: a detached complete artifact document.

Use Python integer city/cluster counts and seeds, and built-in int/float scalar lengths. Booleans and NumPy scalars are rejected. Require `n_cities >= 2`, `1 <= n_clusters <= n_cities`, finite positive side length and finite non-negative standard deviation. The default four clusters therefore requires at least four cities; specify a smaller cluster count explicitly for smaller inputs. Seeds cover `[0, 2**64)`. Unsupported distance conventions and malformed parameters fail explicitly.

## Generation protocols

Each invocation constructs a private `Generator(PCG64(seed))`, where `seed` is the **effective generation seed**. It does not seed or consume process-global NumPy/Python RNG state or a solver's mutable RNG.

### Uniform v1

`uniform-v1` draws one C-order `(n_cities, 2)` array with `rng.random()` and multiplies it by `side_length`. The mathematical target is uniform sampling in `[0, side_length)` on each axis. Floating-point scaling can round a coordinate to the upper boundary. There is no rejection sampling, deduplication, or city sorting.

### Clustered v1

`clustered-v1` performs these operations in this order:

1. Draw `n_clusters` centres uniformly in the origin-anchored square.
2. Construct labels `arange(n_cities) % n_clusters`, then shuffle those labels using the same private generator.
3. Draw an `(n_cities, 2)` standard-normal array; multiply by `standard_deviation` and add the labelled centres.

Cluster sizes differ by at most one, and every cluster has at least one assigned city. The standard deviation is an absolute coordinate length, not a fraction of the square's side. The offsets are isotropic Gaussian draws, not uniform discs. **Cities are not clipped:** they may lie outside the square or have negative coordinates. Overlapping centres/clusters and duplicate coordinates are allowed. A zero standard deviation places each city at its assigned centre.

The label assignment, rather than spatial separation, defines the clusters: requesting four clusters does not guarantee four visually distinct groups. Finite parameter values that overflow coordinate arithmetic are rejected. Finite coordinates do not guarantee that every derived distance/tour total fits float64; the independent objective checks those operations when evaluated.

Both generators default to unrounded Euclidean distances. Changing `distance_kind` changes the objective/instance ID, not the generated coordinates. Small unit-scale coordinates under `tsplib_euc_2d` can produce many rounded zero edges and ties; these are valid, but should not be mistaken for representative benchmark instances.

## Deterministic fixtures

City IDs follow the listed row order. The natural tour `[0, 1, ..., n-1]`, with implicit closure, attains each listed optimum. These exact optima hold under **both supported distance conventions**; they are not merely best-known values.

| Name | Coordinates | Optimum | Expected property |
| --- | --- | --- | --- |
| `two-city` | `(0,0), (3,4)` | 10 | Both outward and return edges count; smallest supported size |
| `triangle` | `(0,0), (3,0), (3,4)` | 12 | 3-4-5 edge lengths; every tour has the same cost |
| `square` | `(0,0), (1,0), (1,1), (0,1)` | 4 | Symmetry and nearest-distance ties; rounded diagonals introduce additional ties |
| `collinear` | `(0,0), (1,0), (3,0), (6,0)` | 12 | A closed tour must travel between both extrema and return |
| `duplicates` | `(0,0), (0,0), (3,4), (3,4)` | 10 | Distinct city IDs at identical coordinates; zero edges and two crossings between locations |
| `coincident` | four copies of `(0,0)` | 0 | Every edge and every tour has zero cost |

The two-city/triangle optima follow directly from their edges; the line/duplicate optima from travelling between the extreme locations and returning. The unrounded square perimeter is optimal, and its rounded distinct-city edges all cost one. Tests additionally enumerate every tour with city zero fixed for these tiny fixtures, independently scoring them with the reference objective. This is a test oracle, not an integrated exact TSP solver.

## Saved artifacts and shared inputs

The [shared collection](../examples/instances/README.md) contains the six fixtures and two eight-city generated samples. These are development/correctness inputs, not a statistically representative benchmark suite. All solvers should load the **same saved coordinate artifact**, not independently regenerate a dataset during timed search.

The v1 JSON document contains exactly:

- `schema_version: 1`.
- `instance`: content-based instance ID, coordinate checksum, city count, distance convention and effective generation seed.
- `coordinates`: ordered pairs of numbers.
- `provenance`: versioned generator name, complete effective parameters, effective seed, bit-generator identifier, and Python/NumPy/package versions at construction.

The ID/checksum follow the existing [identity encoding](manifests.md#encoding-contract). Source filenames, runtime versions, generation parameters and seeds are provenance, not components of the content ID. Identical coordinate bytes and distance convention have identical content IDs even if their provenance differs. Save paths are not identities.

The reader rejects unknown/missing fields, duplicate JSON keys, non-finite coordinates, invalid types/shapes, unsupported versions, conflicting seeds/counts, stale IDs/checksums and mismatched named fixtures. It verifies actual coordinate content rather than merely trusting a declared checksum. It never loads pickle. JSON float round-trips preserve the saved float64 coordinates; city ordering and signed-zero bits are retained.

Checksums detect content mismatches, **not authenticity**. Generation provenance is schema-validated, but random data is not regenerated to prove that the recorded seed produced it. Loading preserves original runtime metadata even under a different runtime. Regeneration should use the recorded generator protocol and locked runtime; cross-version distribution-algorithm equivalence is not promised. Golden checksum/ID tests pin the current generators. Incompatible generator changes require a protocol-version decision.

The single-file JSON format deliberately favours inspectability for small inputs. It materializes coordinates and JSON in memory, with O(n) storage rather than a dense O(n²) matrix. It is not the final large-campaign storage format. Parent directories must exist; saving refuses overwrites. Writes are not crash-atomic: an interrupted write can leave a partial file, which must not be used unless loading and verification succeed.

## Example

Run in the installed development environment. This example uses a disposable directory rather than overwriting the committed collection:

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from tsp_hpc.instances import uniform_instance, save_instance, load_instance
from tsp_hpc.manifests import Manifest
from tsp_hpc.random_streams import derive_stream

# Optional deterministic assignment of separate dataset seeds from a generation root.
seed = derive_stream(123, "instance", "uniform-v1", "sample-a").seed
record = uniform_instance(32, seed=seed, side_length=100.0)

with TemporaryDirectory() as directory:
    path = Path(directory) / "sample-a.json"
    save_instance(record, path)
    loaded = load_instance(path)
    assert loaded.to_dict() == record.to_dict()

    manifest = Manifest({
        "schema_version": 1,
        "instances": [loaded.descriptor],
        "experiments": [{
            "instance_id": loaded.instance.instance_id,
            "algorithm": "sa",
            "budget": {"max_proposals": 10000},
            "solver_seed": 42,
            "repetitions": [0, 1],
            "executions": [{
                "backend": "serial",
                "resources": {"profile": "local-cpu"}
            }]
        }]
    })
    assert len(manifest.expand()) == 2  # Plans only; no solver is executed.
```

The artifact stores the effective seed passed to the generator. If a campaign derives it from a root and logical sample IDs, retain that parent derivation context in campaign provenance separately. Never use worker IDs, arrival order or a solver's RNG state to choose dataset seeds. Changing a solver repetition must not change the cities.

## Validation

`uv run --locked pytest -W error` checks private RNG ownership, seed boundaries, generation draw order, balanced cluster populations, absence of clipping, known fixture optima under both conventions, golden IDs/checksums, artifact round-trips and corruption rejection, manifest integration and subprocesses with different `PYTHONHASHSEED` values. The repository test suite also loads all eight committed samples and compares their content to the generation protocols. That repository-only check is skipped when sample assets are absent from an installed wheel/container; numerical and artifact API tests still run. These checks do not establish solver performance or cluster compatibility.
