# Local development

**Status:** the Python package, CLI scaffold, locked dependencies, numerical-stack smoke tests, and automated CI workflow are implemented. Solvers, experiment commands, and the cluster image remain separate tasks.

## Environment and dependency policy

Use Python **3.12** and **uv**. The repository's `.python-version` selects the Python minor version, and `pyproject.toml` currently supports only that minor version. The scaffold was validated with CPython **3.12.3** and uv **0.10.4** on Linux; other platforms and the cluster image still need validation.

The initial locked stack is:

| Component | Locked version | Installation group |
| --- | --- | --- |
| NumPy | `2.5.4` | Runtime |
| Numba | `0.68.1` | Runtime |
| llvmlite | `0.50.0` | Runtime, through Numba |
| pytest | `9.2.0` | `dev` (installed by default) |
| pandas | `3.1.0` | Optional `analysis` |
| Matplotlib | `3.11.2` | Optional `analysis` |

Numba's dependency constraints permit this NumPy version; the tests additionally compile and execute a small `float64` kernel. The dependency declarations bound the numerical packages to the selected minor release lines. [uv.lock](../uv.lock) fixes the resolved versions and artifacts for runtime and dependency groups; the build backend is pinned separately in [pyproject.toml](../pyproject.toml).

The lockfile does **not** pin the Python patch release, operating system, CPU, or drivers. Record the actual runtime for experiments. The future container task will pin its interpreter and system environment; this local setup is not evidence of cluster compatibility.

## Set up and validate

With uv available on `PATH`, run these commands from the project root:

```bash
uv --version
uv sync --locked
uv run --locked tsp-hpc --help
uv run --locked tsp-hpc --version
uv run --locked pytest
```

uv locates a compatible Python 3.12 interpreter or can download one if needed. It creates `.venv`, installs the package in editable mode, and includes the default `dev` group. No global NumPy/Numba installation or manual `PYTHONPATH` changes are needed. The `src/` layout makes tests exercise an installed package rather than accidentally importing code from the repository root.

Use `--locked` in routine commands: it fails when dependency declarations and the committed lockfile disagree instead of silently resolving a new environment. Do not use an unrelated `pip install` workflow to establish the experiment environment.

The CLI currently provides help and version information only. Running `tsp-hpc` without arguments shows help; unknown options are rejected. The module entry point is equivalent:

```bash
uv run --locked python -m tsp_hpc --help
```

The tests check package metadata, console-entry registration, CLI behavior from outside the checkout, actual Numba nopython compilation/execution, the [shared TSP data/objective utilities](data-and-objective.md), [manifest/stream invariants](manifests.md), [instance generation/artifact integrity](instances.md), and [compiled distance/numerical-boundary checks](distances.md). They do not implement TSP solvers or establish benchmark performance. No cluster account, GPU, container runtime, or network access is required to run the tests once the environment is installed. The CI workflow below runs these checks automatically.

## Automated correctness checks

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs the **CI / Python checks** job on branch pushes and pull requests targeting `master`. It uses GitHub-hosted Ubuntu 24.04, Python 3.12 selected through `.python-version`, and uv `0.10.4`. Python patch versions and runner software may change; tool versions are printed in the log. This is not a substitute for a pinned experiment image.

The validation commands are the same locally and in CI:

```bash
uv sync --locked
uv run --locked pytest
uv build --no-sources
```

The first command rejects a missing or inconsistent lockfile rather than silently updating it. pytest discovers the complete `tests/` suite, including actual Numba compilation/execution; future solver tests join automatically. The final command builds the source distribution and wheel without relying on local uv source overrides. It checks packaging, not installation of the resulting wheel in a separate environment. The optional `analysis` group is not installed in this initial job.

Operational policy:

- GitHub Actions are pinned to full commit IDs; uv is explicitly versioned. Update those pins deliberately.
- The job has read-only repository permissions, does not persist checkout credentials, and requires no custom secrets or cluster credentials. Fork PRs use ordinary `pull_request`, not privileged `pull_request_target`; GitHub may require approval before running a first-time contributor's workflow.
- Cache only uv's dependency cache, not `.venv` or native Numba caches. Tests use a separate temporary Numba cache, and numerical libraries are limited to one thread.
- A 15-minute timeout bounds a job. New commits cancel superseded runs for the same branch/event or PR. A same-repository PR can have both a branch-push run and a separate PR merge-result run.
- Run checks even for documentation-only changes; no path filters or performance thresholds are used. **CI timings are not benchmark evidence.**
- No Apptainer build, container execution, cluster connection, Slurm job, GPU/MPI check, deployment, or automated merge is part of this workflow.

Following successful GitHub runs and explicit owner approval, `Python checks` is now required on `master`, bound to the GitHub Actions app. Branches must be up to date before merging; the existing administrator enforcement and review rules are preserved. This gate is a GitHub repository setting, not something enabled by the workflow file alone.

## Optional analysis environment

pandas and Matplotlib are kept out of the base numerical runtime:

```bash
uv sync --locked --group analysis
uv run --locked --group analysis pytest
```

This includes both the default development dependencies and the analysis group. No plotting or aggregation commands exist yet. When running future analysis scripts, use the same `--group analysis` selection so uv supplies their dependencies explicitly.

For a runtime-only installation without pytest or analysis packages:

```bash
uv sync --locked --no-dev
uv run --locked --no-dev tsp-hpc --help
```

Run `uv sync --locked` again to restore the normal development environment.

## Repository layout

| Path | Purpose |
| --- | --- |
| [src/tsp_hpc](../src/tsp_hpc/__init__.py) | Importable package with CLI, data/objective utilities, manifest/stream planning, instance generation/storage, and compiled distance backends |
| [tests](../tests/test_numerical_stack.py) | pytest checks, including the compiled numerical smoke test |
| [pyproject.toml](../pyproject.toml) | Package metadata, entry point, dependency groups, build backend, and pytest settings |
| [uv.lock](../uv.lock) | Version-controlled dependency resolution |
| [Shared contracts](contracts.md) and [algorithm baselines](algorithm-baselines.md) | Specifications for the later implementation tasks |

[.gitignore](../.gitignore) excludes local environments, Python/Numba/tool caches, build outputs, `.env` files, generated or downloaded data under `data/generated/` and `data/downloaded/`, outputs under `results/`, `outputs/`, and `artifacts/`, and Apptainer `*.sif` images. Keep small intentional correctness fixtures under `tests/fixtures/`; they are not covered by those generated-data exclusions. A sanitized `.env.example` may be committed, but actual credentials must never be included.

## Updating dependencies and building

Make dependency updates on their own branch. Change declarations deliberately when moving outside the current bounds, regenerate `uv.lock`, and commit both files. For example, to refresh NumPy within its declared range:

```bash
uv lock --upgrade-package numpy
uv sync --locked
uv run --locked pytest
uv run --locked --group analysis pytest
uv build --no-sources
```

`uv build` writes a source distribution and wheel to the ignored `dist/` directory. It validates packaging, not application performance or cluster execution. The project does not depend on an external TSP solver or benchmark framework.
