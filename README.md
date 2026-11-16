# Computational Methods Project: TSP Heuristics and HPC

Study nearest-neighbour (NN), genetic algorithms (GA) and simulated annealing (SA)
for the Traveling Salesman Problem.

**Research question:** How do implementation choices, memory behavior and parallel
execution affect solution quality under limited computational budgets?

Python, NumPy and Numba provide the numerical core. A shared runner records saved
inputs, logical random streams, independently validated solutions and phase timings;
analysis keeps failed, incomplete and duplicate attempts explicit. The objective is
**unrounded Euclidean distance** on complete symmetric 2D instances. Rounded benchmark
reference costs are not interchangeable with this objective.

## Quick start

Use Python 3.12 and uv from the repository root:

```bash
uv sync --locked
uv run --locked tsp-hpc --help
uv run --locked pytest -W error
```

For the interactive workflow, install the interface dependencies and launch both services:

```bash
uv sync --locked --extra interface
pnpm --dir frontend install --frozen-lockfile
uv run --locked --extra interface python tools/local_app.py
```

Use Node 24/pnpm 10.32.1. Supply a private pairing secret at the prompt and open
http://127.0.0.1:5173. Generate an instance, select NN distance modes, review/run,
and inspect shared comparisons and verified tours—no manual manifest or ID copying.
See [application operation](docs/interface.md#start-the-application), including explicit
execution authority, output locations and recovery.

For scripted CLI work, follow [Run a manifest](docs/usage.md#run-a-manifest),
then [Analyze result shards](docs/usage.md#analyze-result-shards).
Use a fresh output path for each attempt; result shards are never appended or overwritten.
For direct Python calls, see [Run nearest-neighbour](docs/usage.md#run-nearest-neighbour).

The standalone `serve` command is read-only by default; the app launcher explicitly
opts into jobs with disjoint read/output roots. Algorithm availability comes from the
solver registry and API capabilities, not the scientific specifications.

## Documentation

| Reference | Contents |
| --- | --- |
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
