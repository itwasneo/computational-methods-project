# Computational Methods Project: TSP Heuristics and HPC

Implement and compare nearest-neighbour (NN), a genetic algorithm (GA), and simulated annealing (SA) for the Traveling Salesman Problem.

**Research question:** How do implementation choices, memory behavior, and parallel execution affect solution quality under limited computational budgets?

We use Python, NumPy and Numba with our own lightweight experiment runner, rather than an external TSP solver or benchmark framework. CPU baselines come first; MPI, GPU, NUMA, SIMD and targeted C++ investigations are optional and must be justified by measurements.

## Current capabilities

This is the project-wide implementation-status summary; specifications describe agreed behavior, not necessarily available execution features.

| Area | Available | Remaining work |
| --- | --- | --- |
| Inputs and objective | Canonical data/validation, independent objective, seeded generators, saved artifacts and fixtures | TSPLIB import and production datasets |
| Execution | Dense/on-demand distances; serial NN with `visited-scan-v1` and `remaining-array-v1` implementations | GA/SA, multicore and optional accelerator backends |
| Experiments | Manifests, logical RNG planning, sequential NN runner, versioned JSONL results and validated analysis tables/plots | Campaign scheduling, resume and production protocol |
| Stopping and progress | Fixed-work NN and phase timing | Time budgets and progress histories |
| Tooling | Locked environment, tests/builds in CI; run/analyze CLI; fixture-only Results/viewer preview; optional workspace API, bounded result ingestion and paged trial queries | Real-data interface integration, editing and job controls |
| Cluster deployment | ULHPC selected; local image build evidence in [draft PR #53](https://github.com/itwasneo/computational-methods-project/pull/53) | Finished-image and allocated-cluster runtime validation |

Solver execution uses **unrounded Euclidean distances** on complete symmetric 2D instances. Rounded data/reference compatibility is retained, but is not an executable solver mode. See the [objective contract](docs/contracts.md#distance-conventions-and-representations) before choosing benchmark reference costs.

No comparative solver performance or cluster-scaling results are established yet. Track implementation work and reviews on the [project board](https://github.com/users/itwasneo/projects/11); PRs and issues hold change history and verification evidence.

## Quick start

With uv installed, run from the repository root:

```bash
uv sync --locked
uv run --locked tsp-hpc --help
uv run --locked pytest -W error
```

To run and save NN experiments, follow [Run a manifest](docs/usage.md#run-a-manifest), then [Analyze result shards](docs/usage.md#analyze-result-shards). Individual Python solver calls are covered in [Run nearest-neighbour](docs/usage.md#run-nearest-neighbour). For installation groups, dependency updates and builds, see [development](docs/development.md).

## Documentation

Each reference owns one kind of information. Update that reference rather than copying its rules or current status into other pages.

| Read this | For |
| --- | --- |
| [Usage guide](docs/usage.md) | Run manifests, analyze saved results, then use the data/solver Python APIs |
| [Shared contracts and experiment methodology](docs/contracts.md) | Data/objective/result rules, validation, execution boundaries and fair comparisons |
| [Algorithm specifications](docs/algorithm-baselines.md) | NN/GA/SA variants, tie rules, operators, budgets, initialization accounting and stopping checkpoints |
| [Manifests and reproducibility](docs/manifests.md) | Schema, implementation labels, scientific/execution identities and RNG ownership |
| [Development](docs/development.md) | Environment, package layout, tests, CI and packaging |
| [Optional local interface](docs/interface.md) | Preview/backend usage, security boundaries and clearly separated planned API contracts |
| [HPC operations](docs/hpc-resources.md) | Owner-operated allocations, containers, placement, storage and deployment checks |

The [saved input collection](examples/instances/README.md) contains tiny correctness examples, not a benchmark campaign. [Presentations](presentations/README.md) preserve the original proposal and a dated progress snapshot; they are not the current status reference.

## Research sequence

1. Establish correct serial NN, GA and SA against shared validation.
2. Compare separately named serial implementations and distance representations without changing scientific workloads.
3. Use the runner to establish and freeze a reproducible tuning/evaluation protocol.
4. Measure controlled multicore scaling; distinguish single-search acceleration from experiment throughput.
5. Select a focused advanced extension only if profiling and available resources justify it.

Benchmark sizes, tuning budgets, reference sources and final campaign settings still need to be chosen. Exact small-instance optima are correctness/quality references, not a fourth primary solver; SA reversal moves do not add a separate 2-opt solver. Completing every optional backend is not a project requirement.
