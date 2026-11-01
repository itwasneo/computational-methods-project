# Initial algorithm variants and stopping semantics

**Status:** v1 design for task #2; implementation and parameter tuning are pending. These are reproducible starting variants, not claims of optimal parameters or finalized benchmark settings.

This document complements the [shared contracts](contracts.md). The required algorithms remain nearest-neighbour (NN), a genetic algorithm (GA), and simulated annealing (SA). An exact reference and SA's reversal moves do not add primary solvers.

## Common decisions

- Minimize the declared complete-tour cost directly. Do not convert cost to reciprocal fitness: zero-cost tours are valid.
- Use exact comparisons of computed `float64` values for algorithmic decisions. The contract's validation tolerances are not tie-breaking, acceptance, or convergence thresholds.
- On equal best costs, retain the earliest candidate in **logical** order: NN start-list position; GA initialization slot followed by generation/offspring slot; SA initial tour followed by proposal number. Parallel completion order must not decide the winner. Across independent searches, break ties by the fixed logical search index.
- Do not normalize rotations or orientations, deduplicate equal tours, seed a population with NN, apply local improvement after crossover, or restart a stalled search implicitly. Such changes alter the baseline.
- Generate uniform random permutations using Fisher-Yates shuffling. All random choices use explicit solver/subsearch streams; task #6 defines their concrete implementation. Fixed-work serial and parallel execution must preserve logical searches, configuration, and random streams.
- The baseline GA and SA start randomly; they do not accept an `initial_tour` parameter. The shared interface permits future variants with supplied or heuristic initialization, but these must be identified separately.
- Record the variant identifier and all effective settings, including resolved defaults. Reject unknown fields, non-finite numeric settings, invalid ranges, and booleans where integer counts are required.

## Greedy nearest-neighbour

**Variant:** `nn-fixed-starts-v1`.

For each city in the ordered `budget.start_cities` list:

1. Place that city first and mark it visited.
2. Repeatedly choose the nearest unvisited city. On exactly equal edge costs, choose the **lowest internal city ID**.
3. Close the tour implicitly and evaluate its full cost once with the shared routine.
4. Update the best complete tour; equal costs retain the earlier start-list entry.

The start list contains distinct valid city IDs and is never reordered by the solver. To try every city, explicitly supply `[0, ..., n - 1]`. No random starts or duplicate constructions are added to spend unused time. The common seed argument is retained in provenance but does not affect this deterministic variant.

An empty list is a legal zero-work request: return `no_solution`, null tour/cost, zero counters, and `work_limit`. Otherwise, after completing `S` starts, the baseline records `nn_starts_completed = S` and `full_tour_evaluations = S`. Final correctness recomputation remains outside these search counters.

## Genetic algorithm

**Variant:** `ga-tournament-ox-swap-elitist-v1`.

### Initialization and selection

- Create `P = parameters.population_size` independent random permutations in stable population-slot order. Duplicate permutations and equivalent cycles are allowed, including when `P > n!`.
- Evaluate all `P` tours and establish the best-so-far solution. This is initialization, **not generation 1**.
- Select each parent with a tournament of `K = parameters.tournament_size` slot indices sampled uniformly **with replacement** from the current population. Choose the lowest-cost contender; on equal cost, choose the lowest current population-slot index.
- Run two independent tournaments per offspring slot. Selecting the same parent twice is allowed; do not retry to force distinct parents.

### Ordered crossover (OX)

Create one child per offspring slot, not an implicit pair of children. With probability `crossover_probability`, apply this precise OX rule:

1. Choose two distinct boundaries uniformly from `0, ..., n` and sort them as `a < b`. The copied segment is the half-open interval `[a, b)`; single-city and whole-tour segments are allowed.
2. Copy parent A's segment into those same child positions.
3. Scan parent B cyclically, beginning at position `b % n`. Skip cities already copied.
4. Fill the remaining child positions cyclically, also beginning at `b % n`, preserving that scan order.

For example:

```text
parent A = [0, 1, 2, 3, 4]
parent B = [4, 3, 0, 2, 1]
segment  = [1, 3)             # retain cities 1 and 2 in positions 1 and 2
child    = [0, 1, 2, 4, 3]
```

If crossover is skipped, copy parent A. Use a membership array or equivalent linear-time bookkeeping; repeated linear membership searches must not turn OX into quadratic work. Never modify either parent.

### Mutation and replacement

- Independently for each child, with probability `mutation_probability`, swap two uniformly selected distinct positions. This probability is **per child**, not per city. Mutation also applies when crossover was skipped.
- Select `E = parameters.elite_count` current individuals by `(cost, population_slot)`. Copy them unchanged, with cached costs, into the first `E` slots of the next population in that order.
- Fill the remaining slots with the `P - E` independently constructed offspring, in stable slot order. Evaluate each child's full tour cost and update best-so-far tracking in logical order.
- Every parent comes from the old population. Replace the population only after the entire generation is complete; children do not become parents within that same generation.
- Elites are neither crossed nor mutated. Even with `E = 0`, keep a separate best-so-far tour for the result.

Require integers `P >= 2`, `K >= 1`, and `0 <= E < P`; sampling with replacement allows `K > P`. Both probabilities must lie in `[0, 1]`. Uniform permutations, OX, and swap mutation remain valid for `n = 2`; no population uniqueness requirement is introduced.

The work limit is `budget.max_generations = G`, a non-negative integer. After `G` complete generations, `ga_generations_completed = G`. With ordinary uncached offspring evaluation, `full_tour_evaluations = P + G * (P - E)`. `G = 0` still initializes and evaluates the population, then returns its best tour with `work_limit`. There is no automatic stagnation or zero-cost early stop.

## Simulated annealing

**Variant:** `sa-reversal-geometric-v1`.

### Initialization and neighborhood

Initialize one uniform random permutation and evaluate its full cost. Keep independent current-tour and best-tour state.

For `n >= 4`, choose two non-adjacent tour edges uniformly:

- Sample two distinct edge indices, sort them as `0 <= i < j < n`, and retry if `j = i + 1` or `(i, j) = (0, n - 1)`. Retrying an ineligible pair is not an evaluated proposal.
- Edge `k` joins `p[k]` to `p[(k + 1) % n]`. Reverse positions `i + 1` through `j`, inclusive, reconnecting the two broken edges.
- With `a = p[i]`, `b = p[i + 1]`, `c = p[j]`, and `d = p[(j + 1) % n]`, compute:

```text
delta = distance(a, c) + distance(b, d) - distance(a, b) - distance(c, d)
```

This includes moves touching the closing edge. Adjacent-edge pairs and whole-cycle reversals are excluded, so the four boundary vertices are distinct. The first stored city stays fixed within a chain; this does not exclude any undirected Hamiltonian cycle.

For `n >= 4`, the eligible neighborhood has `n * (n - 3) / 2` edge pairs. For `n = 2` or `n = 3`, it is empty: after initialization, return the initial tour with `algorithm_stop` and zero evaluated proposals, unless a work/time limit takes precedence as specified below. Do not enter an endless resampling loop or invent no-op proposals.

### Acceptance and cooling

For proposal index `k`, starting at zero:

1. Use the current temperature `T_k` and compute `delta` from affected edges.
2. Accept if `delta <= 0`, including equal-cost moves. Otherwise draw a uniform `u` strictly between zero and one and accept if `log(u) < -delta / T_k`. Non-uphill proposals do not consume an acceptance draw.
3. On acceptance, reverse the segment in place and update the current cost. Rejected proposals must not copy the full tour. Applying an accepted reversal is proportional to segment length even though delta evaluation is constant-time.
4. Finish best-tour/numerical bookkeeping, then increment `sa_proposals_evaluated`, whether the move was accepted or rejected.
5. Cool with `T_next = max(minimum_temperature, cooling_factor * T_k)` after **every** completed proposal.

Require finite `initial_temperature > 0`, `0 < cooling_factor <= 1`, and `0 < minimum_temperature <= initial_temperature`. `T_0 = initial_temperature`. The positive floor prevents temperature underflow to zero; reaching it does not stop or reheat the chain. Cooling depends on completed proposals, not elapsed time, worker count, or the requested total proposal limit.

### Cost bookkeeping

Maintain the current cost incrementally, but confirm a potential new best with a full recomputation before replacing the saved best tour. Also recompute the current cost every `cost_check_interval` completed proposals, using a positive integer interval. If both checks coincide, recompute only once. After any recomputation, compare the recomputed cost with the saved best and update only on strict improvement.

Check incremental-versus-recomputed cost against the [shared validation rules](contracts.md#tour-representation-and-shared-objective). Drift beyond tolerance is a numerical failure, not something to silently conceal. Within tolerance, rebase the current cost to the recomputed value. Count each in-search recomputation in `full_tour_evaluations`; final post-search verification remains excluded. The check interval is fixed across an implementation-scaling comparison and does not consume randomness.

The work limit is `budget.max_proposals = M`, a non-negative integer. `M = 0` still initializes and evaluates one tour, then returns it with `work_limit`. For non-degenerate inputs and no time limit, exactly `M` acceptance decisions complete. Do not stop early for apparent convergence, lack of improvements, or a zero-cost tour. Return the best tour, not necessarily the final current tour.

## Budgets and stopping

### Fixed-work inputs

| Solver | Scientific configuration and work limit | Zero-work behavior |
| --- | --- | --- |
| NN | Explicit ordered `budget.start_cities`; its length is the number of constructions | Empty list: no solution, no construction |
| GA | `parameters.population_size = P`, `parameters.elite_count = E`, and `budget.max_generations = G` | `G = 0`: evaluate all `P` initial tours, no generations |
| SA | Temperature/check settings and `budget.max_proposals = M` per logical chain | `M = 0`: evaluate the initial tour, no proposals |

Initialization is part of measured search and is charged to `full_tour_evaluations`; it is not hidden preprocessing or JIT warm-up. GA population size, SA chain count, and NN start count all affect search effort. Equal generation/proposal counts across algorithms are not equal computational budgets.

For independent chains or populations, specify logical IDs, seeds, and per-search budgets before scheduling. Changing worker count must not multiply those budgets, add chains, or change the cooling schedule.

### Time-budget extension

Task #19 adds optional `budget.time_limit_s`, a finite positive duration. GA and SA may then omit their work-limit field for time-only runs, or provide both limits. NN always has a finite start list, even in a timed run; finishing it early is a work-limit termination, not permission to repeat starts. No variant may run without a stopping limit.

Start the monotonic search clock immediately before initializing solver working state. Include initialization, numerical checks, and progress instrumentation. Exclude input loading, distance preprocessing, and JIT warm-up. Final verification and persistence must still appear in end-to-end measurements; they do not make the stopping limit a hard job deadline.

Use these safe checkpoints:

| Solver phase | Checkpoint and possible overrun |
| --- | --- |
| NN | Before the first start and after each complete construction; one construction can cross the deadline |
| GA initialization | Before initialization and after each complete initial tour evaluation; a partial population may yield a valid best tour, but no generation starts until initialization completes |
| GA generations | After complete initialization and after each complete generation; a generation is atomic for stopping and can cross the deadline |
| SA initialization | Before initialization and after the complete initial tour evaluation |
| SA search | After chunks of at most `budget.time_check_interval_proposals` completed proposals; default 256, positive integer. Trim the last chunk to any remaining work limit |

The timed SA chunk size is recorded checkpoint policy, not a thread count. It permits a compiled inner loop without returning to Python after every move. Preserve RNG state, cooling state, and cost-check cadence across chunks. A fixed-work implementation may run one longer loop, provided its search behavior is unchanged.

At a checkpoint where initialization is complete, resolve simultaneous stopping conditions in this order:

1. Reached the explicit work limit: `work_limit`.
2. Reached or exceeded the time limit: `time_limit`.
3. SA has no eligible moves (`n < 4`): `algorithm_stop`.

Before or during unfinished GA/SA initialization, check the deadline without treating a zero generation/proposal limit as permission to skip initialization. NN needs no initial complete solution, so its empty start list terminates immediately with `work_limit`.

On a time stop, return the best complete tour observed so far, or `no_solution` with null tour/cost if none exists. Never return a partial permutation. Record actual work and elapsed time; do not truncate reported runtime to the requested limit. Checkpoint overshoot, especially a large NN construction or GA generation, must be visible in comparisons. Finer interruption rules would change the stopping specification and must be recorded rather than introduced silently.

There is no baseline quality-target early stopping. Later time-to-target analysis records the first observed target crossing without changing the search, and retains runs that never reach it. Unsupported timed/history requests fail explicitly until task #19 is implemented.

## Starting values, not tuned conclusions

These values provide development/pilot presets. Manifests must record the resolved configuration rather than relying on hidden solver defaults. They are not a final campaign protocol or a claim that GA and SA receive equal compute.

| Setting | Initial value |
| --- | --- |
| NN start cities | `[0]` |
| GA population / generations | `128` / `100` |
| GA tournament size / elites | `3` / `1` |
| GA crossover / per-child mutation probability | `0.9` / `0.2` |
| SA proposals per chain | `10_000` |
| SA initial temperature / cooling factor / temperature floor | `1.0` / `0.999` / `1e-12` |
| SA cost-check interval | `1024` completed proposals |
| Time limit / history | Disabled for initial fixed-work baselines |

Temperature is expressed in the instance's edge-cost units: `1.0` is a development value, not scale-invariant calibration. Task #27 will tune temperatures and other numeric settings on a tuning split, with declared comparable tuning opportunities; task #35 freezes the production protocol.

Changing a documented numeric parameter or workload remains within the named variant but creates a different scientific configuration. Changing initialization strategy, selection/replacement rules, crossover, mutation/neighborhood, cooling law, or early-stopping behavior requires a separately identified variant. Do not present such changes as pure implementation speedups.

## Implementation checks

Follow-up implementation tasks must cover deterministic NN ties and start ordering; OX cut boundaries, permutation validity, and parent immutability; GA tournament ties, elitism, and initialization accounting; SA wraparound deltas, rejected moves, geometric cooling, and numerical checks; and zero-work/expired-time precedence for every solver.

Include two- and three-city inputs, duplicate coordinates, all-zero distances, probability boundaries, and both supported distance conventions. This document selects behavior; executable solvers and their tests remain in their implementation tasks.
