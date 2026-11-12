# Optional local interface

The interface is a read-only companion to [run → analyze](usage.md), not a second scientific engine. Python owns validation and statistics. **Browser benchmarks, profiling campaigns and numerical browser-performance gates remain withdrawn**, not certified as passed.

## Current boundary and selected stack

| Part | Implemented | Not implemented |
| --- | --- | --- |
| Svelte 5 / TypeScript / Vite | Pairing, workspace selection, ingestion status, paged real trial rows, server-side filters/sorting | Facet lists, charts, editor and job controls |
| FastAPI / Pydantic / Uvicorn | Session, authorized catalog, bounded ingestion and scalar trial queries | Instance/tour authentication, details/geometry endpoints, solver jobs |
| WebGL2 | Separate synthetic viewer preview | Real-result artifact binding |
| Delivery | Guarded same-origin development proxy | Bundled frontend serving from the installed CLI |

No SSR/SvelteKit, cloud accounts, telemetry, CDNs, arbitrary shell endpoints or cluster operations. ECharts is selected for future presentational charts but is not installed. Numerical-only installations remain independent of interface dependencies.

## Browser local results

Use Python 3.12, Node 24 and the pinned pnpm version; see [development](development.md). From the repository root, start the backend in one terminal:

```bash
uv sync --locked --extra interface
uv run --locked --extra interface tsp-hpc serve --workspace results --port 8000
```

Choose a small trusted workspace containing your JSONL files, not a repository with large dependency directories. At the hidden prompt, supply a fresh random **43–128-character URL-safe secret** (for example, generated privately with a password manager). Keep it for browser pairing; never put it in arguments, URLs, environment variables, shell history or logs.

In another terminal:

```bash
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend run dev
```

Open **http://127.0.0.1:5173**, enter the same secret, select `nn.jsonl` and click **Load**. Leave the expected manifest unset unless an actual manifest JSON is present; instance/reference JSON is not a manifest. The owner's square example produces four real trial rows. This reads the existing shard without changing it.

- The secret input clears on submission; the session token stays in memory, never browser storage. Reload/disconnect requires restarting the backend before pairing again. Switching views retains the session.
- Trial pages contain up to 100 attempts. Previous/Next, sorting and exact-value filters operate on the entire dataset **on the server**. Enter one value per line; blank filters are unrestricted. Nullable columns offer **Include missing values**. There are no misleading current-page facet lists.
- Attempt expansion shows completeness, duplicate and usable flags. `usable` does not certify comparison eligibility or producer provenance. Real geometry is explicitly unavailable; synthetic geometry is never substituted.
- Loading, empty, failed and stale states are explicit. Selection/refresh changes discard old pages; request/context checks reject late replies. Ready datasets are rechecked every five seconds and on queries, not through an instantaneous filesystem watcher.

The development ports are fixed at 5173/8000. An occupied frontend port fails rather than changing the trusted origin. Use the exact address above, not `localhost`. Stop each server with Ctrl-C. `vite preview` is static-only: it has no API proxy.

## Results and viewer preview

**Viewer preview** needs no backend. It contains only synthetic 128-city geometry and a synthetic permutation, not a second Results table or solver evidence.

Drag/arrow keys pan; wheel/plus/minus zoom; Fit/Home resets; repeated coincident picks cycle city IDs. Fullscreen exits with Escape. The shared icon toggle changes themes without replacing geometry/camera; theme preference may use local storage, credentials may not. Context loss exposes Retry; unmounting releases resources. Editing and complete non-canvas accessibility remain unimplemented.

The [rendering primitives](../frontend/src/rendering/) retain canonical float64 coordinates separately from float32 display buffers. They use instanced points, a final selection overlay, an O(n) closed polyline and bounded picking. Unsupported float32 edits fail atomically; automatic rebasing is not implemented. WebGL2 is a display choice, not a solver accelerator. [Archived rendering evidence](https://github.com/itwasneo/computational-methods-project/pull/97) retains its stalls/memory limitations without requiring further measurements.

## Local backend usage and security

Installed users can install `tsp-hpc[interface]`. `tsp-hpc serve` binds only `127.0.0.1` and serves the API, not frontend assets. Startup validates the workspace/scan limits before requesting a secret; an occupied port fails explicitly. Ctrl-C cancels ingestion cooperatively, joins its worker and closes the catalog. Help/version and numerical commands do not import web dependencies.

### Session and HTTP boundary

Native clients supply the startup secret through the hidden prompt or `--session-secret-stdin` with a private pipe, then exchange it via `POST /api/session`. One successful exchange returns a Bearer token; five failed attempts exhaust pairing. Restart for a new client. Credentials are neither persisted nor logged; raw server tracebacks/access logs are disabled.

Backend requests must use exactly `http://127.0.0.1:<port>`; native clients should disable environment-configured proxies. Strict Host/Origin/Fetch Metadata guards reject foreign/null origins, cross-site/same-site requests and duplicate authority headers. Native clients may omit Origin but still need credentials. Query strings, WebSockets, permissive CORS and public API-doc endpoints are unavailable.

The Vite proxy validates the **frontend** authority before rewriting Host/Origin for the fixed backend. Foreign requests are not converted into trusted requests. Development CORS is disabled; Vite filesystem access stays within the frontend.

Request bodies are limited to **16 KiB**, including chunked bodies; Uvicorn concurrency is capped at **32**. Strict request models reject extra fields and scalar coercions. Responses carry no-store/no-sniff/frame-denial headers and sanitized errors. Catalog operations run in the HTTP worker pool under a nonblocking operation lock.

### Workspace authority and limits

The owner selects a trusted POSIX root at startup. Its open directory descriptor anchors no-follow descendant reads; changing roots requires restart.

- Only regular `.json`/`.jsonl` candidates are listed; hidden entries, symlinks, special files and unsupported suffixes are excluded. Candidate names are escaped display labels, not paths or authority. Embedded artifact paths confer no access.
- Handles are opaque and session-bound. No uploads, writes, arbitrary paths, downloads or raw-file endpoints exist. Ingestion reopens known handles with no-follow/nonblocking flags and verifies identity before/after reads.
- Catalog scans allow **10,000 entries / 16 directory levels**; exceeding either publishes no partial catalog. Device/inode/mode/size/mtime/ctime and directory changes invalidate metadata. Refresh rotates revision, handles and signed cursors even without file changes.

Catalog revisions are not content validation. Choosing a root authorizes its regular files, including owner-created hard links. Hostile same-user writers, filesystem owners/mounts, arbitrary directory-renaming races and metadata forgery between syscalls are outside the threat model; keep untrusted writers out of the root.

### Background ingestion and in-memory index

One session holds **one in-memory dataset and one worker, with no ingestion queue, database, persistent cache or shard copies**. A new accepted selection evicts the old dataset. Concurrent submission returns `ingestion_busy`, including while cancelled work unwinds.

| Bound | Limit |
| --- | --- |
| Selected sources | 64 distinct shards; 64 MiB total including optional manifest |
| Manifest / JSONL record | 64 KiB / 4 MiB |
| Observed trials | 10,000 |
| Expected-manifest expansion, before materialization | 10,000 plans; 100,000 plan × logical-search items; conservative 16 MiB search/execution storage estimate |
| Retained summaries/table | 16 MiB serialized scalar rows before construction; 32 MiB completed pandas deep-byte estimate |
| Public diagnostics | 100 summaries, with explicit total/truncation fields |

Input bounds fail before queuing; ingestion bounds produce `failed`/`dataset_limit_exceeded`, never silently truncated data. Estimates are **not RSS or wall-clock guarantees**: decoding, diagnostics, libraries and temporary allocations add memory. Cancellation occurs between bounded records/phases; shutdown waits for the worker.

Shared `read_sources`/`read_shards` validation owns the [malformed, incomplete, failed, duplicate and missing-execution policy](usage.md#validation-and-comparison-rules). Tables retain scalar metadata and lazy source locators, not every tour/plan. An expected manifest is necessary to identify wholly missing executions. `ready` means structural acceptance, potentially with exclusions—not independently authenticated objective costs or producer provenance.

Publication is atomic. Revisions bind content SHA-256, manifest bytes, analysis/schema code, relevant dependency versions, limits and session/catalog context; this is cache invalidation, not verified build provenance. Status/query/lazy-read boundaries recheck sources. Observed changes, refresh and shutdown prevent obsolete publication; unrelated catalog changes conservatively invalidate too. Code changes require restart. Public diagnostics never echo raw exceptions, contents or private paths; full analysis diagnostics remain internal.

## Read-only Results contract

[Python DTOs](../src/tsp_hpc/interface/contracts.py), [TypeScript DTOs](../frontend/src/contracts.ts) and the [wire fixture](../examples/interface-contracts/read-only-v1.json) describe the boundary. [Request models](../src/tsp_hpc/interface/requests.py) and the [browser client](../frontend/src/results/client.ts) validate implemented traffic; static types alone do not.

All fields are required, including nullable ones. Use `api_version: 1` and `request_id`; workspace operations include `workspace_id`, and trial queries add `dataset_id`/`revision`. Echo request IDs or generate a correlation ID for invalid requests without one. Booleans are not numbers; reject non-finite numbers and fractional/unsafe integer metadata. Nullable observations use JSON null, never sentinel strings or substituted zeros.

| Implemented route | Request / response | Specific input |
| --- | --- | --- |
| `POST /api/session` | `SessionRequest` / `SessionResponse` | Startup secret; no Bearer token yet |
| `GET /api/workspace` | `WorkspaceResponse` | Optional `X-Request-ID` header |
| `POST /api/artifacts/query` | `ArtifactQuery` / `ArtifactPage` | `limit`, nullable `cursor` |
| `POST /api/artifacts/refresh` | `CatalogRefreshRequest` / `CatalogRefreshResponse` | Rebuild same root, invalidate dataset |
| `POST /api/datasets` | `DatasetRequest` / `Dataset` | `artifact_catalog_revision`, `result_handles`, nullable `expected_manifest_handle`; HTTP 202 |
| `POST /api/datasets/status` | `DatasetStatusRequest` / `Dataset` | `dataset_id`; states `loading`, `ready`, `failed`, `stale` |
| `POST /api/trials/query` | `TrialQuery` / `TrialPage` | Ready revision, filters, sort, limit and cursor |

Only `ready` has a queryable non-null revision. Page sizes are conventionally 100, maximum 500; filters allow at most 500 values per column.

### Paged trial queries

Example body for `POST /api/trials/query`:

```json
{
  "api_version": 1, "request_id": "trials-1", "workspace_id": "<workspace>",
  "dataset_id": "<dataset>", "revision": "<ready revision>",
  "filters": [{"field": "outcome", "values": ["completed"]}],
  "sort": {"field": "best_cost", "direction": "ascending"},
  "limit": 100, "cursor": null
}
```

Responses echo context and return `query_key`, scalar `items` and `next_cursor`. Continue with the same revision, normalized filters/sort and page size; request IDs may change. Empty pages are valid; exhaustion returns null. Cursors are session-signed and bound to that context; reject tampering/mismatches rather than restarting pagination. Queries run over the ingestion-bounded table in the HTTP worker pool, without scientific recomputation, raw-tour loading or query caches.

### Trial identity, filtering and sorting

Rows are **attempts**, keyed and labelled by `attempt_id`, not campaign/configuration IDs. Repeated executions remain distinct attempts with shared exclusions. Instance selection uses `instance_id`, not labels. Queryable fields:

`attempt_id`, `instance_id`, `implementation`, `n_cities`, `algorithm`, `outcome`, `best_cost`, `search_time_s`.

- Exact typed equality: OR within a column, AND across columns; absent/empty filters are unrestricted. Reject duplicate filter entries, normalize duplicate values, and permit null only for implementation/cost/time. Strings are case-sensitive; zero is a value. Cost/time filters accept finite numbers; city counts require safe integers ≥2.
- Sort one column numerically or by Unicode code points, nulls last in either direction. Ties/default ordering use ascending attempt IDs. Global Reset clears filters/sort; Reset column clears only its filter and active sort.
- `outcome` is `completed`/`failed`, not job state or eligibility. Preserve `campaign_complete`, `duplicate_execution` and `usable`; filters cannot repair exclusions or choose baselines.
- Apply responses only while request/workspace/revision/query/selection still match; cancellation alone is insufficient. Changed sources produce `stale_revision`; loading/failed datasets produce `dataset_not_ready`.

### Scientific authority and snapshots — planned

**Facets, projected details, geometry, aggregate/comparison charts and jobs remain unimplemented.** Their existing design requirements are:

- Facets page through whole-dataset distinct values independently of active filters, with typed ascending/null-last ordering. Do not derive options from loaded pages or discard off-page selections; counts are not required.
- Details expose projected IDs, exact seeds, provenance and availability—not raw runner records. `requested_execution_id` is non-null; effective `execution_id` can be null on failure.
- Use shared `Analysis.trial`, `summary_table`, `paired_comparisons` and `reference_gaps`. Keep caches bounded and revision-bound. Use server-side filtering/sorting and bounded pages with virtualized visible rows. Bound chart points; request narrower scope or explicit aggregation instead of silently sampling evidence. Provide tables and preserve rejected comparisons as diagnostics, not zeros or ratios of display aggregates.
- Resolve only authorized artifacts through `load_instance`, matching instance ID, checksum, city count, convention, generation seed and validated provenance/content identity. A handle is a candidate, not proof. Ambiguous bindings require explicit selection, not filename order.
- Bind `snapshot_id` to dataset revision + attempt + descriptor + validated artifact provenance/content + recorded result/tour identity. Missing/mismatched/stale bindings produce no usable snapshot/geometry; resolved handles/snapshots are non-null only when available.
- Validate a tour against its original instance before publication. Failed trials may expose geometry but no tour. Artifact matches cannot repair unknown/dirty producer provenance. Fetch full supported geometry on demand; unsupported sizes require explicit limits/export, not silent reduction. Do not fabricate histories, progress or intermediate tours; UI/network/job timing is not scientific trial timing.

Editing/undo and run controls require separate immutable submission, capabilities and lifecycle contracts. They must reuse shared Python science, reject unsupported execution and never perform cluster operations.

### Exact geometry and seed transport — planned

Seeds are canonical unsigned decimal strings in `[0, 2**64)`: no signs, leading zeros except `"0"`, exponents or JavaScript Number conversion. Nullable seeds stay null; other integer/count metadata must be exactly safe.

| Payload | Encoding |
| --- | --- |
| Coordinates | Standard padded base64, `f64-le-base64`; C-order little-endian float64 pairs; exactly `16 * n_cities` decoded bytes |
| Non-null tour | Standard padded base64, `u32-le-base64`; exactly `4 * n_cities` bytes; open permutation with implicit closure |

Cap combined decoded geometry/tour at 16 MiB before allocation; account for base64 expansion. Decode with explicit little-endian `DataView` or an endian-checked equivalent. Preserve city order, duplicate points and signed zero; reject non-finite coordinates and invalid tour length/range/permutation. Byte round trips must preserve [checksum framing](manifests.md#encoding-contract). Float32 displays/JSON cycles do not replace canonical coordinates, and browser checks do not replace objective validation. Transport bounds do not redefine scientific tours.

### Errors and compatibility

`ErrorResponse` carries version/request ID, stable `code`, sanitized `message` and `retryable`. Retryable permits recovery such as reload, not unbounded retries.

| HTTP status | Codes |
| --- | --- |
| 400 | `invalid_request` (also 404/405/415 for unavailable endpoints/methods/content types) |
| 403 / 404 | `unauthorized` / `artifact_not_found`, `dataset_not_found` |
| 409 | `catalog_busy`, `ingestion_busy`, `dataset_not_ready`, `stale_revision`, `cursor_invalid` |
| 413 | `catalog_limit_exceeded`, `dataset_limit_exceeded`, `request_too_large` |
| 422 / 503 / 500 | `invalid_artifact` / `workspace_unavailable` / `internal_error` |

`snapshot_mismatch` (409), `artifact_too_large` and `geometry_too_large` (413) remain reserved for future artifact/geometry operations. Wire fixtures test compatibility, not solver evidence or authenticated real-data geometry. Functional client/proxy tests and real runner ingestion checks remain separate from rendering measurements.
