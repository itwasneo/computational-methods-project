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

The integration test builds a disposable committed snapshot, installs its wheel in a temporary environment with links to the already-locked dependencies, and runs the real CLI through analysis. It checks accepted and rejected comparison provenance, not a performance claim. Container handoff remains [owner-operated](hpc-resources.md#reproducible-container-environment).

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
└── interface/           # authority, snapshots, preflight/jobs/worker, publication/analysis
frontend/src/
├── results/             # typed client, controller, input parsing and Results view
├── rendering/           # WebGL2/model primitives and focused regressions
└── FixturePreview.svelte # synthetic viewer only; no duplicate Results table
```

Tests mirror the Python packages under `tests/`; CLI, local-server and native-cache integration checks live in `tests/integration/`. For example, `uv run --locked pytest tests/solvers/nn -W error` selects NN conformance tests.

Keep package initializers lightweight: root imports and CLI help must not load numerical, plotting or web dependencies. `tsp_hpc.analysis` exposes the pandas data API without importing Matplotlib reports. Interface code must reuse scientific APIs, not duplicate their rules. New solvers belong in sibling solver packages; keep implementation choices explicit.

Old flat imports have no compatibility aliases; use the paths in this guide and the [usage examples](usage.md). The [package migration PR](https://github.com/itwasneo/computational-methods-project/pull/95) records the old-to-new mapping. Native caches may rebuild after relocation and are not portable project artifacts.

## Solver registration

`solvers.configuration` owns scientific parameter/budget normalization for both
manifests and adapters. It is independent of installed implementations, so reading
historical records does not depend on today's registry.

`solvers.registry` defines `SolverAdapter`, immutable `SolverSpec` records and an
explicit `SolverRegistry`. Factories bind a prepared distance representation and
warm an adapter outside measured search. NN registrations use ordered-start
`batch` execution; future GA populations and SA chains use `independent`
execution. Defaults are explicit, descriptions are detached, and unsupported
algorithms/backends/objectives/capabilities fail rather than falling back.

Only the two real NN implementations are registered by `default_registry()`.
Adding an algorithm requires its adapter/registrations plus an explicit entry in
that function; no filesystem or third-party plugin discovery occurs. Test-only
registries are constructed separately, never advertised as real GA/SA solvers.
`SearchControl` implements shared deadline precedence and bounded observations;
NN supports time/history directly using the same native kernels at complete-start
checkpoints. Its history-disabled fixed-work path retains the original single
kernel call. Instrumented execution includes per-start call/workspace overhead in
search time. The serial runner dispatches registered factories and persists envelope-v3 logical
outcomes. `TrialPlan.invocations()` supplies one NN batch or ordered independent
population/chain seeds. Shared result validation covers all three agreed variants
without importing their implementations. Tests inject clearly named, non-native
scripted adapters to exercise GA/SA-shaped accounting—not scientific solvers.
Production capabilities continue to advertise only real NN.

### Algorithm-author handoff and conformance

Algorithm authors own their solver/operator package, its adapter and explicit
registration, and algorithm-specific tests. They do **not** edit the runner,
result codec, analysis or future transport to make an algorithm executable.
The service/queue milestone is separate; its current NN-only design DTOs are not
an alternative solver integration contract.

An adapter factory accepts `PreparedDistances`, validates its supported objective,
and warms only the selected kernels with synthetic state. `metadata` is a detached
`{**prepared.metadata, algorithm, variant, backend="serial", implementation,
solver_warmup_time_s}` dictionary checked by `validate_adapter_metadata`.
Native adapters must also expose `native_dispatchers`: a nonempty tuple of the
actually selected, warmed Numba CPU dispatchers. The runner and conformance
helper verify nopython signatures and serial/strict-math options; do not expose a
baseline alias while executing a different callable. `native=False` is an explicit
non-native declaration for test adapters or separately identified code, never an
interpreted fallback under a native baseline label.
`solve` accepts the complete [shared signature](contracts.md#logical-solver-interface).
Use `normalize_parameters`, `normalize_budget`, `normalize_history` and
`validate_seed` at the direct Python boundary too; registry callers are not the
only callers. Check instance/preparation binding before entering kernels.

Create `SearchControl` immediately before initialization, then fresh logical RNG
state from the supplied seed. Preserve RNG, cooling/population and numerical-check
state across safe checkpoints, but never across invocations. Return owned
`SolverResult` snapshots and actual counters; use the independent objective for
final verification. Recording must not consume randomness. A template registration
inside the completed algorithm package is:

```python
# Structural example only: GASolver is the teammate's actual implementation.
from tsp_hpc.solvers.registry import SolverSpec


def solver_specs():
    return (SolverSpec("ga", "ga-serial-v1", GASolver, "independent",
                       supports_time=True, supports_history=True),)
```

Add that package's registrations and explicit default in `default_registry()`.
Do not discover modules dynamically, opportunistically hide import errors, or
register placeholders. `TrialPlan.invocations()` owns the three agreed logical
units. Budget/checkpoint changes remain scientific inputs, not resource knobs.

The reusable `tsp_hpc.solvers.conformance.check_adapter` has no pytest dependency:

```python
from tsp_hpc.problem.instances import fixture
from tsp_hpc.solvers.registry import default_registry
from tsp_hpc.solvers.conformance import check_adapter

spec = default_registry().resolve("nn", implementation="remaining-array-v1")
result = check_adapter(spec, fixture("square").instance, seed=2**63 + 7,
                       budget={"start_cities": [2, 0]}, distance_mode="dense")
```

It bounds inputs to 64 cities, checks immutable inputs/results/configuration,
Python/NumPy global RNG ownership, shared work/stopping/history rules, independent
objective validation and nested timings. Fixed work is replayed after an
intervening different-seed invocation and with history toggled. Timed runs are
validated without assuming identical wall-clock-dependent work.

`tests/solvers/test_conformance.py` automatically parametrizes **all production
registrations**, both distance modes, every canonical tiny fixture, zero work,
small/combined deadlines and invalid direct calls. Its pipeline test reaches real
JSONL ingestion and history projection. `tests/experiments/test_generic_runner.py`
adds test-only GA/SA-shaped multi-search, seed, tie and failure cases. Those scripts
are not algorithms, production capabilities, or scientific acceptance evidence.

Run the full warnings-as-errors suite. The common checks are not proofs of native
RNG ownership, operator correctness or heuristic optimality: GA still needs OX,
tournament, mutation, elitism and initialization tests; SA still needs reversal,
acceptance, cooling, rejection, drift and checkpoint-state tests. PR #50's Smart
interface/swap variant does not meet the agreed SA contract merely by rebasing.

## CI and review

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) owns tool versions, action pins and commands. On pushes and PRs to `master`, it runs:

- **Python checks:** locked installation with the interface extra, pytest with warnings as errors, and distribution builds.
- **Rendering primitive checks:** the historical status-check name is retained, but this job now installs pinned pnpm/locked dependencies and runs Node syntax checks, Svelte/TypeScript checking, all existing frontend/shared-contract regressions and the Vite build.

Captured execution/publication/analysis payloads are checked against both Python and
TypeScript DTOs. Installed acceptance runs a real wheel server outside the checkout,
including native workers, an actual controller crash with a surviving child,
restart-held dispatch, verified results/history and fresh reruns. Separate tests
cover strict JSON, resource/persistence failures and the deterministic #75 race
negative control. These are functional gates, not browser-performance, solver
benchmark, hardware or cached-machine-code-origin attestations.

Actions are commit-pinned, repository permissions are read-only, and checkout credentials are not persisted. CI uses ordinary `pull_request` events and no custom/cluster secrets. It caches dependencies, not `.venv` or restored native JIT artifacts; numerical thread limits are one and the Numba cache is temporary. Jobs have timeouts and superseded runs are cancelled separately for push and PR events.

CI performs no cluster operations, container builds, deployments or automatic merges. Repository protection requires `Python checks` and an up-to-date branch under the existing review rules; those protections are repository settings, not defined by this workflow.

## Dependency updates and repository hygiene

Make dependency changes on a separate branch, regenerate the relevant lock deliberately, and commit declarations and lock together. For example, `uv lock --upgrade-package numpy` updates that Python dependency; rerun the affected checks/builds afterward. Do not treat a dependency update as part of an unrelated refactor.

The frontend introduction recorded a low-severity `deepmerge` advisory in development tooling with no patch then available ([PR #101](https://github.com/itwasneo/computational-methods-project/pull/101)). That historical audit is not a current clearance: recheck on dependency updates and before distribution. Exact transitive licenses and bundled notices still require packaging review.

Keep small intentional inputs in `examples/instances/`. [`.gitignore`](../.gitignore) excludes environments, caches, generated data/results, build products and SIF images. Keep credentials, private account settings and unreviewed logs out of Git. The [HPC guide](hpc-resources.md) owns container and owner-operated cluster procedures.

Status belongs in the README, rules in their reference and completion evidence in PRs/issues. Update an existing example when behavior changes; do not create a new guide for each task or module.
