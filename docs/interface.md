# Optional local interface

The interface supports the TSP workflow; it is not a second scientific engine or a prerequisite for CLI experiments. See the [README](../README.md#current-capabilities) for project-wide status and [development](development.md) for checks/builds.

**No further browser benchmarks, profiling campaigns or numerical browser-performance gates are planned.** Earlier measurement requirements are withdrawn, not certified as passed. Keep functional checks proportionate; do not restore the retired benchmark harnesses.

## Current boundary and selected stack

| Part | State |
| --- | --- |
| Svelte 5 + TypeScript + Vite frontend | Fixture-only Results table and read-only viewer; no API wiring |
| WebGL2 viewer | Shared rendering/picking primitives; no production artifact binding |
| FastAPI + Pydantic + Uvicorn backend | Session pairing, read-only catalog, bounded ingestion and paged scalar trial queries |
| Apache ECharts | Selected for bounded runtime/quality charts, not installed or integrated |
| Results/geometry API | Dataset lifecycle and trial queries implemented; facets/details/geometry remain planned |

The intended workflow remains results explorer → instance editor → local run controls. Editing/undo, manual-instance provenance, capabilities/preflight, isolated jobs, cancellation/recovery, comparison charts and CLI replay integration remain future work. Editing must use shared generation/artifact APIs and bounded undo/redo without mutating submitted snapshots. Jobs must use immutable submissions, reject unsupported configurations and report actual lifecycle outcomes. Local execution initially targets the existing fixed-work, unrounded serial NN runner; additional algorithms/history require their actual shared-backend support.

The selected architecture is a client-side application without SvelteKit/SSR or another application server. Installed delivery is planned as one CLI command serving bundled assets and the API from one localhost origin; Node is for development/builds. A Vite API proxy is planned for development, but neither proxy integration nor installed frontend serving exists yet. Do not introduce permissive CORS or CDN dependencies to bridge that gap.

ECharts is presentational, imported directly by required components without a Svelte wrapper; it does not compute scientific results or replace the city renderer. Python owns validation, grouping, baseline/reference eligibility and diagnostics through the existing analysis/artifact APIs. Accounts, cloud services, telemetry, collaborative editing, arbitrary shell execution and cluster operations are out of scope. Numerical installations must remain independent of interface dependencies.

## Results and viewer preview

Use Node 24 and the pnpm version pinned in [frontend/package.json](../frontend/package.json). From the repository root:

```bash
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend run dev
# Open the local URL printed by Vite; stop with Ctrl-C.
```

The four rows are fictional presentation fixtures, not API records or solver evidence. Their costs/times do not describe the displayed geometry. The preview performs no real-data ingestion, pagination/virtualization, chart calculation or solver execution.

- Column popovers filter by multiple values: OR within a column, AND across columns. Empty selections are unrestricted; options come from the full fixture collection. Reset column clears its filter and active sort; global Reset clears all filters/sorting. Escape/outside clicks dismiss popovers.
- Sorting is single-column, numeric where appropriate, with missing observations last and source-order ties. These preview rules are not the production query contract below. Selecting an Experiment ID toggles one inline detail row; narrow views scroll horizontally.
- Only `preview-01` and `preview-02` bind the same synthetic 128-city instance; only the first has a **Fixture tour**. Other rows show unavailable geometry rather than generating a substitute.
- Drag/arrow keys pan; wheel/plus/minus zoom; Fit/Home resets the view. Repeated coincident picks cycle city IDs. Fullscreen exits with Escape; light/dark switching preserves geometry/camera. Theme preference uses local storage when available, otherwise system/in-session state.
- Context loss exposes Retry; unmounting releases resources. Editing and non-canvas city inspection are not implemented in this preview. Functional checks do not certify complete accessibility.

The viewer uses `model.mjs` and `webgl.mjs` in [frontend/src/rendering](../frontend/src/rendering/), alongside their TypeScript declarations and focused tests. The Svelte preview is the only viewer entry point; the standalone demo has been removed. Vite permits filesystem access only within the frontend.

The model preserves canonical float64 coordinates separately from display buffers. Its fixed projection rejects unsupported float32 edits atomically instead of clamping; automatic rebasing and UI editing are not implemented. The renderer uses instanced points, a final selection overlay and an O(n) closed polyline. Explicit open tour permutations retain city IDs and implicit closure; the renderer's row-order fallback remains unchanged.

WebGL2 is the selected Firefox baseline, not a solver accelerator or a universal responsiveness guarantee. [Archived rendering evidence](https://github.com/itwasneo/computational-methods-project/pull/97) records stalls and memory above a former proposed ceiling; it does not require more measurements. WebGPU and the diagnostic/export harnesses are retired.

## Local backend usage and security

From the repository root:

```bash
uv sync --locked --extra interface
uv run --locked --extra interface tsp-hpc serve --workspace /path/to/workspace --port 8000
```

Installed-package users can install `tsp-hpc[interface]`. The service binds only `127.0.0.1`; it can ingest selected result shards but does not serve the frontend or execute solvers. Startup checks the workspace and scan limits before requesting a credential. An occupied port fails explicitly; Ctrl-C cancels ingestion, joins its worker and closes the session/catalog. Help, version and numerical commands do not import web dependencies.

### Session and HTTP boundary

An owner client creates and privately retains a fresh random URL-safe secret, for example with `secrets.token_urlsafe(32)`. Supply the same 43–128-character secret through the hidden terminal prompt, or use `--session-secret-stdin` with a private pipe. Never pass credentials in arguments, URLs, environment variables or shell history. This is developer bootstrap, not a finished browser pairing UI; the [loopback integration test](../tests/integration/test_local_backend.py) demonstrates the in-memory flow.

Send `SessionRequest` (`api_version: 1`, `request_id`, `secret`) to `POST /api/session`. One successful exchange returns a random `session_token`; thereafter send `Authorization: Bearer <token>`. Pairing stops after success or five failed credential attempts; restart for another client. Credentials stay in process memory, are not persisted/logged and have no public discovery endpoint. Raw ASGI tracebacks/access logs are disabled; errors are sanitized.

| Implemented route | Request / response |
| --- | --- |
| `POST /api/session` | `SessionRequest` / `SessionResponse`; authority/body guards apply before credential checking |
| `GET /api/workspace` | `WorkspaceResponse`; optional `X-Request-ID`, otherwise a generated correlation ID |
| `POST /api/artifacts/query` | `ArtifactQuery` / `ArtifactPage`; required `limit` (1–500) and nullable `cursor` |
| `POST /api/artifacts/refresh` | `CatalogRefreshRequest` / `CatalogRefreshResponse`; rebuild the same root's catalog, rotate revision/handles and invalidate its dataset |
| `POST /api/datasets` | `DatasetRequest` / `Dataset`; HTTP 202 acknowledges background ingestion in `loading` state |
| `POST /api/datasets/status` | `DatasetStatusRequest` / `Dataset`; observe `loading`, `ready`, `failed` or `stale` |
| `POST /api/trials/query` | `TrialQuery` / `TrialPage`; filtered, sorted scalar rows from a ready dataset revision |

Use exactly `http://127.0.0.1:<port>` and disable environment-configured proxies in owner clients. Host/origin/session guards reject foreign or duplicate Host headers, foreign/null origins, cross-site/same-site fetch requests and unauthorized clients. Native clients may omit Origin but still need credentials. All query strings are rejected; there is no permissive CORS, WebSocket or OpenAPI/docs endpoint.

JSON bodies are limited to 16 KiB even without Content-Length. Strict Pydantic requests reject coercions, extra/path fields and missing required fields, including nullable ones. Responses have no-store/no-sniff/frame-denial headers. Catalog operations use FastAPI's worker pool and a nonblocking single-operation lock; the initial scan precedes serving requests. Uvicorn concurrency is bounded to 32.

### Workspace authority and limits

The explicitly configured root is resolved once, pinned to an open directory descriptor and fixed for the session. Switching roots requires restarting; no database is required. It must be a trusted owner-controlled POSIX workspace with no-follow directory access.

- Descendants are opened relative to that descriptor. Symlinks, special files, hidden entries/subtrees and non-JSON/JSONL candidates are excluded. No writes, uploads, arbitrary paths or download endpoints are exposed.
- `.json` and `.jsonl` classify only `json` and `result_shard` candidates. Every item is `validation: unvalidated`: neither schema nor scientific content has been checked.
- Handles are opaque and session-bound, not paths or scientific IDs. Display names are untrusted relative labels: escape them, never use them to resolve files. Embedded paths in artifacts/shards confer no authority. Responses must not expose raw host paths/private settings.
- Scans allow at most 10,000 entries and 16 directory levels. Exceeding either fails without publishing a partial catalog. File metadata (device/inode/mode/size/mtime/ctime) and directory changes invalidate the catalog. Refresh rotates handles/revision and HMAC-protected cursors even without file changes.
- The small internal byte reader remains capped at 64 KiB. Dataset ingestion uses separately bounded streaming sources below. Neither exposes a raw-file HTTP endpoint. Both reopen known handles with no-follow/nonblocking flags, verify regular-file identity before/after reads and recheck the catalog. Observed replacements/deletions/races fail closed.

These metadata revisions are not scientific content hashes. Ingestion consumes authorized verified handles, never embedded paths; future artifact loaders must preserve that boundary. Choosing the workspace authorizes its regular files, including owner-created hard links. Hostile same-user processes, malicious filesystem owners/mounts, arbitrary directory-renaming races and metadata forgery between syscalls are outside the threat model; keep untrusted writers out of the root.

### Background ingestion and in-memory index

After pairing, list candidates, then send a `DatasetRequest` to `POST /api/datasets`:

```json
{
  "api_version": 1,
  "request_id": "ingest-1",
  "workspace_id": "<workspace response ID>",
  "artifact_catalog_revision": "<catalog page revision>",
  "result_handles": ["<selected JSONL handle>"],
  "expected_manifest_handle": null
}
```

Supply a selected JSON handle instead of null to check expected/missing executions. Poll `POST /api/datasets/status` with `api_version`, `request_id`, `workspace_id` and the returned `dataset_id`. A ready dataset has a non-null revision; other states never expose a queryable index. There is no browser pairing or Results wiring yet.

- **Storage choice:** one in-memory `Analysis` per session, no database, temporary shard copies or persistent cache. The shared-reader prototype retains scalar rows and byte/digest locators rather than every tour/plan; focused tests compare real runner output with CLI analysis, exercise lazy reads and enforce bounds. This supports a small local first slice, not a large-campaign performance claim. A new accepted selection evicts the previous dataset ID; restart requires re-ingestion.
- **Execution:** one dedicated thread, no ingestion queue. Expensive parsing, validation, manifest expansion and table construction run off the request path. A second submission while the worker is active receives `ingestion_busy`, including while cancelled work is unwinding. Publication is atomic. Cancellation is cooperative between bounded records/phases; shutdown waits for the worker, not a hard wall-clock deadline or isolated subprocess kill.
- **Initial hard input/work limits:** 64 distinct shards; 64 MiB total selected bytes including the optional manifest; 64 KiB manifest; 4 MiB per JSONL record; 10,000 observed trials. Expected-manifest expansion is checked before materialization: at most 10,000 plans, 100,000 plan × logical-search items and a conservative 16 MiB search/execution storage estimate (including allowance for fixed metadata and repetition values). Oversized selections fail before queuing; limits reached during ingestion produce `failed` with `dataset_limit_exceeded`, never a silently truncated dataset.
- **Retained data limits:** serialized compact scalar rows are capped at 16 MiB before table construction; the completed pandas table's deep byte estimate is capped at 32 MiB. These do not promise a process-RSS ceiling: parsing, Python objects, diagnostics, table construction and libraries require additional memory. Raw source evidence is unchanged. Tours/plans are read lazily through the same authorized source boundary, not retained in the table.
- **Scientific policy:** `read_shards` and the authorized `read_sources` adapter share one validator. Malformed complete records, duplicate campaigns/attempts and contradictory summaries fail the import. Valid incomplete prefixes, failed trials, repeated execution exclusions and expected/missing execution diagnostics retain the existing analysis policy. `ready` means the shared reader accepted the dataset, potentially with exclusions—not that producer provenance or objective costs have been authenticated against original instance artifacts. That verification remains a later slice.
- **Diagnostics:** status exposes sanitized codes, optional opaque artifact handles and messages. At most 100 summaries are returned, with explicit `diagnostics_total` and `diagnostics_truncated`; the authoritative full diagnostics remain on the internal analysis. Diagnostic pagination/inspection is not yet exposed. Failures never echo raw exceptions, input contents or private paths.
- **Invalidation:** revisions bind content SHA-256 digests, expected-manifest bytes, analysis/schema source code, relevant dependency versions, ingestion configuration and authorization/catalog context. This identity is cache invalidation, not verified build provenance. Status polling and internal query/lazy-read boundaries recheck metadata/revision; no filesystem watcher is required. A changed/deleted/replaced source, changed manifest or catalog refresh makes the index stale and prevents late publication. Catalog-wide checks conservatively invalidate for unrelated catalog changes too. Code changes require a backend restart. Interrupted indexes are discarded; no partially built index survives publication or restart.

### Paged trial queries

Send `POST /api/trials/query` after ingestion reaches `ready`:

```json
{
  "api_version": 1,
  "request_id": "trials-1",
  "workspace_id": "<workspace ID>",
  "dataset_id": "<dataset ID>",
  "revision": "<ready dataset revision>",
  "filters": [],
  "sort": null,
  "limit": 100,
  "cursor": null
}
```

All fields are required; supply the conventional initial limit of 100 (maximum 500). Filters use `{"field":"outcome","values":["completed"]}`; sort uses `{"field":"best_cost","direction":"ascending"}` or `descending`. The filtering/sorting rules below are implemented for trial queries. Numeric cost/time filters accept finite numbers; city counts require safe integers of at least two. Booleans and numeric strings are rejected.

The response echoes context and contains `query_key`, `items` and `next_cursor`. Continue with the returned cursor and the same dataset/revision, normalized filters, sort and page size; request IDs may change. Cursors are session-signed. Empty results return `items: []` and `next_cursor: null`. Changed sources/revisions produce `stale_revision`, not a partial page; loading/failed datasets produce `dataset_not_ready`.

Queries only project the shared analysis table, preserving failure/completeness/duplicate/usable flags. They neither recompute scientific statistics nor load tours/plans. Filtering and sorting run in the HTTP worker pool over the ingestion-bounded table, with no query cache or additional work queue. Facets, aggregates, comparisons, details, geometry and frontend API wiring remain separate work; this endpoint does not change the fixture-only browser preview.

## Read-only Results contract

**Dataset lifecycle and trial queries are implemented; facet/detail/geometry endpoints below remain planned.** Python [DTO types](../src/tsp_hpc/interface/contracts.py), mirrored [TypeScript types](../frontend/src/contracts.ts) and the [shared wire fixture](../examples/interface-contracts/read-only-v1.json) describe the boundary. Keep them and this contract consistent. Static types/fixture tests do not replace runtime validation; runtime request validators cover the session/catalog/dataset/trial-query subset above. Jobs, editor/run snapshots, execution capabilities and group/chart/comparison responses still need contracts.

### Operations, limits and state

| Operation | Request / response | Meaning |
| --- | --- | --- |
| Select sources | `DatasetRequest` / `Dataset` | Distinct shard handles and optional expected-manifest handle from one catalog revision; reject wrong kinds/stale handles; at most 64 shards |
| Observe ingestion | `DatasetStatusRequest` / `Dataset` | `loading`, `ready`, `failed`, `stale`; only `ready` has a queryable non-null revision |
| Query trials | `TrialQuery` / `TrialPage` | Scalar rows without tours, coordinates or plans |
| List column values | `FacetQuery` / `FacetPage` | Distinct typed values from the whole selected dataset |
| Expand attempt | `DetailsRequest` / `TrialDetails` | Projected IDs, seeds, provenance and artifact availability, not the raw runner record; optional instance handle for disambiguation |
| Inspect geometry | `GeometryRequest` / `Geometry` | Verified snapshot coordinates and optional recorded tour |

All DTO fields are required, including nullable ones. Reject unsupported `api_version` (currently `1`), unknown fields, unsupported query fields and invalid scalar types: booleans are not integers; NaN, infinities and fractional/unsafe integer values are invalid. Dataset context includes `workspace_id`, `dataset_id`, `revision`, `api_version` and `request_id`. Echo the request ID; generate a correlation ID when an invalid request has none. `query_key` identifies normalized filters/sort/scope independently of request ID/cursor. Apply responses only if workspace, revision, query and selected attempt/snapshot still match; cancelling a request alone is insufficient.

Initial design limits are 100 items by default, at most 500 per page and 500 selected values per column. Combined decoded geometry/tour bytes are capped at 16 MiB; account for base64 expansion in encoded-response limits. Enforce bounds before large allocations/decoding. Bound source sizes and ingestion queues separately: the existing analysis reader retains O(trial count) summaries. These are safety limits, not performance guarantees.

### Trial identity, filtering and sorting

A row is an **attempt**, keyed by `attempt_id` and labelled **Attempt ID**, not a configuration/campaign. `execution_id` may be null on failure; `requested_execution_id` is not. Repeated executions remain distinct attempts subject to shared duplicate-observation exclusions. Campaign/execution/search IDs belong in details; instance resolution/filtering uses `instance_id`, never display labels.

Queryable fields are `attempt_id`, `instance_id`, `implementation`, `n_cities`, `algorithm`, `outcome`, `best_cost`, `search_time_s`:

- Match exact typed values: OR within a column, AND across columns; absent/empty selections are unrestricted. Reject duplicate filter entries, normalize duplicate values, allow null only for nullable fields and treat zero as a value. Strings are case-sensitive; no text search.
- `outcome` is `completed`/`failed`, not job state or comparison eligibility. Preserve `campaign_complete`, `duplicate_execution` and `usable` from analysis. Unknown provenance can still block comparisons for usable rows.
- Facets ignore active filters and page through the whole dataset's distinct values. Do not infer options from loaded trial pages or discard off-page selections. No distinct-value counts are required; use the column's typed ascending/null-last ordering.
- Sort one column numerically or by Unicode code-point string order, nulls last in either direction. Ties and default ordering use ascending `attempt_id`. Global Reset clears filters/sort; Reset column clears only its filter and active sort.
- Cursors are opaque, session-bound and tied to catalog/dataset revision, normalized filters, sort and page size. Reject tampering/mismatches; exhaustion yields `next_cursor: null`. Empty pages are valid; never silently restart or reuse a cursor after source changes.
- View filters must not recompute completeness, remove exclusions or select an implicit baseline. Comparisons use shared analysis on the proper evidence scope, not displayed rows.

### Scientific authority and snapshots

Use `read_shards` / its authorized-source adapter `read_sources`, `Analysis.trial`, `summary_table`, `paired_comparisons` and `reference_gaps`, not browser equivalents. Preserve malformed-record errors, incomplete prefixes and failed/missing/duplicate observations; an expected manifest is required to identify wholly missing executions. Run ingestion/expensive analysis off the request path with bounded concurrency and publish a ready dataset atomically for its revision.

Dataset revisions bind source content, expected manifest/configuration, relevant analysis implementation/schema and authorization session—not just filenames/package versions. Source changes/deletions invalidate pages, facets, details and snapshots with `stale_revision`; require reload. Keep only bounded page/detail/chart caches. Tables use server-side filters/sorts and bounded pages with virtualized visible rows. Charts bound series and points: request narrower filters or explicit aggregation rather than silently sampling/truncating evidence or deriving ratios from display aggregates. Provide tabular alternatives and retain rejected comparisons as diagnostics, not zeros.

Resolve instances only through authorized artifacts and `load_instance`, matching the complete descriptor: instance ID, coordinate checksum, city count, convention and generation seed, plus validated artifact provenance/content identity. `snapshot_id` binds **dataset revision + attempt + descriptor + validated artifact identity/provenance + recorded result/tour identity**. A supplied handle is a candidate, not proof. Ambiguous provenance/bindings require explicit selection, never filename-order choice. Unavailable/mismatched/stale bindings yield no usable snapshot/geometry; handles/snapshots for resolved artifacts are non-null only when available.

Before publishing a tour, use the shared solution validator against its original instance. A failed trial may have inspectable geometry, but `tour` is null. An artifact match cannot repair unknown/dirty producer provenance. Fetch full supported geometry only when opened, retaining canonical coordinates; unsupported sizes need explicit limitations and export where available, not silent reduction or export in place of supported inspection/editing. Never synthesize intermediate tours, progress percentages or histories from final results. Job/UI/network timings must not become scientific trial timings.

### Exact geometry and seed transport

Seeds (`solver_seed`, `trial_seed`, `instance_seed`) are canonical unsigned decimal strings in `[0, 2**64)`: no signs, leading zeros except `"0"`, exponent notation or JavaScript Number conversion. Nullable seeds remain null. Other integer/count metadata must fit JavaScript's exact safe-integer range; reject rather than round.

| Payload | Encoding and invariant |
| --- | --- |
| `Geometry.coordinates` | Standard padded base64, `f64-le-base64`: C-order little-endian IEEE-754 float64 pairs; exactly `16 * n_cities` decoded bytes for shape `(n_cities, 2)` |
| Non-null `tour` | Standard padded base64, `u32-le-base64`: little-endian uint32 city IDs; exactly `4 * n_cities` decoded bytes; an open permutation visiting every ID once |

Decode using explicit little-endian `DataView` access or an endian-checked equivalent, without assuming host byte order/alignment. Reject non-finite coordinates; preserve city order, duplicates and signed-zero bits. Byte round trips must preserve the [coordinate checksum framing](manifests.md#encoding-contract); neither float32 display buffers nor JSON stringify/parse cycles replace canonical coordinates. Check tour length, range and permutation; do not append the closure city. The uint32 and byte limits restrict supported transport sizes, not scientific tour semantics. Browser checks of encoding/shape/bounds do not replace backend objective validation.

Nullable observations use JSON null, never NaN/Infinity, sentinel strings or substituted zeros. `best_cost: 0` and `search_time_s: 0` are valid.

### Errors and compatibility

`ErrorResponse` contains `api_version`, `request_id`, stable `code`, sanitized `message` and `retryable`. The mapping includes implemented catalog errors and planned Results errors:

| HTTP status | Codes |
| --- | --- |
| 400 | `invalid_request` (also 404/405/415 for unavailable endpoints/methods/content types) |
| 403 | `unauthorized` |
| 404 | `artifact_not_found`, `dataset_not_found` |
| 409 | `catalog_busy`, `ingestion_busy`, `dataset_not_ready`, `stale_revision`, `cursor_invalid`, `snapshot_mismatch` |
| 413 | `catalog_limit_exceeded`, `dataset_limit_exceeded`, `artifact_too_large`, `request_too_large`, `geometry_too_large` |
| 422 / 503 / 500 | `invalid_artifact` / `workspace_unavailable` / `internal_error` |

Retryable means recovery such as reload is possible, not permission for unbounded retries. Messages must not expose raw exceptions or private paths.

The shared wire fixture is compatibility evidence, not runner output. Python and TypeScript checks cover DTO shape, large seeds, signed zero, duplicates, checksum/byte preservation, tour order, null/zero and missing/unknown provenance. Backend tests separately exercise implemented HTTP/file boundaries. Neither establishes a working real-data viewer or authenticated instance/tour bindings. Ingestion lifecycle tests separately exercise real runner output, shared-validator equivalence, source invalidation and bounded publication.
