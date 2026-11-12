"""Owner-run image checks; no cluster connections, allocations or submissions.

The pre-install wheel check uses only the stdlib. Later checks require the
installed package and reuse its provenance, runner, objective and analysis APIs.
"""

import argparse
import hashlib
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
from xml.etree import ElementTree
import zipfile

ROOT = Path("/opt/tsp")
# Host-only Git/build tests and optional HTTP/browser tests stay outside the image
# validation selection. This is not a claim that the full repository suite ran.
PACKAGED_TESTS = (
    "tests/problem", "tests/reproducibility", "tests/solvers", "tests/experiments", "tests/analysis",
    "tests/integration/test_cli.py", "tests/integration/test_native_runtime.py",
)
THREAD_VARIABLES = ("NUMBA_NUM_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def check_wheel(root):
    """Match a trusted producer's receipt to the archive and staged source inputs.

    Receipt/source-revision are consistency inputs, not independent attestations.
    Full installed-file/identity verification follows installation.
    """
    wheels = list((root / "wheels").glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("stage exactly one verified wheel")
    wheel = wheels[0]
    receipt = read_json(wheel.with_name(wheel.name + ".build.json"))
    if (type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1
            or receipt["wheel_filename"] != wheel.name or receipt["wheel_sha256"] != digest(wheel)):
        raise ValueError("wheel receipt/checksum mismatch")
    with zipfile.ZipFile(wheel) as archive:
        embedded = json.loads(archive.read("tsp_hpc/_build_provenance.json"))
    if embedded != receipt["build"]:
        raise ValueError("wheel metadata differs from retained receipt")
    revision = (root / "source-revision").read_text().strip()
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision) is None:
        raise ValueError("invalid staged source revision")
    if (embedded["source"] != {"revision": revision, "dirty": False} or embedded["source"]["dirty"] is not False
            or embedded["pyproject_sha256"] != digest(root / "pyproject.toml")
            or embedded["lock_sha256"] != digest(root / "uv.lock")):
        raise ValueError("wheel and staged source inputs disagree")
    return wheel, receipt


def build_information(root):
    from tsp_hpc.experiments.runner import runtime_provenance

    wheel, receipt = check_wheel(root)
    runtime = runtime_provenance()
    identity = {k: v for k, v in receipt["build"].items() if k != "files"}
    if (runtime["build"]["method"] != "package-sha256-v1" or runtime["build"]["status"] != "verified"
            or runtime["build"]["identity"] != identity or runtime["source"] != identity["source"]):
        raise ValueError("executing package is not the staged verified build")
    definition = root / "container.def"
    base, = [line.removeprefix("From: ").strip() for line in definition.read_text().splitlines()
             if line.startswith("From: ")]
    return {
        "schema_version": 1, "package_build": identity,
        "wheel_filename": wheel.name, "wheel_sha256": receipt["wheel_sha256"],
        "base_image": base, "definition_sha256": digest(definition),
        "lock_sha256": digest(root / "uv.lock"), "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "uv_version": subprocess.check_output(["uv", "--version"], text=True).strip(),
        "packages": sorted([{"name": dist.metadata["Name"], "version": dist.version}
                            for dist in distributions()], key=lambda item: item["name"].lower()),
    }


def scientific_workflow(root, inputs, output, build_info, environment):
    """Actual installed CLI round trip; no reconstructed browser/scientific logic."""
    import numpy as np
    from tsp_hpc.analysis import read_shards, paired_comparisons
    from tsp_hpc.experiments.manifests import Manifest
    from tsp_hpc.problem.instances import load_instance
    from tsp_hpc.problem.objective import validate_solution

    manifest_path = inputs / "manifests/nn-fixed-work.json"
    artifact_path = inputs / "instances/square.json"
    reference_path = inputs / "references/square.json"
    input_hashes = {str(path.relative_to(inputs)): digest(path)
                    for path in (manifest_path, artifact_path, reference_path)}
    manifest = Manifest.from_file(manifest_path)
    instance = load_instance(artifact_path).instance
    shard = output / "trials.jsonl"
    command = [sys.executable, "-I", "-B", "-m", "tsp_hpc"]
    subprocess.run([*command, "run", str(manifest_path), "--instance", f"{instance.instance_id}={artifact_path}",
                    "--output", str(shard)], cwd=root, env=environment, check=True)
    analysis = read_shards([shard], expected_manifest=manifest)
    if analysis.diagnostics or len(analysis.trials) != 4 or not analysis.trials["usable"].all():
        raise ValueError("expected four usable square trials with no diagnostics")
    for attempt in analysis.trials["attempt_id"]:
        trial = analysis.trial(attempt)
        if (trial["schema_version"] != 2 or trial["runtime"]["build"]["identity"] != build_info["package_build"]
                or trial["runtime"]["build"]["status"] != "verified" or trial["result"]["best_cost"] != 4.0):
            raise ValueError("trial build identity or square cost mismatch")
        validate_solution(instance, np.array(trial["result"]["best_tour"], dtype=np.int64), trial["result"]["best_cost"])
    pairs = paired_comparisons(analysis, baseline_implementation="visited-scan-v1")
    if len(pairs) != 2 or pairs["reason"].notna().any():
        raise ValueError("matching-build comparisons are not eligible")
    subprocess.run([*command, "analyze", str(shard), "--manifest", str(manifest_path),
                    "--references", str(reference_path), "--baseline", "visited-scan-v1",
                    "--output", str(output / "analysis")], cwd=root, env=environment, check=True)
    report = read_json(output / "analysis/report.json")
    if report["has_issues"] or report["comparison"]["blocked_pairs"] != 0:
        raise ValueError("CLI analysis did not preserve comparison eligibility")
    if input_hashes != {name: digest(inputs / name) for name in input_hashes}:
        raise ValueError("smoke-test inputs changed")
    return {"trials": 4, "eligible_pairs": 2, "best_cost": 4.0, "input_sha256": input_hashes}


def verify(root, output, inputs=None):
    import numba

    if Path(sys.prefix).resolve() != (root / ".venv").resolve() or sys.flags.isolated != 1:
        raise RuntimeError("use the image's isolated .venv Python (-I)")
    if any(os.environ.get(key) != "1" for key in THREAD_VARIABLES):
        raise RuntimeError("this serial correctness check requires all numerical thread limits to be 1")
    cpus = sorted(os.sched_getaffinity(0))
    if not cpus or numba.get_num_threads() != 1:
        raise RuntimeError("unexpected CPU affinity/Numba thread count")
    build_info = read_json(root / "build-info.json")
    if build_information(root) != build_info:
        raise ValueError("image build information no longer matches installed/staged artifacts")
    # External inputs must actually be mounted read-only, not merely labelled so.
    if inputs is not None and not os.statvfs(inputs).f_flag & os.ST_RDONLY:
        raise ValueError("external smoke inputs must be mounted read-only")
    inputs = inputs or root / "examples"
    output.mkdir()  # Exclusive directory; failures retain partial evidence, not a pass report.
    for variable in ("TMPDIR", "XDG_CACHE_HOME", "MPLCONFIGDIR", "NUMBA_CACHE_DIR"):
        directory = Path(os.environ[variable])
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory) as stream:
            stream.write(b"writable cache check\n")
            stream.flush()
    # Fresh per-check cache: never mistake a pre-existing native cache for a write.
    cache = Path(tempfile.mkdtemp(prefix="image-check-", dir=os.environ["NUMBA_CACHE_DIR"]))
    environment = {**os.environ, "NUMBA_CACHE_DIR": str(cache)}
    junit = output / "tests.xml"
    subprocess.run([sys.executable, "-I", "-B", "-m", "pytest", "-W", "error", "-p", "no:cacheprovider",
                    "--basetemp", str(output / "pytest-tmp"), "--junitxml", str(junit),
                    *(str(root / path) for path in PACKAGED_TESTS)], cwd=root, env=environment, check=True)
    suites = list(ElementTree.parse(junit).getroot().iter("testsuite"))
    counts = {key: sum(int(suite.get(key, "0")) for suite in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    if counts["tests"] == 0 or any(counts[key] for key in ("failures", "errors", "skipped")):
        raise RuntimeError("packaged scientific tests must pass without skipped checks")
    workflow = scientific_workflow(root, inputs, output, build_info, environment)
    if not any(cache.rglob("*.nbc")):
        raise RuntimeError("no native Numba cache artifacts were written")
    if build_information(root) != build_info:
        raise ValueError("installed artifacts changed during verification")
    report = {
        "status": "passed", "scope": "container correctness, not performance or hardware equivalence",
        "build": build_info, "workflow": workflow,
        "tests": {"selection": list(PACKAGED_TESTS), **counts},
        "runtime": {"allowed_cpus": cpus, "numba_threads": numba.get_num_threads(),
                    "cpu_models": sorted({line.split(":", 1)[1].strip()
                                          for line in Path("/proc/cpuinfo").read_text().splitlines()
                                          if line.startswith("model name") and ":" in line}),
                    "numba_cache_dir": str(cache), "slurm_job_id": os.environ.get("SLURM_JOB_ID")},
    }
    write_json(output / "report.json", report)
    if read_json(output / "report.json") != report:
        raise RuntimeError("verification report did not persist")
    print(f"Verification passed; retain {output} and the external SIF checksum")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-wheel", action="store_true", help="pre-install archive/input consistency check")
    mode.add_argument("--record-build", action="store_true", help="record verified installed build information")
    mode.add_argument("--output", type=Path, help="new directory for tests, trials, analysis and pass report")
    parser.add_argument("--inputs", type=Path, help="read-only mounted smoke examples; default: packaged examples")
    args = parser.parse_args()
    if args.inputs is not None and args.output is None:
        parser.error("--inputs requires --output")
    if args.check_wheel:
        print(check_wheel(ROOT)[0])
    elif args.record_build:
        if Path(sys.prefix) != ROOT / ".venv" or sys.flags.isolated != 1:
            raise RuntimeError("record build information with the installed isolated interpreter")
        write_json(ROOT / "build-info.json", build_information(ROOT))
    else:
        verify(ROOT, args.output, args.inputs)


if __name__ == "__main__":
    main()
