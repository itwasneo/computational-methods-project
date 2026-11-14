# Development

Use the [README](../README.md#current-capabilities) for project status, [usage](usage.md) for the scientific workflow and Python APIs, and the [interface guide](interface.md) for the optional frontend/backend.

## Environment and dependencies

Use Python 3.12 and uv. [`.python-version`](../.python-version) selects the minor release; [pyproject.toml](../pyproject.toml) declares dependencies, the CLI and pinned build backend. [uv.lock](../uv.lock) pins resolved Python packages and artifacts, not Python's patch version, the OS, CPU or drivers. Record the actual runtime for experiments.

| Environment | Contents |
| --- | --- |
| Base runtime | NumPy and Numba; no plotting or web dependencies |
| `dev` (default group) | pytest, HTTP test client and the `analysis` group |
| Optional `analysis` group | pandas and Matplotlib |
| Optional `interface` extra | FastAPI, Pydantic, Uvicorn and pandas for shared result ingestion |
| Frontend development | Node 24 and pnpm; versions/dependencies in [frontend/package.json](../frontend/package.json), resolutions in [frontend/pnpm-lock.yaml](../frontend/pnpm-lock.yaml) |

Use `--locked` for Python and `--frozen-lockfile` for pnpm. Do not resolve experiment environments with unrelated ad-hoc installs or introduce npm/Yarn lockfiles. Frontend dependency build scripts are allowlisted only for `esbuild`.

## Install, test and build

From the repository root:

```bash
uv sync --locked
uv run --locked tsp-hpc --help
uv run --locked pytest -W error
uv build --no-sources
```

uv creates `.venv` and installs the project in editable mode. The `src/` layout needs no manual `PYTHONPATH`. Builds go to ignored `dist/`; a successful build alone does not test a clean wheel installation.

The default development environment exercises numerical code and analysis reports. Optional HTTP tests skip without the interface extra. To include them, as CI does:

```bash
uv sync --locked --extra interface
uv run --locked --extra interface pytest -W error
```

Use `uv sync --locked --no-dev` for the minimal numerical installation, or add `--group analysis` for reports without development tools. `uv sync --locked` restores the default development environment.

After the [frontend installation](interface.md#browser-local-results), run its checks from the repository root:

```bash
pnpm --dir frontend run check
pnpm --dir frontend run test
pnpm --dir frontend run build
```

Frontend tests include Results client/state, an ephemeral-port Vite proxy check and rendering/model regressions. Python tests cover native kernels, independent references, reproducibility and API boundaries. No cluster account, GPU, container or browser measurement campaign is needed. Loopback tests do not certify browser behavior; test timings are not benchmark evidence. Share scientific conformance tests across implementations rather than copying suites.

## Verified installed builds

Ordinary `uv build` wheels have no verified source claim. For recorded installed-package campaigns, use the controlled producer from a **clean committed checkout** (Git, Python 3.12 and uv; CI pins uv 0.10.4):

```bash
python3.12 tools/build_verified_wheel.py --output dist/verified
uv export --locked --no-dev --group analysis --no-emit-project \
  --format requirements-txt --output-file dist/verified/dependencies.txt
uv venv --python 3.12 dist/wheel-env
uv pip sync --python dist/wheel-env/bin/python --require-hashes dist/verified/dependencies.txt
uv pip install --python dist/wheel-env/bin/python --no-deps \
  dist/verified/tsp_hpc-0.1.0-py3-none-any.whl
```

The producer builds a private Git archive with the existing backend, verifies package bytes against that snapshot, embeds `_build_provenance.json`, and rewrites wheel `RECORD`. It never writes metadata into `src/` or accepts a caller-supplied revision. Dirty/untracked work, unsupported layouts and existing output files are rejected. `--offline` requires the matching uv fast path or cached build backend. Plain wheels/sdists remain supported but unverified; the controlled path currently produces pure-Python wheels only.

Keep the wheel and adjacent `.whl.build.json` receipt together. The receipt contains the final wheel SHA-256 and full inventory; that archive hash is external to avoid self-reference. Retain the source revision and matching lock/export too. The [runtime protocol and trust boundary](usage.md#build-provenance) distinguish payload identity from archive/image identity.

Run `dist/wheel-env/bin/python -I -m tsp_hpc run ...` and `... analyze ...` using the [existing commands](usage.md#run-a-manifest), with fresh output paths. Do not use `uv run` for this installed-package check: it selects the checkout environment. Use a fresh process, trusted caches and no source/PYTHONPATH overlays. The installed environment does not need Git or the source checkout. Do not subsequently run a project sync that replaces the wheel with an editable/plain installation.

The integration test builds a disposable committed snapshot, installs its wheel in a temporary environment with links to the already-locked dependencies, and runs the real CLI through analysis. It checks accepted and rejected comparison provenance, not a performance claim. The container definition consumes this wheel/receipt without rebuilding the project; [image builds and runtime validation](hpc-resources.md#reproducible-container-environment) remain owner-operated. Host tests also exercise the image verifier against an isolated wheel installation, not an actual SIF.

## Package layout

```text
src/tsp_hpc/
├── __init__.py, __main__.py, cli.py
├── problem/             # instances, objective, validation and distances
├── reproducibility/     # identities, logical RNG streams and build consistency
├── solvers/
│   ├── results.py       # shared result/counter contracts
│   └── nn/              # adapter and separately named native implementations
├── experiments/         # manifest planning and sequential execution
├── analysis/            # record validation, streaming tables/comparisons, reports
└── interface/           # request models, HTTP boundary, catalog/ingestion/queries
frontend/src/
├── results/             # typed client, controller, input parsing and Results view
├── rendering/           # WebGL2/model primitives and focused regressions
└── FixturePreview.svelte # synthetic viewer only; no duplicate Results table
```

Tests mirror the Python packages under `tests/`; CLI, local-server and native-cache integration checks live in `tests/integration/`. For example, `uv run --locked pytest tests/solvers/nn -W error` selects NN conformance tests.

Keep package initializers lightweight: root imports and CLI help must not load numerical, plotting or web dependencies. `tsp_hpc.analysis` exposes the pandas data API without importing Matplotlib reports. Interface code must reuse scientific APIs, not duplicate their rules. New solvers belong in sibling solver packages; keep implementation choices explicit.

Old flat imports have no compatibility aliases; use the paths in this guide and the [usage examples](usage.md). The [package migration PR](https://github.com/itwasneo/computational-methods-project/pull/95) records the old-to-new mapping. Native caches may rebuild after relocation and are not portable project artifacts.

## CI and review

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) owns tool versions, action pins and commands. On pushes and PRs to `master`, it runs:

- **Python checks:** locked installation with the interface extra, pytest with warnings as errors, and distribution builds.
- **Rendering primitive checks:** Node syntax checks and the shared rendering/model tests.

**Frontend type checks, non-rendering frontend tests and the Vite build are not yet run by CI.** Use the commands above for changes affecting them; a green Python/rendering run does not establish frontend correctness.

Actions are commit-pinned, repository permissions are read-only, and checkout credentials are not persisted. CI uses ordinary `pull_request` events and no custom/cluster secrets. It caches dependencies, not `.venv` or restored native JIT artifacts; numerical thread limits are one and the Numba cache is temporary. Jobs have timeouts and superseded runs are cancelled separately for push and PR events.

CI performs no cluster operations, container builds, deployments or automatic merges. Repository protection requires `Python checks` and an up-to-date branch under the existing review rules; those protections are repository settings, not defined by this workflow.

## Dependency updates and repository hygiene

Make dependency changes on a separate branch, regenerate the relevant lock deliberately, and commit declarations and lock together. For example, `uv lock --upgrade-package numpy` updates that Python dependency; rerun the affected checks/builds afterward. Do not treat a dependency update as part of an unrelated refactor.

The frontend introduction recorded a low-severity `deepmerge` advisory in development tooling with no patch then available ([PR #101](https://github.com/itwasneo/computational-methods-project/pull/101)). That historical audit is not a current clearance: recheck on dependency updates and before distribution. Exact transitive licenses and bundled notices still require packaging review.

Keep small intentional inputs in `examples/instances/`. [`.gitignore`](../.gitignore) excludes environments, caches, generated data/results, build products and SIF images. Keep credentials, private account settings and unreviewed logs out of Git. The [HPC guide](hpc-resources.md) owns container and owner-operated cluster procedures.

Status belongs in the README, rules in their reference and completion evidence in PRs/issues. Update an existing example when behavior changes; do not create a new guide for each task or module.
