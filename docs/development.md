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

## Local interface scope and performance acceptance

This is the **owner-approved acceptance baseline** for [#68](https://github.com/itwasneo/computational-methods-project/issues/68), under the [interface roadmap #67](https://github.com/itwasneo/computational-methods-project/issues/67). The owner accepted the reference desktop, browser/profile matrix, workloads and budgets during task planning. This is not an implemented-feature list or measured performance claim; rendering/data prototypes (#69–#70) must supply evidence before selecting the production stack. Change a failing budget only through an explicit reviewed decision, not by silently reducing the workload.

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

### Reference environment

Use a physical desktop session, local SSD workspace, AC power, normal browser hardware acceleration and a 60 Hz display. The candidate primary machine available during drafting has an Intel Core i7-9700K (8 cores), about 78 GiB OS-visible RAM and an NVIDIA RTX 2060. This is the accepted reference machine, **not evidence of performance on lower-end laptops**. Cap application memory through the budgets below rather than treating all host RAM as available.

Test both Chromium stable and Firefox stable on that same machine. Record exact browser builds, OS/kernel, GPU driver, graphics API/adapter, acceleration status and refresh rate in every evidence set; “latest” alone is not reproducible. Firefox 159.0 was detected during drafting; Chromium availability and browser GPU support have not been verified. Do not count software rendering as a passing GPU run. WebGPU availability is not a release assumption: #69 must document a supported fallback or an explicit unsupported-browser state.

Use a 1440 × 900 CSS-pixel viewport at DPR 1 for all performance gates. Repeat graphics gates at DPR 2 (same CSS viewport); check keyboard/focus, panel reflow and non-canvas inspection at 1024 × 768, DPR 1. These are selected profiles, not every viewport/browser/device combination. Freeze exact hardware/browser profiles with the first measurements and repeat final acceptance on the recorded profiles, documenting upgrades.

### Reproducible workload matrix

Keep graphics and campaign workloads separate; do not require their full Cartesian product or execute a 50k-city NN search to benchmark rendering.

| Profile | Scales | Required exercise |
| --- | --- | --- |
| G: graphics/editor | 1k, 10k, 50k cities | Fixed-seed uniform and clustered coordinates plus duplicate/coincident, collinear and zero-extent cases; pan, zoom, resize, pick, inspect, move a selection, undo/redo and draw one closed final-tour polyline. Never render a complete graph. |
| R: result explorer | 1k, 10k, 100k trial rows | Deterministic runner-v1-compatible shards with multiple instances/configurations/repetitions, valid outcomes, failures and incomplete trials; scroll, sort, filter, change baselines, inspect summaries and request lazy details. Include rejection/missing-artifact cases. |
| C: concurrency | G at 10k; R at 100k, tested separately | Repeat interactions during bounded background ingestion, then during one real NN worker restricted to one CPU thread. Record worker workload and duration; do not saturate all cores or run ingestion and a solver together as an implicit requirement. |
| E: end-to-end correctness | Small canonical fixtures | Import/edit/save, submit NN, continue editing, inspect original-snapshot results, cancel and replay through the CLI. Verify identities and shared-analysis decisions, not solver speed. |

Retain exact seeds, generator parameters, artifact checksums, shard layout/byte sizes and scripted actions with the harness. Keep coordinate/row generation outside measured interactions. Synthetic rendering tours and campaign records are interface fixtures, never solver-quality or speedup evidence; isolate and label them, and use real runner output for E. Keep invalid shards in separate diagnostic cases so they do not accidentally invalidate the valid-data performance fixture. Do not bypass scientific validation to improve timings.

### Blocking budgets

These targets apply to every listed scale of G/R on both reference browsers, including G at DPR 2 and the C repetitions. Times are end-to-end from browser input/request to presented feedback/result, not just backend compute. Use monotonic clocks within each process; correlate request IDs rather than subtracting unsynchronized browser/server clocks.

| Measurement | Gate |
| --- | --- |
| Continuous pan/zoom, drag and table scroll | Frame interval p95 ≤ 16.7 ms (60 FPS target), p99 ≤ 33.4 ms over a 10-second scripted interaction; record dropped frames and worst stall. |
| Local selection/edit/undo feedback | Input-to-visible-feedback p95 ≤ 50 ms; canonical commit may be asynchronous but must not be lost or reordered. |
| Filter/sort/baseline request | Pending feedback p95 ≤ 50 ms; latest warm result rendered p95 ≤ 250 ms. Obsolete responses must never overwrite newer state. |
| Cold application start | Launch to usable workspace shell ≤ 3 s; shell remains usable while sources are indexed. |
| First result page | Once shell is ready, source-open to first usable page ≤ 5 s cold / ≤ 1 s warm, including required source validation/indexing. No partially validated data presented as accepted. |
| Instance/tour open | Request to navigable G view ≤ 2 s cold / ≤ 500 ms warm, including transfer/parse/upload. |
| Memory | Peak incremental browser process-tree RSS ≤ 512 MiB and API/index/ingestion process-tree RSS ≤ 512 MiB at the largest individual G/R scale. Record absolute RSS too; report GPU allocation separately. Solver process/matrix memory is separate, not hidden in these totals. |
| Retained resources | After 20 open/filter/close or edit/undo cycles and a 30 s settle, retained RSS growth ≤ 32 MiB relative to the first settled cycle; histories/caches must remain bounded. |
| Idle | With no job/ingestion and a static visible workspace, average application CPU ≤ 1% of one logical core over 60 s; no continuous animation/redraw or unbounded polling. Report browser and API separately and together. |

Cold means a stopped application, a fresh browser profile/cache and no interface index/summary cache; OS filesystem caches are not forcibly dropped, and this limitation must be disclosed. Warm means the same source identities already validated/indexed in the current session, with no source changes. Separately measure invalidation after modifying/replacing a shard: stale data must be labelled immediately upon detection, and replacement data must satisfy the cold first-page budget. Report detection latency and invalidation work; do not claim warm hits for changed sources. Cold-start budgets exclude installation/build, but include normal application startup; report any first-job JIT preparation separately in canonical runner timings.

### Evidence and acceptance procedure

1. Run production builds without browser extensions, open developer tools or profiling instrumentation in the acceptance pass. Collect traces in a separate diagnostic pass. Record repository revision/dirty state, lockfile identities, environment, workloads, commands and harness version.
2. For each required G/R/C browser/profile case, perform five independent cold trials and five warm trials. Each interaction trial includes a 10-second continuous segment and at least 100 discrete actions for feedback/query measurements. Report per-trial p50/p95/p99 and worst observations; do not pool a failing trial into a passing average. Startup/open gates must pass in all five trials.
3. Record transfer/parse/upload, backend query/index time, frame/input timings, memory and idle CPU separately so a failure can be located. Sample process trees consistently, identify shared browser/GPU processes and avoid double-counting. Measure concurrency for the whole interaction window and retain worker settings.
4. Retain a reproducible harness and sanitized compact measurements with the prototype work; record interpretation and acceptance links in the issue/PR. Keep raw traces, generated large fixtures and private machine details out of Git. No measurements have been collected for this baseline.
5. A correctness/accessibility failure, stale-response overwrite, unsupported required profile, missing evidence or budget breach blocks acceptance for that profile. Record failures and mitigation/retest explicitly. Functional CI checks remain required, but shared-runner CI timings are not these hardware performance gates. UI speed must never be reported as solver speedup or scaling efficiency.

**Decision:** use the desktop and two-browser/profile matrix, workload scales and numerical budgets above. Budget feasibility remains unverified: #69–#70 must measure it and justify the stack. Final release checks (#92) repeat this matrix rather than introducing unmeasured responsiveness claims.

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
