# Frontend architecture

The local application is a Svelte 5 SPA served through the guarded Vite development
boundary. Python owns scientific planning, execution, validation and analysis. The browser
owns interaction state and presentation, not replacement scientific calculations.
See [operation and authority](interface.md) and [development checks](development.md).

## Ownership and lifetimes

- **Application lifetime:** `App.svelte` creates one `WorkspaceSession` and one navigation
  service, provided through typed Svelte context in `application.ts`. The session owns
  pairing, workspace identity, the artifact catalog and catalog refreshes. Features do
  not clear shared credentials when they unmount. Explicit disconnect, correlated
  authorization expiry or application disposal clears the in-memory grant and aborts
  obsolete browser work. Forgetting a token is not server-side job cancellation.
- **Catalog lifetime:** a refresh immediately invalidates old artifact handles, including
  when its acknowledgement is uncertain. All consumers receive the same replacement
  catalog/revision. Saved-result selections and their filter drafts live within an explicit
  session/catalog key; they cannot survive as invisible selections after reconnect.
- **Setup lifetime:** instance snapshots and editable configuration are distinct from a
  reviewed plan. Edits invalidate review. A fresh-rerun action returns an explicit outcome;
  the form is restored only from that successful action's returned configuration. Original
  logical/repetition IDs are retained while their associated starts/count remain unchanged.
  The exact reviewed plan remains available for inspection.
- **Job/result lifetime:** selected job, accepted configuration, copied instance, query page
  and verified geometry carry their own identity/revision. Analysis controls are keyed to
  the selected job, not the presence of a successful page. Failed page requests do not
  unmount the form or reset its draft. An applied query and the most recently requested
  query are distinct; pagination uses the applied query and opaque server cursors.
- **Renderer lifetime:** geometry identity is narrowed before decoding. Unrelated view
  snapshots do not create a new inspection object or reconstruct the renderer. Actual
  artifact changes, explicit reinspection and GPU retry can recreate it. Theme, selection, camera and tour
  controls use the renderer adapter, whose teardown owns listeners, observers, frames and
  GPU resources. Canonical float64 coordinates remain separate from display buffers.

Server DTOs are replacement-based snapshots, bridged into components with `$state.raw`.
Editable controls use local `$state`; computed views use `$derived`. Effects synchronize
external resources such as timers, document visibility and drawing. They are not a
counter-based command bus or a mechanism for reconstructing query state from page rows.

## Operations and failure boundaries

`ExperimentController` tracks independent operation states for session initialization,
catalog, instance, review, submission, job reads, job controls, selection, analysis,
inspection and export. An operation returns a discriminated success/error outcome. Repeated
mutations in the same scope are not silently launched in parallel. Superseded reads and
selection-bound requests are cancelled; commits check cancellation before publishing.

A status read must not swallow stop intent. Job controls cancel an obsolete status read
and send their own explicit command. This does **not** promise instantaneous interruption
or bypass backend serialization, revision checks, worker ownership or exit verification.

Errors are scoped to the failed resource. Invalid instance input does not erase a verified
result or disable job polling. A stale shared result binding, however, invalidates every
analysis/geometry publication from that source. Authentication expiry resets all consumers.
Job-status errors pause automatic polling until an explicit successful refresh. Polling
exists only for known progressing work while both Experiments and the document are visible;
there is no idle/terminal/held polling or saved-dataset watcher.

The transport has a 30-second request deadline and a 4 MiB streaming response ceiling.
Aborting a browser request is not cancellation of accepted server work. In particular,
submission timeouts retain the original preflight/submission key. Only definitive server
pre-admission rejections discard it automatically; no operation automatically resubmits work.
Structured errors retain code, HTTP status, request correlation and retryability metadata.

## Contracts and exact data

`tools/interface_schema.py` produces static TypeScript types and runtime wire schemas from
Python's DTOs. `contracts.ts` re-exports these types; `endpoints.ts` connects each execution
route to its request and response definitions. Callers cannot choose an unrelated claimed
response type independently of a schema name. Semantic checks still verify job/result,
snapshot, campaign, attempt, geometry and paging bindings beyond structural validation.

Canonical instance exports share bounded transport plumbing but return the original bytes;
they never pass through JSON-number parsing/stringifying. Geometry uses the existing binary
float64/uint32 contract. Display metadata in `columns.ts` assigns duration units only to
known timing fields/statistics, never to timing sample counts. IDs, costs, seeds, exact
filters and downloaded scientific values are not replaced by their abbreviated display.

Analysis charts use shared server statistics, label their page scope and show recorded
observations without interpolation. Scaling points for display is not a new scientific
aggregation. Missing ratios and exclusions remain visible.

## Navigation and persistence

Hash links identify the view and optionally an output root, job, attempt and explicitly
requested analysis query. Query/inspection intent is recorded before loading; completion
does not replay navigation. The successfully applied query remains attached to its page.
They support Back/Forward and reload without adding a routing framework. The linked root
must match the freshly paired authority before a job is restored. Invalid query shapes are
not request bodies; valid queries still undergo server validation. Reference handles remain
catalog-scoped and may require reselection after refresh.

Credentials never enter URLs or storage. Tab session storage has two separate purposes:

1. Non-secret uncertain-admission metadata retains its original key/root/preflight across
   reconnect/reload. Unreadable metadata remains an explicit blocked recovery state. The
   owner must inspect jobs before confirming removal; clearing it never deletes accepted
   work or makes a new submission safe by itself.
2. Bounded, validated configuration drafts are namespaced by output root. They may reference
   the last configured immutable input, which is restored only by an explicit snapshot
   request and identity check. They do not contain a reviewed plan, grant or permission to
   execute. Invalid/unavailable draft storage is only a lost convenience, not lost authority.

Async completions do not navigate away from a view chosen while the request was pending.
An acknowledged admission can update the remembered Experiments link without changing the
active URL. Returning to a pending query/inspection does not replay or cancel that read.
No URL, draft or reconnect operation selects an implicit latest-success result or submits work.

## Presentation and accessibility

Setup, configuration/review, jobs, analysis and inspection have separate components. Shared
presentation includes a table scrolling region, operation status panels, semantic column
metadata, downloads and focus handling. Table headers remain aligned while both axes scroll;
regions have names and explicit keyboard access. A busy table can reserve its previous height
without presenting unvalidated replacement data.

Navigation uses links/current-page semantics, one main landmark and a skip link. Deliberate
inspection moves focus to the result heading when the originating view is still active;
background completion does not steal focus. Motion honors reduced-motion preference. The
viewer has keyboard pan/zoom, numeric and row-based city selection, textual selected-city
coordinates, a non-color-only selection marker, and an accessible coordinate/permutation view.
Controls remain visible rather than fading to low contrast. Native popovers track scrolling
and resizing. Theme preference can explicitly follow OS changes.

Accessible action names include both visible compact IDs and full identity. Full-precision
values and extra columns are independent choices. Opaque text/control/chart/viewer palettes
have contrast regression tests; axe and keyboard/browser tests cover interactions. These checks
do not establish complete WCAG conformance or replace manual screen-reader/zoom assessment.

## Regression layers

- Node tests cover protocol typing/validation, scoped controllers, cancellation, deadlines,
  uncertain submissions, draft/route validation, display metadata, palettes and rendering.
- Fast Playwright tests mount the real Svelte app on an ephemeral Vite port and intercept
  API calls with contract-derived fixtures. They exercise success, failure, reconnect,
  navigation, selection, layout, focus and accessibility in Chromium, Firefox and WebKit.
  GPU lifecycle assertions use Chromium's software WebGL; the other engines still exercise
  the surrounding interaction and accessible fallback paths.
- Separate Chromium acceptance tests retain a real guarded proxy, Python service and owned
  native worker. They establish functional integration, not performance or optimality.
- Svelte/TypeScript, ESLint, Prettier, production build and Python schema-drift checks are CI
  gates. Generated files are deterministic outputs, not formatter-maintained source.

Supported browser targets are the current evergreen engines exercised by the pinned
Playwright toolchain. The app uses native popovers, modern abort signals, ResizeObserver and
WebGL2 where available; coordinates/results remain accessible if WebGL2 cannot initialize.
