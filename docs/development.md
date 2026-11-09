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

## Local interface scope

The [interface roadmap #67](https://github.com/itwasneo/computational-methods-project/issues/67) remains subordinate to the TSP project. **The owner ended browser measurement work: no further browser benchmarks, profiling campaigns or numerical browser-performance gates are planned.** Earlier measurement requirements are superseded, not certified as passed. Keep implementation and functional checks focused; do not recreate a separate browser testing platform.

### First-release journeys and boundaries

| Journey | Required outcome |
| --- | --- |
| Inspect saved results | Open workspace-local runner shards; browse a paged/virtualized trial table; filter and inspect summaries, failures and incomplete trials without loading full tours into every row. Missing/stale sources and invalid records remain visible diagnostics. |
| Compare experiments | Select an explicit baseline and timing metric; use shared analysis for eligibility, paired ratios and objective-compatible reference gaps. Show rejection reasons, unknown provenance and missing references rather than manufacturing a comparison. |
| Inspect an instance/tour | Navigate and select cities; inspect a final tour only against its exact instance identity. Keep duplicate city IDs distinguishable, including through a non-canvas inspection path. Missing artifacts must not be replaced with a similarly named instance. |
| Create/edit an instance | Generate through shared generators, import validated artifacts, edit coordinates with bounded undo/redo, and save/export through the canonical artifact API. Editing must not mutate an already submitted snapshot. Manual-instance provenance requires #81. |
| Run locally | Discover supported capabilities, preflight and submit an immutable NN run, monitor its actual lifecycle, cancel/recover jobs, inspect snapshot-bound results and export CLI replay inputs. Unsupported configurations must be rejected, not silently substituted. |

The initial execution scope is the runner's serial NN implementations with fixed work and unrounded Euclidean distances. Capability discovery must reflect the actual runtime. GA/SA, parallel solvers, time budgets and convergence history are not promised until their shared runner/analysis integration exists. Do not synthesize intermediate tours, percentage completion, histories or efficiency evidence. Queue/job states are distinct from scientific trial outcomes; UI/queue timings are not runner phase timings.

The interface is optional, local-first, workspace-bounded and usable without external assets. Accounts, cloud services, telemetry, cluster submission/operations, collaborative editing and a new scientific engine are out of scope. Reuse the [shared contracts](contracts.md) and [identity rules](manifests.md#identity-rules): finite float64 coordinates, legal duplicates, row order, exact uint64 seeds and immutable identities must survive transport. GPU display coordinates/level of detail never replace canonical scientific coordinates. Existing CLI and minimal numerical installations must not require interface dependencies.

### Interface stack and data-flow decision

For [#70](https://github.com/itwasneo/computational-methods-project/issues/70), adopt the following stack for the optional local interface. This is an architecture decision, not an implemented scaffold or a performance claim.

| Layer | Decision and rationale |
| --- | --- |
| Frontend | Svelte 5 + TypeScript + Vite. Use a client-side workspace without SvelteKit/SSR or a second application server; keep navigation and state proportionate to the local application. |
| Charts | Apache ECharts, importing only required chart/components directly rather than adding a Svelte wrapper. Use it for interactive runtime and conditional-quality views, not scientific calculations or the city viewer. |
| Instance/tour viewer | WebGL2 primitives described below; canonical float64 geometry remains separate from display buffers. |
| Local API | FastAPI + Pydantic + Uvicorn, adapting the existing Python APIs with explicit validated contracts and OpenAPI rather than duplicating the scientific engine. |
| Installed workflow | One CLI command starts the local API and serves bundled frontend assets from the same localhost origin. Node is a development/build requirement, not an installed-use requirement. |
| Development workflow | Vite serves the frontend and proxies API requests to the local Python service. Do not require permissive production CORS or external/CDN assets. |

Svelte, Vite, FastAPI and Pydantic use MIT licenses; TypeScript and ECharts use Apache-2.0, and Uvicorn uses BSD-3-Clause. These permissive upstream licenses fit local distribution, but do not substitute for checking exact versions, transitive licenses and required bundled notices when dependencies are locked in #73. ECharts provides scatter/line charts, zooming, tooltips and coordinated selection in one maintained library, avoiding a custom chart engine; direct integration avoids a separate wrapper compatibility lifecycle. Bounded Python-produced datasets keep its role presentational, without implying a measured performance advantage. Keep its rendering separate from the WebGL2 viewer and provide accessible tabular alternatives to chart-only information.

The data path must stay bounded and preserve the shared scientific rules:

- **Tables:** filter and sort on the server; return bounded pages of scalar summaries and virtualize visible rows. Do not put full tours, coordinates or manifests in table rows. Retain only a bounded window of pages and bounded detail/chart caches; evict obsolete data when the query or workspace changes. Virtualized rendering alone does not bound retained browser memory. Specify stable ordering, pagination and response limits in #72; implement the index/query path in #75–#76.
- **Charts:** request bounded summaries for the selected scope, with limits on both series and points. If the scope exceeds a limit, request narrower filters or explicitly identified aggregation; never silently sample, truncate evidence or calculate ratios from display aggregates. Missing/rejected comparisons remain diagnostics, not zero-valued observations.
- **Lazy details:** retrieve full trial records and identity-bound instance/tour artifacts only when opened, never eagerly for an entire campaign. Bound detail responses and define supported artifact sizes/access mechanisms in #72 and the viewer/editor tasks. A selected supported instance may require its complete canonical coordinates for editing and identity validation; reduced-precision display buffers cannot replace that data. For unsupported sizes, show an explicit limitation and an export option where available, rather than silently reducing scientific data or treating export as a substitute for supported viewing/editing.
- **Scientific authority:** use `tsp_hpc.analysis` (`read_shards`, `summary_table`, `paired_comparisons`, `reference_gaps`) and the shared artifact/manifest APIs. Python owns validation, grouping, baseline/reference eligibility and rejection reasons. TypeScript owns presentation and interaction, not a second implementation of these rules. Preserve the [transport and identity constraints](#first-release-journeys-and-boundaries); typed API details and fixtures belong to #72.
- **Staleness:** identify workspace/source revisions and query identity in requests/responses. Cancel superseded client requests where possible and discard responses that no longer match the active scope. Invalidate source-bound cached results when inputs change; cancellation alone is not a correctness guarantee.
- **Background work:** run shard ingestion and expensive analysis outside the API event loop/request execution path, with bounded concurrency and identifiable job/source revisions. Publish results only for their matching revision; show loading, invalid and stale states explicitly. The worker/index/cache design belongs to #75–#76, not this stack decision. The current reader retains O(trial count) summaries, so bounded browser responses are not a claim of constant-memory backend ingestion.
- **Optionality:** isolate interface dependencies and imports from the numerical runtime and existing CLI commands. Bundle local assets; introduce no accounts, telemetry, cloud services or cluster operations.

Implementation is staged: #71 owns workspace/interaction design, #72 owns typed contracts, and #73 introduces locked dependencies, the optional scaffold and focused type/build/smoke checks. Subsequent flow tests should cover page/detail boundaries, oversized-chart handling, stale-response rejection and agreement with shared Python analysis. This documentation-only decision introduces no runtime behavior to test; it does not certify those future checks. No browser measurement matrix, numerical performance gate or benchmark harness is required.

### Firefox rendering decision

Use **WebGL2** as the city/final-tour viewer baseline: instanced points, a final selection overlay, one O(n) tour polyline, spatial picking and frame-batched changed-point uploads. Keep canonical float64 geometry separate from float32 display buffers. Do not introduce a WebGPU dependency or a second rendering backend without a demonstrated need. Framework/chart choices are recorded in the [stack decision above](#interface-stack-and-data-flow-decision).

[Archived decision evidence (#97)](https://github.com/itwasneo/computational-methods-project/pull/97) records the completed Firefox work; it is not a request for more measurements. WebGPU was disabled by default on the tested profile; WebGL2 used the RTX 2060. Limitations remain recorded: occasional stalls and a transient RSS observation above the former proposed ceiling. These diagnostics do not establish universal responsiveness. Reuse contexts/buffers and avoid unnecessary allocations during ordinary implementation; no follow-up browser measurement campaign is required.

### Small rendering reference

`examples/interface-rendering/` retains only the adopted WebGL2/picking primitives, a small interactive example and focused regression checks—not a frontend scaffold or permanent benchmark subsystem. From the repository root:

```bash
node --test examples/interface-rendering/*.test.mjs
python3 -m http.server 8765 --bind 127.0.0.1 --directory examples/interface-rendering
# Open http://127.0.0.1:8765 in Firefox; stop the server after use.
```

Load/reset a synthetic fixture; drag pans, wheel zooms, shift-drag moves the selected city, and repeated coincident picks cycle IDs. Keyboard navigation and city fields offer non-canvas controls. The reference uses a fixed fixture projection: unsupported float32 edits are rejected atomically, not clamped; automatic rebasing, snapshot identity binding and undo remain future viewer/editor work. Context loss requires a reload. Synthetic row-order tours are not solver results. The page has no framework, external assets, solver execution or production API.

Node 24.14.0 CI retains only model/picking and WebGL draw-order/resource-ownership regressions, plus syntax checks. Mock GL calls do not validate shaders, and CI timings are not performance evidence. WebGPU, diagnostic/export UI, browser-matrix drivers and their obsolete mocks/tests have been retired. Historical evidence remains linked above. Future functional checks must stay proportionate to the application; do not restore browser measurement campaigns or the retired harnesses.

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
