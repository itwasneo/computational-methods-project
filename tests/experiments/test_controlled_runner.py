"""Trusted trial-boundary control preserves ordinary scientific records/timing."""

import json

import pytest

from tsp_hpc.analysis import read_shards
from tsp_hpc.experiments.runner import run_manifest


def test_trial_boundary_stop_retains_durable_prefix_without_completed_footer(serial_campaign):
    _, manifest, bindings, output = serial_campaign("nn", repetitions=[0, 1, 2])
    observed = []
    def checkpoint():
        if observed:
            raise InterruptedError("stop after completed trial")
    def on_trial(attempt, counts):
        records = output.read_text().splitlines()
        assert json.loads(records[-1])["attempt_id"] == attempt
        observed.append((attempt, dict(counts)))
        counts["failed"] = 123  # Detached notification, never mutable runner state.
    with pytest.raises(InterruptedError):
        run_manifest(manifest, instance_paths=bindings, output_path=output, campaign_id="a" * 32,
                     checkpoint=checkpoint, on_trial=on_trial)
    row, = [json.loads(line) for line in output.read_text().splitlines()]
    assert row["record_type"] == "trial" and row["campaign_id"] == "a" * 32
    assert observed == [(row["attempt_id"], {"completed": 1, "failed": 0})]
    data = read_shards([output], expected_manifest=manifest)
    assert len(data.trials) == 1
    assert not data.trials["campaign_complete"].any()
    assert not data.trials["usable"].any()
    assert {item["code"] for item in data.diagnostics} == {"incomplete_campaign", "missing_executions"}


def test_last_trial_notification_does_not_cancel_already_finished_work(serial_campaign):
    _, manifest, bindings, output = serial_campaign("nn")
    stop = []
    def checkpoint():
        if stop:
            raise InterruptedError
    def observed(attempt, counts):
        stop.append(attempt)
    summary = run_manifest(manifest, instance_paths=bindings, output_path=output,
                           checkpoint=checkpoint, on_trial=observed)
    assert summary["counts"] == {"completed": 1, "failed": 0}
    assert read_shards([output]).trials["campaign_complete"].all()


@pytest.mark.parametrize("options", [{"campaign_id": "not-uuid"}, {"campaign_id": True},
                                    {"on_trial": 1}, {"checkpoint": False}])
def test_invalid_controls_fail_before_creating_output(serial_campaign, options):
    _, manifest, bindings, output = serial_campaign("nn")
    with pytest.raises((ValueError, TypeError)):
        run_manifest(manifest, instance_paths=bindings, output_path=output, **options)
    assert not output.exists()
