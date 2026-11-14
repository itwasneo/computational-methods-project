"""Compiler/native observations affect context, never scientific identities."""

import json

import pytest

from tsp_hpc.analysis import paired_comparisons, read_shards, summary_table
from tsp_hpc.analysis.core import ShardValidationError
from tsp_hpc.experiments.manifests import Manifest
from tsp_hpc.experiments.runner import run_manifest


@pytest.fixture
def native_rows(serial_campaign):
    _, manifest, bindings, output = serial_campaign("nn", history=False)
    document = manifest.to_dict()
    execution = document["experiments"][0]["executions"][0]
    document["experiments"][0]["executions"] = [{**execution, "implementation": implementation}
        for implementation in ("visited-scan-v1", "remaining-array-v1")]
    run_manifest(Manifest(document), instance_paths=bindings, output_path=output)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    for trial in rows[:-1]:
        # Synthetic source eligibility, not benchmark/attestation evidence.
        trial["runtime"]["source"] = {"revision": "a" * 40, "dirty": False}
    return rows, output


def test_failed_verification_does_not_drop_failures_from_the_workload_denominator(serial_campaign, monkeypatch):
    from tsp_hpc.solvers.nn import solver as nn
    _, manifest, bindings, output = serial_campaign("nn", history=False, repetitions=[0, 1])
    original = nn.warmup_nearest_neighbour
    calls = []
    def warm(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("test-only warm-up failure")
        return original(**kwargs)
    monkeypatch.setattr(nn, "warmup_nearest_neighbour", warm)
    run_manifest(manifest, instance_paths=bindings, output_path=output)
    data = read_shards([output])
    assert data.trials["native_verified"].tolist() == [False, True]
    summary = summary_table(data)
    assert len(summary) == 1
    assert summary.iloc[0]["attempt_count"] == 2
    assert summary.iloc[0]["failed_count"] == summary.iloc[0]["usable_count"] == 1


def save(rows, output):
    output.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return read_shards([output])


def test_changed_effective_config_blocks_pairing_and_old_v3_stays_unverified(native_rows):
    rows, output = native_rows
    data = save(rows, output)
    assert data.trials["execution_kind"].tolist() == ["native-required"] * 2
    assert data.trials["native_verified"].all()
    assert paired_comparisons(data, baseline_implementation="visited-scan-v1")["reason"].isna().all()
    identifiers = data.trials["search_id"].tolist()
    rows[1]["runtime"]["compiler"]["config"]["boundscheck"] = True
    changed = save(rows, output)
    assert changed.trials["search_id"].tolist() == identifiers
    assert paired_comparisons(changed, baseline_implementation="visited-scan-v1")["reason"].tolist() == ["incompatible_execution_context"]
    rows[1]["runtime"].pop("compiler")
    rows[1].pop("native_execution")
    legacy = save(rows, output)
    assert legacy.trials["execution_kind"].tolist() == ["native-required", "legacy-unobserved"]
    assert paired_comparisons(legacy, baseline_implementation="visited-scan-v1")["reason"].tolist() == ["incompatible_execution_context"]


@pytest.mark.parametrize("fault", ["missing-native", "missing-compiler", "null-native", "disabled-jit",
                                  "signatures", "fastmath", "verification", "unverified", "compiler-container"])
def test_contradictory_native_claims_are_structured_artifact_errors(native_rows, fault):
    rows, output = native_rows
    trial = rows[0]
    if fault == "missing-native": trial.pop("native_execution")
    elif fault == "missing-compiler": trial["runtime"].pop("compiler")
    elif fault == "null-native": trial["native_execution"] = None
    elif fault == "disabled-jit": trial["runtime"]["compiler"]["config"]["disable_jit"] = True
    elif fault == "signatures": trial["native_execution"]["dispatchers"][0]["nopython_signatures"] = []
    elif fault == "fastmath": trial["native_execution"]["dispatchers"][0]["options"]["fastmath"] = True
    elif fault == "verification": trial["native_execution"]["verified"] = False
    elif fault == "unverified":
        trial["native_execution"].update(verified=False, dispatchers=[])
    elif fault == "compiler-container": trial["runtime"]["compiler"] = []
    with pytest.raises(ShardValidationError) as caught:
        save(rows, output)
    assert caught.value.line == 1
