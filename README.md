# Computational Methods Project: TSP Heuristics and HPC

Implement and compare three heuristics for the Traveling Salesman Problem (TSP), then investigate how implementation choices and high-performance computing techniques affect their efficiency.

**Research question:** How do implementation choices, memory behavior, and parallel execution affect the solution quality achievable by three TSP heuristics under limited computational budgets?

## Project status

The project is in the planning and implementation stage. A manual [swap-based SA baseline](docs/sa-baseline.md) and an exploratory [Smart TSP testbench](benchmarks/README.md) are available. The other solvers, common result interface, and final benchmark protocol remain in progress. The repository also contains the [original team presentation](presentations/tsp_team_presentation.html).

## Core scope

The three algorithms from the original project proposal remain the required implementation scope:

| Algorithm | Role | Main components |
| --- | --- | --- |
| **Greedy nearest-neighbour** | Fast constructive baseline | Starting-city selection, nearest-unvisited-city search, deterministic tie-breaking |
| **Genetic algorithm (GA)** | Population-based search | Fitness evaluation, selection, permutation-preserving crossover, mutation |
| **Simulated annealing (SA)** | Temperature-guided local search | Move proposals, acceptance decisions, cooling schedule, best-tour tracking |

Initially, focus on complete, symmetric, two-dimensional Euclidean TSP instances. A valid tour visits every city exactly once and returns to its starting city. All solvers must use the same distance convention and cost calculation for a given instance.

Exact optima for small instances provide quality and correctness references, not a fourth primary solver. Segment-reversal moves may be used within SA; a separate 2-opt solver is not required.

Distributed execution, CPU parallelism, and GPU acceleration are **possible investigation directions, not a mandatory technology checklist**. The project should select extensions based on profiling, available hardware, and development time.

## Investigation areas

### 1. Algorithm engineering

Establish efficient serial implementations before attributing improvements to additional hardware.

- **Nearest-neighbour:** reduce bookkeeping overhead and efficiently scan unvisited cities.
- **GA:** profile fitness evaluation, selection, crossover, and mutation separately; avoid unnecessary copying and expensive permutation bookkeeping.
- **SA:** evaluate move cost differences using affected edges rather than rescoring entire tours, and avoid copying the full tour for rejected proposals.

For symmetric TSP, a segment reversal permits constant-time cost-difference evaluation, but applying an accepted reversal can still take time proportional to the segment length.

Measure serial implementation improvements separately from parallel speedups. Changes to search behavior, such as a new neighborhood or crossover operator, should be identified as algorithmic variants rather than implementation-only optimizations.

### 2. Memory behavior and data representation

Compare precomputed distance matrices with distances calculated on demand from coordinates.

- A precomputed matrix avoids repeated arithmetic but requires quadratic storage and may incur expensive memory accesses.
- On-demand distances use less memory but require additional computation.
- The trade-off depends on instance size, cache capacity, access patterns, and how often an instance is reused.
- GA population storage and tour modifications in SA introduce additional memory costs.

Measure preprocessing separately from search, and also report end-to-end costs. Investigate data layout, allocation overhead, cache behavior, and CPU SIMD/vectorization where profiling suggests they matter.

### 3. Parallel execution and task granularity

Use each algorithm's natural sources of concurrency rather than applying the same strategy everywhere.

| Algorithm | Initial multicore direction | Possible deeper investigation |
| --- | --- | --- |
| **Nearest-neighbour** | Run a fixed set of starting cities concurrently | Vectorized candidate scans; distribute starts or instances |
| **GA** | Evaluate individuals or construct offspring concurrently | Distributed island populations with occasional migration; batched GPU fitness evaluation |
| **SA** | Run independent chains or restarts concurrently | Distribute chains; investigate batched GPU execution if the workload justifies it |

A single nearest-neighbour construction and a single SA chain have sequential dependencies. Parallelizing independent searches is different from accelerating the dependent steps inside one search.

Evaluate task size, scheduling overhead, synchronization, load balance, and communication costs. GPU work must also account for data transfers and sufficient batch sizes. Accelerating one kernel does not necessarily accelerate the complete solver.

Parallel execution of independent instances or parameter experiments is also useful, but should be reported as experiment throughput rather than single-search acceleration.

### 4. Search quality and budget allocation

Efficiency means obtaining useful solutions within a computational budget, not simply terminating quickly.

- **Nearest-neighbour:** how much quality is gained by trying additional starting cities?
- **GA:** is a budget better spent on larger populations, more generations, or independent populations?
- **SA:** is a fixed move budget better spent on one long chain or several shorter chains?

Give stochastic algorithms comparable parameter-tuning opportunities, and evaluate selected configurations on instances not used for tuning. Equal iteration counts across different algorithms do not imply equal computational work.

## Evaluation and validation

### Instances and correctness

- Use shared generated instances with varied sizes and spatial layouts, plus selected compatible TSPLIB instances.
- Respect each benchmark's distance definition, including rounding rules, when comparing against reference costs.
- Validate that tours contain every city exactly once and include the closing edge in their cost.
- Test permutation operators and compare incremental cost updates against full tour-cost recomputation.
- Compare small cases against exact optima; clearly distinguish certified optima from best-known reference solutions.

### Separate experimental questions

| Question | Comparison approach |
| --- | --- |
| **Does the implementation execute faster?** | Keep total work, seeds, and logical search configuration fixed while changing execution resources. |
| **Does additional computation improve quality?** | Compare quality under a fixed wall-clock budget and report the resources used. |
| **How quickly is a useful solution found?** | Measure time to a predefined quality target and report runs that do not reach it. |

For example, execute the same fixed collection of SA chains on different worker counts to measure scaling. Comparing one chain with many chains changes the search budget as well as the execution configuration. Likewise, increasing the GA population or changing the number of islands is not a pure implementation-speedup experiment.

### Measurements and reproducibility

- Tour length, gap to the stated reference, and quality-over-time curves.
- Wall-clock runtime, memory use, and variation across repeated trials.
- Parallel speedup `S_p = T_1 / T_p` and efficiency `E_p = S_p / p` for equivalent workloads.
- Preprocessing, communication, synchronization, and transfer costs where relevant.
- Kernel-only and end-to-end timings for acceleration experiments, using appropriate device synchronization and an optimized CPU baseline.
- Recorded instance identifiers, configurations, seeds, hardware, compiler settings, and worker counts.

Assign random streams to logical runs or chains rather than execution threads. Specify tie-breaking and numerical precision. Parallel floating-point evaluation may change trajectories, so correctness and statistically meaningful comparisons are required even when bitwise agreement is not practical.

## Planned roadmap

1. **Correct serial solvers:** implement the three algorithms with shared instance, distance, tour-validation, and cost utilities.
2. **Shared experiment framework:** add reproducible runs, reference comparisons, structured results, and plots.
3. **Profiling and serial optimization:** identify bottlenecks and measure algorithm-engineering and memory-representation improvements.
4. **Multicore experiments:** evaluate the natural parallelism of each algorithm with controlled workloads.
5. **One focused extension:** choose a deeper memory-layout, SIMD, distributed-computing, or GPU investigation based on evidence and available resources.

A successful project does not require every backend or a GPU implementation of all three algorithms. Energy efficiency and performance portability are optional extensions if time and measurement facilities permit.

## Documented technical decisions

| Decision | Agreed direction |
| --- | --- |
| [Shared instance, tour, solver, and result contracts](docs/contracts.md) | Coordinate-based symmetric TSP; implicitly closed `int64` tours; shared distance and cost conventions; solver results independent of execution resources |
| [Technology stack and execution architecture](docs/tech-stack.md) | Python, NumPy, and Numba; our own lightweight experiment runner; pytest, pandas, and Matplotlib; uv for local dependency management and Apptainer for the cluster application environment |
| [HPC resources and execution](docs/hpc-resources.md) | University of Luxembourg HPC, with Slurm-managed allocations; grouped short experiments, controlled CPU/NUMA placement, and optional MPI or GPU investigations |

These choices are agreed, but implementation and cluster validation are pending. Exact package versions, account-specific resource access, and any GPU/MPI compatibility must be checked before production experiments.

## Decisions still to make

- Concrete crossover, mutation, SA neighborhood, and cooling choices.
- Benchmark sizes, tuning budgets, reference sources, and stopping criteria.
- Which advanced investigation offers the strongest experiment within the project timeline.
