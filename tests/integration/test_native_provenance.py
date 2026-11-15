"""Subprocess native-mode regressions, separate from performance evidence."""

import json
import os
import subprocess
import sys

import pytest

PROGRAM = r'''
import json
from pathlib import Path
from tsp_hpc.problem.instances import fixture, save_instance
from tsp_hpc.experiments.manifests import Manifest
from tsp_hpc.experiments.runner import run_manifest
from tsp_hpc.analysis import read_shards
record = fixture("square")
source, output = Path("input.json"), Path("runs.jsonl")
save_instance(record, source)
manifest = Manifest({"schema_version": 1, "instances": [record.descriptor], "experiments": [{
    "instance_id": record.instance.instance_id, "algorithm": "nn", "solver_seed": 7,
    "budget": {"start_cities": [2, 0]}, "repetitions": [0],
    "executions": [{"backend": "serial", "implementation": "remaining-array-v1",
                    "distance_mode": MODE, "resources": {"profile": "test-only"}}]}]})
summary = run_manifest(manifest, instance_paths={record.instance.instance_id: source}, output_path=output)
trial = json.loads(output.read_text().splitlines()[0])
row = read_shards([output]).trials.iloc[0]
print(json.dumps({"counts": summary["counts"], "error": trial["error"], "result": trial["result"],
                  "compiler": trial["runtime"]["compiler"], "native": trial["native_execution"],
                  "environment": trial["runtime"]["environment"], "execution_kind": row["execution_kind"],
                  "context": row["runtime_context_id"], "search_id": row["search_id"],
                  "execution_id": row["execution_id"]}))
'''


def probe(directory, settings, mode="on_demand"):
    directory.mkdir()
    # Isolate compiler experiments/cache configuration from caller settings.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("NUMBA_")}
    environment.update(NUMBA_CPU_NAME="generic", NUMBA_CPU_FEATURES="", NUMBA_CACHE_DIR=str(directory / "cache"),
                       PYTHONWARNINGS="error", **settings)
    process = subprocess.run([sys.executable, "-B", "-c", "MODE = " + repr(mode) + "\n" + PROGRAM],
                             cwd=directory, env=environment, capture_output=True, text=True, timeout=90)
    assert process.returncode == 0, process.stderr
    assert "Traceback" not in process.stderr
    return json.loads(process.stdout)


@pytest.mark.parametrize("mode", ["dense", "on_demand"])
def test_disabled_jit_is_an_explicit_failed_trial_not_a_native_result(tmp_path, mode):
    result = probe(tmp_path / mode, {"NUMBA_DISABLE_JIT": "1"}, mode)
    assert result["counts"] == {"completed": 0, "failed": 1}
    assert result["error"]["type"] == "NativeExecutionError"
    assert result["error"]["phase"] == "distance_preparation"
    assert "NUMBA_DISABLE_JIT" in result["error"]["message"]
    assert result["result"] is None
    assert result["native"] == {"required": True, "verified": False, "dispatchers": []}
    assert result["compiler"]["config"]["disable_jit"] is True
    assert result["environment"]["NUMBA_DISABLE_JIT"] == "1"
    assert result["execution_kind"] == "native-required"


def test_effective_compiler_changes_context_not_scientific_ids(tmp_path):
    normal = probe(tmp_path / "normal", {})
    checked = probe(tmp_path / "checked", {"NUMBA_BOUNDSCHECK": "1"})
    maximum = probe(tmp_path / "maximum", {"NUMBA_OPT": "max"})
    for result in (normal, checked, maximum):
        assert result["counts"] == {"completed": 1, "failed": 0}
        assert result["native"]["verified"] is True
        assert result["execution_kind"] == "native-required"
        dispatcher = result["native"]["dispatchers"][0]
        assert ".remaining_array." in dispatcher["name"]
        assert dispatcher["nopython_signatures"]
        assert dispatcher["options"]["fastmath"] is dispatcher["options"]["parallel"] is False
        assert result["result"]["best_cost"] == 4
        assert result["compiler"]["config"]["disable_jit"] is False
    assert normal["compiler"]["config"]["optimization"] == "3"
    assert checked["compiler"]["config"]["optimization"] == "3"
    assert checked["compiler"]["config"]["boundscheck"] is True
    assert maximum["compiler"]["config"]["optimization"] == "max"
    assert len({result["context"] for result in (normal, checked, maximum)}) == 3
    assert len({result["search_id"] for result in (normal, checked, maximum)}) == 1
    assert len({result["execution_id"] for result in (normal, checked, maximum)}) == 1
