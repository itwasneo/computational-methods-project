# Shared Smart TSP testbench pilot

This testbench uses the **testing package** [`smart-tsp-benchmark` 1.0.1](https://github.com/smartlegionlab/smart-tsp-benchmark), referenced by [smart-tsp-solver](https://github.com/smartlegionlab/smart-tsp-solver). It does not import or run Smart TSP's solver algorithms. The suite is a small proposed pilot for comparing our own solvers; it is not yet the team's approved final campaign.

## Solver contract

Register an importable function as `module:function`. The suite calls:

```python
closed_route = solver(points, seed=solver_seed, **parameters)
```

- `points`: a writable copy of a finite `float64` array of shape `(n, 2)`, with row indices `0..n-1` as city IDs. Do not rely on changing the caller's array. Use unrounded Euclidean distances.
- `seed`: explicit integer supplied by the suite; use it for a local RNG. Separate instance-generation and solver seeds. Do not rely on global NumPy state.
- Return a list, tuple, or NumPy array of **`n+1` integer indices**: every city exactly once and the starting city repeated at the end. The testbench rejects invalid routes before Smart TSP can score them.
- List all scientific parameters explicitly in `--params` JSON and document their meanings. All solvers are compared on the same instance hashes, but the same iteration count is not automatically equivalent work. The suite does not impose identical algorithm parameters or time limits.

The team's merged internal contract uses an **open** `n`-city permutation with implicit closure. An adapter must add the final city only at this Smart TSP boundary; the SA adapter is `simulated_annealing:smart_sa`. Use an adapter for another internal result format, too. The suite reserves `seed`; do not include it in `--params`.

## Representative scenarios

| Distribution | Cities | Instance seed | Purpose |
| --- | ---: | ---: | --- |
| Random | 50 | 123 | General unstructured pilot |
| Cluster | 50 | 124 | Nearby groups with longer inter-cluster edges |
| Circle | 50 | 125 | Approximately ordered geometric layout, with upstream default noise |
| Grid | 100 | 126 | Larger structured layout, with upstream default noise |

Each scenario runs solver seeds `101`, `102`, and `103` (12 runs per solver). Generation follows the upstream package's defaults (`scale=100`, `noise=5` where used). The suite restores the caller's NumPy random state after upstream generation, records SHA-256 of canonical little-endian float64 coordinates, and verifies the same route cost independently. Output contains environment details, each route, cost, elapsed time, explicit parameters, scenario and both seeds. Output creation refuses to overwrite an existing file.

## Run locally

From the project root with Python 3.12:

```bash
python -m pip install -r benchmarks/requirements.txt
python -m unittest discover -s tests -v
python -m benchmarks.smart_suite --solver simulated_annealing:smart_sa \
  --params '{"initial_temperature":40.0,"cooling_rate":0.95,"moves_per_temperature":200,"max_moves":10000}' \
  --output sa-results.json
```

The suite does one untimed-for-search six-city warm-up, recording `warmup_seconds` separately. Each reported timer includes the solver call, its coordinate-to-distance preprocessing, the protective input copy, and route validation; generation, independent cost checking, and output writing are excluded. This is an end-to-end adapter timing, not a compute-only kernel measurement. Upstream plotting and post-optimization are disabled. Preserve separate warm-up, preprocessing, and end-to-end measurements if a future experiment needs finer comparisons.

## Aion smoke run

Prepare a Python 3.12 environment with `benchmarks/requirements.txt`; discover the actual account, partition, QoS, interpreter and storage for the current user. From the project checkout, submit using an allocation appropriate to those discoveries:

```bash
sbatch --account=<account> --partition=<partition> --qos=<qos> \
  --time=00:05:00 --cpus-per-task=1 --mem=2G \
  benchmarks/aion_smoke.sh /absolute/path/to/python /absolute/path/to/new-results.json
```

The smoke script sets CPU thread counts to one, runs the unit/integration tests, and executes the 12 SA pilot cases. It must run from the project root as the Slurm working directory. Check `sacct` and the job log for a completed zero exit code; submission is not completion. Substantial final campaigns need the team's approved workload, resources and independent result handling.

**Observed:** on 2026-11-01, job `16175356` used Aion `students`, `interactive`, QoS `debug`, one CPU and 2 GiB on `aion-0230`. It completed `0:0`; ten tests and all 12 cases passed. A local run with the same dependency versions produced identical instance hashes and SA routes, with matching independently checked lengths. Raw results are retained outside this repository. These results validate the testbench; they are not a cross-algorithm performance conclusion.
