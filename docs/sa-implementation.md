# Simulated annealing: implementation and evaluation

The scientific variant is [sa-reversal-geometric-v1](algorithm-baselines.md#simulated-annealing).
The implementation is `serial-reversal-v1`; it uses no external benchmarking or solver package.

## Mechanism

Initialize a uniformly shuffled tour and keep the current and best tours separately.
Choose two non-adjacent edges uniformly, and reverse the segment between them. For
old edges `(a,b)` and `(c,d)`, the cost difference is

```text
distance(a,c) + distance(b,d) - distance(a,b) - distance(c,d)
```

Accept non-increasing moves; accept an uphill move when `log(u) < -delta/T`.
Decrease the temperature after every evaluated proposal, with a positive floor.
Return the best tour, independently checked with the shared objective.

## Performance and correctness

- Evaluate four edges rather than rescoring the entire tour for each proposed move.
  A rejected proposal does not copy a tour. An accepted reversal still takes time
  proportional to the reversed segment, and best-tour updates and periodic checks
  cost O(n); the whole proposal is not unconditionally O(1).
- Compile the proposal loop with Numba and strict floating-point arithmetic. Timed
  or observed searches return to Python at bounded checkpoints while retaining the
  chain, RNG, temperature and evaluation cadence. JIT warm-up is outside search time.
- Use the shared on-demand or dense distances. A dense matrix takes `8*n*n` bytes
  and has preparation cost; neither mode is assumed faster without measurement.
- Confirm potential bests and periodically recompute current cost; excessive
  incremental drift raises `NumericalError`. The output is independently checked.
- Separate proposal counts, full evaluations, initialization, search, warm-up and
  validation. For two or three cities the specified move neighborhood is empty.

These techniques connect to the course's local approximation, floating-point
validation, memory-layout and parallel-work lessons: an efficient update replaces
repeated full work, but is checked against an independent complete calculation.

## Planned measurement, not a speed claim

Use the existing runner with identical saved inputs, seeds, variant, proposal
counts, logical chains and numeric settings when comparing implementations.
Warm each implementation; report search and end-to-end times separately, along
with actual work, preprocessing, memory, CPU allocation and runtime versions.
Repeat runs and show distributions. Dense versus on-demand comparisons must also
report matrix construction and storage. Run substantial measurements in an
appropriate compute allocation.

A chain is sequential because each decision depends on the previous state.
`tsp_hpc.solvers.sa.parallel.solve_chains` instead runs a fixed ordered collection
of independent fixed-work chains on a bounded native thread pool. Each chain owns
its RNG, tour buffers and cooling state; Numba releases the GIL in the compiled
chain kernels. The caller passes the already-derived logical seeds in logical
order. Results retain that order and equal best costs select the earliest chain.
One thread runs the same collection sequentially for a matched baseline. Thread
limits follow `solvers.resources`, including the affinity check and ceiling of 8.
The batch wall time includes thread creation, scheduling and final validation;
individual chain times overlap and must not be summed as elapsed wall time.
The thread count is a configured ceiling, not a measurement of simultaneous
busy cores. Affinity is not proof of a scheduler allocation: on a cluster, the
caller must request suitable resources and keep this ceiling within the allocation.

This is a direct Python API, **not yet a registered multicore runner backend**:
the current runner executes each logical invocation serially and its record format
assumes non-overlapping timings. It rejects time budgets and does not offer progress
history, rather than inventing parallel checkpoint or timing semantics.

```python
from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.problem.instances import uniform_instance
from tsp_hpc.solvers.sa.parallel import solve_chains
from tsp_hpc.solvers.sa.solver import SimulatedAnnealingSolver

instance = uniform_instance(100, seed=42).instance
solver = SimulatedAnnealingSolver(prepare_distances(instance, mode="on_demand"))
batch = solve_chains(solver, seeds=[11, 22, 33, 44],
                     budget={"max_proposals": 100_000}, parameters={}, threads=2)
print(batch.best.best_cost, batch.best_index, batch.wall_time_s)
```

For strong scaling, hold the **same logical chains and per-chain budgets** fixed
while changing workers; use independent logical RNG streams and stable tie-breaking.
Adding chains is a different search-effort experiment, not pure parallel speedup.
Quality comparisons with NN/GA require a declared wall-time or other defensible
budget, tuning/held-out separation and compatible exact references. A small local
pilot is not evidence of general superiority or cluster speedup.
