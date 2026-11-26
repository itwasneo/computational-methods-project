# Computational Methods Project: TSP Heuristics and HPC

Available solvers for the Traveling Salesman Problem: two serial nearest-neighbour (NN)
implementations, fixed-work multicore NN and a serial genetic algorithm (GA).
Simulated annealing (SA) can be planned in a manifest but has no registered solver.

**Research question:** How do implementation choices, memory behavior and parallel
execution affect solution quality under limited computational budgets?

The NumPy/Numba solvers use **unrounded Euclidean distance** (`euclidean_2d`) on complete
symmetric 2D instances. Artifacts and [exact references](docs/exact-and-multicore.md#tiny-exact-reference)
also support rounded `tsplib_euc_2d`. Compare costs only within the same convention.
The runner saves seeds, validated tours and phase timings; analysis flags failed, incomplete and duplicate attempts.

## Quick start

Run the scientific tests from the repository root with Python 3.12 and uv. Leave Numba JIT enabled:

```bash
uv sync --locked
uv run --locked tsp-hpc --help
uv run --locked pytest -W error tests/problem tests/solvers tests/experiments tests/analysis tests/reproducibility
```

The launcher and saved-results view work on macOS and Linux. Native browser jobs and the
[full Python test suite](docs/development.md#install-test-and-build) require Linux with pidfds
and readable procfs. To [launch the app](docs/interface.md#start-the-application), use Node 24 and pnpm 10.32.1:

```bash
uv sync --locked --extra interface
pnpm --dir frontend install --frozen-lockfile
uv run --locked --extra interface python tools/local_app.py
```

Open http://127.0.0.1:5173 and pair with the secret you entered at the terminal prompt.
On Linux, generate an instance, review and run an NN or GA job, then inspect the result.
On macOS, run from the CLI and [browse saved shards](docs/interface.md#browser-local-results).
Standalone `serve` is read-only by default; the launcher explicitly enables jobs.

For a CLI example, [run the square NN manifest](docs/usage.md#run-a-manifest) and
[analyze its four trials](docs/usage.md#analyze-result-shards). Use a new output path each time.
For Python, see [NN](docs/usage.md#run-nearest-neighbour) and [serial GA](docs/usage.md#run-serial-ga).

## Documentation

| Reference | Contents |
| --- | --- |
| [TSPLIB coordinate import](docs/tsplib.md) | Offline symmetric EUC_2D artifacts and objective-specific reference costs (not native solver execution) |
| [Usage](docs/usage.md) | CLI workflow, analysis, records/provenance and Python APIs |
| [Shared contracts](docs/contracts.md) | Data, objective, results, validation and fair comparisons |
| [Algorithm specifications](docs/algorithm-baselines.md) | NN/GA/SA rules, operators, budgets and checkpoints |
| [Manifests](docs/manifests.md) | Configuration schema, identities and RNG ownership |
| [Exact references and multicore NN](docs/exact-and-multicore.md) | Bounded exhaustive validation, native-thread execution and scaling pilot |
| [Development](docs/development.md) | Environment, architecture, solver integration, tests and packaging |
| [Local interface](docs/interface.md) | Owner operation, HTTP contracts, security and recovery |
| [HPC operations](docs/hpc-resources.md) | Allocations, containers, placement and storage |

[Example inputs](examples/instances/README.md) are tiny correctness fixtures, not a
benchmark campaign. [Presentation assets](presentations/README.md) are archived separately.
Change history and review evidence belong in [issues and PRs](https://github.com/itwasneo/computational-methods-project),
not in the reference documentation.
