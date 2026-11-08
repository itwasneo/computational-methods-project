# Shared development inputs

These eight saved artifacts contain actual coordinates, content IDs/checksums and generation provenance. They are tiny correctness/development inputs, not benchmark results or a representative experimental sample. See the [generation and artifact API](../../docs/usage.md#instances-and-artifacts) for precise distribution definitions and fixture optima.

| Artifact | Cities | Construction |
| --- | --- | --- |
| [two-city.json](two-city.json) | 2 | `fixture("two-city")` |
| [triangle.json](triangle.json) | 3 | `fixture("triangle")` |
| [square.json](square.json) | 4 | `fixture("square")` |
| [collinear.json](collinear.json) | 4 | `fixture("collinear")` |
| [duplicates.json](duplicates.json) | 4 | `fixture("duplicates")` |
| [coincident.json](coincident.json) | 4 | `fixture("coincident")` |
| [uniform-8.json](uniform-8.json) | 8 | `uniform_instance(8, seed=42)` |
| [clustered-8.json](clustered-8.json) | 8 | `clustered_instance(8, seed=42)` |

All use `euclidean_2d`. Generator defaults are saved explicitly in each artifact, along with the original runtime versions. Loading uses these coordinates rather than regenerating them. Recreating artifacts under another Python patch version can change provenance text without changing their coordinate checksums.

From the repository root:

```python
from tsp_hpc.problem.instances import load_instance

record = load_instance("examples/instances/uniform-8.json")
instance = record.instance
manifest_descriptor = record.descriptor
assert instance.n_cities == 8
```

Use the returned descriptor/ID when building a manifest. In particular, `square.json` has a content-based ID; the separate [planning-only manifest example](../manifests/fixed-work.json) uses the human-supplied alias `unit-square`. Their coordinates/checksums agree, but their logical IDs are not interchangeable without explicitly updating that manifest.

Files are included in the repository, not installed as package resources. Supply an artifact path explicitly when using an installed wheel or container. `save_instance()` refuses to overwrite these files; use a new output directory when generating more inputs.
