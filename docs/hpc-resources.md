# HPC resources and execution

**Status:** ULHPC is the selected execution platform; account-specific access and runtime compatibility still require validation.

Use the University of Luxembourg HPC facility for substantial experiments. Begin with CPU execution on one node, then expand only when measurements justify additional resources. Distributed computation and GPU acceleration remain optional investigations, not required backends for all three algorithms.

The application stack and container workflow are described in [Technology stack and execution architecture](tech-stack.md).

## Documentation boundary

All cluster-specific facts in this document are sourced exclusively from the supplied local checkout at `../ulhpc-docs/docs/`, relative to the project root. Source links below assume that sibling checkout exists; the ULHPC documentation is not vendored into this project.

The documentation describes the facility, not our team's permissions or current resource availability. No live cluster validation has been performed. Record the actual hardware and software configuration used by each experiment rather than treating documentation examples as a live inventory.

## Target resources

| Resource | Documented configuration | Planned use |
| --- | --- | --- |
| **Aion CPU nodes** | Two AMD EPYC Rome 7H12 processors, 128 cores and 256 GB RAM per node | Preferred CPU target, subject to account access; multicore and potential distributed experiments |
| **Iris CPU nodes** | Intel Broadwell or Skylake processors, 28 cores and 128 GB RAM per node | Alternative CPU target or a separately identified architecture comparison |
| **Iris `gpu` nodes** | Four NVIDIA V100 GPUs per node, with 16 or 32 GB memory per GPU; 28 CPU cores and 768 GB host RAM | Optional GPU investigation, starting with one GPU |

Sources: [Aion compute nodes](../../ulhpc-docs/docs/systems/aion/compute.md), [Iris compute nodes](../../ulhpc-docs/docs/systems/iris/compute.md).

The documentation also lists a `hopper` partition associated with H100 GPUs. Its hardware is less fully described in the reviewed pages; do not assume access or a particular GPU memory capacity. See [partitions](../../ulhpc-docs/docs/slurm/partitions.md) and [long-job resource types](../../ulhpc-docs/docs/jobs/long.md).

For controlled scaling experiments, keep the CPU architecture fixed. Do not present a single-core Iris versus multicore Aion comparison as pure parallel speedup. Record GPU model, device memory, and host CPU configuration for acceleration experiments.

## Slurm owns resource scheduling

Our Python runner defines logical experiments; Slurm allocates their resources. A logical experiment is not necessarily a separate Slurm job.

| Workload | Execution approach |
| --- | --- |
| Development and small validation checks | Interactive allocation |
| Many short independent trials | Group work inside an allocation, using GNU Parallel to launch bounded workers that each process a batch |
| Longer independent runs with the same resource requirements | Throttled Slurm job arrays where appropriate |
| Multicore solver or search batch | Explicit task count and cores per task, with matching runtime thread limits |
| Cooperating distributed searches | Coordinated MPI job, not unrelated array tasks |
| GPU search or kernel experiment | GPU allocation and GPU-enabled container; do not occupy GPU nodes for CPU-only work |

ULHPC recommends grouping short work rather than overwhelming the scheduler. Its guidance identifies roughly **10 allocations per minute** and **1,000 job steps per minute** as thresholds for considering additional grouping. Treat these as workload-design guidance, not targets to saturate.

Array elements share their requested scheduler resources. Group experiments by resource profile and use a concurrency cap. Array execution order must not affect experiment identifiers or random seeds. Native Slurm job dependencies can connect preparation, execution, and aggregation stages without a custom scheduling service.

Sources: [GNU Parallel](../../ulhpc-docs/docs/jobs/gnu-parallel.md), [job arrays](../../ulhpc-docs/docs/jobs/arrays.md), [job dependencies](../../ulhpc-docs/docs/jobs/dependencies.md).

### Operational rules

- Login nodes are for preparation, file management, and submission, not benchmark execution.
- Load modules on compute nodes inside an allocation.
- Interactive allocations are for development, testing, and debugging; production experiments belong in batch jobs.
- Follow the documented login-shell initialization in launcher scripts, such as `#!/bin/bash --login`.
- Explicitly request resources and set process/thread limits accordingly. Prevent nested parallelism from oversubscribing the allocation.
- Monitor resource utilization and use measured runtimes to refine allocation sizes and walltime requests.
- Separate scheduler waiting time from application execution time in performance reports.

Sources: [Slurm resource management](../../ulhpc-docs/docs/slurm/index.md), [interactive-job policy](../../ulhpc-docs/docs/jobs/interactive.md), [launchers](../../ulhpc-docs/docs/slurm/launchers.md), [monitoring jobs](../../ulhpc-docs/docs/jobs/submit.md).

## CPU placement and NUMA

Aion exposes eight virtual sockets/NUMA domains with 16 cores each. Iris regular CPU nodes have two sockets with 14 cores each. The documentation states that simultaneous multithreading is disabled on the compute nodes.

This makes placement a useful possible investigation, alongside algorithm engineering and memory representation:

- Compare compact and spread placement while keeping the logical workload fixed.
- Compare larger thread teams spanning NUMA domains with smaller teams within domains.
- Consider how distance data is initialized, shared, or replicated, and report the resulting memory footprint.

A full-node layout of eight processes with 16 cores each is one documented Aion pattern, not an assumption that it will be optimal for TSP. Throughput-oriented packing and controlled solver-scaling experiments should be distinguished; colocated runs can contend for shared resources.

### Containers do not remove placement controls

Launch the application through `srun` with the intended binding and distribution options, then `apptainer exec`. The container supplies the application environment; it does not replace the host topology or grant resources outside the allocation.

Keep three controls distinct:

1. **Process affinity:** the cores on which a process may run.
2. **Thread placement:** how its worker threads use those cores.
3. **Memory placement:** where the data resides and the memory policy used when it is allocated and initialized.

CPU pinning alone does not establish memory locality. Validate the effective process and thread placement from inside the container, using the documented affinity-reporting tools or `/proc/self/status`. Check that the required memory-policy operations are available before treating a NUMA configuration as established.

Sources: [affinity and pinning](../../ulhpc-docs/docs/jobs/affinity_and_pinning.md), [numactl](../../ulhpc-docs/docs/software/swsets/tools/numactl.md), [Apptainer](../../ulhpc-docs/docs/environment/containers.md).

## Optional GPU and MPI execution

### GPU

For the documented Iris V100 configuration, the GPU guide recommends seven CPU cores per allocated GPU. Start with one GPU and use the documented Apptainer NVIDIA integration (`--nv`). This CPU/GPU ratio is not a prescription for the separately listed Hopper hardware.

Validate host-driver and container-runtime compatibility before selecting CUDA-dependent package versions. The versions in documentation examples are not evidence of the current host driver. Keep transfers and synchronization explicit in timing, and report both kernel-only and end-to-end measurements.

Source: [GPU jobs](../../ulhpc-docs/docs/jobs/gpu.md), [GPU-enabled containers](../../ulhpc-docs/docs/environment/containers.md#gpu-enabled-apptainer-containers).

### MPI

Use mpi4py only if a distributed investigation is selected. A containerized MPI installation must be compatible with the cluster launcher and communication environment; successful Python imports are not sufficient validation.

Before TSP scaling runs, check multi-node correctness and communication performance. The local documentation provides an MPI-container example launched through `srun`, but this does not establish compatibility for an arbitrary new MPI/mpi4py image.

Source: [MPI containers](../../ulhpc-docs/docs/environment/containers.md#mpi-and-apptainer-containers), [MPI launchers](../../ulhpc-docs/docs/slurm/launchers.md#mpi).

## Storage and results

| Location | Intended project use | Important limitation |
| --- | --- | --- |
| `$HOME` | Configuration and environment artifacts | Not backed up |
| `${PROJECTHOME}/<project>` | Shared inputs and outputs, if project storage is available | Documented backup coverage is limited to the `backup` subdirectory |
| `$SCRATCH` | Temporary working data and checkpoints | Not backed up; not an archive |
| Node-local `/tmp` | Temporary small-file work, including compilation/cache activity where appropriate | Cleared after the job; not shared across nodes |

Persist completed result batches outside node-local temporary storage during long runs, and copy remaining outputs before normal job completion. Keep an independent backup of important inputs, configurations, and results.

Write separate result shards per worker or batch, then aggregate them. Avoid both a shared multi-writer CSV and excessive tiny-file traffic on shared storage.

Sources: [storage overview](../../ulhpc-docs/docs/filesystems/index.md), [backup policy](../../ulhpc-docs/docs/data/backups.md).

## Checks before production experiments

The platform choice is resolved. These are deployment checks, not additional required research directions:

- Confirm the course/project Slurm account, permitted partitions and QoS, and available project storage.
- Validate the Apptainer CPU image in a small allocation, including JIT execution and result persistence.
- Verify effective CPU affinity, runtime thread counts, and any selected memory-placement policy.
- Record image checksum, source revision, package/runtime versions, loaded host modules, CPU/GPU model, resource layout, seeds, and Slurm job/array identifiers.
- Validate any MPI or GPU extension separately before using it for reported experiments.

Source for account-specific access: [Slurm accounts](../../ulhpc-docs/docs/slurm/accounts.md).
