# Development

Use the [README](../README.md#current-capabilities) for current capabilities and the [usage guide](usage.md) for library examples.

## Environment and dependencies

Use Python 3.12 and uv. [`.python-version`](../.python-version) selects the minor release; [pyproject.toml](../pyproject.toml) declares supported versions, dependency groups, the CLI entry point and the pinned build backend. [uv.lock](../uv.lock) is the authoritative list of resolved package versions and artifacts.

| Layer | Tools and role |
| --- | --- |
| Runtime | Python orchestration; NumPy arrays; Numba native loops, with `prange` for planned shared-memory paths |
| `dev` (default group) | pytest plus the `analysis` group, so the full suite exercises reports without skips |
| Optional `analysis` | pandas aggregation and Matplotlib figures |
| Cluster environment | Apptainer application image, launched through Slurm; see [HPC operations](hpc-resources.md) |

Use `--locked` routinely: it fails if declarations and the lockfile disagree instead of silently resolving a different environment. Do not establish experiment environments with unrelated ad-hoc `pip install` commands. The lockfile does not pin Python's patch version, OS, CPU or drivers; record the actual runtime for experiments.

## Install, test and build

From the repository root:

```bash
uv sync --locked
uv run --locked tsp-hpc --help
uv run --locked python -m tsp_hpc --version
uv run --locked pytest -W error
uv build --no-sources
```

uv locates or downloads a compatible interpreter, creates `.venv`, and installs the project in editable mode with the default development group. The `src/` layout tests an installed package without manual `PYTHONPATH` changes. Builds write a source distribution and wheel to ignored `dist/`; building alone does not test a clean wheel installation.

Tests exercise production native kernels, independent references, reproducibility and public contracts. They need no cluster account, GPU or container runtime, and no network once dependencies are installed. Test timings are not benchmark evidence. During refactors, replace obsolete checks, consolidate overlap, and share conformance tests across implementations rather than copying suites.

Optional environments:

```bash
uv sync --locked --group analysis
uv run --locked --group analysis pytest -W error
uv sync --locked --no-dev
uv run --locked --no-dev tsp-hpc --help
uv sync --locked  # Restore development dependencies.
```

For analysis without development tools, use `uv sync --locked --no-dev --group analysis`. pandas/Matplotlib remain outside the base numerical runtime; the default development environment includes them to test the complete feature set.

## Package layout

Keep the package root limited to version metadata and CLI entry points. Group library code by responsibility; keep existing algorithm implementations separately named rather than mixing an adapter with every kernel.

```text
src/tsp_hpc/
├── __init__.py, __main__.py, cli.py
├── problem/             # data.py, objective.py, distances.py, instances.py
├── reproducibility/     # identity.py, random_streams.py
├── solvers/
│   ├── results.py       # shared result/counter contracts
│   └── nn/              # solver.py, visited_scan.py, remaining_array.py
├── experiments/         # manifests.py, runner.py
└── analysis/            # core.py, reports.py; public data API in __init__.py
```

Tests mirror these contexts under `tests/`; CLI and cross-package cold/cache checks live in `tests/integration/`. For example, `uv run --locked pytest tests/solvers/nn -W error` selects NN conformance tests. Future algorithms belong in sibling solver packages, while future interface code should call these shared APIs rather than duplicate them. Namespace initializers stay lightweight; importing the root or asking for CLI help must not load numerical/plotting dependencies. Importing `tsp_hpc.analysis` exposes the pandas data API but does not import the Matplotlib reports layer.

**Import migration:** former flat modules move under the groups above: e.g. `tsp_hpc.data` → `tsp_hpc.problem.data`, `tsp_hpc.runner` → `tsp_hpc.experiments.runner`, and `tsp_hpc.nearest_neighbour` → `tsp_hpc.solvers.nn.solver`. The NN kernel modules are now `solvers.nn.visited_scan` and `solvers.nn.remaining_array`; `analysis_reports` is now `analysis.reports`. Update imports in downstream branches/scripts; no flat compatibility shims are retained. The public `tsp_hpc.analysis` data API, CLI commands, JSON formats, identities and implementation labels are unchanged. Native caches may be rebuilt after module relocation; cached machine code is not a portable project artifact.

## CI and review

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) is authoritative for runner/tool versions, action pins and commands. It installs the locked environment, runs the full suite and builds distributions on branch pushes and PRs to `master`, including documentation-only changes.

- Actions are pinned to commit IDs. Repository permissions are read-only, checkout credentials are not persisted, and no custom or cluster secrets are needed. Forks use ordinary `pull_request`, not privileged `pull_request_target`; GitHub may require contributor approval.
- Cache dependencies, not `.venv` or restored native JIT artifacts. Numba uses a separate temporary cache; numerical thread limits are one.
- Jobs have a bounded timeout; superseded runs are cancelled separately for push and PR events. A same-repository PR can have both checks.
- CI performs no cluster operations, container builds, deployments or automatic merges.
- Repository protection requires passing `Python checks` from GitHub Actions and an up-to-date branch, together with the existing review rules. This is a repository setting, not a property of the workflow file.

## Dependency updates and repository hygiene

Make dependency changes on a separate branch, regenerate the lock deliberately, and commit declarations and lock together. For example:

```bash
uv lock --upgrade-package numpy
uv sync --locked
uv run --locked pytest -W error
uv run --locked --group analysis pytest -W error
uv build --no-sources
```

Code lives in `src/tsp_hpc/`, tests in `tests/`, and small intentional inputs in `examples/instances/`. [`.gitignore`](../.gitignore) excludes environments, caches, build products, generated/downloaded data, result directories and SIF images. Keep credentials, private account settings and unreviewed logs out of Git; a sanitized `.env.example` is acceptable.

Keep documentation focused: status belongs in the README, rules in their reference, and completion evidence in PRs/issues. Add or update an example when behavior warrants it, not a new page automatically for each module.
