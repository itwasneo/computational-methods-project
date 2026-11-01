# Local development

**Status:** the Python package, CLI scaffold, locked dependencies, and numerical-stack smoke tests are implemented. Solvers, experiment commands, CI, and the cluster image remain separate tasks.

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

The tests check package metadata, console-entry registration, CLI behavior from outside the checkout, and actual Numba nopython compilation/execution. They do not implement TSP solvers or establish benchmark performance. No cluster account, GPU, container runtime, or network access is required to run the tests once the environment is installed. Automated execution of these checks belongs to task #4.

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
| [src/tsp_hpc](../src/tsp_hpc/__init__.py) | Importable `tsp_hpc` package; initially metadata and CLI only |
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
