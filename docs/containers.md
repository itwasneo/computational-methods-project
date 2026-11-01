# CPU image and repository workflow

**Status:** image definition and verification procedure prepared; no SIF has been built or executed as part of this change. Cluster access/storage are owner-confirmed, not independently tested. This is preparation for task #21, not evidence that its runtime checks or task #22 are complete. CI task #4 also remains outstanding.

**Execution boundary:** cluster connections, transfers, allocations, and job execution are performed by the project owner. Coding agents must not connect to ULHPC or submit/run jobs there. The commands below are instructions for the owner, not automated remote execution.

## Where the repository and artifacts belong

| Location | Role |
| --- | --- |
| GitHub | Canonical versioned source, definitions, lockfile, tests, manifests, and PR history |
| Local development machine | Feature branches, `uv` development environment, tests, and code review |
| Authorized off-cluster Linux x86_64 build machine | Build a SIF from an exact committed source snapshot |
| Cluster project workspace | Versioned SIF/checksum pairs, inputs, run manifests, logs, and result shards |
| Optional cluster checkout | Launch instructions and manifests at a recorded commit; not a mutable replacement for the code inside a measured image |
| Job-specific temporary/cache directory | Writable Python/Numba temporary files and caches; separate from archived results |

A possible cluster workspace is:

```text
tsp-workspace/
    images/                 # immutable, revision-labelled SIFs and checksums
    inputs/                 # versioned datasets; mounted read-only
    runs/<run-id>/           # effective manifest, logs, results, validation reports
    repo/                   # optional checkout for instructions/manifests
```

Develop locally and synchronize source through Git, not by copying a changing working directory over the cluster checkout. Use a fresh checkout/worktree at a known commit when preparing a run. Do not copy the local `.venv` to the cluster: the image supplies its own interpreter and installed packages.

The packaged application is installed **non-editably** inside the SIF. Production runs must not bind a host checkout over `/opt/tsp`, replace its virtual environment, or install packages at runtime. Rebuild for a new source revision. A separate development workflow may use source mounts later, but those runs must not be presented as execution of the unchanged image.

## Image contents and pins

[containers/cpu.def](../containers/cpu.def) uses:

- The Linux/amd64 manifest for `python:3.12.16-slim-bookworm`, pinned by its SHA-256 digest in the definition, not merely by its tag.
- uv `0.10.4`, installed from the hash-checked x86_64 wheel in [bootstrap-requirements.txt](../containers/bootstrap-requirements.txt).
- The committed [uv.lock](../uv.lock), installed with `--locked --no-editable` and the explicit `dev` group so the image can run its packaged tests. Analysis dependencies are not included.
- Only explicitly listed source/build/test files. No `.git`, local environment, credentials, generated results, or host JIT cache is copied into the image.
- Conservative one-thread defaults for Numba, OpenMP, OpenBLAS, and MKL. Later multicore runs must explicitly set thread counts to match their allocation.

No host Python, uv, NumPy, or Numba module is needed to run the SIF. This definition does not provide GPU or MPI support. It does not install unpinned OS packages with `apt`; runtime availability of all required libraries must still be checked by building and executing it.

`/opt/tsp/build-info.json` records the source commit, base reference/digest, definition and lockfile hashes, interpreter version, uv version, architecture, and installed package versions. The complete SIF checksum is computed **after** building and stored alongside the image; it cannot meaningfully be embedded as a checksum of itself.

Input versions are pinned, but byte-for-byte identical SIF rebuilds are not promised: build timestamps and image metadata can differ. Identify the actual artifact used in every reported experiment by its checksum.

## Owner-operated local build

The supplied cluster guidance describes building off-cluster with the required privileges, then transferring the artifact. Do not assume that cluster access or partition permissions grant image-build privileges. Do not run this build on a cluster login or compute node.

Use a trusted Linux x86_64 builder with Apptainer and network access for the pinned base and Python packages. The recipe was prepared with local Apptainer 1.5.0 command syntax; the image itself is not yet build-tested. Review the definition before a privileged build. If your local installation uses another approved build mechanism, adapt that local invocation without changing the source/pinning rules.

After committing the definition and all intended source changes, run the following in **Bash on the off-cluster builder**:

```bash
(
    set -euo pipefail
    cd "$(git rev-parse --show-toplevel)"
    test "$(uname -m)" = x86_64
    test -z "$(git status --porcelain)" || {
        echo "Commit changes first; build only from an identified clean snapshot." >&2
        exit 1
    }
    revision=$(git rev-parse --verify HEAD)
    context=$(mktemp -d)
    trap 'rm -rf -- "$context"' EXIT
    git archive "$revision" | tar -xf - -C "$context"
    mkdir -p "$context/.image-build"
    printf '%s\n' "$revision" > "$context/.image-build/source-revision"

    directory="$PWD/artifacts/containers"
    name="tsp-cpu-${revision}.sif"
    mkdir -p "$directory"
    test ! -e "$directory/$name" || {
        echo "Image already exists; do not overwrite an identified artifact." >&2
        exit 1
    }
    (
        cd "$context"
        sudo apptainer build "$directory/$name" containers/cpu.def
    )
    (
        cd "$directory"
        sha256sum "$name" > "$name.sha256"
    )
    printf 'Image: %s/%s\nChecksum: %s/%s.sha256\n' "$directory" "$name" "$directory" "$name"
)
```

The temporary archive avoids including uncommitted, ignored, or secret local files. `source-revision` is generated from the same commit being archived, not guessed from a filename. Relative `%files` paths are evaluated from that archive directory.

Do not bypass `%test` with `--notest`: it runs [check_runtime.py](../containers/check_runtime.py), including the packaged pytest suite, serial and parallel Numba compilation, cache writes, and a temporary report. Test caches/reports are removed before finishing the image so build-host native JIT artifacts are not shipped as runtime caches. A failed check means the image is not validated; retain the build log and investigate rather than suppressing the failure.

Keep the SIF and checksum in the ignored `artifacts/` directory locally. Transfer them yourself using your approved method, then verify the checksum from the destination image directory:

```bash
sha256sum -c tsp-cpu-<source-commit>.sif.sha256
```

No repository script performs a cluster connection, transfer, or job submission.

## Runtime mounts and environment

Use explicit mounts with `--cleanenv --containall --no-mount home,cwd` and a fixed container working directory. Do not rely on a host home directory, current checkout, or user-site Python installation being visible.

| Container path | Host role | Mode |
| --- | --- | --- |
| `/inputs` | Selected immutable input directory | Read-only |
| `/outputs` | A new run-specific result directory | Read/write |
| `/cache` | A job-specific writable directory containing `tmp`, `numba`, and `xdg` subdirectories | Read/write |

The entry point uses isolated Python (`-I`) and disables bytecode writes (`-B`). Environment settings also disable Python user packages and clear `PYTHONPATH`, `PYTHONHOME`, and `VIRTUAL_ENV`. Do not inject those variables or host library paths back into the container.

NUMBA caches are generated on the executing CPU. Do not share one warmed cache across different images/CPU architectures or assume that compilation on the build machine validates another CPU. Cold-start/JIT time and warmed search time remain separate measurements. Runtime resource limits and affinity still come from Slurm, not the image.

## Owner-operated cluster verification

First confirm the actual Apptainer module name/version and `apptainer --version` on each target system. Load modules only within an allocation. The documented module name is `tools/Apptainer`; use the actual version available to your account.

The example below assumes **you have already obtained a one-node CPU allocation and are in its compute-node shell**. No allocation/submission command is included. Replace the placeholder paths and run it yourself, once on each cluster you intend to claim as validated:

```bash
module load tools/Apptainer
apptainer --version

image="/absolute/path/to/images/tsp-cpu-<source-commit>.sif"
inputs="/absolute/path/to/inputs"
outputs="/absolute/path/to/runs/<new-validation-run>"
cache=$(mktemp -d /tmp/tsp-cache.XXXXXX)
mkdir -p "$outputs" "$cache/tmp" "$cache/numba" "$cache/xdg"

srun --nodes=1 --ntasks=1 --cpus-per-task=1 --cpu-bind=cores \
    apptainer exec --cleanenv --containall --no-mount home,cwd --pwd /opt/tsp \
    --env NUMBA_NUM_THREADS=1,OMP_NUM_THREADS=1,OPENBLAS_NUM_THREADS=1,MKL_NUM_THREADS=1 \
    --env SLURM_JOB_ID="$SLURM_JOB_ID" \
    --bind "$inputs:/inputs:ro" \
    --bind "$outputs:/outputs:rw" \
    --bind "$cache:/cache:rw" \
    "$image" /opt/tsp/.venv/bin/python -I -B /opt/tsp/check_runtime.py \
    --output /outputs/container-check.json
```

This is a small correctness/compatibility check, not a benchmark campaign. The parallel smoke kernel uses only one worker here; it verifies the available threading backend, not multicore scaling. The current application has no solver command yet.

The verifier checks compilation/execution, writable Numba cache, the package tests, and report write/read-back. It records CPU model(s), effective CPU affinity, thread count/threading layer, package versions, image build metadata, and the explicitly passed job ID. It does not inspect accounts, request resources, or connect elsewhere. The input mount is reserved for future workload inputs; this smoke test uses an internally generated tiny array and does not validate a dataset loader.

After the command completes, inspect the report from the **host output directory** and retain it alongside the SIF checksum, loaded module/version, and final job/step exit status. A report left only in node-local temporary storage is not persistent evidence. Report paths are created exclusively; choose a new output directory for another validation run.

A successful local build is not a successful Aion/Iris test, and one cluster's result does not establish the other's compatibility. Until the owner supplies these results, runtime validation remains pending.
