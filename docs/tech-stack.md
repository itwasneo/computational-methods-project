# Technology stack and execution architecture

**Status:** decided; implementation and cluster validation are pending.

Use a Python-first stack with compiled numerical kernels, a small project-specific experiment runner, and an Apptainer image for reproducible cluster execution. The required solvers remain nearest-neighbour, genetic algorithm (GA), and simulated annealing (SA).

See [HPC resources and execution](hpc-resources.md) for the ULHPC target, scheduling, placement, and storage decisions.

## Selected stack

| Component | Choice | Responsibility |
| --- | --- | --- |
| Main language | Python | Solver interfaces, configuration, experiment orchestration, and analysis |
| Numerical representation | NumPy | Coordinates, distance data, tours, populations, and working arrays |
| CPU computation | Numba | Compile performance-critical loops to native code |
| Shared-memory parallelism | Numba parallel loops (`prange`) | Independent starts, chains, fitness evaluations, or offspring construction |
| Experiment orchestration | Our own lightweight Python runner | Reproducible workloads, timing, validation, and result collection |
| Tests | pytest | Solver correctness, permutation invariants, and incremental-cost checks |
| Analysis and plotting | pandas and Matplotlib | Aggregate trials and produce report figures |
| Local dependency management | `pyproject.toml` and uv | Declare dependencies and maintain a version-controlled lockfile |
| Cluster application environment | Apptainer SIF image | Package the Python interpreter, numerical dependencies, and application |
| Cluster scheduling | Slurm | Allocate resources and launch workers; group short work with GNU Parallel |

The cluster host does not need uv or the project's Python environment installed. Its Apptainer module launches the packaged application.

### Optional extensions

- **Distributed computation:** mpi4py, with an MPI implementation validated against the cluster's launch and communication environment.
- **GPU computation:** a separate, compatible GPU image or image variant if GPU acceleration is selected. The Python GPU library and CUDA versions are not yet chosen.
- **Native kernels:** targeted C++ code only if profiling or implementation limitations justify it. A second implementation of every solver is not required.

## Separate orchestration from computation

Keep configuration, scheduling decisions, logging, and result serialization in ordinary Python. Keep hot numerical paths in functions operating on explicitly typed NumPy arrays, with minimal copying and allocation.

- **Nearest-neighbour:** compile the complete construction loop; parallelize independent starting cities where appropriate.
- **GA:** compile and profile fitness evaluation and permutation operators; independent tasks must write to separate output regions.
- **SA:** compile the iteration loop rather than calling a tiny compiled function from Python for every proposed move. Independent chains provide the initial parallelism.

A common solver contract should accept an instance, solver seed, search budget, and algorithm parameters, and return a tour with search statistics. Backend and resource selection belong in the run configuration. The exact API will be defined during implementation.

## Own the experiment runner, not the scheduler

We will not use a third-party TSP benchmark package as the primary orchestrator. Timing boundaries, stochastic budgets, correctness checks, and metadata are central to this project's experimental method and should remain explicit in our code.

The runner should:

1. Read an experiment manifest identifying instances, algorithms, backends, seeds, parameters, budgets, and resource profiles.
2. Prepare the instance and execution backend, including a separate warm-up phase where needed.
3. Run each measured trial with fresh, explicitly seeded solver state.
4. Validate the tour and recompute its cost using the instance's distance convention.
5. Save structured results, failures, timing components, and provenance.
6. Support aggregation without silently omitting failed or incomplete trials.

An instance-generation seed and a solver seed are separate fields. Logical experiment identifiers must remain stable when worker counts, batch sizes, or scheduling order change.

One Slurm allocation may execute many logical experiments. A worker should be able to process a batch of experiments, avoiding repeated interpreter and JIT startup for very short trials. Slurm and GNU Parallel handle resource scheduling and process launching; our runner defines and measures the scientific work.

Use separate result shards per worker or batch, then merge them. Do not have concurrent workers append to one shared results file. JSON Lines is a suitable initial raw-result format, with tables and plots generated separately.

## Reproducible Apptainer environment

An Apptainer image is the preferred cluster application environment. This avoids coupling the Python/NumPy/Numba combination to the versions in a particular ULHPC module release.

### Build and release workflow

1. Keep the image definition, dependency specification, and lockfile in Git.
2. Pin the base image and application dependencies rather than relying on floating `latest` tags.
3. Follow the documented ULHPC workflow: build the image off-cluster with the required privileges, then transfer the SIF artifact. Do not assume an in-cluster image-building facility.
4. For recorded benchmark campaigns, use a fixed image containing a known application revision and record its checksum and source revision.
5. Keep inputs, outputs, and writable temporary/cache directories outside the immutable application image through deliberately configured mounts.

Development may expose a working source tree to the container, but measured runs must record the code actually executed, including any deviation from the packaged revision.

### Runtime boundaries and caveats

- Load the ULHPC `tools/Apptainer` module inside an allocation. The application need not load the host Python or Numba modules.
- Control inherited environment settings and Python user-package visibility. ULHPC documents that the default home-directory mount can override the container environment, and provides `--no-mount home` where appropriate.
- Containers do not replace Slurm resource controls or prevent CPU-affinity and NUMA experiments. Verify actual placement inside the container; see [placement guidance](hpc-resources.md#cpu-placement-and-numa).
- Match thread counts to the allocation and control nested threading. Do not let every worker independently use all cores visible on the host.
- Treat startup, imports, JIT compilation/cache loading, steady-state search, and end-to-end runtime as distinct measurement components.
- Keep compiled artifacts compatible with the target CPU architecture; do not assume binaries built specifically for a development machine will run on every cluster node.
- A GPU image still depends on compatible host drivers and the documented NVIDIA integration. An MPI image still needs launcher and communication-stack compatibility checks.

## Implementation-time validation

The architecture is decided, but exact package versions and a working image have not yet been established. Before production experiments:

- Select and lock a compatible Python/NumPy/Numba combination.
- Build and test the CPU image, including solver tests, JIT execution, writable caches, and result persistence.
- Verify resource limits, effective affinity, and thread behavior in a small cluster allocation.
- Validate any GPU or MPI image separately before claiming acceleration or distributed scaling.

## ULHPC documentation basis

Cluster-specific guidance comes only from the supplied local documentation checkout at `../ulhpc-docs/docs/`, relative to the project root. These links assume that sibling checkout exists:

- [Apptainer, image builds, home mounts, GPU integration, and MPI execution](../../ulhpc-docs/docs/environment/containers.md)
- [ULHPC module environment](../../ulhpc-docs/docs/environment/modules.md)
- [Slurm launchers](../../ulhpc-docs/docs/slurm/launchers.md)
