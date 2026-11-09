# Development

Use the [README](../README.md#current-capabilities) for current capabilities and the [usage guide](usage.md) for library examples.

## Environment and dependencies

Use Python 3.12 and uv. [`.python-version`](../.python-version) selects the minor release; [pyproject.toml](../pyproject.toml) declares supported versions, dependency groups, the CLI entry point and the pinned build backend. [uv.lock](../uv.lock) is the authoritative list of resolved package versions and artifacts.

| Layer | Tools and role |
| --- | --- |
| Runtime | Python orchestration; NumPy arrays; Numba native loops, with `prange` for planned shared-memory paths |
| `dev` (default group) | pytest plus the `analysis` group, so the full suite exercises reports without skips |
| Optional `analysis` | pandas aggregation and Matplotlib figures |
| Optional `interface` extra | FastAPI, Pydantic and Uvicorn for the loopback workspace API; not required by numerical commands |
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

### Incremental Results design preview

`frontend/` begins the Svelte interface as a **fixture-only visual review slice**, not a production workspace or a second rendering benchmark. The owner requested incremental frontend review instead of approving unseen layouts: #71 stays open while the Results layout is reviewed; this slice brings forward only the minimal frontend scaffold from #73. #72 still owns real API contracts, and the broader #73 acceptance criteria remain outstanding. Fixture types are presentation data, not proposed API DTOs.

Use Node 24 and pnpm 10.32.1 (pinned in `frontend/package.json`), from the repository root. pnpm is the frontend package manager; do not generate npm or Yarn lockfiles. Python dependencies continue to use uv.

```bash
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend run dev
# Open the localhost URL printed by Vite; stop with Ctrl-C.
pnpm --dir frontend run check
pnpm --dir frontend run test
pnpm --dir frontend run build
```

The preview contains four explicitly fictional trials with separate experiment ID, instance, implementation, city count, algorithm, outcome, cost and search-time columns. Each column has a filter icon opening a native popover of checkboxes for its distinct values from the complete fixture collection. A selected-value count marks active filters. Values combine with OR within a column and AND across columns; no selected values means no restriction. A single Reset column action clears that column's filter and its sort, if active; it leaves other columns unchanged. The compact global Reset control clears all column filters and sorting; it is disabled only when neither is active. Options remain available even when other filters exclude their rows. Escape or clicking outside dismisses the popover. Dropdowns also offer ascending/descending single-column sorting. Numeric columns sort numerically with missing observations last in either direction; ties retain source order. A header arrow marks sorting without changing button dimensions. There is no text search. Fixed column proportions with a minimum table width, reserved filter-count space and a minimum-height table region keep ordinary filtering stable; narrow windows scroll horizontally rather than squeezing the columns. Expanded rows grow with the document instead of being clipped inside a fixed-height vertical table viewport. The inline square city view is compact (up to 420px, sized against viewport height), with controls overlaid inside it. A native fullscreen toggle expands the viewer for detailed inspection; Escape exits fullscreen. Fullscreen resizing reuses the same geometry, selection and camera. The application-wide Light/Dark switch also changes the WebGL background, points, selection and tour colours without rebuilding geometry or resetting filters/camera. The initial theme follows the system unless a valid local preference is stored; explicit choices persist when local storage is available. Theme storage failure does not prevent in-session switching. Viewer controls remain subdued until hover/visible keyboard focus, with 200ms transitions and reduced-motion/touch exceptions. Experiment IDs are presentation-only fixture IDs, not a decision about production campaign/execution/attempt identity. Selecting an experiment ID toggles its inline detail row; only one row can be expanded at a time. The fixtures illustrate successful/failed/blocked-comparison states and an empty-filter state. Only implemented controls are shown; future navigation, decorative headings and placeholder metadata/viewer sections are omitted. There is no file access, API, solver execution, chart integration, scientific comparison calculation or production pagination/virtualization. No backend capability or measured performance can be inferred from the fixture values. The four-row table is deliberately finite; bounded production data handling remains required by the stack decision.

The next read-only inspection slice mounts only inside the expanded row. `preview-01` and `preview-02` have explicit bindings to the same synthetic 128-city geometry, including two coincident IDs; only `preview-01` has a deliberately synthetic permutation exposed as **Fixture tour**. No route is inferred for the other rows. Missing bindings show an unavailable-artifact state instead of generating replacement geometry. These bindings are not checksum validation or the production snapshot/artifact contract in #80; the fixture costs/timings do not describe the displayed route.

The viewer reuses `examples/interface-rendering/model.mjs` and `webgl.mjs`, retaining canonical float64 coordinates separately from display buffers. The renderer now accepts an optional explicit open permutation for indexed tour drawing, preserving city IDs and closing the line implicitly. The original standalone reference still supports its synthetic row-order loop. Drag/arrow keys pan, wheel/plus/minus zoom, and Fit/Home restore the view. Clicking coincident points cycles their IDs. The square viewer stands alone; the coordinate list and pagination were removed at the owner's request. Editing is not enabled. Keyboard pan/zoom remains available, but non-canvas city inspection is not implemented in this slice and remains an accessibility follow-up. WebGL failure/context loss exposes a retry action. Unmounting releases GPU resources, observers, listeners and scheduled frames. The Vite development server allows only the frontend and the shared rendering-reference directory, not the whole repository.

Review this slice locally before expanding it: expand/collapse each experiment using mouse and keyboard; select multiple values in a column and combine column filters; dismiss filter popovers with Escape/outside clicks; filter out the expanded row; try a combination with no matching rows and clear the filters; inspect horizontal scrolling on a narrow window, including keyboard scrolling. Evaluate column readability, inline details and the clarity of diagnostic states. For inspection, expand the first two rows; test pan/zoom/fit, canvas selection, coincident picks, the fixture-tour toggle and fullscreen entry/exit (including Escape), then switch to a row without geometry. Verify collapse/filtering releases the view and reopening or sorting never attaches another row's geometry. Check context loss/retry in the browser; pure model/mock-GL checks do not certify real shader execution or recovery. Type/build checks do not certify browser rendering or accessibility; those still need interactive review. Keep feedback incremental rather than treating the initial layout as final approval. Prefer plain controls and useful labels over branding, slogans or explanatory filler.

`frontend/pnpm-lock.yaml` pins the frontend dependency tree; generated `frontend/dist/` and `frontend/node_modules/` remain ignored. Dependency build scripts are allowlisted only for `esbuild`, which Vite uses for builds. At introduction, `pnpm --dir frontend audit` reports one low-severity `deepmerge` prototype-poisoning advisory through the Svelte Vite plugin, with no patched version offered. This is development/build tooling, not an excuse to accept untrusted configuration: the preview loads no user-provided configuration or data. Recheck before release and when updating dependencies; full dependency/license distribution review and CI integration remain scaffold/packaging work.

### Read-only contract slice

The [read-only API v1 design](contracts.md#read-only-local-interface-contract-design-v1) defines the fixed-workspace Results boundary for #72. The workspace/session/catalog subset is now implemented below; dataset indexing, trial queries and geometry responses remain design-only, and the UI still uses presentation fixtures. Editing/job/summary-comparison contracts remain outstanding, so #72 stays open.

`src/tsp_hpc/interface/contracts.py` contains standard-library-only design types; it does not import the numerical stack or require web dependencies. `frontend/src/contracts.ts` mirrors them. The shared JSON example under `examples/interface-contracts/` exercises the same values in both languages; update both declarations, the contract reference and affected fixtures/tests together. Neither type system replaces runtime validation. The implemented workspace endpoints use strict Pydantic request models; the remaining Results DTOs are not yet runtime validators.

```bash
uv run --locked --extra interface pytest tests/interface -W error
pnpm --dir frontend run check
pnpm --dir frontend run test
```

Wire-fixture checks cover schema shape and exact numeric transport. Separate backend tests exercise workspace authorization and HTTP behavior, but there is still no working real-data Results viewer. The selected page/geometry bounds are conservative initial API limits, not browser performance gates.

### Local backend foundation

Install and launch from the repository root:

```bash
uv sync --locked --extra interface
uv run --locked --extra interface tsp-hpc serve --workspace /path/to/workspace --port 8000
```

Installed-package users can install `tsp-hpc[interface]`. The command binds **only `127.0.0.1`**; no remote host option, proxy-header trust, static frontend serving, uploads, arbitrary file/download endpoints, database or execution service is included. The current Vite preview remains independent. Missing optional dependencies fail with an installation hint, while `--help`, `--version` and numerical commands do not import the server. Startup validates the root and scan bounds before requesting a credential. An occupied port fails clearly; Ctrl-C closes the session and catalog.

**Session bootstrap:** an owner client creates a fresh random URL-safe secret, e.g. with Python `secrets.token_urlsafe(32)`, and retains it privately. Interactive startup asks for that same 43–128-character secret using a hidden terminal prompt. Automated owner clients can start the command with `--session-secret-stdin` and pass the secret through a private stdin pipe, never command arguments, URLs, environment variables or shell history. The [loopback integration test](../tests/integration/test_local_backend.py) demonstrates the complete in-memory launch/pair/request/shutdown flow without printing credentials. This is a developer bootstrap, not an account/login system or the final browser pairing UI.

The client then sends JSON `SessionRequest` (`api_version: 1`, `request_id`, `secret`) to `POST /api/session`. Exactly one successful exchange returns `SessionResponse` with a fresh random `session_token`; use `Authorization: Bearer <token>` thereafter. Pairing is disabled after success or five failed credential attempts. Restart to pair another client. Neither credential is logged or stored on disk. Use the token only in process memory in this slice. No endpoint reveals it without the private startup secret; raw ASGI tracebacks/access logs are disabled, and errors are sanitized.

| Implemented route | Contract and behavior |
| --- | --- |
| `POST /api/session` | One-time private bootstrap exchange; origin/host/body checks apply before credential checking. |
| `GET /api/workspace` | Authenticated `WorkspaceResponse`; optional `X-Request-ID`, otherwise a server correlation ID. |
| `POST /api/artifacts/query` | Authenticated `ArtifactQuery` → `ArtifactPage`, at most 500 candidates per page. All fields are required, including nullable `cursor`. |
| `POST /api/artifacts/refresh` | Authenticated `CatalogRefreshRequest` → `CatalogRefreshResponse`; atomically rebuilds the same root's catalog and rotates its revision/handles. |

Use exactly `http://127.0.0.1:<port>` as authority and disable environment-configured proxies in owner clients so credentials stay on loopback. Foreign/null origins, foreign/duplicate Host headers, cross-site/same-site fetch requests, unauthorized sessions and all query strings are rejected. Native owner clients may omit Origin; they still require the secret/token. There is no permissive CORS, WebSocket channel, OpenAPI/docs endpoint or frontend proxy integration yet. Responses use no-store/no-sniff/frame-denial headers. JSON bodies are bounded to 16 KiB even without Content-Length; strict request validation rejects coercions and extra/path fields. Sync catalog operations run in FastAPI's worker pool with a nonblocking single-operation lock; startup performs the initial scan before accepting requests. Uvicorn concurrency is bounded to 32.

**File policy:** the explicitly configured root is resolved once, pinned to an open directory descriptor and fixed for the session. It must be a trusted owner-controlled POSIX local workspace. Descendant directories/files are opened relative to that descriptor with no-follow flags; symlinks, special files, hidden entries/subtrees and extensions other than `.json`/`.jsonl` are excluded. `.json` is a generic JSON candidate; `.jsonl` is a result-shard candidate. Every listed item has `validation: unvalidated`; no parser, schema, checksum or scientific validity is implied. Original evidence is not modified. Absolute root paths/raw exceptions never appear in API responses. Candidate display names are relative labels, not accepted request paths.

Scans count at most 10,000 entries and descend at most 16 levels; exceeding either bound fails explicitly without publishing a partial catalog. Metadata identity (device/inode/mode/size/mtime/ctime) and directory changes invalidate the catalog. Refresh rotates session-bound handles and HMAC-protected cursors, even if no file changed. An internal 64-KiB-bounded reader reopens known handles with no-follow/nonblocking flags, checks regular-file identity before/after reading and rechecks the catalog; it has **no HTTP endpoint**. Replaced/deleted files and observed races fail closed. These metadata revisions are not scientific content digests; #75 must establish content-verified dataset revisions before analysis caching.

The trust boundary excludes hostile same-user processes, malicious filesystem owners/mounts and hard links created by the trusted owner: choosing a workspace authorizes its regular files. The implementation does not claim to defeat arbitrary same-user directory renames or metadata forgery between syscalls. Keep untrusted writers out of the root. Future scientific loaders must consume authorized verified bytes/handles, not reopen arbitrary embedded paths. No cluster operations are performed.

Tests cover API credentials/authority/body limits, cursor/session isolation, hidden/symlink/special files, scan limits, replacements/deletions, changes during bounded reads, cleanup and a real loopback startup/shutdown. Run all backend checks with the extra enabled; without it, optional HTTP tests skip. CI installs the extra and runs them with warnings treated as errors. Security tests do not complete the remaining frontend lifecycle, upload/export or execution flows in #73/#74.

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

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) is authoritative for runner/tool versions, action pins and commands. It installs the locked environment with the optional `interface` extra, runs the full suite with warnings treated as errors and builds distributions on branch pushes and PRs to `master`, including documentation-only changes.

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
