# Offline TSPLIB coordinate import

Convert an owner-supplied local file **before** planning or timing a solver. No downloads,
new package dependencies, external parser or solver are involved.

```bash
mkdir -p results
uv run --locked tsp-hpc import-tsplib examples/instances/half-unit.tsp --output results/half-unit.json
```

The output is an ordinary canonical instance artifact. Existing paths are not overwritten.
Retain the original `.tsp` as well: import provenance records the TSPLIB name, importer version,
city count and SHA-256 of the **original bytes** (not the filename). Canonical coordinate
checksum and objective-specific instance ID are separate. Loading a JSON artifact verifies
its descriptor, not the authenticity of an externally supplied source hash.

## Accepted subset

- Local uncompressed ASCII, up to 2 MiB and 2–4096 cities. Uppercase `KEY: value` headers;
  whitespace around the colon and blank lines are allowed. Require one each of `NAME` (1–128
  printable ASCII characters), `TYPE: TSP`, `DIMENSION`, and `EDGE_WEIGHT_TYPE: EUC_2D`.
- Optional repeated `COMMENT` (ignored, never treated as an optimum), `NODE_COORD_TYPE: TWOD_COORDS`,
  `EDGE_WEIGHT_FORMAT: FUNCTION`, and `DISPLAY_DATA_TYPE: COORD_DISPLAY` or `NO_DISPLAY`.
- Exactly one `NODE_COORD_SECTION` with `ID X Y` rows. IDs must be decimal integers from 1 to
  `DIMENSION`, each appearing once, with no leading zeros. Rows may be unordered. Original ID `k`
  becomes city index `k-1`. Coordinates use decimal or scientific notation (at most 128 characters)
  and are read as finite float64 values. An optional `EOF` must be the last nonblank line.
- Reject unknown headers/sections, explicit matrices, asymmetric problems, tours, GEO, ATT and
  other edge types rather than dropping constraints or guessing an objective.

## Costs and reference data

`EUC_2D` rounds **each edge** to its nearest integer (half up), then sums, including the closing
edge. In the supplied three-city fixture, the unrounded tour is 2; the TSPLIB tour is 3. The
existing independent objective and tiny exact reference support this convention. **Registered
NN/GA distance backends still reject rounded instances.** Do not relabel the artifact or compare
an unrounded solver result with a published rounded optimum.

```python
from tsp_hpc.problem.tsplib import load_tsplib, tsplib_reference

record = load_tsplib('examples/instances/half-unit.tsp')
row = tsplib_reference(record, cost=3, kind='exact',
                       source='Hand proof for half-unit: every cycle uses rounded edges 1+1+1')
reference_document = {'schema_version': 1, 'references': [row]}
```

For external data, verify the named source instance and objective, and cite the publication or
certificate. Use `kind='exact'` only for a certified/proven optimum; use `kind='best_known'`
for an incumbent. Neither the importer nor a supplied tour proves optimality. No implicit reference
lookup is performed. The row binds the declared cost to the instance ID, coordinate checksum and
rounded objective, for the existing analysis reference-document format. Rounded reference totals
must be nonnegative exact integers through 2^53; an observation even one unit below an exact
rounded reference is inconsistent. Unrounded comparisons retain their separate float tolerance.
