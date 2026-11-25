# Exact references and fixed-work multicore NN

Use exhaustive references to check tiny instances and native threads to parallelize NN starts within a trial.

## Tiny exact reference

`problem.exact.exact_reference(instance)` exhaustively enumerates tours for **2–9
cities**. The hard size guard precedes enumeration. City zero is fixed first;
every orientation is evaluated in lexical order using the independent coordinate
objective, not an NN kernel, prepared matrix or maintained fitness. Reverse tours
are retained because stored-order float64 sums can differ in their last bits.
Equal computed costs keep the first tour. Numerical failures propagate instead of
silently omitting candidates.

The result is an immutable `ExactReference` containing a tuple tour, cost,
instance identity, coordinate checksum, objective, enumeration count and versioned
method name. It supports both existing objective conventions, including rounded
`tsplib_euc_2d`; it is not a TSPLIB importer. “Exact” denotes exhaustive combinatorial
search, not arbitrary-precision certification of Euclidean distances. Rotations
may change float64 accumulation in the last bits; unrounded comparisons use the
existing objective validation tolerance. A heuristic is not required to find the optimum.

Example compatible reference document for shared reference-gap analysis:

```python
import json
from pathlib import Path
from tsp_hpc.problem.instances import load_instance
from tsp_hpc.problem.exact import exact_reference

instance = load_instance('examples/instances/square.json').instance
reference = exact_reference(instance)
assert reference.cost == 4.0
Path('results').mkdir(exist_ok=True)
with Path('results/square-reference.json').open('x') as stream:
    json.dump({'schema_version': 1, 'references': [reference.reference()]}, stream,
              indent=2, allow_nan=False)
```

This Python reference is not a registered heuristic or an HTTP endpoint. Reference declarations remain
owner-supplied evidence; analysis does not turn arbitrary `kind: exact` files into
certified optima.

## Execution contract

The production registry adds `visited-scan-threaded-v1` under `backend: multicore`.
The default remains serial `visited-scan-v1`. The scientific variant remains
`nn-fixed-starts-v1` in both cases. Explicit implementation/backend mismatches fail;
a multicore request never falls back to serial execution.

Manifest execution example:

```json
{
  "backend": "multicore",
  "implementation": "visited-scan-threaded-v1",
  "distance_mode": "on_demand",
  "resources": {
    "profile": "local-multicore",
    "workers": 1,
    "threads_per_worker": 2
  }
}
```

Use this execution with the same ordered `budget.start_cities` as the serial
baseline and `record_history: false`, without `time_limit_s`. Both dense and
on-demand distances are supported. Scientific IDs, logical searches and seeds do
not depend on the thread count; execution IDs do. One thread is a valid multicore
implementation baseline, not a claimed acceleration.

### Native concurrency, ownership and limits

- One process owns a bounded `ThreadPoolExecutor`. Each task runs a **GIL-free
  Numba wrapper around the preserved strict-math visited-scan kernel**. This is
  native CPU concurrency, not Python loops executing under the GIL and not a
  Numba `prange` team. The choice preserves the kernel and ordinary exception
  propagation through futures rather than relying on exceptions inside a parallel loop.
- At most one contiguous chunk per requested thread is scheduled. Chunks own
  their starts and candidate/visited/best arrays; prepared coordinates/matrix are
  shared read-only. No per-start Python scheduling or nested parallel kernel is used.
- Chunk results are reduced in original logical order, not completion order.
  Equal-cost solutions retain the earliest original start. Work counters describe
  completed constructions and full evaluations, not worker count.
- The supported thread ceiling is **8**, further limited by CPU affinity (CPU
  count fallback where affinity is unavailable) and the local owner's execution
  policy. This counts allowed logical CPUs, not certified physical cores or a
  scheduler/cgroup allocation. It does not grant cluster execution permission.
- The runner still executes trials sequentially and the local queue still owns
  one active worker process. Concurrent external calls to the Python API are not
  a global scheduling service; callers must avoid overlapping pools themselves.
- `runtime.solver_threads` records the configured executor concurrency ceiling,
  checked against requested resources. It is not a measurement of simultaneously
  busy cores; fewer starts or short tasks may use fewer threads. Native provenance
  records the warmed wrapper and its `nogil: true`, `parallel: false`, strict-math
  options. Legacy serial records remain readable.
- The worker keeps BLAS/OpenMP/Numba nested thread limits at one. Solver threads
  are the Python-owned native-call pool, independent of `NUMBA_NUM_THREADS`.
- Review includes conservative solver-array scratch estimates in addition to the
  matrix estimate. These are not process RSS/thread-stack hard quotas. The existing
  address-space/output limits remain. Aggregate `RLIMIT_CPU` is the wall-time
  allowance multiplied by the thread ceiling; the independent owner watchdog
  still enforces the unchanged whole-job wall-clock ceiling.

Thread creation, workspace allocation, scheduling and reduction are included in
search time. Compilation/warm-up remains separate. Each returned tour is independently
validated. A failed chunk fails the invocation; it never becomes a partial success.
The process-level stop/recovery authority remains pidfd/lease-bound: immediate stop
terminates the owned process and its threads, while graceful stop is at trial boundaries.

### Timed execution and history are intentionally unsupported

Both preflight and direct adapter calls reject multicore time limits or progress
history. Parallel in-flight work requires its own specified checkpoint/publication
semantics; sorting completion events into a fictional serial history is not permitted.
Serial timed/history execution is unchanged. Empty fixed work returns `no_solution`
and zero counters without creating a pool.

## Local application and API

In configuration, select **Execution backend → Multicore NN (fixed work)**, choose
**Solver threads**, distance modes and an explicit start list, then review. Changing
resources invalidates review. Timed/history controls are disabled for this backend.
Fresh-rerun review and non-secret tab drafts retain resource choices; neither can
restore execution authority without review.

The preflight configuration accepts `backend` and `threads_per_worker`, defaulting
to `serial` and `1` for older request clients. Responses explicitly include both.
Capabilities publish the effective `limits.max_solver_threads`; review also returns
`estimated_solver_scratch_bytes`. Old accepted serial review metadata is projected
with equivalent defaults without rewriting private submission bytes or reauthorizing
old preflights. Old queued work is still subject to the existing code/runtime binding.

Threaded NN runs from the CLI on macOS with JIT enabled. [Native browser jobs](interface.md#start-the-application)
additionally require Linux pidfds and readable procfs.

## Profiling and scaling pilot

From a clean checkout, choose a **new** output directory outside Git and thread
counts supported by your CPU affinity:

```bash
uv run --locked python tools/multicore_pilot.py /tmp/tsp-nn-pilot \
  --cities 256 1024 --starts 64 --threads 1 2 4 --repetitions 3
```

On a two-CPU allocation use `--threads 1 2`. The pilot rejects unsupported thread counts
and existing output directories, but caps `--starts` at each instance’s city count.
It limits nested libraries to one thread. Where CPU affinity is available, it pins to as
many allowed CPUs as the largest requested thread count; otherwise it runs unpinned.
It creates canonical inputs, manifests, native JSONL records and shared-validation
checks. A separate warmed serial `cProfile` run identifies the native-call boundary;
it cannot profile individual instructions inside compiled code. Profiling is excluded
from the scaling samples. Execution order rotates across repetitions.

`summary.json` reports median search/end-to-end/preprocessing/warm-up phases and
explicitly descriptive ratios across resource counts. Exact tour, cost and work
agreement are checked before reporting. These are a local exploratory pilot, not
production performance evidence: CPU topology, frequency, contention and statistical
uncertainty still need controlled study. Small workloads may be slower with threads.
The existing implementation-comparison API remains conservative and does **not**
relax its equal-resource checks to manufacture scaling ratios.
