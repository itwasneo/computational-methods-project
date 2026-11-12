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

Use an Apptainer SIF containing the installed application, locked numerical/analysis dependencies and scientific tests. The cluster needs Apptainer, not a host Python/Numba environment. **The updated definition still requires owner build and finished-image validation.** The successful historical build recorded in [draft PR #53](https://github.com/itwasneo/computational-methods-project/pull/53) predates this workflow and does not validate current code. Account/allocation checks in #22 and the launcher in #23 remain separate.

[containers/cpu.def](../containers/cpu.def) retains the Linux/amd64 Python 3.12.16 slim-bookworm digest and [hash-pinned uv 0.10.4 bootstrap](../containers/bootstrap-requirements.txt). It installs locked `dev` dependencies (including analysis/pytest, not the interface extra), then the [verified wheel](development.md#verified-installed-builds), never an editable or rebuilt project. No Git checkout, application source tree, host environment or native cache is copied into the image. GPU/MPI runtimes are not included.

Before installation, wheel bytes/embedded metadata must match the retained receipt, staged revision, pyproject and lock hashes. After installation, shared package verification must match that build identity. `/opt/tsp/build-info.json` retains the identity, wheel checksum, definition/lock hashes, base digest, tool versions and installed package list. A revision text file or image label alone confers no authority. The final SIF checksum stays **outside** the image; byte-identical SIF rebuilds are not promised. See the [trust boundary](usage.md#eligibility-and-trust).

### Owner: build off-cluster

Use a trusted Linux x86_64 machine with Python 3.12, uv and Apptainer, network access for pinned dependencies/base, and the required local build privileges. Review the definition before `sudo`. Run only after all intended changes are committed and the checkout is clean; do not modify it during staging. This creates a new revision/time-labelled artifact directory and does not overwrite earlier builds:

```bash
(
    set -euo pipefail
    cd "$(git rev-parse --show-toplevel)"
    test "$(uname -m)" = x86_64
    test -z "$(git status --porcelain)"
    revision=$(git rev-parse --verify HEAD)
    mkdir -p artifacts/containers
    directory="$PWD/artifacts/containers/$revision-$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir "$directory"
    python3.12 tools/build_verified_wheel.py --output "$directory/wheels"
    context=$(mktemp -d)
    trap 'rm -rf -- "$context"' EXIT
    git archive "$revision" | tar -xf - -C "$context"
    mkdir -p "$context/.image-build"
    cp -R "$directory/wheels" "$context/.image-build/wheels"
    printf '%s\n' "$revision" > "$context/.image-build/source-revision"
    (
        cd "$context"
        sudo apptainer build "$directory/tsp-cpu.sif" containers/cpu.def
    ) 2>&1 | tee "$directory/build.log"
    (cd "$directory"; sha256sum tsp-cpu.sif > tsp-cpu.sif.sha256)
    printf 'Retain artifact directory: %s\n' "$directory"
)
```

Do not bypass `%test` with `--notest`. It uses disposable `/tmp` caches and outputs, even when the image filesystem is read-only. Failures propagate and temporary state is removed on exit; retain the build log, wheel/receipt and SIF/checksum outside the temporary context. A failed build is not validated—use a fresh artifact directory for another attempt. Do not commit generated artifacts or unreviewed logs.

### Owner: verify the finished SIF locally

A successful build is not a finished-image mount/persistence check. Set `artifact_dir` to the **printed directory above**, not a later Git HEAD. Run from the same committed checkout so the tiny input examples match:

```bash
(
    set -euo pipefail
    artifact_dir="/absolute/path/to/the/retained/artifact-directory"
    (cd "$artifact_dir"; sha256sum -c tsp-cpu.sif.sha256)
    inputs="$PWD/examples"
    outputs="$artifact_dir/local-validation-$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir "$outputs"
    cache=$(mktemp -d /tmp/tsp-cache.XXXXXX)
    trap 'rm -rf -- "$cache"' EXIT
    mkdir -p "$cache/tmp" "$cache/numba" "$cache/xdg" "$cache/matplotlib"
    apptainer exec --cleanenv --containall --no-mount home,cwd --pwd /opt/tsp \
        --env NUMBA_NUM_THREADS=1,OMP_NUM_THREADS=1,OPENBLAS_NUM_THREADS=1,MKL_NUM_THREADS=1 \
        --bind "$inputs:/inputs:ro" --bind "$outputs:/outputs:rw" --bind "$cache:/cache:rw" \
        "$artifact_dir/tsp-cpu.sif" /opt/tsp/.venv/bin/python -I -B /opt/tsp/check_runtime.py \
        --inputs /inputs --output /outputs/check
    test -f "$outputs/check/report.json"
    printf 'Retain host-side evidence: %s/check\n' "$outputs"
)
```

The [verifier](../containers/check_runtime.py) requires the installed isolated interpreter, one-thread limits and a nonempty effective CPU affinity. External inputs must actually be read-only. It requires the explicit scientific domain suites plus CLI/native-cache regressions to pass with no skipped checks and records their counts; Git/wheel-building, HTTP and browser tests remain host-side, not silently represented as image coverage. It then runs the installed NN CLI on the square manifest: four v2 trials, independently checked tours/cost 4, matching verified build identities, two eligible pairs, and CLI analysis/reference reports. Production kernels must create native cache files in a fresh writable cache. These tiny-run timings/ratios are **correctness evidence, not benchmark results**; no parallel solver capability is claimed.

Retain `check/report.json`, `tests.xml`, `trials.jsonl`, `analysis/` and the build log with the wheel/receipt and external SIF checksum. The pass report is written last and read back; existing output directories are refused. Partial output after a failed check is not success evidence. The verifier rechecks installed/build inputs after testing, records CPU models/affinity and thread count, and never schedules or connects elsewhere.

### Owner: transfer and allocated execution

Transfer the identified SIF/checksum together using your approved SSH configuration; verify against the **original** checksum at the destination before execution. Never overwrite an image referenced by results or regenerate a checksum from a damaged copy. Keep an independent artifact backup and confirm storage visibility/permissions from the intended compute nodes. No repository script performs the transfer.

After local verification, repeat the finished-image check within an authorized allocation on each target platform you intend to claim as validated. Load the available `tools/Apptainer` module inside the allocation, record its actual version, and launch through Slurm with matching thread/resource limits. If recording the job ID, pass `SLURM_JOB_ID` explicitly through the clean container environment. Account/partition selection and a reusable launcher are separate owner-reviewed work, not supplied here.

Use deliberate `/inputs:ro`, `/outputs:rw` and job-specific `/cache:rw` binds. Never overlay `/opt/tsp`, bind a host venv into the image, install packages at runtime, or reintroduce Python/library search paths. Native caches are CPU/image-specific and not portable build artifacts. A local result does not certify Aion/Iris; one cluster does not certify the other. Containerization does not replace [placement controls](#cpu-placement-and-numa), and GPU/MPI need their separate checks below.

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
