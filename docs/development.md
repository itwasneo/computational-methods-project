# Development

Use [usage](usage.md) for scientific workflows and [the interface guide](interface.md)
for local-server/browser operation. Keep scientific rules in their shared Python APIs;
transport and presentation must not introduce a second solver or analysis engine.

## Environment and dependencies

Use Python 3.12 and uv; frontend development uses Node 24 and the pnpm version pinned
in [frontend/package.json](../frontend/package.json). [pyproject.toml](../pyproject.toml)
defines Python dependencies and the build backend. Lockfiles pin packages/artifacts,
not the Python patch release, OS, CPU or drivers; record the actual experiment runtime.

| Environment | Contents |
| --- | --- |
| Base runtime | NumPy and Numba |
| `dev` (default group) | pytest, HTTP test client, Playwright and `analysis` |
| `analysis` group | pandas and Matplotlib |
| `interface` extra | FastAPI, Pydantic, Uvicorn and pandas |
| Frontend | Svelte/TypeScript/Vite; `frontend/pnpm-lock.yaml` pins resolutions |

Use `--locked` and `--frozen-lockfile`. Commit dependency declarations and their lock
updates together on a dedicated branch; avoid unrelated upgrades, ad-hoc experiment
installs and npm/Yarn lockfiles. Frontend dependency build scripts are allowlisted only
for `esbuild`. Recheck dependency advisories, licenses and notices before distribution.

## Install, test and build

From the repository root:

```bash
uv sync --locked --extra interface
uv run --locked --extra interface pytest -W error
uv build --no-sources
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend run check
pnpm --dir frontend run lint
pnpm --dir frontend run format:check
pnpm --dir frontend run test
pnpm --dir frontend run build
```

uv creates an editable `.venv`; the `src/` layout needs no `PYTHONPATH`. Builds go to
ignored `dist/`. Without `--extra interface`, optional HTTP tests skip. Use
`uv sync --locked --no-dev` for numerical-only work, or add `--group analysis` for
reports. `uv sync --locked` restores the default development environment.

Tests mirror packages under `tests/`; for example, select NN checks with
`uv run --locked pytest tests/solvers/nn -W error`. Integration tests exercise native
kernels/caches, installed wheels, CLI reports, actual local workers and crash recovery.
Frontend tests cover contracts, client/state, a real loopback Vite proxy and rendering
primitives. No cluster account, GPU or browser measurement campaign is required.
For real browser acceptance (free local ports 5173/8000 required):

```bash
uv run --locked python -m playwright install chromium
uv run --locked --extra interface pytest frontend/acceptance -q -W error
```

These tests use Chromium, the guarded Vite proxy, a real backend and native workers;
CI installs browser OS dependencies with `playwright install --with-deps chromium`.
They cover generation, exact export, reviewed comparisons, lost acknowledgements,
verified geometry/history, cancellation, restart-held dispatch and fresh reruns.
They are functional browser tests, not browser-performance gates.

For fast mounted-Svelte interactions, run `uv run --locked --extra interface pytest
frontend/ui_tests -q -W error`. These tests start Vite on an available ephemeral port and
intercept the API using shared DTO captures; they do not run workers or establish scientific
results. They cover failure recovery, shared authority/catalogs, drafts, navigation,
renderer lifetime, keyboard behavior, responsive layout and axe checks. Set
`TSP_UI_BROWSER=firefox` or `TSP_UI_BROWSER=webkit` to exercise another installed engine.
CI installs each engine and its OS dependencies; Chromium additionally checks software-WebGL
lifecycle. This does not replace manual screen-reader/zoom review or certify WCAG conformance.

`tools/interface_schema.py` generates both `frontend/src/wire-schema.json` and
`frontend/src/wire-types.ts` from Python DTOs. Regenerate after contract changes with
`uv run --locked python tools/interface_schema.py`; a Python test rejects drift in either.
`contracts.ts` re-exports generated types, and the typed endpoint registry binds routes,
request bodies and response validators. Generated artifacts are excluded from formatting.
Use `pnpm --dir frontend run format` for handwritten frontend source.
See [frontend architecture](frontend-architecture.md) for state ownership and lifecycle rules.

Functional test timings are not performance evidence. Keep distinct unit, scientific
conformance, fault-injection and installed-process coverage even when workflows overlap.

## Architecture

| Package | Responsibility |
| --- | --- |
| `problem` | Canonical instances, artifacts, independent objective, distance preparation |
| `reproducibility` | Identities, logical RNG streams, build/native observations |
| `solvers` | Configuration, registry, results, stopping/history, validation and conformance |
| `solvers/nn` | Adapter and explicitly named native implementations |
| `experiments` | Manifest planning and serial execution |
| `analysis` | Record validation, lazy sources, tables/comparisons and reports |
| `interface` | Authority, snapshots, preflight, durable jobs/worker, publication/query adapters |
| `frontend/src/session.ts`, `application.ts`, `navigation.ts` | App-owned authority/catalog context and durable-resource navigation |
| `frontend/src/results` | Bounded transport and read-only saved-results controller/table |
| `frontend/src/experiments` | Instance/plan/job orchestration, verified geometry and shared analysis presentation |
| `frontend/src/rendering` | Float64 model and WebGL2 display primitives |

Keep initializers lightweight: root imports and CLI help/version must not load
numerical, plotting or web dependencies. `tsp_hpc.analysis` exposes pandas APIs
without importing Matplotlib reports. Native caches are rebuildable runtime artifacts,
not portable project inputs. Changes to ingestion/publication dependencies must also
update `interface.ingestion.RULE_FILES`, which fingerprints validation rules.

## Solver registration and conformance

Algorithm authors own the solver/operator package, thin adapter, explicit registration
and algorithm-specific tests. Do not add algorithm branches to the runner, codec,
analysis or HTTP service. `solvers.configuration` normalizes science independently of
the installed registry so historical records remain readable.

`SolverRegistry` contains immutable `SolverSpec` entries and explicit defaults.
`default_registry()` registers `visited-scan-v1` and `remaining-array-v1`; it performs
no filesystem/plugin discovery. Add concrete registrations there, never placeholders
or imports that silently fall back. Test-only scripted GA/SA adapters are non-native
orchestration fixtures, not algorithms or advertised capabilities.

An adapter must:

1. Accept `PreparedDistances`, validate the objective/instance binding and warm only
   its selected kernels on synthetic state, outside search timing.
2. Expose detached `{**prepared.metadata, algorithm, variant, backend="serial",
   implementation, solver_warmup_time_s}` metadata. Native adapters also expose a
   nonempty `native_dispatchers` tuple of the selected warmed CPU dispatchers.
3. Implement the [shared solve signature](contracts.md#logical-solver-interface),
   validating direct calls with shared parameter/budget/history/seed normalization.
4. Create `SearchControl` immediately before initialization. Use fresh logical RNG
   state per invocation; preserve RNG/cooling/population state across safe checkpoints.
5. Return owned results, actual work and independently verified final costs. Follow
   the [sampling rules](contracts.md#solver-results-and-progress) without consuming RNG.

NN's logical mode is `batch`; independent populations/chains use `independent`.
`TrialPlan.invocations()` owns ordering and seeds. A native declaration requires warmed
nopython signatures with serial strict-math options, not a baseline alias around another
callable. Unsupported objectives/backends/time/history requests must fail explicitly.

The pytest-independent conformance helper provides a direct check:

```python
from tsp_hpc.problem.instances import fixture
from tsp_hpc.solvers.registry import default_registry
from tsp_hpc.solvers.conformance import check_adapter

spec = default_registry().resolve("nn", implementation="remaining-array-v1")
check_adapter(spec, fixture("square").instance, seed=2**63 + 7,
              budget={"start_cities": [2, 0]}, distance_mode="dense")
```

It bounds inputs to 64 cities and checks ownership, global RNG isolation, work/stopping/
history, independent costs, timings and fixed-work replay after an intervening solve
and with history toggled. Timed work need not replay identically.
`tests/solvers/test_conformance.py` parametrizes production registrations, both distance
modes, tiny fixtures, zero work, deadlines and invalid calls, including runner-to-analysis
round trips. Add separate operator/trajectory tests from the
[algorithm specification](algorithm-baselines.md#implementation-checks); common checks
cannot prove native RNG ownership, heuristic optimality or operator correctness.

## Verified installed builds

Ordinary `uv build` output has no verified source claim. For installed campaigns,
produce a wheel from a **clean committed checkout** using Git, Python 3.12 and uv:

```bash
python3.12 tools/build_verified_wheel.py --output dist/verified
uv export --locked --no-dev --group analysis --no-emit-project \
  --format requirements-txt --output-file dist/verified/dependencies.txt
uv venv --python 3.12 dist/wheel-env
uv pip sync --python dist/wheel-env/bin/python --require-hashes dist/verified/dependencies.txt
uv pip install --python dist/wheel-env/bin/python --no-deps \
  dist/verified/tsp_hpc-0.1.0-py3-none-any.whl
```

The producer archives HEAD privately, verifies package bytes, embeds build provenance
and rewrites wheel `RECORD`. It rejects dirty/untracked inputs, unsupported layouts
and existing outputs; it never modifies `src/` or accepts a caller-supplied revision.
`--offline` requires the matching uv fast path or cached build backend. The controlled
producer supports pure-Python wheels; ordinary wheels/sdists remain unverified.

Retain the wheel, adjacent `.whl.build.json` receipt, lock/export and source revision.
Use `dist/wheel-env/bin/python -I -m tsp_hpc run ...` with fresh output paths outside
the checkout—not `uv run`, which selects the checkout environment. Use trusted caches
and no source overlays; do not replace the wheel with a later editable/plain sync.
The installed environment needs neither Git nor the checkout.

[Runtime build provenance](usage.md#build-provenance) defines payload identity and its
trust limits; [HPC operations](hpc-resources.md#reproducible-container-environment) covers
image handoff. Installed tests build a disposable committed snapshot, install it with
locked dependencies and launch outside the checkout; this is functional acceptance,
not certification of an arbitrary release or container.

## CI and repository hygiene

[ci.yml](../.github/workflows/ci.yml) owns tool versions and commands. Its Python job
runs the locked interface-enabled warnings-as-errors suite and distribution builds;
its rendering-named job runs syntax, lint/format, frontend unit and Svelte/TypeScript checks
and Vite build. A browser matrix exercises mounted UI interactions in Chromium, Firefox and
WebKit. A separate browser-application job runs the real end-to-end acceptance
suite. Captured payloads compile against Python/TypeScript contracts.

Actions are commit-pinned with read-only permissions and no persisted checkout
credentials or cluster secrets. CI caches dependencies, not `.venv` or native JIT
artifacts; numerical threads are limited to one. Jobs have timeouts and superseded
runs are cancelled. CI does not deploy, operate the cluster or merge PRs.

Keep small intentional inputs in `examples/instances/`. Generated results, environments,
caches, builds, images, credentials and private account settings stay out of Git.
Document durable rules and commands here; keep task status and completion evidence in
issues/PRs. Update an existing reference instead of adding a guide for every task.
