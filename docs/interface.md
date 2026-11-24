# Local interface

The browser connects generation, configuration, native NN and GA jobs, verified tours and
shared analysis. Python owns scientific validation, execution and statistics; browser
charts present server observations without computing new scientific summaries.

## Start the application

Use Python 3.12, Node 24 and pnpm 10.32.1. From the checkout, install once:

```bash
uv sync --locked --extra interface
pnpm --dir frontend install --frozen-lockfile
```

Start both local services with one command:

```bash
uv run --locked --extra interface python tools/local_app.py
```

This owner command explicitly enables local jobs, with one active worker process.
The browser and API configure serial GA, serial NN, and fixed-work multicore NN. The launcher creates a read workspace at
`data/generated/app-inputs` and a separate private execution root at
`results/app-output`. Override them with `--workspace PATH --execution-root PATH`;
roots must be disjoint. Put saved instances/references or CLI result shards in the read
workspace, not inside the private execution state.

At the hidden prompt, supply a fresh random **43–128-character URL-safe secret** from
a private password manager. Open **http://127.0.0.1:5173** and enter the same secret.
Never place credentials in arguments, URLs, environment variables or logs. Ctrl-C
stops both services, holding pending jobs and interrupting active work safely.

The launcher uses the checkout's Vite development server on 5173 and API on 8000;
occupied ports fail. Use `127.0.0.1`, not `localhost`. This is a local checkout app,
not bundled frontend delivery from an installed wheel. Native jobs require Linux
pidfds/procfs and enabled JIT. Numerical-only installations need no web dependencies.

## Run and compare an experiment

1. **Create or load:** choose uniform/clustered generation, city count, decimal uint64
   seed and dimensions, then **Generate instance**. Alternatively select an authorized
   instance JSON and **Load instance**. The backend creates an immutable snapshot;
   **Download canonical instance** saves exact CLI-compatible bytes, including large seeds.
2. **Configure:** choose one algorithm per reviewed job, implementations, distance modes,
   repetitions and a decimal uint64 solver seed. You can reuse the same input snapshot for both algorithms.
   - **Nearest neighbour:** enter ordered start cities. Serial is the default; fixed-work multicore
     NN also accepts a thread count, but not a time limit or history. See [resource limits](exact-and-multicore.md).
   - **Genetic algorithm:** select serial GA. Enter unique comma-separated population IDs (in logical order),
     population/tournament sizes, elite count, and crossover/mutation probabilities. Each ID is an independent
     population with its own derived seed, not a thread or repetition. Set a generation limit, a time limit,
     or both. Zero generations evaluates the initial population; for a time-only run, uncheck the generation
     limit. For a small first run, try two IDs (`a,b`), population size 5, three generations and one repetition.
     With both distance modes selected this produces two trials, each running both populations.
   - Serial runs can record history. Editing the algorithm, budget or resources invalidates review.
     A fresh rerun of a supported API- or UI-created job restores its configuration for review, not automatic
     execution. NN starts and GA generations are different work units, not a basis for speedup claims.
3. **Review:** **Review experiment** validates the shared plan without running it.
   Inspect resolved configuration, identities/seeds, estimates and job ceiling, then
   **Run reviewed experiment**. Configuration edits or choosing a new instance invalidate
   review. The expiry countdown disables an expired plan; the server remains the final
   authority. A failed fresh-rerun review never leaves an older plan executable. No manifest
   editing or copied ID/path binding is needed: the backend constructs the accepted manifest.
4. **Monitor:** job counts/state refresh while known jobs are progressing and the
   Experiments view and browser tab are visible. Idle/terminal/held queues do not poll;
   use **Refresh jobs** to discover work submitted by another client. Selecting a terminal result
   loads shared trial analysis, including distance mode, timings and eligibility.
   Completion does not mean every trial succeeded; failed/incomplete evidence stays visible.
5. **Compare:** select **Paired comparison**, baseline implementation/mode and timing
   metric, then **Show analysis**. For distance comparisons, choose on-demand as the
   baseline and end-to-end trial time to include matrix preparation. Ratio = baseline /
   target; above one means the target was faster. Search-only timing excludes preparation.
   Dirty/unknown source or incompatible work/context produces explicit rejection reasons.
6. **Inspect:** click an attempt ID to fetch independently verified final coordinates
   and tour. Pan/zoom/fit, toggle the tour or use the accessible coordinate/permutation
   view. The City ID control and coordinate-row buttons support keyboard selection;
   the selected city has a diamond marker and a textual coordinate readout. A failed/no-solution aggregate has no invented tour. Canonical float64 data stays
   separate from display buffers; signed zero/order survive export.
7. **Analyze:** summary, diagnostic, recorded-history, observed-target and reference-gap
   tables call shared Python functions. Choose an authorized reference JSON for gaps.
   Charts show only the current page and never interpolate history. Page controls and
   JSON downloads state their scope; a page download is not a complete campaign archive.

Table headers stay visible while scrolling through rows; horizontal scrolling keeps
headers aligned with their columns. Tables abbreviate long IDs and display durations with at most two decimal places,
using ms, s, min or h. Hover for original values; **Full-precision values** restores
exact analysis values and field names, independently of **All columns**. Saved-result expansion shows full IDs and seconds.
Storage, downloads and server-side sorting remain unrounded; exact time filters still
use seconds and ID filters use full IDs. Tiny positive durations display `<0.01 ms`, not zero.

Timings on tiny instances are functional examples, not controlled performance evidence.
Keep the checkout clean for eligible provenance-based comparisons. Time-budget runs
measure quality under budget and cannot supply implementation-speed ratios. Reference
kinds remain declarations; the interface does not prove optimality.

### Controls and reconnects

- Cancel queued work, request stopping after a trial, or explicitly confirm immediate
  interruption. Stop intent is not terminal evidence until the backend verifies exit.
- Disconnect/reload does not cancel jobs. Type `pair` in the owner terminal with a new
  secret before reconnecting; switching tabs shares the same in-memory grant.
- A lost submission acknowledgement or request timeout offers **Retry original submission**,
  never an automatic new campaign. Non-secret preflight/key/root retry metadata is retained
  in tab session storage when available; credentials remain memory-only. A retry for a
  different output root is refused. Inspect existing jobs if storage was unavailable.
  Unreadable retry metadata blocks new setup/submission and has an explicit owner-confirmed
  clearing action. Clearing a reference never cancels accepted work.
- Valid non-secret configuration drafts are saved separately, scoped to the output root.
  After reconnecting, **Restore saved input snapshot** revalidates the referenced input;
  execution still requires a fresh review. Draft storage never restores reviewed authority.
- View, job, attempt and explicit analysis-query links support Back/Forward and reload.
  A linked job is restored only after pairing with its original output root. Credentials,
  preflight authority and submission keys are not put in URLs. Catalog-bound reference
  selections may require explicit reselection after a refresh.
- Every backend restart holds dispatch. Inspect jobs and use **Continue held queue**
  explicitly. **Review fresh rerun** reloads the original snapshot/configuration for a
  new review/key/campaign; it never appends or resumes an old search.
- A job-status error pauses polling until **Refresh jobs** succeeds. Unrelated input,
  catalog or analysis errors do not disable job controls or erase unrelated verified views.
  Authorization loss resets both views; a stale result binding invalidates all publications
  from that source. Catalog refreshes invalidate old handles/selections in both views.
  A stale review requires re-review, not repeated execution. Capacity exhaustion preserves
  evidence; choose a new output root rather than deleting private state to reset keys.

## Browser local results

The **Saved results** tab shares pairing with Experiments. Select existing JSONL shards
and optionally an actual expected manifest (not an instance/reference JSON), then **Load**.
Whole-dataset filters/sorting and paged rows preserve incomplete/duplicate/usable flags.
Refresh invalidates obsolete datasets; ready sources are checked on queries, not by
background polling. This tab reads CLI shards; verified job tours are in Experiments.

For read-only operation, run `tsp-hpc serve --workspace PATH --port 8000` without execution
flags and start `pnpm --dir frontend run dev` separately. Pair in Experiments, then use
Saved results. `vite preview` is static-only and has no API proxy.

The separate **Viewer preview** is labelled synthetic and never substitutes for a real
result. Shared viewer controls support pan/zoom/fit, coincident-city picking, fullscreen,
theme changes and context-loss recovery.

## Local backend usage and security

Installed clients use `tsp-hpc[interface]`. The service binds only loopback and
validates the workspace before pairing. To permit execution, choose **existing,
disjoint** read and output roots and explicitly opt in:

```bash
uv run --locked --extra interface tsp-hpc serve \
  --workspace /path/to/read-workspace \
  --enable-execution --execution-root /path/to/execution-output
```

Both execution flags are required. HTTP cannot enable execution, change roots,
choose commands/imports/PIDs, or write to the read workspace. Local workers require
native JIT, Linux pidfds and readable procfs ownership metadata; capabilities probe
actual kernel permissions. These requirements do not restrict the standalone CLI.

### Pairing and transport

`POST /api/session` exchanges the startup secret once for a Bearer token and a
correlation-only `session_id`. Five failed attempts exhaust pairing. The owner's
terminal `pair` command accepts a fresh secret, revokes the old grant and permits
one new exchange. Invalid/unchanged secrets leave access unchanged. Rotation
preserves catalogs, datasets and jobs; it is not cancellation. No HTTP reset exists.

Native clients can use `--session-secret-stdin` with a private pipe; this disables
terminal re-arming. Ctrl-D disables terminal controls without stopping the service;
Ctrl-C shuts it down. Credentials, access logs and raw server tracebacks are not
persisted. Browser credentials must not enter storage.

Requests must use exactly `http://127.0.0.1:<port>`; native clients should disable
proxy environment settings. Host/Origin/Fetch Metadata checks reject foreign/null
origins, cross-site/same-site traffic and duplicate authority headers. Native clients
may omit Origin but still need credentials. The Vite proxy checks frontend authority
**before** rewriting it for the fixed backend. No permissive CORS, query strings,
WebSockets or public API-doc endpoints are provided.

Bodies are bounded to **16 KiB**, including chunked bodies; concurrency is capped at
**32**. JSON must be UTF-8 and finite, with no duplicate keys, escaped unpaired
surrogates or nesting beyond 64 levels. Strict models reject extra fields and scalar
coercion. Responses use no-store/no-sniff/frame-denial headers and sanitized errors.
Authorization is rechecked at handler admission after body parsing. Bounded handlers
and rotation serialize; an admitted operation may finish before its grant is revoked.
Background ingestion and scientific work run outside that admission lock.

### Workspace and private storage

The read catalog uses a POSIX directory descriptor and no-follow descendant reads.
Only regular `.json`/`.jsonl` candidates are listed; hidden entries, symlinks and
special files are excluded. Names are display labels; handles are references, not
credentials. Persisted paths never confer filesystem authority. Refresh rotates all
catalog handles/revisions/cursors and invalidates the dataset under the admission lock.

Execution creates owner-private `.tsp-local` state under the output root. A controller
lock excludes a second service; directory and lease identities are checked throughout
access. State is bound to the original read-workspace identity. Unsupported private
schemas, missing owner/queue state with retained evidence, or replaced roots/locks
fail closed. Inspect evidence offline and use a new root when it cannot be reconciled;
never delete state merely to reset submission keys. Scientific JSONL compatibility is
separate from this private protocol.

These checks do not defend against hostile same-user writers, filesystem owners,
mount changes or metadata forgery between syscalls. Selecting a root authorizes its
regular files, including owner-created hard links. Keep untrusted writers out.

## API contracts

[Python DTOs](../src/tsp_hpc/interface/contracts.py) and
[TypeScript DTOs](../frontend/src/contracts.ts) describe the wire format;
[request models](../src/tsp_hpc/interface/requests.py) validate input.
[Fixtures](../examples/interface-contracts/) exercise both languages. Static types
are not runtime validation, authority or scientific evidence.

All fields are required, including nullable ones. Use `api_version: 1` and a
`request_id`; responses echo the ID or generate one for invalid input. Workspace
requests carry `workspace_id`; execution requests add the current `session_id`
(`RunContext`). Dataset requests bind `dataset_id` and its ready `revision`.
All routes below are POST unless marked GET.

| Route | Request → response |
| --- | --- |
| `/api/session` | `SessionRequest` → `SessionResponse` |
| GET `/api/workspace` | optional `X-Request-ID` → `WorkspaceResponse` |
| `/api/artifacts/query` | `ArtifactQuery` → `ArtifactPage` |
| `/api/artifacts/refresh` | `CatalogRefreshRequest` → `CatalogRefreshResponse` |
| `/api/datasets` | `DatasetRequest` → `Dataset` (202) |
| `/api/datasets/status` | `DatasetStatusRequest` → `Dataset` |
| `/api/trials/query` | `TrialQuery` → `TrialPage` |
| `/api/execution` | `RunContext` → `ExecutionInfo` |
| `/api/instances/load`, `/generate` | `InstanceLoadRequest`, `InstanceGenerateRequest` → `StoredInstance` (201) |
| `/api/instances/snapshot`, `/export` | `SnapshotRequest` → `StoredInstance`, canonical `instance.json` attachment |
| `/api/runs/capabilities` | `RunContext` → `ExecutionCapabilities` |
| `/api/runs/preflight` | `ExecutionPreflightRequest` → `ReviewedExecution` |
| `/api/jobs/submit` | `JobSubmitRequest` → `JobSubmission` (202, including retries) |
| `/api/jobs/status`, `/review` | `JobStatusRequest` → `JobEnvelope`, `JobReview` |
| `/api/jobs/control` | `ExecutionControlRequest` → `JobEnvelope` |
| `/api/jobs/query` | `JobsQueryRequest` → `JobsPage` |
| `/api/queue/status`, `/continue` | `RunContext`, `QueueContinueRequest` → `QueueStatus` |
| `/api/jobs/result`, `/geometry` | `JobPublicationRequest`, `JobGeometryRequest` → `JobDetailsResponse`, `JobGeometryResponse` |
| `/api/trials/details`, `/geometry` | `TrialPublicationRequest`, `TrialGeometryRequest` → `TrialDetailsResponse`, `TrialGeometryResponse` |
| `/api/analysis/query`, `/api/jobs/analysis` | `DatasetAnalysisRequest`, `JobAnalysisRequest` → `DatasetAnalysisResponse`, `JobAnalysisResponse` |

### Read-only Results contract

One ingestion worker maintains one in-memory dataset, with no ingestion queue,
persistent cache or shard copies. A new accepted selection replaces the old dataset;
concurrent submissions return `ingestion_busy`. States are `loading`, `ready`,
`failed`, `stale`; only `ready` has a queryable revision. Publication binds source
hashes, manifest bytes, validation code, dependencies, limits and workspace context.
Status/query/lazy-read boundaries recheck sources; observed changes invalidate the
index. Code changes require restart. Shutdown cancels cooperatively and joins the worker.

`ready` means shared structural acceptance, not independently verified objective
costs or provenance. The [analysis rules](usage.md#validation-and-comparison-rules)
preserve failures, duplicates and incomplete prefixes. An expected manifest is needed
to detect entirely absent executions. Public diagnostics omit private paths/content.

Trial rows are keyed by **attempt ID**, not configuration or campaign. Query fields:
`attempt_id`, `instance_id`, `implementation`, `n_cities`, `algorithm`, `outcome`,
`best_cost`, `search_time_s`.

- Filters use typed equality: OR within a column, AND across columns; absent/empty
  filters are unrestricted. Duplicate columns are rejected; duplicate values normalize.
  Null is legal only for implementation/cost/time. Strings are case-sensitive.
- Sort one column numerically or by Unicode code point, nulls last in either direction;
  ties/default ordering use ascending attempt IDs. Cost/time accept finite float64
  spellings; city counts require exact safe integers ≥2. Booleans are not numbers.
- Keep `campaign_complete`, `duplicate_execution` and `usable`; filtering cannot
  repair exclusions. `outcome` is trial completion/failure, not job state.
- Pages use `limit` (1–500), nullable `cursor`, `query_key`, `items`, `next_cursor`.
  Signed cursors bind revision, normalized query and size; request IDs may change.
  Changed context requires a fresh first page, never silent pagination restart.

### Snapshots and preflight

Loading captures an authorized catalog artifact; generation uses the shared
uniform/clustered generators. Immutable snapshots retain canonical bytes, SHA-256,
coordinates, signed zero, seeds and provenance. Source edits do not mutate them;
stored-byte changes fail closed. Partial creations remain evidence and consume capacity.
Export is a bounded canonical attachment, not arbitrary file access. Save its bytes;
JavaScript Number parse/re-serialization can corrupt the artifact's uint64 seeds.

Capabilities enumerate real registry entries, resolved defaults, availability and
limits. `ScientificConfiguration` reuses shared parameters/budgets/history and exact
string seeds; null implementation means registry default, not algorithm fallback.
Independent populations/chains require explicit logical IDs. NN uses one ordered batch.

Preflight binds the snapshot, session, output root, code/dependencies/compiler and
resource policy. It returns ordered trial/logical IDs/seeds, estimates and an expiring
review ID. It prepares no distances, warms no solvers, launches no worker and writes
no results. Bounds precede manifest expansion; duplicate default/explicit aliases
are rejected. Expiry, re-pairing, restart or changed bindings require fresh review.
Search allowances apply per logical invocation; their sum is not a wall-clock guarantee.

### Jobs, controls and recovery

One execution root permits **one active campaign/owned Python worker**. Immutable
input and reviewed settings precede atomic, fsynced queue admission; only committed
entries dispatch. Same submission key + same preflight ID returns the original job,
even after expiry/re-pairing/restart. A different preflight conflicts; a new key is a
deliberate new campaign. Unacknowledged/orphan directories consume retention and never
silently execute. No credential enters the worker plan.

Workers use a fixed isolated interpreter command and inherited job/cache/lease FDs,
not a shell. They recheck bindings, apply reviewed affinity/serial library settings and
OS resource limits, then call the CLI's `run_manifest`. Queue order does not affect
scientific IDs/seeds. `completed` means every trial was recorded, **not** every trial
succeeded; inspect `failed_trials`. Job wall time is not search time.

Controls require the current `expected_revision`:

- `cancel_queued`: cancel pending/held work without launching it.
- `stop_after_trial`: persist intent for the next observed trial boundary; never
  fabricate a partial logical search. Current-revision repetitions are idempotent.
- `stop_now`: additionally require `confirm_stop_now: true`; signal only verified
  owned work. Stops cannot be downgraded. `stop_requested`/`stopping` remain nonterminal
  until exit is verified. An already recorded completion wins over a late stop.

Disconnect/re-pairing does not cancel work. Shutdown holds pending jobs and interrupts
active work. Restart **always holds dispatch**, adopts verified survivors and never
retries interrupted jobs. Recovery checks boot/start/worker/job identity and the
kernel executable before acquiring a pidfd; it never signals a guessed PID. Only an
unreaped direct child is eligible for cleanup if initial pidfd acquisition fails.
Unverifiable live ownership stays `recovering` with dispatch held. Continue explicitly
using `/api/queue/continue` and its current revision; inputs/bindings are checked again.
Cancel other unwanted pending jobs too: stopping one campaign does not cancel the queue.

Result handles appear only after verified exit and shared source/manifest validation.
Operational failure cannot be relabelled success by a completion footer; valid scientific
prefixes remain inspectable. A state-write error **after rename** can leave a durable
admission without acknowledgement: the controller freezes mutations/dispatch and stops
owned work, rather than overwriting disk from stale memory. Restart, inspect, and retry
the original key/preflight. Preserve evidence if reconciliation fails.

For a whole-campaign rerun, get the accepted configuration from `/api/jobs/review`,
preflight against current capabilities and the original snapshot, then submit a **new
key**. Campaign/attempt IDs and output files are fresh; scientific IDs/seeds can match.
There is no append, resume, automatic retry or latest-successful-attempt selection.

### Verified publication and exact transport

Job publication binds the terminal job/result revisions, campaign, attempt and original
snapshot. Both the copied input and original snapshot must match the admitted content.
Read-only publication instead names an authorized instance handle/catalog revision and
exact dataset/campaign/attempt. Neither path follows a record's input path, searches
for substitute files or chooses an attempt implicitly.

Shared readers and independent coordinate-based `validate_solution` verify every
accepted logical final tour and the aggregate. Details expose projected settings,
work, seeds, timings and eligibility, not raw records/environment/exception text.
Legacy records are not retroactively certified native. A failed trial has no aggregate
winner; a completed trial in an interrupted campaign may have a valid final tour while
remaining ineligible for aggregates.

Details return a `geometry_revision` required by the geometry request. Coordinates
are `f64-le-base64` C-order little-endian pairs (**16n bytes**); a real final tour is
`u32-le-base64` (**4n bytes**, exactly n cities, implicit closure), otherwise null.
Use explicit little-endian decoding, preserve signed zero/duplicates/order, and check
bounds before allocation. No intermediate tours or normalized coordinates are invented.
All seeds are canonical uint64 decimal strings; larger-than-safe exact integer
observations are strings too. Missing observations are null, never zero or a sentinel.

### Shared analysis queries

`query` contains `table`, nullable `attempt_ids`, `limit` and nullable `cursor`.
Null attempts means all; an empty list means none. IDs must be distinct and known.
Subset selection preserves full-index exclusions, including duplicates.

| Table | Additional required fields |
| --- | --- |
| `trials`, `summary` | none |
| `diagnostics` | none; `attempt_ids` must be null |
| `facets` | whitelisted trial `field`; whole-selection counts, missing last |
| `comparisons` | `baseline_implementation`, nullable `baseline_distance_mode`, `metric` |
| `references` | authorized `reference_handle`, `artifact_catalog_revision` |
| `progress` | `max_points` |
| `targets` | `instance_id`, finite nonnegative `target_cost`, `max_points` |

These call the same [analysis functions and history rules](usage.md#reusable-data-api)
as the CLI. Responses include columns/items, total rows, coverage, clock labels and
optional reference binding. Private source/exception/citation text is omitted. Cursors
bind selection/options/size/content, including reference SHA-256. History streams one
page at a time, but the **entire selection** must fit `max_points`; paging is not sampling.

Logical and concatenated-search clocks exclude setup/validation/persistence gaps;
targets are first **observed** crossings, with exclusions and censoring preserved.
No interpolation, first-hitting-time inference or cross-algorithm work equivalence is
implied. Timed trials cannot yield implementation-speed ratios. References use shared
checksum/objective rules; `exact`/`best_known` is a declaration, not proof.
`validation: "shared-records"` certifies structural/source checks, not independent
coordinate verification of every historical observation. Final publication is separate.

## Resource bounds and failures

Defaults below are admission/materialization/observed limits, **not total RSS, hard
real-time or filesystem quotas**. OS/cgroup limits may be stricter. Exhaustion rejects
work or stops the owned worker; it never silently truncates science or evicts evidence.

| Resource | Default bound |
| --- | --- |
| Catalog | 10,000 scanned entries; depth 16 |
| Ingestion | 64 shards; 64 MiB including manifest; 64 KiB manifest; 4 MiB record |
| Ingestion index | 10,000 trials/plans; 100,000 plan × logical items; 16 MiB plan/serialized rows; 32 MiB table estimate |
| Public ingestion diagnostics | 100 summaries, with explicit total/truncation |
| Execution | 4,096 cities; 128 logical searches; 32 repetitions; 128 trials |
| Retention | 8 pending jobs; 256 job directories; 64 snapshots; 64 preflights (900 s validity) |
| Private state | 32 root entries, including torn atomic-write temporaries |
| Input/matrix/output | 2 MiB instance; 128 MiB matrix; 4 MiB plan/record/response; 64 MiB results |
| Worker | 4 GiB address space; 300 s wall ceiling; CPU/file/core limits |
| Compiler cache | 256 MiB apparent/allocated bytes; 4,096 entries; 16 contexts; traversal depth 8 |
| Analysis | 100,000 selected history points; 10,000 materialized rows; 128 MiB estimate; 64 KiB reference |
| Geometry | 16 MiB combined decoded coordinates/tour, also subject to response bound |

Cache files use an owner-only umask and no-follow scans before launch, every 250 ms
while ownership is known, and at finalization. Transient overshoot is possible; cache
faults stop owned work and preserve files. Use a new root or inspect retained state
offline at capacity. Ingestion estimates likewise exclude temporary/library allocations.

Every endpoint uses `ErrorResponse`: version/request ID, stable `code`, sanitized
`message`, `retryable`. Retryable permits explicit recovery, not an unbounded loop.
400 covers invalid fields/JSON; 403 authority; 404 missing objects/routes; 409 stale
context, unavailable state, busy queues or conflicts; 413 resource bounds; 422 invalid
artifacts/plans; 503 unavailable workspace/execution; 500 internal errors. Unsupported
methods/content types use 405/415. `result_unavailable` is 409 before valid publication
or 404 for a missing exact attempt. Reload stale context; never substitute data or
create a new submission key merely to retry a lost acknowledgement.
