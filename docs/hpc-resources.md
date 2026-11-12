# HPC resources and execution

This is the operating reference for University of Luxembourg HPC experiments, not evidence that a particular account or image is validated. Begin with CPU execution on one node and expand only when measurements justify it. See the [README](../README.md#current-capabilities) for current deployment status and [development guide](development.md) for the application stack.

**The project owner performs cluster connections, transfers, allocations and jobs.** Coding-agent and CI workflows do not operate the cluster.

## Resource strategy

This document defines how the project will use resources, not a fixed inventory of the cluster. Confirm the team's access, available hardware, and allocation limits before running experiments.

| Resource | Planned use | Allocation approach |
| --- | --- | --- |
| **Aion CPU nodes** | Preferred target for serial baselines, multicore experiments, and possible distributed execution | Start on one node with explicit process, thread, memory, and walltime requests |
| **Iris CPU nodes** | Alternative CPU target or a separately labelled architecture comparison | Use a consistent CPU model within each scaling experiment |
| **Iris GPU resources** | Optional acceleration of one profiled workload | Start with one GPU after confirming access and software compatibility |

Select exact resource profiles during deployment rather than hardcoding node specifications into the solvers. Record the hardware actually allocated to each run, including CPU model, core allocation, memory, and any GPU model and device memory.

For controlled scaling experiments, keep the CPU architecture fixed. Do not present a single-core Iris versus multicore Aion comparison as pure parallel speedup.

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

Batch short trials to avoid excessive allocation and job-step requests. Use pilot runtimes to choose batch sizes and concurrency limits, and keep submission rates within the limits applicable to the team's account.

Array elements share their requested scheduler resources. Group experiments by resource profile and use a concurrency cap. Array execution order must not affect experiment identifiers or random seeds. Native Slurm job dependencies can connect preparation, execution, and aggregation stages without a custom scheduling service.

### Operational rules

- Use login nodes for preparation, file management, and submission, not benchmark execution.
- Load required host modules on compute nodes inside an allocation.
- Use interactive allocations for development, testing, and debugging; submit production experiments as batch jobs.
- Initialize launcher scripts with a login shell, such as `#!/bin/bash --login`, before loading host modules.
- Explicitly request resources and set process/thread limits accordingly. Prevent nested parallelism from oversubscribing the allocation.
- Monitor utilization and use measured runtimes and memory consumption to refine subsequent requests.
- Keep credentials and private account configuration out of Git; supply deployment-specific values separately.
- Separate scheduler waiting time from application execution time in performance reports.

## Reproducible container environment

Use an Apptainer SIF to package Python, numerical dependencies and the application rather than depending on a particular host Python/Numba module release. The cluster host needs the Apptainer runtime, not uv or a duplicate project environment.

1. Keep the image definition, dependency lock and application revision in Git. Pin the base image and dependencies rather than floating tags; revalidate dependency changes locally.
2. The owner builds off-cluster with the required privileges, validates the image, then transfers it to the workspace. Do not assume an in-cluster build facility.
3. Use a fixed image for recorded campaigns; retain its checksum and source revision. Keep measured runs on the packaged revision without source or local-venv overlays.
4. Keep shared inputs, persistent outputs and writable temporary/cache directories outside the immutable image through deliberate mounts. Validate permissions and persistence, not just imports.
5. Inside an allocation, load `tools/Apptainer` and launch through Slurm. Control inherited settings and Python user-package visibility; the default home mount can override container configuration, so use `--no-mount home` where appropriate and expose needed data paths explicitly.

For package provenance, build and retain the [verified wheel and receipt](development.md#verified-installed-builds) from the committed revision before assembling the image. The image must install that wheel into its locked dependency environment, not rebuild a plain wheel or overlay source afterward. Retain its build ID/wheel checksum in image build information; compute the final SIF checksum outside the finished image. A revision text file, image label or launcher environment variable alone cannot establish the executing package's source identity.

**Draft PR #53 still needs this handoff integrated and owner validation.** Its separate `/opt/tsp/build-info.json` is not currently a verified package binding; this wheel work does not certify that image or operate the cluster. The owner must check v2 trial build status/identity inside the finished image before campaign use.

Validate packaged tests, actual JIT execution/cache writes and result persistence before a campaign. Compiled artifacts must suit the target CPU; a development-machine-specific binary is not automatically portable. Match process/thread limits to the allocation and prevent nested oversubscription. Containerization does not replace [placement controls](#cpu-placement-and-numa). GPU and MPI images require their separate compatibility checks below.

## CPU placement and NUMA

Inspect the allocated node's CPU and NUMA topology before choosing process and thread placement. Do not assume that one large thread team is always preferable to several smaller teams.

Possible placement experiments include:

- Comparing compact and spread placement while keeping the logical workload fixed.
- Comparing larger thread teams spanning NUMA domains with smaller teams within domains.
- Changing how distance data is initialized, shared, or replicated, while reporting the resulting memory footprint.

Distinguish throughput-oriented packing from controlled solver-scaling experiments: colocated runs can contend for shared resources. Record placement and concurrency alongside timing results.

### Containers do not remove placement controls

Launch the application through `srun` with the intended binding and distribution options, then `apptainer exec`. The container supplies the application environment; it does not replace the host topology or grant resources outside the allocation.

Keep three controls distinct:

1. **Process affinity:** the cores on which a process may run.
2. **Thread placement:** how its worker threads use those cores.
3. **Memory placement:** where the data resides and the memory policy used when it is allocated and initialized.

CPU pinning alone does not establish memory locality. Validate effective placement from inside the container using affinity reports, such as `taskset` or `/proc/self/status`. Check that the required memory-policy operations are available before treating a NUMA configuration as established.

## Optional GPU and MPI execution

### GPU

Start with one allocated GPU and explicitly request the CPU cores and host memory needed to feed it. Enable Apptainer's NVIDIA integration with `--nv` and validate the actual host-driver, device, and container-runtime combination before choosing CUDA-dependent package versions.

Accelerate one demonstrated bottleneck rather than porting all three solvers. Keep reusable data on the device where practical, and account for transfers and synchronization. Report both kernel-only and end-to-end measurements against an optimized CPU baseline with its resource allocation stated.

### MPI

Use mpi4py only if a distributed investigation is selected. A containerized MPI installation must be compatible with the cluster launcher and communication environment; successful Python imports are not sufficient validation.

Before TSP scaling runs, launch a small multi-node correctness and communication test through Slurm. Increase node counts only after validating single-node behavior and the distributed runtime. Keep logical islands or chains, seeds, and search budgets fixed when measuring execution scaling.

## Storage and results

| Location | Intended project use | Handling rule |
| --- | --- | --- |
| `$HOME` | Configuration and environment artifacts | Keep usage modest and maintain an independent copy of important files |
| `${PROJECTHOME}/<project>` | Shared inputs, images, and outputs, if project storage is available | Confirm team permissions, quotas, and backup arrangements before use |
| `$SCRATCH` | Temporary working data and checkpoints | Treat as disposable working storage, not an archive |
| Node-local `/tmp` | Temporary small-file work, including compilation/cache activity where appropriate | Do not assume persistence after job completion or visibility from other nodes |

Persist completed result batches outside node-local temporary storage during long runs, and copy remaining outputs before normal job completion. Do not rely on working directories being backed up. Keep an independent backup of important inputs, configurations, and results.

Write separate result shards per worker or batch, then aggregate them; JSON Lines is a suitable initial raw format. Avoid both a shared multi-writer CSV and excessive tiny-file traffic on shared storage. Keep generated results and container images out of Git; retain the manifests, build definitions, checksums, and instructions needed to reproduce them.

## Checks before production experiments

The platform choice is resolved. These are deployment checks, not additional required research directions:

- Confirm the course/project Slurm account, permitted partitions and QoS, and available project storage.
- Validate the Apptainer CPU image in a small allocation, including JIT execution and result persistence.
- Verify effective CPU affinity, runtime thread counts, and any selected memory-placement policy.
- Record image checksum, source revision, package/runtime versions, loaded host modules, CPU/GPU model, resource layout, seeds, and Slurm job/array identifiers.
- Validate any MPI or GPU extension separately before using it for reported experiments.
